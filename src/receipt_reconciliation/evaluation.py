from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal

from .models import (
    DecisionEvaluation,
    ExtractionEvaluation,
    FieldEvaluation,
    Receipt,
    ReimbursementDecision,
)

_WHITESPACE = re.compile(r"\s+")
_TEXT_PUNCTUATION = re.compile(r"[^a-z0-9]+")


def _display(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _text(value: object | None) -> str:
    return _WHITESPACE.sub(" ", str(value or "").casefold()).strip()


def _loose_text(value: object | None) -> str:
    return _TEXT_PUNCTUATION.sub("", _text(value))


def _same_money(actual: object | None, expected: object | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    try:
        return abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal("0.01")
    except (ArithmeticError, ValueError):
        return False


def _same_text(actual: object | None, expected: object | None) -> bool:
    return _loose_text(actual) == _loose_text(expected)


def _same_exact_text(actual: object | None, expected: object | None) -> bool:
    return _text(actual) == _text(expected)


def evaluate_extraction(
    engine: str,
    actual: Receipt,
    expected: Receipt,
) -> ExtractionEvaluation:
    """Score normalized OCR fields against CORD ground truth.

    Only populated ground-truth fields enter the denominator. Items are compared
    by receipt order, which mirrors CORD's menu sequence, and item count is scored
    separately so missing or hallucinated lines remain visible.
    """

    fields: list[FieldEvaluation] = []

    def compare(
        field: str,
        actual_value: object | None,
        expected_value: object | None,
        comparator: Callable[[object | None, object | None], bool],
        *,
        include_when_missing: bool = False,
    ) -> None:
        if expected_value is None and not include_when_missing:
            return
        fields.append(
            FieldEvaluation(
                field=field,
                expected=_display(expected_value),
                actual=_display(actual_value),
                match=comparator(actual_value, expected_value),
            )
        )

    compare("merchant", actual.merchant, expected.merchant, _same_text)
    compare("date", actual.date, expected.date, _same_exact_text)
    compare("time", actual.time, expected.time, _same_exact_text)
    compare("currency", actual.currency, expected.currency, _same_exact_text)
    compare("subtotal", actual.subtotal, expected.subtotal, _same_money)
    compare("tax", actual.tax, expected.tax, _same_money)
    compare("service_charge", actual.service_charge, expected.service_charge, _same_money)
    compare("discount", actual.discount, expected.discount, _same_money)
    compare("total_paid", actual.total_paid, expected.total_paid, _same_money)
    compare(
        "payment_method",
        actual.payment_method,
        expected.payment_method,
        _same_exact_text,
    )
    compare("cash_tendered", actual.cash_tendered, expected.cash_tendered, _same_money)
    compare("change", actual.change, expected.change, _same_money)
    compare(
        "items.count",
        len(actual.items),
        len(expected.items),
        lambda left, right: left == right,
        include_when_missing=True,
    )

    for index, expected_item in enumerate(expected.items):
        actual_item = actual.items[index] if index < len(actual.items) else None
        prefix = f"items[{index}]"
        compare(
            f"{prefix}.name",
            actual_item.name if actual_item else None,
            expected_item.name,
            _same_text,
            include_when_missing=True,
        )
        compare(
            f"{prefix}.quantity",
            actual_item.quantity if actual_item else None,
            expected_item.quantity,
            _same_money,
            include_when_missing=True,
        )
        compare(
            f"{prefix}.unit_price",
            actual_item.unit_price if actual_item else None,
            expected_item.unit_price,
            _same_money,
        )
        compare(
            f"{prefix}.line_total",
            actual_item.line_total if actual_item else None,
            expected_item.line_total,
            _same_money,
        )
        compare(
            f"{prefix}.discount",
            actual_item.discount if actual_item else None,
            expected_item.discount,
            _same_money,
        )

    matched = sum(field.match for field in fields)
    compared = len(fields)
    return ExtractionEvaluation(
        engine=engine,
        matched_fields=matched,
        compared_fields=compared,
        field_accuracy=matched / compared if compared else 0.0,
        fields=fields,
    )


def evaluate_decision(
    actual: ReimbursementDecision,
    expected: ReimbursementDecision,
    tolerance: Decimal = Decimal("0.01"),
) -> DecisionEvaluation:
    """Score the workflow decision independently from extraction accuracy."""

    return DecisionEvaluation(
        expected_status=expected.status,
        actual_status=actual.status,
        status_match=actual.status == expected.status,
        expected_amount=expected.reimbursable_amount,
        actual_amount=actual.reimbursable_amount,
        amount_match=abs(actual.reimbursable_amount - expected.reimbursable_amount) <= tolerance,
    )
