from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from receipt_reconciliation.tracing import WorkflowTracer


class FakeObservation:
    def __init__(self, observation_id: str) -> None:
        self.id = observation_id
        self.updates: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class FakeContext:
    def __init__(self, observation: FakeObservation) -> None:
        self.observation = observation
        self.exit_args: tuple[Any, Any, Any] | None = None

    def __enter__(self) -> FakeObservation:
        return self.observation

    def __exit__(self, *args: Any) -> bool:
        self.exit_args = args
        return False


class FakeLangfuse:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.contexts: list[FakeContext] = []
        self.scores: list[dict[str, Any]] = []
        self.flush_count = 0

    def start_as_current_observation(self, **kwargs: Any) -> FakeContext:
        self.starts.append(kwargs)
        context = FakeContext(FakeObservation(f"remote-{len(self.starts)}"))
        self.contexts.append(context)
        return context

    def get_current_trace_id(self) -> str:
        return "a" * 32

    def create_score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flush_count += 1


def test_nested_local_and_langfuse_observations_are_recorded(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    remote = FakeLangfuse()
    secret = "must-not-appear-in-trace"
    tracer = WorkflowTracer(path, langfuse_client=remote)

    with tracer.workflow(
        input={"claim_id": "claim-1", "employee_id": "employee-42", "api_key": secret}
    ) as workflow:
        with tracer.observation(
            "mistral-ocr", input={"image": "receipt.png"}, model="mistral-ocr-latest"
        ) as ocr:
            ocr.update(
                output={"total_paid": "99.000"},
                metadata={"authorization": f"Bearer {secret}"},
            )
            ocr.score("field_accuracy", 0.95)
        with tracer.observation("comparison"):
            tracer.update(output={"matched": False})
        with tracer.observation("decision") as decision:
            decision.update(output={"status": "partially_approved"})
        with tracer.observation("extraction-evaluation"):
            pass
        workflow.update(output={"reimbursable": "90.000"})
    tracer.score("decision_match", True)
    tracer.flush()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert tracer.trace_id == "a" * 32
    assert payload["trace_id"] == "a" * 32
    assert payload["langfuse_enabled"] is True
    assert [item["type"] for item in payload["observations"]] == [
        "chain",
        "generation",
        "span",
        "chain",
        "evaluator",
    ]
    root_id = payload["observations"][0]["id"]
    assert all(item["status"] == "success" for item in payload["observations"])
    assert all(item["parent_id"] == root_id for item in payload["observations"][1:])
    assert [item["remote_id"] for item in payload["observations"]] == [
        "remote-1",
        "remote-2",
        "remote-3",
        "remote-4",
        "remote-5",
    ]
    assert payload["observations"][0]["input"]["api_key"] == "[REDACTED]"
    assert payload["observations"][0]["input"]["employee_id"] == "employee-42"
    assert remote.starts[0]["input"]["employee_id"] == "[MINIMIZED]"
    assert payload["observations"][1]["metadata"]["authorization"] == "[REDACTED]"
    assert secret not in path.read_text(encoding="utf-8")
    assert len(payload["scores"]) == 2
    assert payload["scores"][0]["remote_observation_id"] == "remote-2"
    assert all(score["remote_accepted"] is True for score in payload["scores"])
    assert remote.starts[1]["as_type"] == "generation"
    assert remote.starts[1]["model"] == "mistral-ocr-latest"
    assert remote.scores[0]["observation_id"] == "remote-2"
    assert remote.scores[1]["data_type"] == "BOOLEAN"
    assert remote.flush_count == 1


def test_local_trace_works_without_credentials_or_langfuse(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    tracer = WorkflowTracer(path, enable_langfuse=False)
    assert path.is_file()  # the trace exists even before the first operation

    with tracer.workflow() as workflow:
        workflow.update(metadata={"mode": "offline"})
    tracer.flush()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["langfuse_enabled"] is False
    assert payload["observations"][0]["status"] == "success"
    assert payload["observations"][0]["remote_id"] is None
    assert len(payload["trace_id"]) == 32


def test_observation_failure_is_persisted_without_error_message(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    tracer = WorkflowTracer(path, enable_langfuse=False)

    with pytest.raises(ValueError, match="sensitive-detail"):
        with tracer.workflow():
            with tracer.observation("comparison"):
                raise ValueError("sensitive-detail")

    payload = json.loads(path.read_text(encoding="utf-8"))
    comparison = payload["observations"][1]
    assert comparison["status"] == "error"
    assert comparison["error"] == {"type": "ValueError"}
    assert "sensitive-detail" not in path.read_text(encoding="utf-8")


def test_remote_tracing_failures_do_not_interrupt_local_trace(tmp_path: Path) -> None:
    class BrokenLangfuse:
        def start_as_current_observation(self, **kwargs: Any) -> Any:
            raise ConnectionError("unavailable")

        def flush(self) -> None:
            raise ConnectionError("unavailable")

    path = tmp_path / "trace.json"
    tracer = WorkflowTracer(path, langfuse_client=BrokenLangfuse())

    with tracer.workflow():
        with tracer.observation("comparison") as observation:
            observation.update(output={"ok": True})
    tracer.flush()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["observations"]) == 2
    assert payload["observations"][1]["status"] == "success"
    assert payload["remote_error_counts"] == {"flush": 1, "observation_start": 2}


def test_all_workflow_stages_and_evaluation_scores_are_mirrored(tmp_path: Path) -> None:
    """The production stage topology remains observable through the v4 SDK boundary."""

    remote = FakeLangfuse()
    tracer = WorkflowTracer(tmp_path / "trace.json", langfuse_client=remote)
    stages = [
        ("cord-dataset-fetch", "span"),
        ("synthetic-claim-generation", "span"),
        ("mistral-ocr-and-structured-extraction", "generation"),
        ("docling-ocr-and-mistral-structured-extraction", "generation"),
        ("mistral-ground-truth-comparison", "span"),
        ("docling-ground-truth-comparison", "span"),
        ("expected-policy-decision", "chain"),
        ("workflow-policy-decision", "chain"),
        ("decision-evaluation", "evaluator"),
    ]

    with tracer.workflow() as workflow:
        for name, as_type in stages:
            with tracer.observation(name, as_type=as_type) as observation:
                observation.update(output={"stage": name})
                if name.endswith("ground-truth-comparison"):
                    observation.score("field_accuracy", 0.9)
                elif name == "decision-evaluation":
                    observation.score("status_accuracy", True)
                    observation.score("reimbursement_amount_accuracy", True)
        workflow.update(output={"status": "partially_approved"})

    tracer.score("mistral_field_accuracy", 0.9)
    tracer.score("docling_field_accuracy", 0.9)
    tracer.score("decision_status_accuracy", True)
    tracer.score("decision_amount_accuracy", True)
    tracer.flush()

    payload = json.loads(tracer.local_path.read_text(encoding="utf-8"))
    assert [(call["name"], call["as_type"]) for call in remote.starts] == [
        ("receipt-reconciliation", "chain"),
        *stages,
    ]
    assert len(remote.starts) == len(payload["observations"]) == 10
    assert all(item["remote_id"] is not None for item in payload["observations"])
    assert [score["name"] for score in remote.scores] == [
        "field_accuracy",
        "field_accuracy",
        "status_accuracy",
        "reimbursement_amount_accuracy",
        "mistral_field_accuracy",
        "docling_field_accuracy",
        "decision_status_accuracy",
        "decision_amount_accuracy",
    ]
    assert len(remote.scores) == len(payload["scores"]) == 8
    assert all(score["remote_accepted"] is True for score in payload["scores"])
    assert remote.flush_count == 1


def test_langfuse_auto_enable_requires_both_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public-only")

    def fail_if_called(self: WorkflowTracer) -> Any:
        raise AssertionError("Langfuse client must not be built with partial credentials")

    monkeypatch.setattr(WorkflowTracer, "_build_langfuse_client", fail_if_called)
    tracer = WorkflowTracer(tmp_path / "trace.json")

    assert tracer.langfuse_enabled is False


def test_failed_remote_score_remains_local_and_marked_unaccepted(tmp_path: Path) -> None:
    class ScoreFailureLangfuse(FakeLangfuse):
        def create_score(self, **kwargs: Any) -> None:
            raise ConnectionError("unavailable")

    path = tmp_path / "trace.json"
    tracer = WorkflowTracer(path, langfuse_client=ScoreFailureLangfuse())

    with tracer.workflow() as workflow:
        workflow.score("decision_match", True)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["scores"][0]["remote_accepted"] is False
    assert payload["scores"][0]["remote_observation_id"] == "remote-1"
    assert payload["remote_error_counts"] == {"score": 1}


def test_embedded_and_common_secret_shapes_are_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "known-mistral-secret")
    path = tmp_path / "trace.json"
    tracer = WorkflowTracer(path, enable_langfuse=False)

    with tracer.workflow(
        input={
            "client_secret": "client-value",
            "credentials": {"user": "a", "password": "b"},
            "note": "prefix known-mistral-secret suffix",
            "header": "Authorization: Bearer abc123, next",
        }
    ):
        pass

    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    root_input = payload["observations"][0]["input"]
    assert root_input["client_secret"] == "[REDACTED]"
    assert root_input["credentials"] == "[REDACTED]"
    assert "known-mistral-secret" not in text
    assert "abc123" not in text


def test_verify_langfuse_checks_authentication_and_records_failure(tmp_path: Path) -> None:
    class UnauthenticatedLangfuse(FakeLangfuse):
        def auth_check(self) -> bool:
            return False

    path = tmp_path / "trace.json"
    tracer = WorkflowTracer(path, langfuse_client=UnauthenticatedLangfuse())

    assert tracer.verify_langfuse() is False

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["remote_error_counts"] == {"auth_check": 1}


def test_update_requires_an_active_observation(tmp_path: Path) -> None:
    tracer = WorkflowTracer(tmp_path / "trace.json", enable_langfuse=False)
    with pytest.raises(RuntimeError, match="No active"):
        tracer.update(output={"orphan": True})
