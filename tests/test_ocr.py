from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from receipt_reconciliation.models import RawReceipt
from receipt_reconciliation.ocr import extract_with_docling, extract_with_mistral

RAW_DATA = {
    "merchant": "TOKO TEST",
    "currency": "idr",
    "items": [
        {
            "name": "Nasi Goreng",
            "quantity": "2x",
            "unit_price": "45.000",
            "line_total": "90,000",
            "discount": None,
        }
    ],
    "subtotal": "90.000",
    "tax": "9,000",
    "total_paid": "99.000",
    "payment_method": "Tunai",
    "cash_tendered": "100.000",
    "change": "1.000",
}


class FakeOcr:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def process(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


class FakeChat:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


class FakeConverter:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.sources: list[Path] = []

    def convert(self, source: Path) -> Any:
        self.sources.append(source)
        document = SimpleNamespace(export_to_markdown=lambda: self.markdown)
        return SimpleNamespace(document=document)


def test_mistral_ocr_sends_base64_and_normalizes_annotation(tmp_path: Path) -> None:
    image = tmp_path / "receipt.png"
    image.write_bytes(b"not-a-real-image")
    response = SimpleNamespace(
        pages=[SimpleNamespace(markdown="# TOKO TEST"), {"markdown": "TOTAL 99.000"}],
        document_annotation=RAW_DATA,
        model="mistral-ocr-latest",
        usage_info={"pages_processed": 1},
    )
    ocr = FakeOcr(response)
    client = SimpleNamespace(ocr=ocr)

    result = extract_with_mistral(image, client=client)

    request = ocr.calls[0]
    assert request["model"] == "mistral-ocr-latest"
    assert request["document"]["type"] == "image_url"
    assert request["document"]["image_url"] == (
        "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode("ascii")
    )
    annotation = request["document_annotation_format"]
    assert annotation["type"] == "json_schema"
    assert "total_paid" in annotation["json_schema"]["schema"]["properties"]
    assert request["include_image_base64"] is False
    assert result.raw_text == "# TOKO TEST\n\nTOTAL 99.000"
    assert result.receipt.currency == "IDR"
    assert result.receipt.total_paid == 99000
    assert result.receipt.cash_tendered == 100000
    assert result.receipt.change == 1000
    assert result.receipt.payment_method == "cash"
    assert result.receipt.items[0].quantity == 2
    assert result.metadata == {
        "page_count": 2,
        "structured_output_strategy": "document_annotation",
        "usage": {"pages_processed": 1},
    }


def test_mistral_ocr_uses_deterministic_guardrail_for_bad_llm_json(tmp_path: Path) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"jpeg")
    response = SimpleNamespace(
        pages=[
            SimpleNamespace(
                markdown=(
                    "THAI ICED TEA\n2 @20.000 40.000\nSUB-TOTAL 40.000\n"
                    "GRANDTOTAL 40.000\nCASH 100.000\nCHANGED 60.000"
                )
            )
        ],
        document_annotation='{"truncated":',
        model="mistral-ocr-latest",
    )
    bad_chat_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    parsed=RawReceipt(cash_tendered="100.000", change="600000000000000"),
                    content=None,
                )
            )
        ]
    )
    client = SimpleNamespace(ocr=FakeOcr(response), chat=FakeChat(bad_chat_response))

    result = extract_with_mistral(image, client=client)

    assert result.receipt.total_paid == 40000
    assert result.receipt.cash_tendered == 100000
    assert result.receipt.change == 60000
    assert result.receipt.items[0].name == "THAI ICED TEA"
    assert result.metadata["structured_output_strategy"] == "deterministic_guardrail_fallback"


def test_mistral_ocr_accepts_json_annotation(tmp_path: Path) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"jpeg")
    response = {
        "pages": [{"markdown": "receipt"}],
        "document_annotation": json.dumps(RAW_DATA),
        "model": "mistral-ocr-latest",
    }

    result = extract_with_mistral(image, client=SimpleNamespace(ocr=FakeOcr(response)))

    assert result.receipt.merchant == "TOKO TEST"
    assert result.receipt.subtotal == 90000


def test_mistral_ocr_falls_back_when_document_annotation_is_missing(tmp_path: Path) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"jpeg")
    response = SimpleNamespace(
        pages=[SimpleNamespace(markdown="Nasi Goreng 90.000\nTOTAL 99.000")],
        document_annotation=None,
    )
    parsed = RawReceipt.model_validate(RAW_DATA)
    chat_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, content=None))]
    )
    client = SimpleNamespace(ocr=FakeOcr(response), chat=FakeChat(chat_response))

    result = extract_with_mistral(image, client=client)

    assert result.receipt.total_paid == 99000
    assert result.metadata["structured_output_strategy"] == "mistral_chat_fallback"


def test_docling_markdown_is_structured_with_same_receipt_schema(tmp_path: Path) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"jpeg")
    markdown = "# TOKO TEST\n\nNasi Goreng 2 x 45.000\nTOTAL 99.000"
    converter = FakeConverter(markdown)
    parsed = RawReceipt.model_validate(RAW_DATA)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, content=None))]
    )
    chat = FakeChat(response)
    client = SimpleNamespace(chat=chat)

    result = extract_with_docling(image, client=client, converter=converter)

    assert result is not None
    assert converter.sources == [image]
    call = chat.calls[0]
    assert call["response_format"] is RawReceipt
    assert call["temperature"] == 0
    assert markdown in call["messages"][1]["content"]
    assert result.engine == "docling"
    assert result.raw_text == markdown
    assert result.receipt.total_paid == 99000
    assert result.metadata["markdown_characters"] == len(markdown)


def test_docling_can_be_skipped_before_file_or_dependency_checks() -> None:
    assert extract_with_docling("does-not-exist.jpg", skip=True) is None


def test_docling_guardrail_handles_detached_reading_order(tmp_path: Path) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"jpeg")
    markdown = (
        "THAI ICED TEA\n\n2\n\n@20.000\n\nSUB-TOTAL\n\nGRANDTOTAL\n\n"
        "CASH CHANGED\n\n<!-- image -->\n\n40.000\n\n40.000\n\n40.000\n\n"
        "100.000\n\n60.000"
    )
    converter = FakeConverter(markdown)
    bad_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(parsed=RawReceipt(change="600000000000000"), content=None)
            )
        ]
    )

    result = extract_with_docling(
        image,
        client=SimpleNamespace(chat=FakeChat(bad_response)),
        converter=converter,
    )

    assert result is not None
    assert result.receipt.items[0].name == "THAI ICED TEA"
    assert result.receipt.items[0].quantity == 2
    assert result.receipt.items[0].unit_price == 20000
    assert result.receipt.items[0].line_total == 40000
    assert result.receipt.subtotal == 40000
    assert result.receipt.total_paid == 40000
    assert result.receipt.cash_tendered == 100000
    assert result.receipt.change == 60000
    assert result.metadata["structured_output_strategy"] == "deterministic_guardrail_fallback"
