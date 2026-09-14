from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

LEVEL_ID_RE = re.compile(r"^\d{7,9}$")


def validate_level_id_shape(level_id: Any) -> str:
    value = str(level_id or "").strip()
    return "" if LEVEL_ID_RE.fullmatch(value) else "Level ID must contain 7 to 9 digits"


def validate_showcase_url(value: Any, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return "A showcase URL is required for this level" if required else ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return "Showcase must be a valid URL"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "Showcase must be a valid HTTP or HTTPS URL"
    return ""


def validation_requires_showcase(validation: dict[str, Any]) -> bool:
    return bool(validation.get("is_demon") or validation.get("platformer"))
