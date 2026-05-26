"""Render tailored resume JSON into a self-contained HTML page.

The template (`templates/resume.html`) is a Jinja-ized copy of
`uliana_sierik_resume_3exp_short_core_skills.html` with absolute mm-positioned
text slots. This module fills those slots from the profile + LLM-tailored
resume JSON, padding to the fixed shape (3 jobs, 9 skills, 4 languages, etc.).
"""
from __future__ import annotations

import base64
import html
import mimetypes
import textwrap
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import TEMPLATES_DIR


# Per-line character widths the slot can hold without horizontal overflow at
# the font sizes baked into the template. Tuned empirically against the
# original Uliana layout. Used by textwrap to break headline / summary across
# the fixed multi-line slots.
TAGLINE_LINE_CHARS = 30   # .tagline ≈ 3.2mm font, ~45mm column
ABOUT_LINE_CHARS = 58     # .body ≈ 2.92mm font, ~91mm column (Slovak/Czech diacritics-safe)


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _photo_data_uri(photo: str) -> str:
    if not photo:
        return ""
    if photo.startswith(("data:", "http://", "https://")):
        return photo
    path = Path(photo)
    if not path.is_absolute():
        path = (TEMPLATES_DIR.parent / path).resolve()
    if not path.is_file():
        return ""
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _default_photo_uri() -> str:
    """Optional shipped fallback portrait. If the file is absent (the default),
    the template renders a blank circle and the candidate can supply their own
    photo via profile.json."""
    candidate = TEMPLATES_DIR / "default_portrait.jpg"
    if candidate.is_file():
        return _photo_data_uri(str(candidate))
    return ""


def _wrap_lines(text: str, width: int, n: int) -> list[str]:
    """Wrap `text` to at most `n` lines of approximately `width` chars each.
    If text fits in fewer lines the remainder are empty strings."""
    if not text:
        return [""] * n
    parts = textwrap.wrap(text, width=width, break_long_words=False) or [""]
    parts = parts[:n]
    while len(parts) < n:
        parts.append("")
    return parts


def _pad(items: list[Any], n: int) -> list[Any]:
    items = list(items)[:n]
    while len(items) < n:
        items.append(None)
    return items


def _build_slots(profile: dict[str, Any], resume: dict[str, Any]) -> dict[str, str]:
    slots: dict[str, str] = {}

    # Contact
    slots["email"] = profile.get("email", "") or ""
    slots["behance"] = profile.get("behance", "") or ""
    slots["linkedin"] = profile.get("linkedin", "") or ""
    slots["location"] = profile.get("location", "") or ""

    # Name + tagline (3 fixed lines)
    slots["name"] = profile.get("full_name", "") or ""
    tagline_lines = _wrap_lines(resume.get("headline", ""), TAGLINE_LINE_CHARS, 3)
    slots["tagline_1"], slots["tagline_2"], slots["tagline_3"] = tagline_lines

    # About Me (3 fixed lines)
    about_lines = _wrap_lines(resume.get("summary", ""), ABOUT_LINE_CHARS, 3)
    slots["about_1"], slots["about_2"], slots["about_3"] = about_lines

    # Skills (9 fixed slots)
    skills = _pad(resume.get("skills", []), 9)
    for i, s in enumerate(skills, start=1):
        slots[f"skill_{i}"] = s or ""

    # Education (1 fixed block: dates, degree-bold, faculty, school)
    edu_list = resume.get("education", []) or []
    edu = edu_list[0] if edu_list else {}
    slots["edu_dates"] = edu.get("dates", "") or ""
    slots["edu_degree"] = edu.get("degree", "") or ""
    # The original layout had separate "faculty" and "school" lines. Our schema
    # has a single `school` field, so split on the first comma if present.
    school = edu.get("school", "") or ""
    if "," in school:
        faculty, rest = school.split(",", 1)
        slots["edu_faculty"] = faculty.strip()
        slots["edu_school"] = rest.strip()
    else:
        slots["edu_faculty"] = school
        slots["edu_school"] = ""

    # Languages (4 fixed slots, formatted as "<b>Name</b> - Level")
    langs = _pad(resume.get("languages", []), 4)
    for i, lang in enumerate(langs, start=1):
        if not lang:
            slots[f"lang_{i}"] = ""
            continue
        name = html.escape(lang.get("name", "") or "")
        level = html.escape(lang.get("level", "") or "")
        if name and level:
            slots[f"lang_{i}"] = f"<b>{name}</b> - {level}"
        elif name:
            slots[f"lang_{i}"] = f"<b>{name}</b>"
        else:
            slots[f"lang_{i}"] = ""

    # Experience: 3 fixed jobs, each with date, role, company, 6 bullets, core line
    jobs = _pad(resume.get("experience", []), 3)
    for ji, job in enumerate(jobs, start=1):
        if not job:
            slots[f"job{ji}_date"] = ""
            slots[f"job{ji}_role"] = ""
            slots[f"job{ji}_company"] = ""
            for bi in range(1, 7):
                slots[f"job{ji}_b{bi}"] = ""
            slots[f"job{ji}_core"] = ""
            continue
        slots[f"job{ji}_date"] = job.get("dates", "") or ""
        slots[f"job{ji}_role"] = job.get("title", "") or ""
        company = job.get("company", "") or ""
        location = job.get("location", "") or ""
        if company and location:
            slots[f"job{ji}_company"] = f"{company} - {location}"
        else:
            slots[f"job{ji}_company"] = company or location
        bullets = _pad(job.get("bullets", []), 6)
        for bi, b in enumerate(bullets, start=1):
            slots[f"job{ji}_b{bi}"] = b or ""
        core = job.get("core_skills", []) or []
        if core:
            slots[f"job{ji}_core"] = "Core Skills: " + " · ".join(core)
        else:
            slots[f"job{ji}_core"] = ""

    return slots


def render_resume_html(
    *,
    profile: dict[str, Any],
    resume: dict[str, Any],
) -> str:
    """Return a self-contained HTML page for printing/viewing."""
    env = _env()
    template = env.get_template("resume.html")

    photo_src = _photo_data_uri(profile.get("photo", "")) or _default_photo_uri()
    slots = _build_slots(profile, resume)

    return template.render(photo_src=photo_src, slots=slots)


def save_html(html_text: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return output_path
