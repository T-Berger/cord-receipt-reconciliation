from decimal import Decimal
from pathlib import Path

import pytest

from receipt_reconciliation.cord import CordSample
from receipt_reconciliation.models import OcrResult, Receipt, ReceiptItem
from receipt_reconciliation.workflow import WorkflowConfig, run_reconciliation


def _receipt() -> Receipt:
    return Receipt(
        merchant="Tea Shop",
        items=[
            ReceiptItem(
                name="THAI ICED TEA",
                quantity=Decimal("2"),
                line_total=Decimal("40000"),
            )
        ],
        subtotal=Decimal("40000"),
        total_paid=Decimal("40000"),
        payment_method="cash",
        cash_tendered=Decimal("100000"),
        change=Decimal("60000"),
    )


def test_run_reconciliation_writes_report_and_trace(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"fake")
    receipt = _receipt()
    sample = CordSample(
        dataset="naver-clova-ix/cord-v2",
        split="train",
        row_index=9,
        image_path=image_path,
        ground_truth_json={"gt_parse": {}},
        receipt=receipt,
        image_url="https://example.invalid/receipt.jpg",
    )

    class FakeDatasetClient:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def fetch_sample(self, **_: object) -> CordSample:
            return sample

    monkeypatch.setattr("receipt_reconciliation.workflow.CordDatasetClient", FakeDatasetClient)
    monkeypatch.setattr(
        "receipt_reconciliation.workflow.extract_with_mistral",
        lambda _: OcrResult(engine="mistral", raw_text="TOTAL 40.000", receipt=receipt),
    )

    report = run_reconciliation(
        WorkflowConfig(
            split="train",
            row_index=9,
            seed=3,
            scenario="claimed_cash_tendered",
            output_dir=tmp_path / "run",
            skip_docling=True,
        )
    )

    assert report.actual_decision.status.value == "partially_approved"
    assert report.actual_decision.reimbursable_amount == Decimal("40000")
    assert report.decision_evaluation.status_match is True
    assert (tmp_path / "run" / "report.json").is_file()
    assert (tmp_path / "run" / "trace.json").is_file()


def test_run_requires_langfuse_when_requested(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Langfuse is required"):
        run_reconciliation(
            WorkflowConfig(
                output_dir=tmp_path / "required",
                require_langfuse=True,
                skip_docling=True,
            )
        )
