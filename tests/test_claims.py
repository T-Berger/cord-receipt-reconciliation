from decimal import Decimal

import pytest

from receipt_reconciliation.claims import SCENARIOS, generate_synthetic_claim
from receipt_reconciliation.models import Receipt, ReceiptItem


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


def test_seeded_random_claim_is_reproducible() -> None:
    first = generate_synthetic_claim(receipt(), ["receipt.png"], seed=42)
    second = generate_synthetic_claim(receipt(), ["receipt.png"], seed=42)

    assert first == second
    assert first.injected_scenario in SCENARIOS


@pytest.mark.parametrize(
    ("scenario", "expected_total", "expected_tax", "expected_discount"),
    [
        ("exact", Decimal("105"), Decimal("10"), Decimal("5")),
        ("claimed_cash_tendered", Decimal("120"), Decimal("10"), Decimal("5")),
        ("change_added", Decimal("120"), Decimal("10"), Decimal("5")),
        ("tax_doubled", Decimal("115"), Decimal("20"), Decimal("5")),
        ("tax_omitted", Decimal("95"), Decimal("0"), Decimal("5")),
        ("discount_ignored", Decimal("110"), Decimal("10"), Decimal("0")),
        ("item_tampered", Decimal("115"), Decimal("10"), Decimal("5")),
        ("unsupported_personal_item", Decimal("115"), Decimal("10"), Decimal("5")),
    ],
)
def test_explicit_scenarios_inject_expected_values(
    scenario: str,
    expected_total: Decimal,
    expected_tax: Decimal,
    expected_discount: Decimal,
) -> None:
    claim = generate_synthetic_claim(receipt(), ["receipt.png"], 7, scenario)

    assert claim.injected_scenario == scenario
    assert claim.claimed_total == expected_total
    assert claim.claimed_tax == expected_tax
    assert claim.claimed_discount == expected_discount


def test_item_scenarios_modify_claim_lines() -> None:
    tampered = generate_synthetic_claim(receipt(), ["receipt.png"], 7, "item_tampered")
    personal = generate_synthetic_claim(receipt(), ["receipt.png"], 7, "unsupported_personal_item")

    assert tampered.items[0].claimed_amount == Decimal("110")
    assert personal.items[-1].name == "Personal gift card"
    assert personal.items[-1].claimed_amount == Decimal("10")


def test_claim_requires_a_receipt_reference() -> None:
    with pytest.raises(ValueError, match="receipt reference"):
        generate_synthetic_claim(receipt(), [], 7, "exact")


def test_unknown_scenario_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        generate_synthetic_claim(receipt(), ["receipt.png"], 7, "invented")
