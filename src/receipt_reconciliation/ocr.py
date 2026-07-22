from __future__ import annotations

import base64
import mimetypes
import os
import re
import ssl
from pathlib import Path
from typing import Any

import httpx

from .models import OcrResult, RawReceipt
from .normalization import money_string, normalize_raw_receipt, parse_money, parse_quantity

MISTRAL_OCR_MODEL = "mistral-ocr-latest"
DOCLING_STRUCTURING_MODEL = "mistral-small-latest"
MISTRAL_STRUCTURING_MODEL = "mistral-small-latest"

_ANNOTATION_PROMPT = """Extract this receipt into the supplied schema.
Copy values as printed. total_paid is the final purchase total: do not use cash
tendered and do not add change. Keep tax, discounts, service charge, cash
tendered, and change in their separate fields. Use an ISO currency code only
when it can be identified; otherwise leave currency null.
"""

_DOCLING_SYSTEM_PROMPT = """You structure receipt OCR into the supplied schema.
Use only evidence in the provided markdown. Copy printed values without doing
speculative arithmetic. total_paid is the final purchase total, never cash
tendered and never purchase total plus change.
"""

_AMOUNT_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:Rp\s*)?-?\d[\d.,]*", re.IGNORECASE)
_DATE_TOKEN = re.compile(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b")
_TIME_TOKEN = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    return str(value)


def _mistral_client(api_key: str | None) -> Any:
    key = api_key or os.getenv("MISTRAL_API_KEY")
    if not key:
        raise RuntimeError(
            "Mistral is required for receipt extraction; set MISTRAL_API_KEY "
            "or pass api_key/client explicitly."
        )
    try:
        from mistralai import Mistral
    except ImportError:
        try:
            # mistralai 2.7+ is distributed as a namespace package on some
            # platforms and exposes the generated client from this submodule.
            from mistralai.client import Mistral
        except ImportError as exc:  # pragma: no cover - only without the dependency
            raise RuntimeError(
                "Install the 'mistralai' package to use receipt extraction."
            ) from exc
    try:
        import truststore

        ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:  # pragma: no cover - dependency is present in normal installs
        ssl_context = ssl.create_default_context()
    return Mistral(api_key=key, client=httpx.Client(verify=ssl_context, timeout=120.0))


def _image_data_url(image_path: Path) -> str:
    content_type, _ = mimetypes.guess_type(image_path.name)
    if not content_type or not content_type.startswith("image/"):
        content_type = "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _annotation_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "raw_receipt",
            "description": "Structured fields extracted from one purchase receipt",
            "schema": RawReceipt.model_json_schema(),
            "strict": True,
        },
    }


def _raw_receipt(value: Any) -> RawReceipt:
    if isinstance(value, RawReceipt):
        return value
    if isinstance(value, str):
        return RawReceipt.model_validate_json(value)
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return RawReceipt.model_validate(value)


def _page_markdown(response: Any) -> str:
    pages = _get_value(response, "pages", []) or []
    markdown = [str(_get_value(page, "markdown", "") or "").strip() for page in pages]
    return "\n\n".join(page for page in markdown if page)


def _last_amount(line: str) -> str | None:
    matches = _AMOUNT_TOKEN.findall(line)
    return matches[-1].strip() if matches else None


def _line_for(lines: list[str], keywords: tuple[str, ...]) -> str | None:
    for line in lines:
        compact = re.sub(r"[\s_-]+", "", line.casefold())
        if any(re.sub(r"[\s_-]+", "", keyword.casefold()) in compact for keyword in keywords):
            if _last_amount(line) is not None:
                return line
    return None


