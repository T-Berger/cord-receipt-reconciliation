from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from receipt_reconciliation.claims import generate_synthetic_claim
from receipt_reconciliation.cord import CordSample
from receipt_reconciliation.evaluation import evaluate_decision
from receipt_reconciliation.models import (
    ClaimItem,
    DecisionStatus,
    OcrResult,
    Receipt,
    ReceiptItem,
)
from receipt_reconciliation.policy import ExpensePolicy, evaluate_claim
from receipt_reconciliation.tracing import WorkflowTracer
from receipt_reconciliation.workflow import WorkflowConfig, run_reconciliation


def _receipt() -> Receipt:
    return Receipt(
        merchant="Adversarial Cafe",
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


def _rules(decision: Any) -> set[str]:
    return {finding.rule_id for finding in decision.findings}


@pytest.mark.parametrize(
    ("scenario", "status", "amount", "required_rules"),
    [
        pytest.param("exact", DecisionStatus.APPROVED, "105", set(), id="approved-exact"),
        pytest.param(
            "claimed_cash_tendered",
            DecisionStatus.PARTIALLY_APPROVED,
            "105",
            {
                "CLAIM_TOTAL_MISMATCH",
                "CASH_TENDERED_NOT_REIMBURSABLE",
                "CHANGE_NOT_REIMBURSABLE",
            },
            id="partial-cash-tendered-and-change",
        ),
        pytest.param(
            "change_added",
            DecisionStatus.PARTIALLY_APPROVED,
            "105",
            {"CLAIM_TOTAL_MISMATCH", "CHANGE_NOT_REIMBURSABLE"},
            id="partial-change-added",
        ),
        pytest.param(
            "tax_doubled",
            DecisionStatus.PARTIALLY_APPROVED,
            "105",
            {"CLAIM_TOTAL_MISMATCH", "TAX_DOUBLE_COUNTED"},
            id="partial-tax-double-counted",
        ),
        pytest.param(
            "tax_omitted",
            DecisionStatus.APPROVED,
            "95",
            {"CLAIM_TOTAL_MISMATCH", "TAX_OMITTED"},
            id="approved-lower-request-with-tax-omitted",
        ),
        pytest.param(
            "discount_ignored",
            DecisionStatus.PARTIALLY_APPROVED,
            "105",
            {"CLAIM_TOTAL_MISMATCH", "DISCOUNT_IGNORED"},
            id="partial-pre-discount-price",
        ),
        pytest.param(
            "item_tampered",
            DecisionStatus.PARTIALLY_APPROVED,
            "105",
            {"CLAIM_TOTAL_MISMATCH", "ITEM_AMOUNT_MISMATCH"},
            id="partial-item-price-tampering",
        ),
        pytest.param(
            "unsupported_personal_item",
            DecisionStatus.PARTIALLY_APPROVED,
            "105",
            {"CLAIM_TOTAL_MISMATCH", "NON_REIMBURSABLE_CLAIM_ITEM"},
            id="partial-unsupported-personal-item",
        ),
    ],
)
def test_synthetic_claim_agent_decisions(
    scenario: str,
    status: DecisionStatus,
    amount: str,
    required_rules: set[str],
) -> None:
    evidence = _receipt()
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=8675309, scenario=scenario)

    decision = evaluate_claim(claim, evidence)

    assert decision.status == status
    assert decision.reimbursable_amount == Decimal(amount)
    assert required_rules <= _rules(decision)
    assert decision.claimed_amount == claim.claimed_total
    assert decision.reimbursable_amount <= evidence.total_paid  # type: ignore[operator]


def test_fully_personal_receipt_is_rejected() -> None:
    evidence = Receipt(
        merchant="Corner Shop",
        currency="IDR",
        items=[ReceiptItem(name="Personal gift card", line_total=Decimal("50"))],
        subtotal=Decimal("50"),
        total_paid=Decimal("50"),
    )
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=1, scenario="exact")

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.REJECTED
    assert decision.reimbursable_amount == Decimal("0")
    assert "NON_REIMBURSABLE_ITEM" in _rules(decision)
    assert "none of the requested amount" in decision.summary.lower()


def test_entirely_unlisted_claim_is_rejected() -> None:
    evidence = _receipt()
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=2, scenario="exact")
    claim = claim.model_copy(
        update={"items": [ClaimItem(name="Noise-cancelling headphones", claimed_amount="105")]}
    )

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.REJECTED
    assert decision.reimbursable_amount == Decimal("0")
    assert "UNSUPPORTED_CLAIM_ITEM" in _rules(decision)


