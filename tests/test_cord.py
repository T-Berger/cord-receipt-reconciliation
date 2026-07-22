from __future__ import annotations

import json
import random
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from receipt_reconciliation.cord import (
    CordDatasetClient,
    CordDatasetError,
    ground_truth_to_receipt,
    parse_ground_truth,
)


def test_ground_truth_to_receipt_normalizes_list_menu_and_cash_fields() -> None:
    ground_truth = {
        "gt_parse": {
            "store": {"nm": "Bebek Bistro"},
            "date": {"date": "2026-07-20", "time": "12:30"},
            "menu": [
                {"nm": "Nasi", "cnt": "2 x", "unitprice": "10,000", "price": "20,000"},
                {"nm": "Tea", "cnt": "1", "price": "5.000", "discountprice": "500"},
            ],
            "sub_total": {
                "subtotal_price": "25,000",
                "tax_price": "2,500",
                "service_price": "1,000",
                "discount_price": "500",
            },
            "total": {
                "total_price": "28,000",
                "cashprice": "30,000",
                "changeprice": "2,000",
            },
        }
    }

    receipt = ground_truth_to_receipt(json.dumps(ground_truth))

    assert receipt.merchant == "Bebek Bistro"
    assert receipt.date == "2026-07-20"
    assert receipt.time == "12:30"
    assert receipt.currency == "IDR"
    assert receipt.items[0].quantity == Decimal("2")
    assert receipt.items[0].unit_price == Decimal("10000")
    assert receipt.items[0].line_total == Decimal("20000")
    assert receipt.items[1].discount == Decimal("500")
    assert receipt.subtotal == Decimal("25000")
    assert receipt.tax == Decimal("2500")
    assert receipt.service_charge == Decimal("1000")
    assert receipt.discount == Decimal("500")
    assert receipt.total_paid == Decimal("28000")
    assert receipt.payment_method == "cash"
    assert receipt.cash_tendered == Decimal("30000")
    assert receipt.change == Decimal("2000")


def test_ground_truth_to_receipt_accepts_single_menu_and_card_variants() -> None:
    ground_truth = {
        "gt_parse": {
            "menu": {
                "name": "S-Lemon Macchiato",
                "quantity": "1X",
                "unit_price": "@21,000",
                "line_total": "21,000",
            },
            "subTotal": {"subTotalPrice": "21,000", "taxAmount": 0},
            "total": {"creditCardPrice": "21,000"},
            "currency_code": "idr",
        }
    }

    receipt = ground_truth_to_receipt(ground_truth)

    assert len(receipt.items) == 1
    assert receipt.items[0].name == "S-Lemon Macchiato"
    assert receipt.items[0].unit_price == Decimal("21000")
    assert receipt.subtotal == Decimal("21000")
    assert receipt.tax == Decimal("0")
    assert receipt.total_paid == Decimal("21000")
    assert receipt.payment_method == "credit_card"
    assert receipt.currency == "IDR"


def test_ground_truth_to_receipt_derives_purchase_total_not_cash_tendered() -> None:
    receipt = ground_truth_to_receipt(
        {
            "menu": {"nm": "CRISPY CHOCO", "price": "14,000"},
            "total": {"cash_price": "20,000", "change_price": "6,000"},
        }
    )

    assert receipt.total_paid == Decimal("14000")
    assert receipt.cash_tendered == Decimal("20000")
    assert receipt.change == Decimal("6000")