def _heuristic_raw_receipt(markdown: str) -> RawReceipt:
    """Recover common receipt fields when an LLM emits malformed structured JSON."""

    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in markdown.splitlines()
        if line.strip() and not line.lstrip().startswith("![")
    ]
    subtotal_line = _line_for(lines, ("sub-total", "subtotal"))
    total_line = _line_for(lines, ("grandtotal", "grand total"))
    if total_line is None:
        total_line = next(
            (
                line
                for line in lines
                if re.match(r"^\s*total\b", line, re.IGNORECASE)
                and "subtotal" not in re.sub(r"[\s_-]", "", line.casefold())
                and _last_amount(line) is not None
            ),
            None,
        )
    tax_line = _line_for(lines, ("tax", "pb1", "ppn"))
    service_line = _line_for(lines, ("service charge", "service"))
    discount_line = _line_for(lines, ("discount", "disc", "voucher"))
    cash_line = _line_for(lines, ("cash", "tunai"))
    change_line = _line_for(lines, ("changed", "change", "kembali"))

    boundary = len(lines)
    for index, line in enumerate(lines):
        compact = re.sub(r"[\s_-]", "", line.casefold())
        if any(token in compact for token in ("subtotal", "grandtotal")):
            boundary = index
            break

    summary_labels: list[tuple[str, int]] = []
    for index, line in enumerate(lines[boundary:], start=boundary):
        compact = re.sub(r"[\s_-]", "", line.casefold())
        if "subtotal" in compact:
            summary_labels.append(("subtotal", index))
        elif "grandtotal" in compact or re.fullmatch(r"total", compact):
            summary_labels.append(("total", index))
        if any(token in compact for token in ("tax", "pb1", "ppn")):
            summary_labels.append(("tax", index))
        if "service" in compact:
            summary_labels.append(("service", index))
        if any(token in compact for token in ("discount", "disc", "voucher")):
            summary_labels.append(("discount", index))
        if any(token in compact for token in ("cash", "tunai")):
            summary_labels.append(("cash", index))
        if any(token in compact for token in ("changed", "change", "kembali")):
            summary_labels.append(("change", index))

    detached_summary: dict[str, str] = {}
    trailing_item_totals: list[str] = []
    if summary_labels and all(_last_amount(lines[index]) is None for _, index in summary_labels):
        trailing_amounts = [
            amount for line in lines[boundary:] for amount in _AMOUNT_TOKEN.findall(line)
        ]
        if len(trailing_amounts) >= len(summary_labels):
            summary_amounts = trailing_amounts[-len(summary_labels) :]
            detached_summary = {
                field: value
                for (field, _), value in zip(summary_labels, summary_amounts, strict=True)
            }
            trailing_item_totals = trailing_amounts[: -len(summary_labels)]

    items = []
    used_name_lines: set[int] = set()
    consumed_lines: set[int] = set()
    for index, line in enumerate(lines[:boundary]):
        if index in consumed_lines:
            continue
        amounts = _AMOUNT_TOKEN.findall(line)
        if not amounts:
            continue
        if _DATE_TOKEN.search(line) or _TIME_TOKEN.search(line):
            continue
        lowered = line.casefold()
        if any(word in lowered for word in ("invoice", "receipt", "order", "table")):
            continue

        inline_name = _AMOUNT_TOKEN.sub("", line).replace("@", " ").strip(" :-xX")
        name = inline_name if re.search(r"[A-Za-z]", inline_name) else ""
        if not name and index > 0 and index - 1 not in used_name_lines:
            candidate = lines[index - 1]
            if not _AMOUNT_TOKEN.search(candidate) and re.search(r"[A-Za-z]", candidate):
                name = candidate.strip(" :-")
                used_name_lines.add(index - 1)
        if not name:
            continue

        quantity = None
        unit_price = None
        line_total = amounts[-1]
        first_value = parse_money(amounts[0])
        if (
            len(amounts) == 1
            and first_value is not None
            and first_value <= 100
            and index + 1 < boundary
            and lines[index + 1].lstrip().startswith("@")
            and _last_amount(lines[index + 1]) is not None
        ):
            quantity = amounts[0]
            unit_price = _last_amount(lines[index + 1])
            consumed_lines.add(index + 1)
            if trailing_item_totals:
                line_total = trailing_item_totals.pop(0)
            else:
                quantity_value = parse_quantity(quantity)
                unit_value = parse_money(unit_price)
                line_total = (
                    money_string(quantity_value * unit_value) if unit_value is not None else None
                )
        elif len(amounts) >= 2 and first_value is not None and first_value <= 100:
            quantity = amounts[0]
            unit_price = amounts[-2] if len(amounts) >= 3 else None
        items.append(
            {
                "name": name,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "discount": None,
            }
        )

    date_match = _DATE_TOKEN.search(markdown)
    time_match = _TIME_TOKEN.search(markdown)
    currency = "IDR" if re.search(r"\b(?:Rp|IDR)\b", markdown, re.IGNORECASE) else None
    return RawReceipt.model_validate(
        {
            "merchant": None,
            "date": date_match.group(0) if date_match else None,
            "time": time_match.group(0) if time_match else None,
            "currency": currency,
            "items": items,
            "subtotal": detached_summary.get("subtotal")
            or (_last_amount(subtotal_line) if subtotal_line else None),
            "tax": detached_summary.get("tax") or (_last_amount(tax_line) if tax_line else None),
            "service_charge": detached_summary.get("service")
            or (_last_amount(service_line) if service_line else None),
            "discount": detached_summary.get("discount")
            or (_last_amount(discount_line) if discount_line else None),
            "total_paid": detached_summary.get("total")
            or (_last_amount(total_line) if total_line else None),
            "payment_method": (
                "cash" if cash_line or any(field == "cash" for field, _ in summary_labels) else None
            ),
            "cash_tendered": detached_summary.get("cash")
            or (_last_amount(cash_line) if cash_line else None),
            "change": detached_summary.get("change")
            or (_last_amount(change_line) if change_line else None),
        }
    )


