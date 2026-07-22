from decimal import Decimal

from receipt_reconciliation.evaluation import evaluate_decision, evaluate_extraction
from receipt_reconciliation.models import (
    DecisionStatus,
    Receipt,
    ReceiptItem,
    ReimbursementDecision,
)


def expected_receipt() -> Receipt:
    return Receipt(
        merchant="CAFE, EXAMPLE!",
        date="2026-07-21",
        currency="IDR",
        items=[
            ReceiptItem(
                name="Nasi Goreng",
                quantity=Decimal("2"),
                unit_price=Decimal("50"),
                line_total=Decimal("100"),
            )
        ],
        subtotal=Decimal("100"),
        tax=Decimal("10"),
        total_paid=Decimal("110"),
        payment_method="cash",
    )


def test_identical_extraction_has_perfect_accuracy() -> None:
    expected = expected_receipt()

    evaluation = evaluate_extraction("mistral", expected, expected)

    assert evaluation.compared_fields > 0
    assert evaluation.matched_fields == evaluation.compared_fields
    assert evaluation.field_accuracy == 1.0


def test_extraction_reports_field_level_mismatches() -> None:
    expected = expected_receipt()
    actual = expected.model_copy(
        update={
            "merchant": "Cafe Example",
            "total_paid": Decimal("100"),
            "items": [
                ReceiptItem(
                    name="Nasi Goreng",
                    quantity=Decimal("2"),
                    unit_price=Decimal("50"),
                    line_total=None,
                ),
                ReceiptItem(name="Hallucinated item", line_total=Decimal("1")),
            ],
        }
    )

    evaluation = evaluate_extraction("docling", actual, expected)
    by_field = {field.field: field for field in evaluation.fields}

    assert by_field["merchant"].match
    assert not by_field["total_paid"].match
    assert not by_field["items.count"].match
    assert not by_field["items[0].line_total"].match
    assert evaluation.field_accuracy < 1.0


def decision(status: DecisionStatus, amount: str) -> ReimbursementDecision:
    return ReimbursementDecision(
        status=status,
        claimed_amount=Decimal("110"),
        reimbursable_amount=Decimal(amount),
        findings=[],
        summary="test",
    )


def test_decision_evaluation_scores_status_and_amount_separately() -> None:
    expected = decision(DecisionStatus.PARTIALLY_APPROVED, "100")
    actual = decision(DecisionStatus.APPROVED, "100.001")

    evaluation = evaluate_decision(actual, expected)

    assert not evaluation.status_match
    assert evaluation.amount_match
    assert evaluation.expected_status == DecisionStatus.PARTIALLY_APPROVED
    assert evaluation.actual_amount == Decimal("100.001")
