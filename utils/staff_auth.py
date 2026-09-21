from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

ROLE_ALIASES = {
    "judge": "reviewer",
    "head_judge": "head_reviewer",
}

ROLE_LABELS = {
    "applicant": "Applicant",
    "reviewer": "Reviewer",
    "head_reviewer": "Head Reviewer",
    "admin": "Admin",
    "owner": "Owner",
    "dev": "Dev",
}

ROLE_ORDER = {
    "applicant": 0,
    "reviewer": 1,
    "head_reviewer": 2,
    "admin": 3,
    "owner": 4,
    "dev": 5,
}

REVIEWER_CAPABILITIES = {
    "applications.apply",
    "notes.private",
    "notes.reviewer",
    "notes.team",
    "outreach.confirm_submission",
    "outreach.record",
    "outreach.view",
    "queue.claim",
    "queue.release_own",
    "queue.view",
    "staff.access",
    "stats.view_own",
    "stats.view_team_summary",
    "tasks.create",
    "tasks.manage_own",
}

HEAD_REVIEWER_CAPABILITIES = REVIEWER_CAPABILITIES | {
    "applications.review_reviewer",
    "applications.review_judge",
    "notes.head",
    "outreach.manage_targets",
    "queue.manage_standard",
    "queue.manage_state",
    "queue.reassign",
    "review.adjust_tier",
    "review.adjust_tier_pre_submission",
    "review.qa",
    "stats.view_reviewers",
    "tasks.assign_reviewers",
    "tasks.assign",
    "tasks.manage_team",
}

ADMIN_CAPABILITIES = HEAD_REVIEWER_CAPABILITIES | {
    "admin.access",
    "applications.review_standard",
    "appeals.execute",
    "appeals.review",
    "audit.view_limited",
    "forum.manage",
    "operations.manage_standard",
    "operations.view",
    "pps.manage_cycles",
    "pps.manage_standard",
    "public_cache.manage",
    "requests.manage",
    "requests.schedule",
    "staff.manage",
    "staff.manage_nicknames",
    "staff.manage_standard_roles",
    "staff.view",
    "support.manage",
    "tracking.manage",
}

OWNER_CAPABILITIES = ADMIN_CAPABILITIES | {
    "applications.review_all",
    "audit.view",
    "audit.view_full",
    "backups.manage",
    "config.manage_safe",
    "cp.override",
    "notes.owner",
    "operations.manage_sensitive",
    "pps.override",
    "releases.manage",
    "restore_drills.manage",
    "retention.manage",
    "staff.manage_all",
}

DEV_CAPABILITIES = OWNER_CAPABILITIES | {
    "developer.access",
    "developer.debug",
    "developer.incidents",
    "developer.maintenance",
    "developer.outbox",
    "developer.providers",
    "developer.release_low_level",
    "developer.resync",
    "developer.restart",
    "developer.runtime",
    "developer.schemas",
    "developer.tasks",
}

ROLE_CAPABILITIES = {
    "applicant": {"applications.apply"},
    "reviewer": REVIEWER_CAPABILITIES,
    "head_reviewer": HEAD_REVIEWER_CAPABILITIES,
    "admin": ADMIN_CAPABILITIES,
    "owner": OWNER_CAPABILITIES,
    "dev": DEV_CAPABILITIES,
}


def canonical_role(role: str) -> str:
    value = str(role or "applicant").strip().casefold()
    return ROLE_ALIASES.get(
        value, value if value in ROLE_CAPABILITIES else "applicant"
    )


def role_label(role: str) -> str:
    return ROLE_LABELS[canonical_role(role)]


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
    portal_nickname: str = ""
    discord_display_name: str = ""
    global_display_name: str = ""
    username: str = ""

    def can(self, capability: str) -> bool:
        return str(capability) in self.capabilities

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise PermissionError(f"Missing capability: {capability}")


def capability_set(role: str) -> frozenset[str]:
    return frozenset(ROLE_CAPABILITIES.get(canonical_role(role), set()))


def resolve_staff_role(
    user_id: int,
    role_ids: Iterable[int],
    config: Any,
) -> str:
    user_id = int(user_id)
    roles = {int(role_id) for role_id in role_ids}
    dev_users = set(config.get_int_list("staff_portal", "dev_user_ids"))
    owner_users = set(config.get_int_list("staff_portal", "owner_user_ids"))
    owner_roles = set(config.get_int_list("staff_portal", "owner_role_ids"))
    admin_roles = set(config.get_int_list("staff_portal", "admin_role_ids"))
    head_roles = set(config.get_int_list("staff_portal", "head_judge_role_ids"))
    reviewer_roles = set(config.get_int_list("staff_portal", "judge_role_ids"))
    if user_id in dev_users:
        return "dev"
    if user_id in owner_users or roles & owner_roles:
        return "owner"
    if roles & admin_roles:
        return "admin"
    if roles & head_roles:
        return "head_reviewer"
    if roles & reviewer_roles:
        return "reviewer"
    return "applicant"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def token_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def tokens_match(raw_value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(token_hash(raw_value), str(expected_hash or ""))