def _structured_receipt_is_usable(raw: RawReceipt, markdown: str) -> bool:
    receipt = normalize_raw_receipt(raw)
    heuristic = normalize_raw_receipt(_heuristic_raw_receipt(markdown))
    if heuristic.total_paid is not None and receipt.total_paid is None:
        return False
    evidence_amounts = [
        value
        for value in (
            heuristic.total_paid,
            heuristic.subtotal,
            heuristic.cash_tendered,
            heuristic.change,
        )
        if value is not None
    ]
    extracted_amounts = [
        value
        for value in (
            receipt.total_paid,
            receipt.subtotal,
            receipt.cash_tendered,
            receipt.change,
        )
        if value is not None
    ]
    if evidence_amounts and extracted_amounts:
        ceiling = max(abs(value) for value in evidence_amounts) * 100
        if any(abs(value) > ceiling for value in extracted_amounts):
            return False
    return bool(receipt.items or receipt.total_paid is not None)


def extract_with_mistral(
    image_path: str | Path,
    *,
    api_key: str | None = None,
    client: Any | None = None,
    model: str = MISTRAL_OCR_MODEL,
    structuring_model: str = MISTRAL_STRUCTURING_MODEL,
) -> OcrResult:
    """OCR and structure a local receipt image with Mistral Document AI.

    The image bytes are sent as a base64 ``image_url``. The request returns page
    markdown and normally a schema-constrained document annotation. Missing or
    malformed annotations fall back to structuring the retained OCR markdown.
    """

    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Receipt image does not exist: {path}")
    mistral = client or _mistral_client(api_key)
    response = mistral.ocr.process(
        model=model,
        document={"type": "image_url", "image_url": _image_data_url(path)},
        document_annotation_format=_annotation_format(),
        document_annotation_prompt=_ANNOTATION_PROMPT,
        include_image_base64=False,
    )

    annotation = _get_value(response, "document_annotation")
    markdown = _page_markdown(response)
    if annotation is None:
        try:
            raw = _structure_markdown(mistral, markdown, structuring_model)
            strategy = "mistral_chat_fallback"
        except ValueError:
            raw = _heuristic_raw_receipt(markdown)
            strategy = "deterministic_guardrail_fallback"
    else:
        strategy = "document_annotation"
        try:
            raw = _raw_receipt(annotation)
        except ValueError:
            # Document annotations can occasionally be cut off on dense or noisy
            # images. The OCR markdown is still valid evidence, so structure that
            # text with the same schema instead of discarding the whole run.
            try:
                raw = _structure_markdown(mistral, markdown, structuring_model)
                strategy = "mistral_chat_fallback"
            except ValueError:
                raw = _heuristic_raw_receipt(markdown)
                strategy = "deterministic_guardrail_fallback"
    if not _structured_receipt_is_usable(raw, markdown):
        raw = _heuristic_raw_receipt(markdown)
        strategy = "deterministic_guardrail_fallback"
    pages = _get_value(response, "pages", []) or []
    response_model = _get_value(response, "model", model) or model
    usage = _jsonable(_get_value(response, "usage_info"))
    metadata: dict[str, object] = {"page_count": len(pages)}
    metadata["structured_output_strategy"] = strategy
    if strategy != "document_annotation":
        metadata["structuring_model"] = structuring_model
    if usage is not None:
        metadata["usage"] = usage
    return OcrResult(
        engine="mistral",
        raw_text=markdown,
        receipt=normalize_raw_receipt(raw),
        model=str(response_model),
        metadata=metadata,
    )


