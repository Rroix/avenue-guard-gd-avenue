from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

ROLE_ORDER = {"applicant": 0, "judge": 1, "head_judge": 2, "owner": 3}

JUDGE_CAPABILITIES = {
    "applications.apply",
    "notes.private",
    "notes.team",
    "outreach.confirm_submission",
    "outreach.record",
    "outreach.view",
    "queue.claim",
    "queue.release_own",
    "queue.view",
    "staff.access",
    "tasks.create",
}

HEAD_JUDGE_CAPABILITIES = JUDGE_CAPABILITIES | {
    "applications.review_judge",
    "notes.head",
    "outreach.manage_targets",
    "queue.manage_state",
    "queue.reassign",
    "review.adjust_tier",
    "review.qa",
    "tasks.assign",
    "tasks.manage_team",
}

OWNER_CAPABILITIES = HEAD_JUDGE_CAPABILITIES | {
    "applications.review_all",
    "audit.view",
    "config.manage_safe",
    "notes.owner",
    "operations.view",
    "pps.manage_cycles",
    "pps.override",
    "staff.manage",
}

ROLE_CAPABILITIES = {
    "applicant": {"applications.apply"},
    "judge": JUDGE_CAPABILITIES,
    "head_judge": HEAD_JUDGE_CAPABILITIES,
    "owner": OWNER_CAPABILITIES,
}


@dataclass(frozen=True)
class StaffPrincipal:
    user_id: int
    guild_id: int
    display_name: str
    avatar_url: str
    role: str
    role_ids: tuple[int, ...]
    capabilities: frozenset[str]
    session_hash: str = ""

    def can(self, capability: str) -> bool:
        return str(capability) in self.capabilities

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise PermissionError(f"Missing capability: {capability}")


def capability_set(role: str) -> frozenset[str]:
    return frozenset(ROLE_CAPABILITIES.get(str(role), set()))


def resolve_staff_role(
    user_id: int,
    role_ids: Iterable[int],
    config: Any,
) -> str:
    user_id = int(user_id)
    roles = {int(role_id) for role_id in role_ids}
    owner_users = set(config.get_int_list("staff_portal", "owner_user_ids"))
    owner_roles = set(config.get_int_list("staff_portal", "owner_role_ids"))
    head_roles = set(config.get_int_list("staff_portal", "head_judge_role_ids"))
    judge_roles = set(config.get_int_list("staff_portal", "judge_role_ids"))
    if user_id in owner_users or roles & owner_roles:
        return "owner"
    if roles & head_roles:
        return "head_judge"
    if roles & judge_roles:
        return "judge"
    return "applicant"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def token_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def tokens_match(raw_value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(token_hash(raw_value), str(expected_hash or ""))

