"""Filename / slug helpers."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_chars: int = 80) -> str:
    """Lowercase ASCII, dashes, no trailing/leading separators."""
    if not value:
        return "untitled"
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", ascii_only.lower()).strip("-")
    if not slug:
        return "untitled"
    if len(slug) > max_chars:
        slug = slug[:max_chars].rstrip("-")
    return slug or "untitled"


def output_basename(
    *, company: str, role: str, today: date | None = None, max_chars: int = 80
) -> str:
    """Return e.g. 2026-05-14_acme-product-designer for output files."""
    if today is None:
        today = date.today()
    combined = f"{company}-{role}".strip("-")
    return f"{today.isoformat()}_{slugify(combined, max_chars=max_chars)}"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
