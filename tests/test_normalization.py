from decimal import Decimal

import pytest

from receipt_reconciliation.models import RawReceipt, RawReceiptItem
from receipt_reconciliation.normalization import normalize_raw_receipt, parse_money, parse_quantity


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Rp 14,000", Decimal("14000")),
        ("14.000", Decimal("14000")),
        ("1,234.56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),
        ("-7,800", Decimal("-7800")),
        ("30.50", Decimal("30.50")),
        (None, None),
    ],
)
def test_parse_money(raw: str | None, expected: Decimal | None) -> None:
    assert parse_money(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("x1", Decimal("1")), ("2.00xITEMS", Decimal("2.00")), (None, Decimal("1"))],
)
def test_parse_quantity(raw: str | None, expected: Decimal) -> None:
    assert parse_quantity(raw) == expected


def test_normalize_raw_receipt_keeps_cash_and_change_separate_from_total() -> None:
    receipt = normalize_raw_receipt(
        RawReceipt(
            items=[RawReceiptItem(name="THAI ICED TEA", quantity="2", line_total="40.000")],
            subtotal="40.000",
            total_paid="40.000",
            payment_method="CASH",
            cash_tendered="100.000",
            change="60.000",
        )
    )

    assert receipt.total_paid == Decimal("40000")
    assert receipt.cash_tendered == Decimal("100000")
    assert receipt.change == Decimal("60000")
    assert receipt.payment_method == "cash"
