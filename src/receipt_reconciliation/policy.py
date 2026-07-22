from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .models import (
    DecisionStatus,
    ExpenseClaim,
    PolicyFinding,
    Receipt,
    ReceiptItem,
    ReimbursementDecision,
)


@dataclass(frozen=True, slots=True)
class ExpensePolicy:
    """Small, explicit policy used by the challenge's deterministic decision engine."""

    tolerance: Decimal = Decimal("0.01")
    manager_approval_threshold: Decimal | None = Decimal("1000000")
    non_reimbursable_keywords: tuple[str, ...] = (
        "alcohol",
        "beer",
        "wine",
        "whisky",
        "whiskey",
        "cigarette",
        "tobacco",
        "gift card",
        "personal",
    )


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _item_amount(item: ReceiptItem) -> Decimal | None:
    if item.line_total is not None:
        return max(item.line_total, Decimal("0"))
    if item.unit_price is not None:
        return max(item.quantity * item.unit_price, Decimal("0"))
    return None


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _contains_keyword(name: str, keywords: tuple[str, ...]) -> bool:
    normalized = f" {_key(name)} "
    return any(f" {_key(keyword)} " in normalized for keyword in keywords)


def _close(left: Decimal | None, right: Decimal | None, tolerance: Decimal) -> bool:
    return left is not None and right is not None and abs(left - right) <= tolerance


