from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .claims import generate_synthetic_claim
from .cord import DEFAULT_DATASET, CordDatasetClient
from .evaluation import evaluate_decision, evaluate_extraction
from .models import (
    DecisionStatus,
    ExtractionEvaluation,
    PolicyFinding,
    Receipt,
    WorkflowReport,
)
from .ocr import MISTRAL_OCR_MODEL, extract_with_docling, extract_with_mistral
from .policy import ExpensePolicy, evaluate_claim
from .tracing import WorkflowTracer


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    split: str = "test"
    row_index: int | None = None
    seed: int = 42
    scenario: str | None = None
    output_dir: Path = Path("artifacts/latest")
    skip_docling: bool = False
    require_langfuse: bool = False
    minimum_extraction_accuracy: float = 0.75
    dataset: str = DEFAULT_DATASET


def _critical_ocr_disagreements(primary: Receipt, secondary: Receipt) -> list[str]:
    disagreements: list[str] = []
    for field in (
        "currency",
        "subtotal",
        "tax",
        "discount",
        "total_paid",
        "payment_method",
        "cash_tendered",
        "change",
    ):
        left = getattr(primary, field)
        right = getattr(secondary, field)
        if left is not None and right is not None and left != right:
            disagreements.append(field)
    return disagreements


def run_reconciliation(config: WorkflowConfig) -> WorkflowReport:
    """Run one fully traced CORD reconciliation and write a JSON report."""

    if not 0 <= config.minimum_extraction_accuracy <= 1:
        raise ValueError("minimum_extraction_accuracy must be between 0 and 1")
    output_dir = config.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "trace.json"
    report_path = output_dir / "report.json"

    tracer = WorkflowTracer(trace_path)
    if config.require_langfuse and not tracer.verify_langfuse():
        raise RuntimeError(
            "Langfuse is required for this run but authentication failed; set valid "
            "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL."
        )
    with (
        tracer,
        tracer.workflow(
            input={
                "dataset": config.dataset,
                "split": config.split,
                "row_index": config.row_index,
                "seed": config.seed,
                "scenario": config.scenario,
                "docling_enabled": not config.skip_docling,
                "langfuse_enabled": tracer.langfuse_enabled,
                "minimum_extraction_accuracy": config.minimum_extraction_accuracy,
            },
            metadata={"challenge": "receipt-reconciliation", "dataset_version": "CORD v2"},
        ) as workflow_observation,
    ):
        with tracer.observation(
            "cord-dataset-fetch",
            input={"split": config.split, "row_index": config.row_index},
        ) as observation:
            with CordDatasetClient(dataset=config.dataset) as dataset_client:
                sample = dataset_client.fetch_sample(
                    split=config.split,
                    row_index=config.row_index,
                    seed=config.seed,
                    output_dir=output_dir / "receipts",
                )
            observation.update(
                output={
                    "row_index": sample.row_index,
                    "image_path": sample.image_path,
                    "ground_truth": sample.receipt,
                }
            )

        with tracer.observation(
            "synthetic-claim-generation",
            input={"seed": config.seed, "scenario": config.scenario},
        ) as observation:
            claim = generate_synthetic_claim(
                sample.receipt,
                receipt_refs=[str(sample.image_path)],
                seed=config.seed,
                scenario=config.scenario,
            )
            observation.update(output=claim)

        with tracer.observation(
            "mistral-ocr-and-structured-extraction",
            as_type="generation",
            input={"image_path": sample.image_path},
            model=MISTRAL_OCR_MODEL,
        ) as observation:
            mistral_result = extract_with_mistral(sample.image_path)
            observation.update(
                output=mistral_result,
                metadata={"stages": ["ocr", "schema_extraction", "normalization"]},
            )

        docling_result = None
        if not config.skip_docling:
            with tracer.observation(
                "docling-ocr-and-mistral-structured-extraction",
                as_type="generation",
                input={"image_path": sample.image_path},
                model="docling + mistral-small-latest",
            ) as observation:
                docling_result = extract_with_docling(sample.image_path)
                observation.update(
                    output=docling_result,
                    metadata={
                        "stages": [
                            "docling_local_ocr",
                            "mistral_schema_extraction",
                            "normalization",
                        ]
                    },
                )

        extraction_evaluations: list[ExtractionEvaluation] = []
        with tracer.observation(
            "mistral-ground-truth-comparison",
            input={"actual": mistral_result.receipt, "expected": sample.receipt},
        ) as observation:
            mistral_evaluation = evaluate_extraction(
                "mistral", mistral_result.receipt, sample.receipt
            )
            extraction_evaluations.append(mistral_evaluation)
            observation.update(output=mistral_evaluation)
            observation.score("field_accuracy", mistral_evaluation.field_accuracy)

        if docling_result is not None:
            with tracer.observation(
                "docling-ground-truth-comparison",
                input={"actual": docling_result.receipt, "expected": sample.receipt},
            ) as observation:
                docling_evaluation = evaluate_extraction(
                    "docling", docling_result.receipt, sample.receipt
                )
                extraction_evaluations.append(docling_evaluation)
                observation.update(output=docling_evaluation)
                observation.score("field_accuracy", docling_evaluation.field_accuracy)

        with tracer.observation(
            "expected-policy-decision",
            input={"claim": claim, "evidence": sample.receipt, "source": "ground_truth"},
        ) as observation:
            expected_decision = evaluate_claim(claim, sample.receipt, ExpensePolicy())
            observation.update(output=expected_decision)

        with tracer.observation(
            "workflow-policy-decision",
            input={
                "claim": claim,
                "evidence": mistral_result.receipt,
                "source": "mistral_ocr",
            },
        ) as observation:
            actual_decision = evaluate_claim(claim, mistral_result.receipt, ExpensePolicy())
            safety_reasons: list[str] = []
            if mistral_evaluation.field_accuracy < config.minimum_extraction_accuracy:
                safety_reasons.append(
                    "Mistral extraction accuracy is below the configured challenge threshold."
                )
            if docling_result is not None:
                disagreements = _critical_ocr_disagreements(
                    mistral_result.receipt, docling_result.receipt
                )
                if disagreements:
                    safety_reasons.append(
                        "Mistral and Docling disagree on critical fields: "
                        + ", ".join(disagreements)
                        + "."
                    )
            if safety_reasons:
                actual_decision = actual_decision.model_copy(
                    update={
                        "status": DecisionStatus.ESCALATED,
                        "findings": [
                            *actual_decision.findings,
                            PolicyFinding(
                                rule_id="OCR_EVIDENCE_CONFLICT",
                                severity="error",
                                message=" ".join(safety_reasons),
                                receipt_field="ocr_comparison",
                            ),
                        ],
                        "additional_evidence_or_approval": list(
                            dict.fromkeys(
                                [
                                    *actual_decision.additional_evidence_or_approval,
                                    "Manually verify the receipt image and final payment evidence.",
                                ]
                            )
                        ),
                        "summary": (
                            "Escalated because OCR evidence is not sufficiently consistent "
                            "for an automated reimbursement decision."
                        ),
                    }
                )
            observation.update(output=actual_decision)

        with tracer.observation(
            "decision-evaluation",
            input={"actual": actual_decision, "expected": expected_decision},
        ) as observation:
            decision_evaluation = evaluate_decision(actual_decision, expected_decision)
            observation.update(output=decision_evaluation)
            observation.score("status_accuracy", decision_evaluation.status_match)
            observation.score("reimbursement_amount_accuracy", decision_evaluation.amount_match)

        tracer.score("mistral_field_accuracy", mistral_evaluation.field_accuracy)
        if docling_result is not None:
            tracer.score("docling_field_accuracy", docling_evaluation.field_accuracy)
        tracer.score("decision_status_accuracy", decision_evaluation.status_match)
        tracer.score("decision_amount_accuracy", decision_evaluation.amount_match)

        report = WorkflowReport(
            dataset=sample.dataset,
            split=sample.split,
            row_index=sample.row_index,
            image_path=str(sample.image_path),
            claim=claim,
            ground_truth=sample.receipt,
            mistral=mistral_result,
            docling=docling_result,
            extraction_evaluations=extraction_evaluations,
            expected_decision=expected_decision,
            actual_decision=actual_decision,
            decision_evaluation=decision_evaluation,
            trace_id=tracer.trace_id,
            langfuse_enabled=tracer.langfuse_enabled,
            local_trace_path=str(trace_path),
        )
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        workflow_observation.update(
            output={
                "status": actual_decision.status,
                "reimbursable_amount": actual_decision.reimbursable_amount,
                "report_path": report_path,
            }
        )

    return report


__all__ = ["WorkflowConfig", "run_reconciliation"]
