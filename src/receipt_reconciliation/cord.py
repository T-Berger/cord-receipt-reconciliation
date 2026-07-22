from __future__ import annotations

import json
import random
import re
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import RawReceipt, RawReceiptItem, Receipt
from .normalization import money_string, normalize_raw_receipt, parse_money

DEFAULT_DATASET = "naver-clova-ix/cord-v2"
DEFAULT_CONFIG = "default"
DEFAULT_VIEWER_URL = "https://datasets-server.huggingface.co"


class CordDatasetError(RuntimeError):
    """Raised when CORD data cannot be obtained from the Dataset Viewer."""


@dataclass(frozen=True, slots=True)
class CordSample:
    dataset: str
    split: str
    row_index: int
    image_path: Path
    ground_truth_json: dict[str, Any]
    receipt: Receipt
    image_url: str


def parse_ground_truth(value: str | bytes | Mapping[str, Any]) -> dict[str, Any]:
    """Parse and validate a CORD ``ground_truth`` cell.

    Dataset Viewer returns the cell as a JSON string, while fixtures and callers
    often already have the decoded mapping. Supporting both keeps the conversion
    function useful outside the HTTP client.
    """

    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("CORD ground_truth is not valid JSON") from exc
    elif isinstance(value, Mapping):
        decoded = dict(value)
    else:
        raise TypeError("CORD ground_truth must be a JSON string, bytes, or mapping")

    if not isinstance(decoded, dict):
        raise ValueError("CORD ground_truth JSON must decode to an object")

    gt_parse = decoded.get("gt_parse")
    if gt_parse is not None and not isinstance(gt_parse, Mapping):
        raise ValueError("CORD ground_truth.gt_parse must be an object")
    if gt_parse is None and not any(key in decoded for key in ("menu", "sub_total", "total")):
        raise ValueError("CORD ground_truth does not contain gt_parse")
    return decoded


def ground_truth_to_receipt(value: str | bytes | Mapping[str, Any]) -> Receipt:
    """Convert CORD v2 ``gt_parse`` data into the workflow's normalized receipt."""

    ground_truth = parse_ground_truth(value)
    parsed_value = ground_truth.get("gt_parse", ground_truth)
    parsed = dict(parsed_value)

    items: list[RawReceiptItem] = []
    for item in _menu_entries(_pick(parsed, "menu", "items")):
        name = _as_text(_pick(item, "nm", "name", "item_name", "description"))
        if not name:
            continue
        items.append(
            RawReceiptItem(
                name=name,
                quantity=_as_text(_pick(item, "cnt", "qty", "quantity")),
                unit_price=_as_text(
                    _pick(item, "unitprice", "unit_price", "price_each", "unit_cost")
                ),
                line_total=_as_text(_pick(item, "price", "line_total", "total_price", "amount")),
                discount=_as_text(_pick(item, "discountprice", "discount_price", "discount")),
            )
        )

    sub_total = _mapping(_pick(parsed, "sub_total", "subtotal"))
    total = _mapping(_pick(parsed, "total", "payment"))
    store_value = _pick(parsed, "store", "merchant")
    store = _mapping(store_value)

    merchant = _as_text(
        _pick(store, "nm", "name", "merchant_name", "store_name") if store else store_value
    ) or _as_text(_pick(parsed, "merchant_name", "store_name"))

    date_value = _pick(parsed, "date", "receipt_date", "transaction_date")
    date_section = _mapping(date_value)
    date = _as_text(_pick(date_section, "date", "value")) if date_section else _as_text(date_value)
    time = _as_text(
        _pick(date_section, "time", "receipt_time", "transaction_time")
        if date_section
        else _pick(parsed, "time", "receipt_time", "transaction_time")
    )

    subtotal = _as_text(_pick(sub_total, "subtotal_price", "subtotal", "sub_total_price"))
    tax = _as_text(_pick(sub_total, "tax_price", "tax", "tax_amount"))
    service_charge = _as_text(
        _pick(sub_total, "service_price", "service_charge", "service", "service_amount")
    )
    discount = _as_text(
        _pick(
            sub_total,
            "discount_price",
            "discountprice",
            "discount",
            "voucher_price",
            "voucher",
        )
    ) or _as_text(_pick(total, "discount_price", "discountprice", "discount"))

    total_paid = _as_text(_pick(total, "total_price", "total", "grand_total", "amount_due"))
    cash_tendered = _as_text(_pick(total, "cashprice", "cash_price", "cash_tendered", "tendered"))
    change = _as_text(_pick(total, "changeprice", "change_price", "change"))
    credit_amount = _as_text(
        _pick(total, "creditcardprice", "credit_card_price", "card_price", "card_amount")
    )
    debit_amount = _as_text(_pick(total, "debitcardprice", "debit_card_price"))
    ewallet_amount = _as_text(
        _pick(total, "emoneyprice", "e_money_price", "ewalletprice", "ewallet_price")
    )

    explicit_payment = _as_text(
        _pick(total, "payment_method", "payment_type", "tender_type")
    ) or _as_text(_pick(parsed, "payment_method", "payment_type"))
    if explicit_payment:
        payment_method = explicit_payment
    elif cash_tendered is not None:
        payment_method = "cash"
    elif debit_amount is not None:
        payment_method = "debit_card"
    elif credit_amount is not None:
        payment_method = "credit_card"
    elif ewallet_amount is not None:
        payment_method = "ewallet"
    else:
        payment_method = None

    # CORD normally supplies total.total_price. For incomplete annotations, a
    # card amount is the purchase amount, while cash tendered must have change
    # subtracted to avoid treating change as an expense.
    if total_paid is None:
        total_paid = debit_amount or credit_amount or ewallet_amount
    if total_paid is None and cash_tendered is not None:
        cash_value = parse_money(cash_tendered)
        change_value = parse_money(change)
        if cash_value is not None:
            total_paid = money_string(cash_value - (change_value or 0))

    currency = _as_text(_pick(parsed, "currency", "currency_code")) or "IDR"
    raw = RawReceipt(
        merchant=merchant,
        date=date,
        time=time,
        currency=currency,
        items=items,
        subtotal=subtotal,
        tax=tax,
        service_charge=service_charge,
        discount=discount,
        total_paid=total_paid,
        payment_method=payment_method,
        cash_tendered=cash_tendered,
        change=change,
    )
    return normalize_raw_receipt(raw)