def test_missing_purchase_total_is_escalated_for_evidence() -> None:
    claim = generate_synthetic_claim(_receipt(), ["receipt.jpg"], seed=3, scenario="exact")
    ambiguous_evidence = _receipt().model_copy(update={"total_paid": None})

    decision = evaluate_claim(claim, ambiguous_evidence)

    assert decision.status == DecisionStatus.ESCALATED
    assert decision.reimbursable_amount == Decimal("0")
    assert "FINAL_TOTAL_MISSING" in _rules(decision)
    assert any(
        "final amount" in request.lower() for request in decision.additional_evidence_or_approval
    )


def test_supported_high_value_claim_is_escalated_for_manager_approval() -> None:
    evidence = _receipt()
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=4, scenario="exact")

    decision = evaluate_claim(
        claim,
        evidence,
        ExpensePolicy(manager_approval_threshold=Decimal("100")),
    )

    assert decision.status == DecisionStatus.ESCALATED
    assert decision.reimbursable_amount == Decimal("105")
    assert "MANAGER_APPROVAL_REQUIRED" in _rules(decision)
    assert any(
        "manager approval" in request.lower()
        for request in decision.additional_evidence_or_approval
    )


def test_decision_evaluation_detects_ocr_decision_drift_from_ground_truth() -> None:
    evidence = _receipt()
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=5, scenario="exact")
    expected = evaluate_claim(claim, evidence)
    mistral_evidence = evidence.model_copy(update={"total_paid": Decimal("100")})
    actual = evaluate_claim(claim, mistral_evidence)

    evaluation = evaluate_decision(actual, expected)

    assert expected.status == DecisionStatus.APPROVED
    assert actual.status == DecisionStatus.PARTIALLY_APPROVED
    assert not evaluation.status_match
    assert not evaluation.amount_match
    assert evaluation.expected_amount == Decimal("105")
    assert evaluation.actual_amount == Decimal("100")


class _FakeRemoteObservation:
    def __init__(self, observation_id: str) -> None:
        self.id = observation_id
        self.updates: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class _FakeRemoteContext:
    def __init__(self, observation: _FakeRemoteObservation) -> None:
        self.observation = observation

    def __enter__(self) -> _FakeRemoteObservation:
        return self.observation

    def __exit__(self, *_: Any) -> bool:
        return False


class _FakeLangfuse:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self.flush_count = 0

    def start_as_current_observation(self, **kwargs: Any) -> _FakeRemoteContext:
        self.starts.append(kwargs)
        return _FakeRemoteContext(_FakeRemoteObservation(f"remote-{len(self.starts)}"))

    def get_current_trace_id(self) -> str:
        return "b" * 32

    def create_score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flush_count += 1


def test_mocked_agent_workflow_reports_and_traces_ground_truth_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"mock receipt")
    ground_truth = _receipt()
    mistral_receipt = ground_truth.model_copy(update={"total_paid": Decimal("100")})
    sample = CordSample(
        dataset="naver-clova-ix/cord-v2",
        split="test",
        row_index=17,
        image_path=image_path,
        ground_truth_json={"gt_parse": {"total": {"total_price": "105"}}},
        receipt=ground_truth,
        image_url="https://example.invalid/receipt.jpg",
    )

    class FakeDatasetClient:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> FakeDatasetClient:
            return self

        def __exit__(self, *_: Any) -> None:
            pass

        def fetch_sample(self, **_: Any) -> CordSample:
            return sample

    remote = _FakeLangfuse()
    monkeypatch.setattr("receipt_reconciliation.workflow.CordDatasetClient", FakeDatasetClient)
    monkeypatch.setattr(
        "receipt_reconciliation.workflow.extract_with_mistral",
        lambda _: OcrResult(
            engine="mistral",
            raw_text="TOTAL 100",
            receipt=mistral_receipt,
            model="mistral-ocr-latest",
        ),
    )
    monkeypatch.setattr(
        "receipt_reconciliation.workflow.extract_with_docling",
        lambda _: OcrResult(
            engine="docling",
            raw_text="TOTAL 105",
            receipt=ground_truth,
            model="mistral-small-latest",
        ),
    )
    monkeypatch.setattr(
        "receipt_reconciliation.workflow.WorkflowTracer",
        lambda path: WorkflowTracer(path, langfuse_client=remote),
    )

    output_dir = tmp_path / "agent-run"
    report = run_reconciliation(
        WorkflowConfig(
            split="test",
            row_index=17,
            seed=6,
            scenario="exact",
            output_dir=output_dir,
            skip_docling=False,
        )
    )

    assert report.expected_decision.status == DecisionStatus.APPROVED
    assert report.actual_decision.status == DecisionStatus.ESCALATED
    assert "OCR_EVIDENCE_CONFLICT" in _rules(report.actual_decision)
    assert not report.decision_evaluation.status_match
    assert not report.decision_evaluation.amount_match
    assert [evaluation.engine for evaluation in report.extraction_evaluations] == [
        "mistral",
        "docling",
    ]
    assert report.extraction_evaluations[0].field_accuracy < 1.0
    assert report.extraction_evaluations[1].field_accuracy == 1.0

    report_payload = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    trace_payload = json.loads((output_dir / "trace.json").read_text(encoding="utf-8"))
    assert report_payload["trace_id"] == "b" * 32
    assert report_payload["decision_evaluation"]["status_match"] is False
    assert trace_payload["langfuse_enabled"] is True
    assert trace_payload["remote_error_counts"] == {}
    assert len(trace_payload["observations"]) == 10
    assert all(item["status"] == "success" for item in trace_payload["observations"])
    assert {item["name"] for item in trace_payload["observations"]} == {
        "receipt-reconciliation",
        "cord-dataset-fetch",
        "synthetic-claim-generation",
        "mistral-ocr-and-structured-extraction",
        "docling-ocr-and-mistral-structured-extraction",
        "mistral-ground-truth-comparison",
        "docling-ground-truth-comparison",
        "expected-policy-decision",
        "workflow-policy-decision",
        "decision-evaluation",
    }
    assert len(trace_payload["scores"]) == 8
    assert {score["name"] for score in trace_payload["scores"]} == {
        "field_accuracy",
        "status_accuracy",
        "reimbursement_amount_accuracy",
        "mistral_field_accuracy",
        "docling_field_accuracy",
        "decision_status_accuracy",
        "decision_amount_accuracy",
    }
    assert len(remote.starts) == 10
    assert len(remote.scores) == 8
    assert remote.flush_count == 1


