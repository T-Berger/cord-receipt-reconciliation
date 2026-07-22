from decimal import Decimal

import pytest

from receipt_reconciliation.claims import generate_synthetic_claim
from receipt_reconciliation.models import (
    ClaimItem,
    DecisionStatus,
    ExpenseClaim,
    Receipt,
    ReceiptItem,
)
from receipt_reconciliation.policy import ExpensePolicy, evaluate_claim


def receipt() -> Receipt:
    return Receipt(
        merchant="Cafe Example",
        currency="IDR",
        items=[ReceiptItem(name="Team meal", line_total=Decimal("100"))],
        subtotal=Decimal("100"),
        tax=Decimal("10"),
        discount=Decimal("5"),
        total_paid=Decimal("105"),
        payment_method="cash",
        cash_tendered=Decimal("120"),
        change=Decimal("15"),
    )


def rules(decision: object) -> set[str]:
    return {finding.rule_id for finding in decision.findings}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("scenario", "status", "amount", "rule"),
    [
        ("exact", DecisionStatus.APPROVED, Decimal("105"), None),
        (
            "claimed_cash_tendered",
            DecisionStatus.PARTIALLY_APPROVED,
            Decimal("105"),
            "CASH_TENDERED_NOT_REIMBURSABLE",
        ),
        (
            "change_added",
            DecisionStatus.PARTIALLY_APPROVED,
            Decimal("105"),
            "CHANGE_NOT_REIMBURSABLE",
        ),
        (
            "tax_doubled",
            DecisionStatus.PARTIALLY_APPROVED,
            Decimal("105"),
            "TAX_DOUBLE_COUNTED",
        ),
        ("tax_omitted", DecisionStatus.APPROVED, Decimal("95"), "TAX_OMITTED"),
        (
            "discount_ignored",
            DecisionStatus.PARTIALLY_APPROVED,
            Decimal("105"),
            "DISCOUNT_IGNORED",
        ),
        (
            "item_tampered",
            DecisionStatus.PARTIALLY_APPROVED,
            Decimal("105"),
            "ITEM_AMOUNT_MISMATCH",
        ),
        (
            "unsupported_personal_item",
            DecisionStatus.PARTIALLY_APPROVED,
            Decimal("105"),
            "NON_REIMBURSABLE_CLAIM_ITEM",
        ),
    ],
)
def test_scenario_decisions(
    scenario: str,
    status: DecisionStatus,
    amount: Decimal,
    rule: str | None,
) -> None:
    claim = generate_synthetic_claim(receipt(), ["receipt.png"], 7, scenario)
    decision = evaluate_claim(claim, receipt())

    assert decision.status == status
    assert decision.reimbursable_amount == amount
    if rule:
        assert rule in rules(decision)


def test_non_reimbursable_receipt_item_is_deducted() -> None:
    evidence = Receipt(
        merchant="Bistro",
        currency="IDR",
        items=[
            ReceiptItem(name="Meal", line_total=Decimal("80")),
            ReceiptItem(name="Beer", line_total=Decimal("20")),
        ],
        subtotal=Decimal("100"),
        total_paid=Decimal("100"),
    )
    claim = generate_synthetic_claim(evidence, ["receipt.png"], 1, "exact")

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.PARTIALLY_APPROVED
    assert decision.reimbursable_amount == Decimal("80")
    assert "NON_REIMBURSABLE_ITEM" in rules(decision)


def test_entirely_unsupported_claim_is_rejected() -> None:
    evidence = receipt()
    base = generate_synthetic_claim(evidence, ["receipt.png"], 1, "exact")
    claim = base.model_copy(
        update={"items": [ClaimItem(name="Unlisted headphones", claimed_amount=Decimal("105"))]}
    )

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.REJECTED
    assert decision.reimbursable_amount == 0
    assert "UNSUPPORTED_CLAIM_ITEM" in rules(decision)


def test_missing_final_total_is_escalated() -> None:
    evidence = receipt().model_copy(update={"total_paid": None})
    claim = generate_synthetic_claim(receipt(), ["receipt.png"], 1, "exact")

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.ESCALATED
    assert decision.reimbursable_amount == 0
    assert "FINAL_TOTAL_MISSING" in rules(decision)
    assert decision.additional_evidence_or_approval


def test_manager_threshold_requires_approval_but_reports_supported_amount() -> None:
    evidence = receipt()
    claim = generate_synthetic_claim(evidence, ["receipt.png"], 1, "exact")

    decision = evaluate_claim(
        claim,
        evidence,
        ExpensePolicy(manager_approval_threshold=Decimal("50")),
    )

    assert decision.status == DecisionStatus.ESCALATED
    assert decision.reimbursable_amount == Decimal("105")
    assert "MANAGER_APPROVAL_REQUIRED" in rules(decision)
    assert any(
        "manager approval" in value.lower() for value in decision.additional_evidence_or_approval
    )


def test_currency_mismatch_is_escalated() -> None:
    evidence = receipt()
    claim = generate_synthetic_claim(evidence, ["receipt.png"], 1, "exact").model_copy(
        update={"currency": "EUR"}
    )

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.ESCALATED
    assert "CURRENCY_MISMATCH" in rules(decision)


def test_empty_business_purpose_is_escalated() -> None:
    evidence = receipt()
    claim: ExpenseClaim = generate_synthetic_claim(
        evidence, ["receipt.png"], 1, "exact"
    ).model_copy(update={"business_purpose": ""})

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.ESCALATED
    assert "BUSINESS_PURPOSE_REQUIRED" in rules(decision)
