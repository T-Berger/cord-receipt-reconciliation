from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RawReceiptItem(StrictModel):
    name: str = Field(description="Item name exactly as printed")
    quantity: str | None = Field(default=None, description="Printed quantity")
    unit_price: str | None = Field(default=None, description="Printed unit price")
    line_total: str | None = Field(default=None, description="Printed item line total")
    discount: str | None = Field(default=None, description="Printed item discount")


class RawReceipt(StrictModel):
    merchant: str | None = None
    date: str | None = None
    time: str | None = None
    currency: str | None = Field(default=None, description="ISO code if identifiable")
    items: list[RawReceiptItem] = Field(default_factory=list)
    subtotal: str | None = None
    tax: str | None = None
    service_charge: str | None = None
    discount: str | None = None
    total_paid: str | None = Field(
        default=None,
        description="Final purchase total, never cash tendered and never total plus change",
    )
    payment_method: str | None = Field(
        default=None, description="cash, credit_card, debit_card, ewallet, or unknown"
    )
    cash_tendered: str | None = None
    change: str | None = None


class ReceiptItem(StrictModel):
    name: str
    quantity: Decimal = Decimal("1")
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    discount: Decimal | None = None


class Receipt(StrictModel):
    merchant: str | None = None
    date: str | None = None
    time: str | None = None
    currency: str = "IDR"
    items: list[ReceiptItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    service_charge: Decimal | None = None
    discount: Decimal | None = None
    total_paid: Decimal | None = None
    payment_method: str | None = None
    cash_tendered: Decimal | None = None
    change: Decimal | None = None
    raw_fields: dict[str, str | None] = Field(default_factory=dict)


class ClaimItem(StrictModel):
    name: str
    quantity: Decimal = Decimal("1")
    claimed_amount: Decimal


class ExpenseClaim(StrictModel):
    claim_id: str
    employee_id: str
    business_purpose: str
    category: str
    currency: str
    items: list[ClaimItem]
    claimed_subtotal: Decimal | None = None
    claimed_tax: Decimal | None = None
    claimed_discount: Decimal | None = None
    claimed_total: Decimal
    payment_method: str | None = None
    receipt_refs: list[str]
    injected_scenario: str


class DecisionStatus(StrEnum):
    APPROVED = "approved"
    PARTIALLY_APPROVED = "partially_approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class PolicyFinding(StrictModel):
    rule_id: str
    severity: str
    message: str
    receipt_field: str | None = None
    claimed_value: str | None = None
    evidence_value: str | None = None


class ReimbursementDecision(StrictModel):
    status: DecisionStatus
    claimed_amount: Decimal
    reimbursable_amount: Decimal
    findings: list[PolicyFinding]
    additional_evidence_or_approval: list[str] = Field(default_factory=list)
    summary: str


class FieldEvaluation(StrictModel):
    field: str
    expected: str | None
    actual: str | None
    match: bool


class ExtractionEvaluation(StrictModel):
    engine: str
    matched_fields: int
    compared_fields: int
    field_accuracy: float
    fields: list[FieldEvaluation]


class DecisionEvaluation(StrictModel):
    expected_status: DecisionStatus
    actual_status: DecisionStatus
    status_match: bool
    expected_amount: Decimal
    actual_amount: Decimal
    amount_match: bool


class OcrResult(StrictModel):
    engine: str
    raw_text: str
    receipt: Receipt
    model: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class WorkflowReport(StrictModel):
    dataset: str
    split: str
    row_index: int
    image_path: str
    claim: ExpenseClaim
    ground_truth: Receipt
    mistral: OcrResult
    docling: OcrResult | None
    extraction_evaluations: list[ExtractionEvaluation]
    expected_decision: ReimbursementDecision
    actual_decision: ReimbursementDecision
    decision_evaluation: DecisionEvaluation
    trace_id: str | None = None
    langfuse_enabled: bool = False
    local_trace_path: str
