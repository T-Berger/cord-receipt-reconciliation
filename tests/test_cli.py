from decimal import Decimal

from receipt_reconciliation.cli import render_summary
from receipt_reconciliation.models import (
    DecisionEvaluation,
    DecisionStatus,
    ExpenseClaim,
    OcrResult,
    Receipt,
    ReimbursementDecision,
    WorkflowReport,
)


def test_render_summary_includes_actionable_decision() -> None:
    receipt = Receipt(total_paid=Decimal("40000"))
    claim = ExpenseClaim(
        claim_id="CLM-1",
        employee_id="EMP-1",
        business_purpose="Customer meeting",
        category="meals",
        currency="IDR",
        items=[],
        claimed_total=Decimal("100000"),
        receipt_refs=["receipt.jpg"],
        injected_scenario="claimed_cash_tendered",
    )
    decision = ReimbursementDecision(
        status=DecisionStatus.PARTIALLY_APPROVED,
        claimed_amount=Decimal("100000"),
        reimbursable_amount=Decimal("40000"),
        findings=[],
        summary="Only the purchase total is reimbursable.",
    )
    report = WorkflowReport(
        dataset="naver-clova-ix/cord-v2",
        split="train",
        row_index=9,
        image_path="receipt.jpg",
        claim=claim,
        ground_truth=receipt,
        mistral=OcrResult(engine="mistral", raw_text="", receipt=receipt),
        docling=None,
        extraction_evaluations=[],
        expected_decision=decision,
        actual_decision=decision,
        decision_evaluation=DecisionEvaluation(
            expected_status=decision.status,
            actual_status=decision.status,
            status_match=True,
            expected_amount=Decimal("40000"),
            actual_amount=Decimal("40000"),
            amount_match=True,
        ),
        local_trace_path="artifacts/trace.json",
    )

    summary = render_summary(report)

    assert "Decision: partially_approved" in summary
    assert "Reimbursable: IDR 40,000" in summary
    assert "Decision evaluation: PASS" in summary
    assert "Langfuse: local-only" in summary