def evaluate_claim(
    claim: ExpenseClaim,
    receipt: Receipt,
    policy: ExpensePolicy | None = None,
) -> ReimbursementDecision:
    """Reconcile a claim against receipt evidence and return an auditable decision.

    The printed final purchase total is the evidence ceiling. Cash tendered and
    change are explicitly excluded, while unsupported and non-reimbursable item
    amounts are deducted. Ambiguous evidence is escalated rather than guessed.
    """

    active_policy = policy or ExpensePolicy()
    tolerance = active_policy.tolerance
    findings: list[PolicyFinding] = []
    additional: list[str] = []
    evidence_blocker = False

    def add_finding(
        rule_id: str,
        severity: str,
        message: str,
        *,
        receipt_field: str | None = None,
        claimed_value: Decimal | str | None = None,
        evidence_value: Decimal | str | None = None,
    ) -> None:
        findings.append(
            PolicyFinding(
                rule_id=rule_id,
                severity=severity,
                message=message,
                receipt_field=receipt_field,
                claimed_value=(
                    _money(claimed_value) if isinstance(claimed_value, Decimal) else claimed_value
                ),
                evidence_value=(
                    _money(evidence_value)
                    if isinstance(evidence_value, Decimal)
                    else evidence_value
                ),
            )
        )

    if not claim.receipt_refs:
        evidence_blocker = True
        additional.append("Attach the referenced receipt image.")
        add_finding(
            "RECEIPT_REQUIRED",
            "error",
            "The claim has no receipt reference.",
            receipt_field="receipt_refs",
        )

    if not claim.business_purpose.strip():
        evidence_blocker = True
        additional.append("Provide a business purpose and attendee or project context.")
        add_finding(
            "BUSINESS_PURPOSE_REQUIRED",
            "error",
            "A business purpose is required before reimbursement.",
            receipt_field="business_purpose",
        )

    actual_total = receipt.total_paid
    if actual_total is None:
        evidence_blocker = True
        additional.append("Provide evidence showing the final amount actually paid.")
        add_finding(
            "FINAL_TOTAL_MISSING",
            "error",
            "The final purchase total cannot be established from the receipt.",
            receipt_field="total_paid",
            claimed_value=claim.claimed_total,
        )
    elif actual_total < 0:
        evidence_blocker = True
        additional.append("Provide a corrected receipt with a valid final total.")
        add_finding(
            "INVALID_FINAL_TOTAL",
            "error",
            "The receipt final total is negative.",
            receipt_field="total_paid",
            evidence_value=actual_total,
        )

    if claim.currency.upper() != receipt.currency.upper():
        evidence_blocker = True
        additional.append("Provide the card statement and the approved exchange-rate evidence.")
        add_finding(
            "CURRENCY_MISMATCH",
            "error",
            "Claim and receipt currencies differ, so conversion cannot be verified.",
            receipt_field="currency",
            claimed_value=claim.currency,
            evidence_value=receipt.currency,
        )

    if claim.claimed_total <= 0:
        add_finding(
            "INVALID_CLAIM_AMOUNT",
            "error",
            "The requested reimbursement must be greater than zero.",
            receipt_field="claimed_total",
            claimed_value=claim.claimed_total,
        )

    if actual_total is None or actual_total < 0:
        return ReimbursementDecision(
            status=DecisionStatus.ESCALATED,
            claimed_amount=claim.claimed_total,
            reimbursable_amount=Decimal("0"),
            findings=findings,
            additional_evidence_or_approval=additional,
            summary="Escalated because the amount actually paid cannot be verified.",
        )

    if not _close(claim.claimed_total, actual_total, tolerance):
        direction = "exceeds" if claim.claimed_total > actual_total else "is below"
        add_finding(
            "CLAIM_TOTAL_MISMATCH",
            "error" if claim.claimed_total > actual_total else "info",
            f"The claimed total {direction} the receipt's final purchase total.",
            receipt_field="total_paid",
            claimed_value=claim.claimed_total,
            evidence_value=actual_total,
        )

    if (
        receipt.change is not None
        and receipt.change > tolerance
        and _close(claim.claimed_total, actual_total + receipt.change, tolerance)
    ):
        add_finding(
            "CHANGE_NOT_REIMBURSABLE",
            "error",
            "Change returned to the employee is not an expense.",
            receipt_field="change",
            claimed_value=claim.claimed_total,
            evidence_value=receipt.change,
        )

    if (
        receipt.cash_tendered is not None
        and receipt.cash_tendered > actual_total + tolerance
        and _close(claim.claimed_total, receipt.cash_tendered, tolerance)
    ):
        add_finding(
            "CASH_TENDERED_NOT_REIMBURSABLE",
            "error",
            "Cash tendered is not the purchase cost; only the final total is reimbursable.",
            receipt_field="cash_tendered",
            claimed_value=claim.claimed_total,
            evidence_value=actual_total,
        )

    if receipt.tax is not None and receipt.tax > tolerance:
        if claim.claimed_tax is None or abs(claim.claimed_tax) <= tolerance:
            add_finding(
                "TAX_OMITTED",
                "info",
                "Receipt tax was omitted from the claim; the lower request does not inflate cost.",
                receipt_field="tax",
                claimed_value=claim.claimed_tax,
                evidence_value=receipt.tax,
            )
        elif _close(claim.claimed_tax, receipt.tax * 2, tolerance):
            add_finding(
                "TAX_DOUBLE_COUNTED",
                "error",
                "The receipt tax was claimed twice.",
                receipt_field="tax",
                claimed_value=claim.claimed_tax,
                evidence_value=receipt.tax,
            )
        elif not _close(claim.claimed_tax, receipt.tax, tolerance):
            add_finding(
                "TAX_MISMATCH",
                "warning",
                "The claimed tax does not match the receipt tax.",
                receipt_field="tax",
                claimed_value=claim.claimed_tax,
                evidence_value=receipt.tax,
            )
    elif claim.claimed_tax is not None and claim.claimed_tax > tolerance:
        add_finding(
            "UNSUPPORTED_TAX",
            "error",
            "The claim includes tax that is not supported by a receipt tax field.",
            receipt_field="tax",
            claimed_value=claim.claimed_tax,
            evidence_value=receipt.tax,
        )

    evidence_discount = abs(receipt.discount or Decimal("0"))
    if evidence_discount > tolerance and (
        claim.claimed_discount is None or abs(claim.claimed_discount) <= tolerance
    ):
        add_finding(
            "DISCOUNT_IGNORED",
            "error" if claim.claimed_total > actual_total else "warning",
            "The receipt discount must reduce the reimbursable purchase cost.",
            receipt_field="discount",
            claimed_value=claim.claimed_discount,
            evidence_value=evidence_discount,
        )

    if (
        claim.claimed_subtotal is not None
        and receipt.subtotal is not None
        and not _close(claim.claimed_subtotal, receipt.subtotal, tolerance)
    ):
        add_finding(
            "SUBTOTAL_MISMATCH",
            "warning",
            "The claimed subtotal does not match the receipt subtotal.",
            receipt_field="subtotal",
            claimed_value=claim.claimed_subtotal,
            evidence_value=receipt.subtotal,
        )

    if (
        claim.payment_method
        and receipt.payment_method
        and claim.payment_method != receipt.payment_method
    ):
        evidence_blocker = True
        additional.append("Provide proof of payment for the claimed payment method.")
        add_finding(
            "PAYMENT_METHOD_MISMATCH",
            "warning",
            "The claimed payment method differs from the receipt.",
            receipt_field="payment_method",
            claimed_value=claim.payment_method,
            evidence_value=receipt.payment_method,
        )

    receipt_amounts: dict[str, Decimal] = {}
    non_reimbursable_actual = Decimal("0")
    unknown_non_reimbursable_value = False
    for item in receipt.items:
        amount = _item_amount(item)
        key = _key(item.name)
        if amount is not None:
            receipt_amounts[key] = receipt_amounts.get(key, Decimal("0")) + amount
        if _contains_keyword(item.name, active_policy.non_reimbursable_keywords):
            add_finding(
                "NON_REIMBURSABLE_ITEM",
                "error",
                f"Policy excludes receipt item {item.name!r}.",
                receipt_field=f"items.{item.name}",
                evidence_value=amount,
            )
            if amount is None:
                unknown_non_reimbursable_value = True
            else:
                non_reimbursable_actual += amount

    remaining_receipt_amounts = dict(receipt_amounts)
    supported_item_total = Decimal("0")
    claimed_item_sum = Decimal("0")
    matched_claim_items = 0
    for item in claim.items:
        key = _key(item.name)
        claimed_item_amount = max(item.claimed_amount, Decimal("0"))
        claimed_item_sum += claimed_item_amount
        if _contains_keyword(item.name, active_policy.non_reimbursable_keywords):
            if key not in receipt_amounts:
                add_finding(
                    "NON_REIMBURSABLE_CLAIM_ITEM",
                    "error",
                    f"Claim item {item.name!r} is excluded by policy.",
                    receipt_field=f"claim.items.{item.name}",
                    claimed_value=item.claimed_amount,
                )
            continue

        if receipt.items and key not in receipt_amounts:
            add_finding(
                "UNSUPPORTED_CLAIM_ITEM",
                "error",
                f"Claim item {item.name!r} is not present on the receipt.",
                receipt_field=f"items.{item.name}",
                claimed_value=item.claimed_amount,
            )
            continue

        matched_claim_items += 1
        evidence_amount = remaining_receipt_amounts.get(key)
        if evidence_amount is not None:
            supported_amount = min(claimed_item_amount, max(evidence_amount, Decimal("0")))
            supported_item_total += supported_amount
            remaining_receipt_amounts[key] = max(evidence_amount - supported_amount, Decimal("0"))
        if evidence_amount is not None and claimed_item_amount > evidence_amount + tolerance:
            add_finding(
                "ITEM_AMOUNT_MISMATCH",
                "error",
                f"Claim item {item.name!r} exceeds its receipt amount.",
                receipt_field=f"items.{item.name}.line_total",
                claimed_value=item.claimed_amount,
                evidence_value=evidence_amount,
            )

    if (
        claim.claimed_subtotal is not None
        and abs(claimed_item_sum - claim.claimed_subtotal) > tolerance
    ):
        add_finding(
            "CLAIM_ITEM_SUM_MISMATCH",
            "error",
            "Claim item amounts do not add up to the claimed subtotal.",
            receipt_field="claimed_subtotal",
            claimed_value=claimed_item_sum,
            evidence_value=claim.claimed_subtotal,
        )

    if receipt.items:
        known_item_amounts = [_item_amount(item) for item in receipt.items]
        if receipt.subtotal is not None and all(value is not None for value in known_item_amounts):
            item_sum = sum(
                (value for value in known_item_amounts if value is not None), Decimal("0")
            )
            if abs(item_sum - receipt.subtotal) > tolerance:
                evidence_blocker = True
                additional.append(
                    "Provide an itemized receipt or merchant clarification reconciling "
                    "the item sum to the printed subtotal."
                )
                add_finding(
                    "RECEIPT_ITEM_SUM_MISMATCH",
                    "error",
                    "Receipt item amounts do not add up to the printed subtotal.",
                    receipt_field="subtotal",
                    claimed_value=item_sum,
                    evidence_value=receipt.subtotal,
                )

    if unknown_non_reimbursable_value:
        evidence_blocker = True
        additional.append("Provide an itemized receipt showing the excluded item's amount.")

    eligible_actual = max(actual_total - non_reimbursable_actual, Decimal("0"))
    requested_subtotal = max(
        claim.claimed_subtotal if claim.claimed_subtotal is not None else claimed_item_sum,
        Decimal("0"),
    )
    if receipt.items and claim.items:
        supported_subtotal = supported_item_total
    elif receipt.subtotal is not None:
        supported_subtotal = min(requested_subtotal, max(receipt.subtotal, Decimal("0")))
    else:
        supported_subtotal = min(requested_subtotal, actual_total)

    requested_tax = max(claim.claimed_tax or Decimal("0"), Decimal("0"))
    supported_tax = min(requested_tax, max(receipt.tax or Decimal("0"), Decimal("0")))
    requested_discount = abs(claim.claimed_discount or Decimal("0"))
    evidence_discount = abs(receipt.discount or Decimal("0"))
    discount_to_apply = max(requested_discount, evidence_discount)

    stated_component_total = requested_subtotal + requested_tax - requested_discount
    unstated_positive_component = max(claim.claimed_total - stated_component_total, Decimal("0"))
    supported_service_charge = min(
        unstated_positive_component,
        max(receipt.service_charge or Decimal("0"), Decimal("0")),
    )
    unsupported_component = unstated_positive_component - supported_service_charge
    if unsupported_component > tolerance:
        add_finding(
            "UNSUPPORTED_CLAIM_COMPONENT",
            "error",
            "Part of the claimed total is not explained by supported items, tax, or service.",
            receipt_field="claimed_total",
            claimed_value=unsupported_component,
            evidence_value=Decimal("0"),
        )

    supported_components = max(
        supported_subtotal + supported_tax + supported_service_charge - discount_to_apply,
        Decimal("0"),
    )
    supported_request = min(max(claim.claimed_total, Decimal("0")), supported_components)
    reimbursable = min(supported_request, eligible_actual)
    if claim.claimed_total <= 0:
        reimbursable = Decimal("0")

    if receipt.items and claim.items and matched_claim_items == 0 and reimbursable > tolerance:
        reimbursable = Decimal("0")

    requires_manager = (
        active_policy.manager_approval_threshold is not None
        and reimbursable > active_policy.manager_approval_threshold
    )
    if requires_manager:
        additional.append(
            "Obtain manager approval because the eligible amount exceeds the policy threshold."
        )
        add_finding(
            "MANAGER_APPROVAL_REQUIRED",
            "warning",
            "The eligible amount exceeds the manager-approval threshold.",
            receipt_field="total_paid",
            claimed_value=reimbursable,
            evidence_value=active_policy.manager_approval_threshold,
        )

    if evidence_blocker:
        status = DecisionStatus.ESCALATED
        if unknown_non_reimbursable_value:
            reimbursable = Decimal("0")
        summary = "Escalated because required evidence or a reliable policy calculation is missing."
    elif reimbursable <= tolerance and claim.claimed_total > tolerance:
        status = DecisionStatus.REJECTED
        reimbursable = Decimal("0")
        summary = "Rejected because none of the requested amount is supported and reimbursable."
    elif requires_manager:
        status = DecisionStatus.ESCALATED
        summary = (
            f"Escalated for manager approval; {receipt.currency} {_money(reimbursable)} "
            "is supported by the receipt and policy."
        )
    elif reimbursable + tolerance < claim.claimed_total:
        status = DecisionStatus.PARTIALLY_APPROVED
        summary = (
            f"Partially approved for {receipt.currency} {_money(reimbursable)}; the remainder "
            "is unsupported or excluded by policy."
        )
    else:
        status = DecisionStatus.APPROVED
        reimbursable = claim.claimed_total
        summary = (
            f"Approved for {receipt.currency} {_money(reimbursable)} because the requested "
            "amount is supported by the receipt and policy."
        )

    return ReimbursementDecision(
        status=status,
        claimed_amount=claim.claimed_total,
        reimbursable_amount=reimbursable,
        findings=findings,
        additional_evidence_or_approval=list(dict.fromkeys(additional)),
        summary=summary,
    )
