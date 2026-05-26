"""Single-call OpenAI-compatible chat completion that returns parsed JSON.

The endpoint, key, and model come from .env (so any OpenAI-compatible API
works: api.openai.com, OpenRouter, DeepSeek, Groq, local server, ...).

For privacy we strip phone, email, and the precise address from the profile
before sending — those are re-injected from profile.json after the response.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from config import AppConfig


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


SYSTEM_PROMPT = _load_prompt("system.md")
USER_MESSAGE_TEMPLATE = _load_prompt("user_message.txt")



class LLMError(RuntimeError):
    def __init__(self, message: str, *, code: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.raw_response = raw_response


def _strip_private(profile: dict[str, Any]) -> dict[str, Any]:
    """Remove fields we'd rather not ship to the LLM."""
    safe = dict(profile)
    safe.pop("phone", None)
    # Keep location (city/country) but drop precise address if present.
    safe.pop("address", None)
    return safe


_OVERRIDE_FIELD_DESCRIPTIONS = {
    "company_name": "company_name (top-level)",
    "role_title": "role_title (top-level)",
    "file_name_slug": "file_name_slug (top-level)",
    "headline": "resume.headline",
    "summary": "resume.summary",
    "skills": "resume.skills",
    "languages": "resume.languages",
    "education": "resume.education",
    "experience": "resume.experience",
}


def _build_locked_fields_block(overrides: dict[str, Any] | None) -> str:
    if not overrides:
        return "LOCKED_FIELDS: (none — generate every field normally)"
    locked: list[str] = []
    for key, label in _OVERRIDE_FIELD_DESCRIPTIONS.items():
        val = overrides.get(key)
        if val:
            locked.append(label)
    cl = overrides.get("cover_letter") or {}
    if cl.get("subject"):
        locked.append("cover_letter.subject")
    if cl.get("body"):
        locked.append("cover_letter.body")
    if not locked:
        return "LOCKED_FIELDS: (none — generate every field normally)"
    bullet_list = "\n".join(f"  - {x}" for x in locked)
    return (
        "LOCKED_FIELDS (user-supplied — DO NOT GENERATE these; emit empty "
        "strings or empty arrays as placeholders; spend zero tokens "
        "optimizing them; downstream code will overwrite with the user's "
        "values):\n" + bullet_list
    )


def _build_user_message(
    *,
    settings: dict[str, Any],
    profile: dict[str, Any],
    limits: dict[str, Any],
    job_description: str,
    master_prompt: str = "",
    language: str = "Always English",
    overrides: dict[str, Any] | None = None,
) -> str:
    dump = lambda obj: json.dumps(obj, ensure_ascii=False, indent=2)
    master_block = (
        (master_prompt or "").strip()
        or "(no master prompt provided)"
    )
    return USER_MESSAGE_TEMPLATE.format(
        master_prompt=master_block,
        language=(language or "Always English").strip(),
        locked_fields_block=_build_locked_fields_block(overrides),
        settings_json=dump(settings),
        profile_json=dump(_strip_private(profile)),
        limits_json=dump(limits),
        job_description=job_description,
    )


def _make_client(cfg: AppConfig) -> OpenAI:
    kwargs: dict[str, Any] = {"api_key": cfg.api_key, "base_url": cfg.base_url}
    headers: dict[str, str] = {}
    if cfg.app_referer:
        headers["HTTP-Referer"] = cfg.app_referer
    if cfg.app_title:
        headers["X-Title"] = cfg.app_title
    if headers:
        kwargs["default_headers"] = headers
    return OpenAI(**kwargs)


def call_llm(
    cfg: AppConfig,
    job_description: str,
    *,
    master_prompt: str = "",
    language: str = "Always English",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send one request to the LLM and return the parsed JSON dict.

    Raises LLMError on transport failure or unparseable JSON.
    """
    settings = cfg.settings
    user_msg = _build_user_message(
        settings=settings,
        profile=cfg.profile,
        limits=cfg.limits,
        job_description=job_description,
        master_prompt=master_prompt,
        language=language,
        overrides=overrides,
    )

    client = _make_client(cfg)

    request_kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": int(settings.get("max_tokens", 8000)),
    }

    # Some hosted/local endpoints choke on response_format. Default to json_object
    # which is the most widely supported; allow "none" to disable entirely.
    response_format_mode = settings.get("response_format_mode", "json_object")
    if response_format_mode == "json_object":
        request_kwargs["response_format"] = {"type": "json_object"}
    elif response_format_mode == "json_schema":
        # OpenAI strict json_schema (gpt-4o family). Provider must support it.
        request_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "resumer_output",
                "strict": False,
                "schema": _output_schema(),
            },
        }

    temperature = settings.get("temperature")
    if temperature is not None:
        request_kwargs["temperature"] = float(temperature)

    reasoning_effort = settings.get("reasoning_effort")
    if reasoning_effort:
        # Only o-series / reasoning models accept this; harmless to attempt
        # via extra_body when the param isn't first-class.
        request_kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}

    try:
        response = client.chat.completions.create(**request_kwargs)
    except OpenAIError as exc:
        raise LLMError(str(exc), code="CLAUDE_REQUEST_FAILED") from exc

    if not response.choices:
        raise LLMError("LLM returned no choices", code="EMPTY_RESPONSE")

    raw_text = response.choices[0].message.content or ""
    raw_text = raw_text.strip()
    # Some models still wrap output in fences despite instructions.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"Model returned invalid JSON: {exc}",
            code="INVALID_JSON",
            raw_response=raw_text,
        ) from exc


def _output_schema() -> dict[str, Any]:
    """JSON schema for endpoints that support response_format=json_schema.

    Kept lenient (strict=False) so providers that enforce strict mode
    don't reject optional/extra fields."""
    return {
        "type": "object",
        "properties": {
            "file_name_slug": {"type": "string"},
            "company_name": {"type": "string"},
            "role_title": {"type": "string"},
            "resume": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "experience": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "company": {"type": "string"},
                                "title": {"type": "string"},
                                "location": {"type": "string"},
                                "dates": {"type": "string"},
                                "description": {"type": "string"},
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                    "skills": {"type": "array", "items": {"type": "string"}},
                    "projects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "skills": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
            "cover_letter": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
            },
            "application_quick_copy": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "location": {"type": "string"},
                    "linkedin": {"type": "string"},
                    "portfolio": {"type": "string"},
                },
            },
            "quality_check": {
                "type": "object",
                "properties": {
                    "job_focus": {"type": "string"},
                    "most_relevant_matches": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "unsupported_job_requirements_not_claimed": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "possible_risks": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    }
