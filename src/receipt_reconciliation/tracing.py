from __future__ import annotations

import json
import os
import re
import ssl
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

_UNSET = object()
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "password",
    "private_key",
    "public_key",
    "secret",
    "secret_key",
    "token",
}
_MINIMIZED_REMOTE_KEYS = {
    "actual",
    "business_purpose",
    "claim",
    "employee_id",
    "evidence",
    "expected",
    "fields",
    "ground_truth",
    "image_path",
    "items",
    "merchant",
    "raw_text",
    "receipt",
    "receipt_refs",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sensitive(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return (
        normalized in _SENSITIVE_KEYS
        or normalized in {"credentials", "cookie", "set_cookie"}
        or normalized.endswith(("_api_key", "_secret", "_token", "_password", "_key"))
    )


def _redact_string(value: str) -> str:
    redacted = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "[REDACTED]", value)
    redacted = re.sub(r"\b(?:sk|pk)-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
    for variable in (
        "MISTRAL_API_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
    ):
        secret = os.getenv(variable)
        if secret and secret in redacted:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _safe(value: Any, *, key: object | None = None) -> Any:
    """Convert trace data to JSON while redacting common credential fields."""

    if key is not None and _sensitive(key):
        return "[REDACTED]"
    if value is None or isinstance(value, str | int | float | bool):
        return _redact_string(value) if isinstance(value, str) else value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(item_key): _safe(item, key=item_key) for item_key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _safe(value.model_dump(mode="json"))
    return str(value)


def _remote_safe(value: Any, *, key: object | None = None) -> Any:
    """Minimize receipt/employee data before it leaves the local machine."""

    normalized_key = str(key).strip().lower().replace("-", "_") if key is not None else None
    if normalized_key in _MINIMIZED_REMOTE_KEYS:
        return "[MINIMIZED]"
    if key is not None and _sensitive(key):
        return "[REDACTED]"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(item_key): _remote_safe(item, key=item_key) for item_key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_remote_safe(item) for item in value]
    return _safe(value, key=key)


def _observation_type(name: str) -> str:
    lowered = name.lower()
    if "evaluation" in lowered or lowered.startswith("evaluate"):
        return "evaluator"
    if "ocr" in lowered or "extraction" in lowered or "extract" in lowered:
        return "generation"
    if "decision" in lowered:
        return "chain"
    return "span"


@dataclass
class _LocalObservation:
    id: str
    parent_id: str | None
    remote_id: str | None
    name: str
    type: str
    started_at: str
    status: str = "running"
    ended_at: str | None = None
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    level: str | None = None
    status_message: str | None = None
    error: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "remote_id": self.remote_id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "input": self.input,
            "output": self.output,
            "metadata": self.metadata,
            "level": self.level,
            "status_message": self.status_message,
            "error": self.error,
        }


class ObservationHandle:
    """One local observation, optionally mirrored to a Langfuse v4 observation."""

    def __init__(
        self,
        tracer: WorkflowTracer,
        local: _LocalObservation,
        remote: Any | None = None,
    ) -> None:
        self._tracer = tracer
        self._local = local
        self._remote = remote

    @property
    def id(self) -> str:
        return self._local.id

    @property
    def remote_id(self) -> str | None:
        return self._local.remote_id

    def update(
        self,
        *,
        input: Any = _UNSET,
        output: Any = _UNSET,
        metadata: Any = _UNSET,
        level: Any = _UNSET,
        status_message: Any = _UNSET,
        **attributes: Any,
    ) -> ObservationHandle:
        remote_values: dict[str, Any] = {}
        if input is not _UNSET:
            self._local.input = _safe(input)
            remote_values["input"] = _remote_safe(input)
        if output is not _UNSET:
            self._local.output = _safe(output)
            remote_values["output"] = _remote_safe(output)
        if metadata is not _UNSET:
            safe_metadata = _safe(metadata)
            if isinstance(safe_metadata, dict):
                self._local.metadata.update(safe_metadata)
            else:
                self._local.metadata["value"] = safe_metadata
            remote_values["metadata"] = _remote_safe(metadata)
        if level is not _UNSET:
            self._local.level = str(level) if level is not None else None
            remote_values["level"] = level
        if status_message is not _UNSET:
            self._local.status_message = str(status_message) if status_message is not None else None
            remote_values["status_message"] = status_message
        if attributes:
            safe_attributes = _safe(attributes)
            self._local.metadata.setdefault("observation_attributes", {}).update(safe_attributes)
            remote_values.update(_remote_safe(attributes))

        if self._remote is not None and remote_values:
            try:
                self._remote.update(**remote_values)
            except Exception:
                self._tracer._record_remote_error("observation_update")
        self._tracer._write_local()
        return self

    def score(
        self,
        name: str,
        value: float | str | bool,
        *,
        data_type: str | None = None,
        comment: str | None = None,
        metadata: Any = None,
    ) -> None:
        self._tracer.score(
            name,
            value,
            observation=self,
            data_type=data_type,
            comment=comment,
            metadata=metadata,
        )


