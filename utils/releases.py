from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


SEMVER_RE = re.compile(
    r"^(?:[vV])?(?P<version>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
MAX_RELEASE_CHANGES = 20
MAX_RELEASE_CHANGE_LENGTH = 300


class ReleaseValidationError(ValueError):
    """Raised when a release proposal cannot be published safely."""


def normalize_version(value: Any) -> str:
    raw = str(value or "").strip()
    match = SEMVER_RE.fullmatch(raw)
    if match is None:
        raise ReleaseValidationError(
            "Version must use semantic versioning such as `2.4.1` or `2.4.1-beta.1`"
        )
    return raw[1:] if raw.casefold().startswith("v") else raw


def normalize_release_changes(value: str | Iterable[Any]) -> list[str]:
    if isinstance(value, str):
        items = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    else:
        items = list(value or [])

    changes: list[str] = []
    for item in items:
        text = str(item or "").strip()
        text = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", text).strip()
        if not text:
            continue
        if len(text) > MAX_RELEASE_CHANGE_LENGTH:
            raise ReleaseValidationError(
                f"Each release change must be {MAX_RELEASE_CHANGE_LENGTH} characters or fewer"
            )
        changes.append(text)

    if not changes:
        raise ReleaseValidationError("Add at least one release change")
    if len(changes) > MAX_RELEASE_CHANGES:
        raise ReleaseValidationError(
            f"A release can contain at most {MAX_RELEASE_CHANGES} changes"
        )
    return changes


def validate_release_payload(
    version: Any,
    title: Any,
    summary: Any,
    changes: str | Iterable[Any],
) -> dict[str, Any]:
    normalized_version = normalize_version(version)
    normalized_title = str(title or "").strip() or f"Avenue Guard {normalized_version}"
    normalized_summary = str(summary or "").strip()

    if len(normalized_title) > 100:
        raise ReleaseValidationError("Release title must be 100 characters or fewer")
    if len(normalized_summary) > 600:
        raise ReleaseValidationError("Release summary must be 600 characters or fewer")

    return {
        "version": normalized_version,
        "title": normalized_title,
        "summary": normalized_summary,
        "changes": normalize_release_changes(changes),
    }


def load_release_manifest(path: str | Path) -> dict[str, Any] | None:
    manifest_path = Path(path)
    if not manifest_path.exists():
        return None

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"Could not read {manifest_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ReleaseValidationError(f"{manifest_path} must contain a JSON object")
    if not str(payload.get("version") or "").strip():
        return None

    return validate_release_payload(
        payload.get("version"),
        payload.get("title"),
        payload.get("summary"),
        payload.get("changes") or [],
    )


def row_to_public_release(row: Any) -> dict[str, Any]:
    try:
        changes = json.loads(str(row["changes_json"] or "[]"))
    except (KeyError, TypeError, json.JSONDecodeError):
        changes = []
    if not isinstance(changes, list):
        changes = []

    return {
        "version": str(row["version"] or ""),
        "title": str(row["title"] or ""),
        "summary": str(row["summary"] or ""),
        "changes": [
            str(change)[:MAX_RELEASE_CHANGE_LENGTH]
            for change in changes[:MAX_RELEASE_CHANGES]
            if str(change or "").strip()
        ],
        "published_ts": int(row["decided_ts"] or 0),
    }