def _docling_markdown(image_path: Path, converter: Any | None) -> str:
    if converter is None:
        try:
            # Docling/RapidOCR may download model artifacts through requests;
            # route that traffic through the OS trust store as well.
            import truststore

            truststore.inject_into_ssl()
            from docling.document_converter import DocumentConverter
        except ImportError as exc:  # pragma: no cover - depends on optional local runtime
            raise RuntimeError(
                "Install the 'docling' package to run the Docling comparison."
            ) from exc
        converter = DocumentConverter()
    result = converter.convert(image_path)
    document = _get_value(result, "document")
    if document is None or not hasattr(document, "export_to_markdown"):
        raise ValueError("Docling conversion did not return an exportable document.")
    return str(document.export_to_markdown())


def _parsed_chat_receipt(response: Any) -> RawReceipt:
    choices = _get_value(response, "choices", []) or []
    if not choices:
        raise ValueError("Mistral structuring response did not contain a choice.")
    message = _get_value(choices[0], "message")
    parsed = _get_value(message, "parsed")
    if parsed is not None:
        return _raw_receipt(parsed)
    content = _get_value(message, "content")
    if not isinstance(content, str):
        raise ValueError("Mistral structuring response did not contain parsed JSON.")
    return _raw_receipt(content)


def _structure_markdown(client: Any, markdown: str, model: str) -> RawReceipt:
    response = client.chat.parse(
        model=model,
        messages=[
            {"role": "system", "content": _DOCLING_SYSTEM_PROMPT},
            {"role": "user", "content": f"Structure this receipt OCR markdown:\n\n{markdown}"},
        ],
        response_format=RawReceipt,
        temperature=0,
        max_tokens=2048,
    )
    return _parsed_chat_receipt(response)


def extract_with_docling(
    image_path: str | Path,
    *,
    api_key: str | None = None,
    client: Any | None = None,
    converter: Any | None = None,
    skip: bool = False,
    structuring_model: str = DOCLING_STRUCTURING_MODEL,
) -> OcrResult | None:
    """Convert locally with Docling, then structure its markdown with Mistral.

    Docling is imported only when this function is actually run. ``skip=True``
    avoids both the local conversion and the Mistral call, which is useful on
    machines where Docling's OCR dependencies are unavailable.
    """

    if skip:
        return None
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Receipt image does not exist: {path}")

    markdown = _docling_markdown(path, converter)
    mistral = client or _mistral_client(api_key)
    strategy = "mistral_chat"
    try:
        raw = _structure_markdown(mistral, markdown, structuring_model)
    except ValueError:
        raw = _heuristic_raw_receipt(markdown)
        strategy = "deterministic_guardrail_fallback"
    if not _structured_receipt_is_usable(raw, markdown):
        raw = _heuristic_raw_receipt(markdown)
        strategy = "deterministic_guardrail_fallback"
    return OcrResult(
        engine="docling",
        raw_text=markdown,
        receipt=normalize_raw_receipt(raw),
        model=structuring_model,
        metadata={
            "local_converter": "docling",
            "structuring_engine": "mistral_chat",
            "structured_output_strategy": strategy,
            "markdown_characters": len(markdown),
        },
    )


# Short aliases make the functions pleasant to use from orchestration code.
mistral_ocr = extract_with_mistral
docling_ocr = extract_with_docling

__all__ = [
    "DOCLING_STRUCTURING_MODEL",
    "MISTRAL_OCR_MODEL",
    "MISTRAL_STRUCTURING_MODEL",
    "docling_ocr",
    "extract_with_docling",
    "extract_with_mistral",
    "mistral_ocr",
]
