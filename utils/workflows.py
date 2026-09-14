from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_correlation_id: ContextVar[str] = ContextVar("avenue_guard_correlation_id", default="")


def new_correlation_id(prefix: str = "wf") -> str:
    safe_prefix = (
        re.sub(r"[^a-z0-9]+", "-", str(prefix).casefold()).strip("-")[:12] or "wf"
    )
    return f"{safe_prefix}-{int(time.time()):x}-{secrets.token_hex(4)}"


def current_correlation_id() -> str:
    return _correlation_id.get("")


def begin_workflow_context(correlation_id: str = "", *, prefix: str = "wf") -> str:
    """Attach a correlation ID to the current asyncio task until it completes."""
    value = str(correlation_id or new_correlation_id(prefix))
    _correlation_id.set(value)
    return value


def clear_workflow_context() -> None:
    _correlation_id.set("")


@contextmanager
def workflow_context(correlation_id: str = "", *, prefix: str = "wf"):
    value = str(correlation_id or new_correlation_id(prefix))
    token = _correlation_id.set(value)
    try:
        yield value
    finally:
        _correlation_id.reset(token)


class InvalidWorkflowTransition(ValueError):
    pass


class WorkflowStateMachine:
    """Small explicit state-transition guard used by durable workflows."""

    def __init__(self, transitions: dict[str, Iterable[str]]):
        self.transitions = {
            str(source): {str(target) for target in targets}
            for source, targets in transitions.items()
        }

    def allows(self, source: str, target: str) -> bool:
        return str(target) in self.transitions.get(str(source), set())

    def require(self, source: str, target: str) -> None:
        if not self.allows(source, target):
            raise InvalidWorkflowTransition(
                f"invalid workflow transition: {source!r} -> {target!r}"
            )


OUTBOX_STATES = WorkflowStateMachine(
    {
        "pending": ("processing", "dead"),
        "processing": ("delivered", "pending", "failed", "dead"),
        "failed": ("processing", "pending", "dead"),
        "delivered": (),
        "dead": ("pending",),
    }
)

REQUEST_REVIEW_STATES = WorkflowStateMachine(
    {
        "pending": ("reviewed",),
        "reviewed": (),
    }
)


async def record_workflow_event(
    db,
    *,
    workflow_type: str,
    event: str,
    entity_id: str = "",
    correlation_id: str = "",
    guild_id: int = 0,
    actor_id: int = 0,
    payload: dict[str, Any] | None = None,
) -> str:
    correlation = str(
        correlation_id or current_correlation_id() or new_correlation_id(workflow_type)
    )
    await db.execute(
        "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            correlation,
            str(workflow_type)[:80],
            str(entity_id)[:120],
            str(event)[:100],
            int(guild_id or 0),
            int(actor_id or 0),
            json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False),
            int(time.time()),
        ),
    )
    return correlation
