from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from .claims import SCENARIOS
from .models import WorkflowReport
from .workflow import WorkflowConfig, run_reconciliation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="receipt-reconcile",
        description="Reconcile a synthetic claim against a CORD v2 receipt.",
    )
    parser.add_argument("--split", choices=("train", "validation", "test"), default="test")
    parser.add_argument("--row-index", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenario", choices=SCENARIOS, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/latest"))
    parser.add_argument(
        "--skip-docling",
        action="store_true",
        help="Skip the local Docling comparison (Mistral OCR remains required).",
    )
    parser.add_argument(
        "--require-langfuse",
        action="store_true",
        help="Fail unless Langfuse credentials are configured and remote tracing is enabled.",
    )
    parser.add_argument(
        "--minimum-extraction-accuracy",
        type=float,
        default=0.75,
        help="Escalate when Mistral's CORD field accuracy falls below this threshold.",
    )
    return parser


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def render_summary(report: WorkflowReport) -> str:
    decision = report.actual_decision
    decision_passed = (
        report.decision_evaluation.status_match and report.decision_evaluation.amount_match
    )
    lines = [
        f"Decision: {decision.status.value}",
        f"Claimed: {report.claim.currency} {_money(decision.claimed_amount)}",
        f"Reimbursable: {report.claim.currency} {_money(decision.reimbursable_amount)}",
        f"Scenario: {report.claim.injected_scenario}",
        f"CORD sample: {report.split}/{report.row_index}",
        "",
        decision.summary,
    ]
    if decision.findings:
        lines.extend(["", "Findings:"])
        lines.extend(f"- {finding.rule_id}: {finding.message}" for finding in decision.findings)
    if decision.additional_evidence_or_approval:
        lines.extend(["", "Additional evidence or approval:"])
        lines.extend(f"- {item}" for item in decision.additional_evidence_or_approval)
    lines.extend(
        [
            "",
            f"Decision evaluation: {'PASS' if decision_passed else 'FAIL'}",
            f"Local trace: {report.local_trace_path}",
            (
                f"Langfuse: enabled (trace {report.trace_id})"
                if report.langfuse_enabled
                else "Langfuse: local-only; configure credentials to upload"
            ),
            f"Report: {Path(report.local_trace_path).with_name('report.json')}",
        ]
    )
    for evaluation in report.extraction_evaluations:
        lines.append(
            f"{evaluation.engine.title()} field accuracy: "
            f"{evaluation.field_accuracy:.1%} "
            f"({evaluation.matched_fields}/{evaluation.compared_fields})"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)
    try:
        report = run_reconciliation(
            WorkflowConfig(
                split=args.split,
                row_index=args.row_index,
                seed=args.seed,
                scenario=args.scenario,
                output_dir=args.output_dir,
                skip_docling=args.skip_docling,
                require_langfuse=args.require_langfuse,
                minimum_extraction_accuracy=args.minimum_extraction_accuracy,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(render_summary(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