def test_internally_inconsistent_receipt_is_escalated_instead_of_fully_approved() -> None:
    evidence = Receipt(
        merchant="Broken Totals Cafe",
        currency="IDR",
        items=[
            ReceiptItem(name="Meal", line_total=Decimal("60")),
            ReceiptItem(name="Coffee", line_total=Decimal("20")),
        ],
        subtotal=Decimal("100"),
        total_paid=Decimal("100"),
    )
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=7, scenario="exact")

    decision = evaluate_claim(claim, evidence)

    assert "RECEIPT_ITEM_SUM_MISMATCH" in _rules(decision)
    assert decision.status == DecisionStatus.ESCALATED
    assert decision.additional_evidence_or_approval


def test_payment_method_mismatch_is_not_approved_before_proof_is_supplied() -> None:
    evidence = _receipt()
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=8, scenario="exact")
    claim = claim.model_copy(update={"payment_method": "credit_card"})

    decision = evaluate_claim(claim, evidence)

    assert "PAYMENT_METHOD_MISMATCH" in _rules(decision)
    assert decision.additional_evidence_or_approval
    assert decision.status == DecisionStatus.ESCALATED


def test_overclaimed_matched_item_is_capped_to_its_receipt_evidence() -> None:
    evidence = Receipt(
        merchant="Line Item Cafe",
        currency="IDR",
        items=[
            ReceiptItem(name="Meal", line_total=Decimal("100")),
            ReceiptItem(name="Coffee", line_total=Decimal("100")),
        ],
        subtotal=Decimal("200"),
        total_paid=Decimal("200"),
    )
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=9, scenario="exact")
    claim = claim.model_copy(
        update={
            "items": [ClaimItem(name="Meal", claimed_amount=Decimal("150"))],
            "claimed_subtotal": Decimal("150"),
            "claimed_total": Decimal("150"),
        }
    )

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.PARTIALLY_APPROVED
    assert decision.reimbursable_amount == Decimal("100")
    assert "ITEM_AMOUNT_MISMATCH" in _rules(decision)


def test_unsupported_tax_cannot_replace_an_unclaimed_receipt_item() -> None:
    evidence = Receipt(
        merchant="Tax Attack Cafe",
        currency="IDR",
        items=[
            ReceiptItem(name="Meal", line_total=Decimal("100")),
            ReceiptItem(name="Coffee", line_total=Decimal("100")),
        ],
        subtotal=Decimal("200"),
        tax=Decimal("0"),
        total_paid=Decimal("200"),
    )
    claim = generate_synthetic_claim(evidence, ["receipt.jpg"], seed=10, scenario="exact")
    claim = claim.model_copy(
        update={
            "items": [ClaimItem(name="Meal", claimed_amount=Decimal("100"))],
            "claimed_subtotal": Decimal("100"),
            "claimed_tax": Decimal("50"),
            "claimed_total": Decimal("150"),
        }
    )

    decision = evaluate_claim(claim, evidence)

    assert decision.status == DecisionStatus.PARTIALLY_APPROVED
    assert decision.reimbursable_amount == Decimal("100")
    assert "UNSUPPORTED_TAX" in _rules(decision)
