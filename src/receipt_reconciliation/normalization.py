from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import RawReceipt, Receipt, ReceiptItem

_NON_NUMERIC = re.compile(r"[^0-9,.+\-]")
_QUANTITY = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def parse_money(value: Any) -> Decimal | None:
    """Parse CORD-style monetary strings without losing thousands separators.

    CORD v2 is predominantly Indonesian receipt data, where both ``14,000`` and
    ``14.000`` mean fourteen thousand. A single separator followed by exactly
    three digits is therefore treated as a grouping mark; one or two trailing
    digits are treated as decimals.
    """

    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = _NON_NUMERIC.sub("", str(value).strip())
    if not text or text in {"+", "-"}:
        return None

    sign = "-" if text.startswith("-") else ""
    text = text.lstrip("+-")
    comma = text.rfind(",")
    dot = text.rfind(".")

    if comma >= 0 and dot >= 0:
        last = max(comma, dot)
        fractional = text[last + 1 :]
        if len(fractional) in {1, 2}:
            normalized = re.sub(r"[,.]", "", text[:last]) + "." + fractional
        else:
            normalized = re.sub(r"[,.]", "", text)
    elif comma >= 0 or dot >= 0:
        separator = "," if comma >= 0 else "."
        parts = text.split(separator)
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            normalized = parts[0] + "." + parts[1]
        else:
            normalized = "".join(parts)
    else:
        normalized = text

    try:
        return Decimal(sign + normalized)
    except InvalidOperation:
        return None


def parse_quantity(value: Any) -> Decimal:
    if value is None:
        return Decimal("1")
    match = _QUANTITY.search(str(value).replace("x", "").replace("X", ""))
    if not match:
        return Decimal("1")
    try:
        return Decimal(match.group(0).replace(",", "."))
    except InvalidOperation:
        return Decimal("1")


def normalize_payment_method(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"[^a-z0-9]", "", value.lower())
    if "cash" in text or "tunai" in text:
        return "cash"
    if "credit" in text or "visa" in text or "master" in text:
        return "credit_card"
    if "debit" in text:
        return "debit_card"
    if any(token in text for token in ("gopay", "ovo", "dana", "qris", "ewallet")):
        return "ewallet"
    return "unknown"


def normalize_raw_receipt(raw: RawReceipt) -> Receipt:
    items: list[ReceiptItem] = []
    for item in raw.items:
        items.append(
            ReceiptItem(
                name=item.name.strip(),
                quantity=parse_quantity(item.quantity),
                unit_price=parse_money(item.unit_price),
                line_total=parse_money(item.line_total),
                discount=parse_money(item.discount),
            )
        )

    raw_fields = {
        "subtotal": raw.subtotal,
        "tax": raw.tax,
        "service_charge": raw.service_charge,
        "discount": raw.discount,
        "total_paid": raw.total_paid,
        "cash_tendered": raw.cash_tendered,
        "change": raw.change,
    }
    return Receipt(
        merchant=raw.merchant,
        date=raw.date,
        time=raw.time,
        currency=(raw.currency or "IDR").upper(),
        items=items,
        subtotal=parse_money(raw.subtotal),
        tax=parse_money(raw.tax),
        service_charge=parse_money(raw.service_charge),
        discount=parse_money(raw.discount),
        total_paid=parse_money(raw.total_paid),
        payment_method=normalize_payment_method(raw.payment_method),
        cash_tendered=parse_money(raw.cash_tendered),
        change=parse_money(raw.change),
        raw_fields=raw_fields,
    )


def money_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")