class WorkflowTracer:
    """Durable local tracing with an optional Langfuse v4 mirror.

    A JSON trace is written on every observation boundary and update. Langfuse
    is auto-enabled only when both Langfuse credential environment variables
    are present, or when an already configured client is injected. Remote
    tracing failures never interrupt receipt processing.
    """

    def __init__(
        self,
        local_path: str | Path,
        *,
        langfuse_client: Any | None = None,
        enable_langfuse: bool | None = None,
    ) -> None:
        self.local_path = Path(local_path)
        self.trace_id = uuid.uuid4().hex
        self._created_at = _now()
        self._updated_at = self._created_at
        self._observations: list[_LocalObservation] = []
        self._scores: list[dict[str, Any]] = []
        self._stack: list[ObservationHandle] = []
        self._remote_errors: dict[str, int] = {}
        self._client = langfuse_client
        if self._client is None and self._should_auto_enable(enable_langfuse):
            self._client = self._build_langfuse_client()
        self._write_local()

    @staticmethod
    def _should_auto_enable(enable_langfuse: bool | None) -> bool:
        if enable_langfuse is False:
            return False
        credentials_present = bool(
            os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
        )
        return credentials_present if enable_langfuse is None else enable_langfuse

    def _build_langfuse_client(self) -> Any | None:
        try:
            import truststore

            # Langfuse v4 uses httpx for its REST client and requests for its
            # OTLP exporter. Inject before constructing either transport.
            truststore.inject_into_ssl()
            from langfuse import Langfuse

            ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            return Langfuse(httpx_client=httpx.Client(verify=ssl_context, timeout=30.0))
        except Exception:
            self._record_remote_error("client_initialization")
            return None

    @property
    def langfuse_enabled(self) -> bool:
        return self._client is not None

    def verify_langfuse(self) -> bool:
        """Verify configured credentials against Langfuse when the client supports it."""

        if self._client is None:
            return False
        auth_check = getattr(self._client, "auth_check", None)
        if auth_check is None:
            return True  # injected test/custom clients are verified by their caller
        try:
            authenticated = bool(auth_check())
        except Exception:
            authenticated = False
        if not authenticated:
            self._record_remote_error("auth_check")
            self._write_local()
        return authenticated

    def __enter__(self) -> WorkflowTracer:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.flush()
        return False

    @contextmanager
    def workflow(
        self,
        name: str = "receipt-reconciliation",
        *,
        input: Any = None,
        metadata: Any = None,
    ) -> Iterator[ObservationHandle]:
        """Create the root workflow observation (Langfuse ``chain``)."""

        with self._observation_context(
            name=name,
            as_type="chain",
            input=input,
            metadata=metadata,
            is_root=True,
        ) as observation:
            yield observation

    @contextmanager
    def observation(
        self,
        name: str,
        *,
        as_type: str | None = None,
        input: Any = None,
        metadata: Any = None,
        model: str | None = None,
    ) -> Iterator[ObservationHandle]:
        """Create a nested OCR, extraction, comparison, decision, or evaluation span."""

        with self._observation_context(
            name=name,
            as_type=as_type or _observation_type(name),
            input=input,
            metadata=metadata,
            model=model,
            is_root=False,
        ) as observation:
            yield observation

    @contextmanager
    def _observation_context(
        self,
        *,
        name: str,
        as_type: str,
        input: Any,
        metadata: Any,
        model: str | None = None,
        is_root: bool,
    ) -> Iterator[ObservationHandle]:
        parent_id = self._stack[-1].id if self._stack else None
        local = _LocalObservation(
            id=uuid.uuid4().hex[:16],
            parent_id=parent_id,
            remote_id=None,
            name=name,
            type=as_type,
            started_at=_now(),
            input=_safe(input),
            metadata=_safe(metadata) if isinstance(_safe(metadata), dict) else {},
        )
        self._observations.append(local)

        remote_context = None
        remote = None
        if self._client is not None:
            remote_args: dict[str, Any] = {
                "name": name,
                "as_type": as_type,
                "input": _remote_safe(input),
                "metadata": _remote_safe(metadata),
            }
            if model is not None and as_type in {"generation", "embedding"}:
                remote_args["model"] = model
            try:
                remote_context = self._client.start_as_current_observation(**remote_args)
                remote = remote_context.__enter__()
                remote_id = getattr(remote, "id", None)
                local.remote_id = str(remote_id) if remote_id else None
            except Exception:
                self._record_remote_error("observation_start")
                remote_context = None
                remote = None

        handle = ObservationHandle(self, local, remote)
        self._stack.append(handle)
        if is_root and remote is not None:
            try:
                remote_trace_id = self._client.get_current_trace_id()
                if remote_trace_id:
                    self.trace_id = str(remote_trace_id)
            except Exception:
                self._record_remote_error("trace_id")
        self._write_local()

        exception_info: tuple[Any, Any, Any] = (None, None, None)
        try:
            yield handle
        except BaseException as exc:
            exception_info = sys.exc_info()
            local.status = "error"
            local.error = {"type": type(exc).__name__}
            handle.update(level="ERROR", status_message=type(exc).__name__)
            raise
        finally:
            if local.status == "running":
                local.status = "success"
            local.ended_at = _now()
            if self._stack and self._stack[-1] is handle:
                self._stack.pop()
            elif handle in self._stack:  # defensive for unusual manual context use
                self._stack.remove(handle)
            if remote_context is not None:
                try:
                    remote_context.__exit__(*exception_info)
                except Exception:
                    self._record_remote_error("observation_end")
            self._write_local()

    def update(self, **attributes: Any) -> ObservationHandle:
        """Update the current observation from orchestration helper code."""

        if not self._stack:
            raise RuntimeError("No active trace observation to update.")
        return self._stack[-1].update(**attributes)

    def score(
        self,
        name: str,
        value: float | str | bool,
        *,
        observation: ObservationHandle | None = None,
        data_type: str | None = None,
        comment: str | None = None,
        metadata: Any = None,
    ) -> None:
        """Record a local score and mirror it to Langfuse when configured."""

        if data_type is None:
            if isinstance(value, bool):
                data_type = "BOOLEAN"
            elif isinstance(value, str):
                data_type = "CATEGORICAL"
            else:
                data_type = "NUMERIC"
        local_score = {
            "name": name,
            "value": _safe(value),
            "data_type": data_type,
            "comment": _safe(comment),
            "metadata": _safe(metadata),
            "trace_id": self.trace_id,
            "observation_id": observation.id if observation else None,
            "remote_observation_id": observation.remote_id if observation else None,
            "remote_accepted": False,
            "timestamp": _now(),
        }
        self._scores.append(local_score)
        if self._client is not None:
            remote_args: dict[str, Any] = {
                "name": name,
                "value": float(value) if isinstance(value, bool) else value,
                "data_type": data_type,
                "trace_id": self.trace_id,
                "comment": _safe(comment),
                "metadata": _remote_safe(metadata),
            }
            if observation is not None and observation.remote_id:
                remote_args["observation_id"] = observation.remote_id
            try:
                self._client.create_score(**remote_args)
                # Langfuse queues events asynchronously. ``True`` means the SDK
                # accepted the event; ``flush`` is still required for delivery.
                local_score["remote_accepted"] = True
            except Exception:
                self._record_remote_error("score")
        self._write_local()

    def flush(self) -> None:
        """Persist the local trace and best-effort flush pending Langfuse events."""

        self._write_local()
        if self._client is not None:
            try:
                self._client.flush()
            except Exception:
                self._record_remote_error("flush")
                self._write_local()

    def _record_remote_error(self, operation: str) -> None:
        self._remote_errors[operation] = self._remote_errors.get(operation, 0) + 1

    def _write_local(self) -> None:
        self._updated_at = _now()
        payload = {
            "schema_version": 1,
            "trace_id": self.trace_id,
            "created_at": self._created_at,
            "updated_at": self._updated_at,
            "langfuse_enabled": self.langfuse_enabled,
            "remote_error_counts": self._remote_errors,
            "observations": [observation.as_dict() for observation in self._observations],
            "scores": self._scores,
        }
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.local_path.with_name(f".{self.local_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.local_path)


__all__ = ["ObservationHandle", "WorkflowTracer"]