class CordDatasetClient:
    """Small synchronous client for the Hugging Face Dataset Viewer API."""

    def __init__(
        self,
        *,
        dataset: str = DEFAULT_DATASET,
        config: str = DEFAULT_CONFIG,
        base_url: str = DEFAULT_VIEWER_URL,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.dataset = dataset
        self.config = config
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "cord-receipt-reconciliation/0.1"},
            verify=_system_trust_context(),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CordDatasetClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_splits(self) -> tuple[str, ...]:
        payload = self._request_json("/splits", dataset=self.dataset)
        split_records = payload.get("splits", [])
        if not isinstance(split_records, list):
            raise CordDatasetError("Dataset Viewer returned an invalid splits response")
        splits = {
            str(record["split"])
            for record in split_records
            if isinstance(record, Mapping)
            and record.get("config") == self.config
            and record.get("split") is not None
        }
        if not splits:
            raise CordDatasetError(f"Dataset Viewer returned no splits for config {self.config!r}")
        return tuple(sorted(splits))

    def validate_split(self, split: str) -> None:
        available = self.list_splits()
        if split not in available:
            choices = ", ".join(available)
            raise ValueError(f"Unknown CORD split {split!r}; choose one of: {choices}")

    def split_size(self, split: str) -> int:
        payload = self._request_json("/size", dataset=self.dataset)
        size = payload.get("size", {})
        records = size.get("splits", []) if isinstance(size, Mapping) else []
        for record in records:
            if (
                isinstance(record, Mapping)
                and record.get("config") == self.config
                and record.get("split") == split
            ):
                count = record.get("num_rows")
                if isinstance(count, int) and count >= 0:
                    return count
        raise CordDatasetError(f"Dataset Viewer did not report a size for split {split!r}")

    def fetch_sample(
        self,
        split: str,
        row_index: int | None = None,
        seed: int | None = None,
        output_dir: Path = Path("artifacts/receipts"),
    ) -> CordSample:
        self.validate_split(split)
        if row_index is not None and row_index < 0:
            raise ValueError("row_index must be zero or greater")

        rng = random.Random(seed)
        explicit_row = row_index is not None
        if row_index is None:
            count = self.split_size(split)
            if count == 0:
                raise CordDatasetError(f"CORD split {split!r} is empty")
            row_index = rng.randrange(count)

        try:
            row_record = self._fetch_row(split, row_index)
        except CordDatasetError as rows_error:
            # The CORD train parquet shard currently exceeds the Dataset
            # Viewer's /rows scan limit. /first-rows is cached separately and
            # still provides a useful, deterministic compatibility path.
            preview = self._first_rows(split)
            if explicit_row:
                row_record = next(
                    (record for record in preview if record.get("row_idx") == row_index), None
                )
                if row_record is None:
                    raise CordDatasetError(
                        f"CORD row {row_index} could not be loaded and is outside the "
                        "Dataset Viewer preview"
                    ) from rows_error
            else:
                if not preview:
                    raise rows_error
                row_record = rng.choice(preview)
                preview_index = row_record.get("row_idx")
                if not isinstance(preview_index, int):
                    raise CordDatasetError(
                        "Dataset Viewer preview row has no integer row_idx"
                    ) from rows_error
                row_index = preview_index

        row = row_record.get("row")
        if not isinstance(row, Mapping):
            raise CordDatasetError("Dataset Viewer row has no row object")
        image_url = _image_url(row.get("image"))
        ground_truth = parse_ground_truth(row.get("ground_truth"))
        receipt = ground_truth_to_receipt(ground_truth)
        image_path = self._download_image(image_url, split, row_index, Path(output_dir))
        return CordSample(
            dataset=self.dataset,
            split=split,
            row_index=row_index,
            image_path=image_path,
            ground_truth_json=ground_truth,
            receipt=receipt,
            image_url=image_url,
        )

    def _fetch_row(self, split: str, row_index: int) -> Mapping[str, Any]:
        payload = self._request_json(
            "/rows",
            dataset=self.dataset,
            config=self.config,
            split=split,
            offset=row_index,
            length=1,
        )
        rows = payload.get("rows", [])
        if not isinstance(rows, list) or not rows:
            raise CordDatasetError(f"CORD row {row_index} does not exist in split {split!r}")
        for record in rows:
            if isinstance(record, Mapping) and record.get("row_idx") == row_index:
                return record
        raise CordDatasetError(f"Dataset Viewer did not return requested CORD row {row_index}")

    def _first_rows(self, split: str) -> list[Mapping[str, Any]]:
        payload = self._request_json(
            "/first-rows", dataset=self.dataset, config=self.config, split=split
        )
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise CordDatasetError("Dataset Viewer returned an invalid first-rows response")
        return [record for record in rows if isinstance(record, Mapping)]

    def _request_json(self, path: str, **params: object) -> dict[str, Any]:
        try:
            response = self._client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise CordDatasetError(f"Dataset Viewer request failed for {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise CordDatasetError(f"Dataset Viewer returned non-object JSON for {path}")
        return payload

    def _download_image(self, image_url: str, split: str, row_index: int, output_dir: Path) -> Path:
        try:
            response = self._client.get(image_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CordDatasetError(
                f"Failed to download CORD image for row {row_index}: {exc}"
            ) from exc
        if not response.content:
            raise CordDatasetError(f"Downloaded CORD image for row {row_index} is empty")

        extension = Path(urlparse(image_url).path).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,5}", extension):
            extension = _extension_for_content_type(response.headers.get("content-type"))
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"cord-v2-{split}-{row_index:05d}{extension}"
        destination.write_bytes(response.content)
        return destination


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _pick(mapping: Mapping[str, Any], *keys: str) -> Any:
    indexed = {_canonical_key(str(key)): value for key, value in mapping.items()}
    for key in keys:
        canonical = _canonical_key(key)
        if canonical in indexed:
            return indexed[canonical]
    return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _menu_entries(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if _pick(value, "nm", "name", "item_name", "description") is not None:
            return [value]
        return [entry for entry in value.values() if isinstance(entry, Mapping)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [entry for entry in value if isinstance(entry, Mapping)]
    return []


def _as_text(value: Any) -> str | None:
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return None
    text = str(value).strip()
    return text or None


def _image_url(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        src = value.get("src") or value.get("url")
        if isinstance(src, str) and src:
            return src
    raise CordDatasetError("Dataset Viewer row has no image URL")


def _extension_for_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/tiff": ".tiff",
    }.get(normalized, ".jpg")


def _system_trust_context() -> ssl.SSLContext:
    """Use the OS trust store so enterprise TLS roots work on Windows/macOS."""

    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:  # pragma: no cover - dependency is present in normal installs
        return ssl.create_default_context()
