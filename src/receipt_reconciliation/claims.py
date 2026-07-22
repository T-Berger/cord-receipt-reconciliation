from __future__ import annotations

import random
from decimal import Decimal

from .models import ClaimItem, ExpenseClaim, Receipt, ReceiptItem

SCENARIOS = (
    "exact",
    "claimed_cash_tendered",
    "change_added",
    "tax_doubled",
    "tax_omitted",
    "discount_ignored",
    "item_tampered",
    "unsupported_personal_item",
)


def _item_amount(item: ReceiptItem) -> Decimal:
    if item.line_total is not None:
        return max(item.line_total, Decimal("0"))
    if item.unit_price is not None:
        return max(item.quantity * item.unit_price, Decimal("0"))
    return Decimal("0")


def _receipt_total(receipt: Receipt) -> Decimal:
    if receipt.total_paid is not None:
        return max(receipt.total_paid, Decimal("0"))
    item_total = sum((_item_amount(item) for item in receipt.items), Decimal("0"))
    if item_total:
        return item_total
    return max(receipt.subtotal or Decimal("0"), Decimal("0"))


def _meaningful_increment(total: Decimal) -> Decimal:
    """Return a visible but currency-scale-aware discrepancy."""

    if total >= Decimal("10000"):
        return Decimal("1000")
    if total >= Decimal("100"):
        return Decimal("10")
    return Decimal("1")


def generate_synthetic_claim(
    receipt: Receipt,
    receipt_refs: list[str],
    seed: int,
    scenario: str | None = None,
) -> ExpenseClaim:
    """Create a reproducible claim, optionally injecting a known inconsistency.

    ``seed`` controls both the selected scenario (when omitted) and harmless claim
    metadata. This makes challenge runs repeatable without making every claim look
    identical.
    """

    if not receipt_refs:
        raise ValueError("at least one receipt reference is required")

    rng = random.Random(seed)
    selected = scenario or rng.choice(SCENARIOS)
    if selected not in SCENARIOS:
        raise ValueError(f"unknown scenario {selected!r}; choose one of {SCENARIOS}")

    total = _receipt_total(receipt)
    increment = _meaningful_increment(total)
    items = [
        ClaimItem(name=item.name, quantity=item.quantity, claimed_amount=_item_amount(item))
        for item in receipt.items
    ]
    if not items:
        items = [ClaimItem(name="Receipt purchase", claimed_amount=total)]

    claimed_subtotal = receipt.subtotal
    if claimed_subtotal is None:
        claimed_subtotal = sum((item.claimed_amount for item in items), Decimal("0"))
    claimed_tax = receipt.tax
    claimed_discount = receipt.discount
    claimed_total = total
    payment_method = receipt.payment_method

    if selected == "claimed_cash_tendered":
        alleged_tender = receipt.cash_tendered
        if alleged_tender is None or alleged_tender <= total:
            alleged_tender = total + max(receipt.change or Decimal("0"), increment)
        claimed_total = alleged_tender
        payment_method = "cash"
    elif selected == "change_added":
        claimed_total = total + max(receipt.change or Decimal("0"), increment)
    elif selected == "tax_doubled":
        tax = abs(receipt.tax or max(total * Decimal("0.10"), increment))
        claimed_tax = tax * 2
        claimed_total = total + tax
    elif selected == "tax_omitted":
        tax = abs(receipt.tax or min(max(total * Decimal("0.10"), increment), total))
        claimed_tax = Decimal("0")
        claimed_total = max(total - tax, Decimal("0"))
    elif selected == "discount_ignored":
        discount = abs(receipt.discount or max(total * Decimal("0.05"), increment))
        claimed_discount = Decimal("0")
        claimed_total = total + discount
    elif selected == "item_tampered":
        first = items[0]
        items[0] = first.model_copy(update={"claimed_amount": first.claimed_amount + increment})
        claimed_subtotal = (claimed_subtotal or Decimal("0")) + increment
        claimed_total = total + increment
    elif selected == "unsupported_personal_item":
        items.append(
            ClaimItem(name="Personal gift card", quantity=Decimal("1"), claimed_amount=increment)
        )
        claimed_subtotal = (claimed_subtotal or Decimal("0")) + increment
        claimed_total = total + increment

    purposes = (
        "Customer meeting",
        "Project team meal",
        "Business travel expense",
        "Office supplies",
    )
    categories = ("meals", "travel", "supplies", "customer_meeting")
    return ExpenseClaim(
        claim_id=f"CLM-{seed:08d}",
        employee_id=f"EMP-{rng.randint(1000, 9999)}",
        business_purpose=rng.choice(purposes),
        category=rng.choice(categories),
        currency=receipt.currency,
        items=items,
        claimed_subtotal=claimed_subtotal,
        claimed_tax=claimed_tax,
        claimed_discount=claimed_discount,
        claimed_total=claimed_total,
        payment_method=payment_method,
        receipt_refs=list(receipt_refs),
        injected_scenario=selected,
    )