@pytest.mark.parametrize(
    "value, error",
    [
        ("not JSON", "not valid JSON"),
        ("[]", "must decode to an object"),
        ({"gt_parse": []}, "gt_parse must be an object"),
        ({"meta": {}}, "does not contain gt_parse"),
    ],
)
def test_parse_ground_truth_rejects_invalid_values(value: object, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        parse_ground_truth(value)  # type: ignore[arg-type]


def test_fetch_explicit_sample_validates_downloads_and_converts(tmp_path: Path) -> None:
    seen_paths: list[str] = []
    ground_truth = {
        "gt_parse": {
            "menu": {"nm": "Coffee", "cnt": "1", "price": "12,000"},
            "total": {"total_price": "12,000", "cashprice": "20,000", "changeprice": "8,000"},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/splits":
            return httpx.Response(
                200,
                json={"splits": [{"config": "default", "split": "train"}]},
            )
        if request.url.path == "/rows":
            assert request.url.params["offset"] == "7"
            assert request.url.params["length"] == "1"
            return httpx.Response(
                200,
                json={
                    "rows": [
                        {
                            "row_idx": 7,
                            "row": {
                                "image": {"src": "https://assets.example/receipt.jpeg?sig=x"},
                                "ground_truth": json.dumps(ground_truth),
                            },
                        }
                    ]
                },
            )
        if request.url.host == "assets.example":
            return httpx.Response(
                200, content=b"jpeg bytes", headers={"content-type": "image/jpeg"}
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = CordDatasetClient(base_url="https://viewer.example", client=http_client)
        sample = client.fetch_sample("train", row_index=7, output_dir=tmp_path)

    assert seen_paths == ["/splits", "/rows", "/receipt.jpeg"]
    assert sample.dataset == "naver-clova-ix/cord-v2"
    assert sample.split == "train"
    assert sample.row_index == 7
    assert sample.image_url.startswith("https://assets.example/")
    assert sample.image_path == tmp_path / "cord-v2-train-00007.jpeg"
    assert sample.image_path.read_bytes() == b"jpeg bytes"
    assert sample.ground_truth_json == ground_truth
    assert sample.receipt.total_paid == Decimal("12000")


def test_fetch_sample_rejects_unknown_split_before_fetching_a_row(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/splits":
            return httpx.Response(
                200,
                json={
                    "splits": [
                        {"config": "default", "split": "train"},
                        {"config": "default", "split": "test"},
                    ]
                },
            )
        raise AssertionError("a row request should not be made for an invalid split")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = CordDatasetClient(base_url="https://viewer.example", client=http_client)
        with pytest.raises(ValueError, match="Unknown CORD split 'validation'"):
            client.fetch_sample("validation", row_index=0, output_dir=tmp_path)


def test_fetch_random_sample_uses_seed_and_split_size(tmp_path: Path) -> None:
    expected_index = random.Random(19).randrange(12)
    ground_truth = {
        "gt_parse": {
            "menu": {"nm": "Tea", "price": "5,000"},
            "total": {"total_price": "5,000"},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/splits":
            return httpx.Response(200, json={"splits": [{"config": "default", "split": "test"}]})
        if request.url.path == "/size":
            return httpx.Response(
                200,
                json={"size": {"splits": [{"config": "default", "split": "test", "num_rows": 12}]}},
            )
        if request.url.path == "/rows":
            assert request.url.params["offset"] == str(expected_index)
            return httpx.Response(
                200,
                json={
                    "rows": [
                        {
                            "row_idx": expected_index,
                            "row": {
                                "image": "https://assets.example/a",
                                "ground_truth": ground_truth,
                            },
                        }
                    ]
                },
            )
        if request.url.host == "assets.example":
            return httpx.Response(200, content=b"image", headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected request: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        sample = CordDatasetClient(
            base_url="https://viewer.example", client=http_client
        ).fetch_sample("test", seed=19, output_dir=tmp_path)

    assert sample.row_index == expected_index
    assert sample.image_path.suffix == ".png"


def test_fetch_sample_falls_back_to_first_rows_for_scan_limit(tmp_path: Path) -> None:
    ground_truth = {"gt_parse": {"total": {"total_price": "1,000"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/splits":
            return httpx.Response(200, json={"splits": [{"config": "default", "split": "train"}]})
        if request.url.path == "/rows":
            return httpx.Response(500, json={"error": "Parquet scan size limit exceeded"})
        if request.url.path == "/first-rows":
            return httpx.Response(
                200,
                json={
                    "rows": [
                        {
                            "row_idx": 3,
                            "row": {
                                "image": {"src": "https://assets.example/3.jpg"},
                                "ground_truth": ground_truth,
                            },
                        }
                    ]
                },
            )
        if request.url.host == "assets.example":
            return httpx.Response(200, content=b"jpg")
        raise AssertionError(f"unexpected request: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        sample = CordDatasetClient(
            base_url="https://viewer.example", client=http_client
        ).fetch_sample("train", row_index=3, output_dir=tmp_path)

    assert sample.row_index == 3
    assert sample.receipt.total_paid == Decimal("1000")


def test_explicit_row_outside_preview_reports_clear_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/splits":
            return httpx.Response(200, json={"splits": [{"config": "default", "split": "train"}]})
        if request.url.path == "/rows":
            return httpx.Response(500, text="scan limit")
        if request.url.path == "/first-rows":
            return httpx.Response(200, json={"rows": []})
        raise AssertionError(f"unexpected request: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(CordDatasetError, match="outside the Dataset Viewer preview"):
            CordDatasetClient(base_url="https://viewer.example", client=http_client).fetch_sample(
                "train", row_index=400, output_dir=tmp_path
            )
