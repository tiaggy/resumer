"""Local Flask server for Resumer.

Run:  python app.py
Then open the URL it prints (defaults to http://127.0.0.1:17321).
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from config import OUTPUTS_DIR, ROOT, load_config
from file_utils import output_basename
from llm import LLMError, call_llm
from pdf import PdfError, html_to_pdf
from renderer import render_resume_html, save_html
from validation import inject_profile_contacts, validate_and_normalize


MIN_JOB_DESCRIPTION_CHARS = 200


def create_app() -> Flask:
    cfg = load_config()
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["RESUMER_CFG"] = cfg

    @app.get("/")
    def index() -> object:
        return send_from_directory(str(ROOT / "templates"), "index.html")

    @app.get("/api/profile")
    def api_profile() -> object:
        p = cfg.profile
        full_name = p.get("full_name", "")
        first_name = p.get("first_name") or ""
        last_name = p.get("last_name") or ""
        if not first_name and not last_name and full_name:
            parts = full_name.rsplit(" ", 1)
            if len(parts) == 2:
                first_name, last_name = parts
            else:
                first_name = parts[0]
        return jsonify(
            {
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
                "email": p.get("email", ""),
                "phone": p.get("phone", ""),
                "location": p.get("location", ""),
                "linkedin": p.get("linkedin", ""),
                "portfolio": p.get("portfolio", ""),
                "behance": p.get("behance", ""),
                "telegram": p.get("telegram", ""),
            }
        )

    @app.get("/api/health")
    def api_health() -> object:
        problems = cfg.validate_runtime()
        return jsonify({"ok": not problems, "problems": problems})

    @app.post("/api/generate")
    def api_generate() -> object:
        problems = cfg.validate_runtime()
        if problems:
            return jsonify(
                {
                    "ok": False,
                    "status": "error",
                    "error_code": "CONFIG_INVALID",
                    "message": "; ".join(problems),
                }
            ), 400

        payload = request.get_json(silent=True) or {}
        job_description = (payload.get("job_description") or "").strip()
        master_prompt = str(payload.get("master_prompt") or "")
        language = str(payload.get("language") or "Always English")
        overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
        if len(job_description) < MIN_JOB_DESCRIPTION_CHARS:
            return jsonify(
                {
                    "ok": False,
                    "status": "error",
                    "error_code": "JOB_DESCRIPTION_TOO_SHORT",
                    "message": (
                        f"Job description must be at least "
                        f"{MIN_JOB_DESCRIPTION_CHARS} characters."
                    ),
                }
            ), 400

        try:
            raw = call_llm(
                cfg,
                job_description,
                master_prompt=master_prompt,
                language=language,
                overrides=overrides,
            )
        except LLMError as exc:
            if exc.raw_response is not None:
                debug_path = OUTPUTS_DIR / "debug_raw_response.txt"
                debug_path.write_text(exc.raw_response, encoding="utf-8")
            return jsonify(
                {
                    "ok": False,
                    "status": "error",
                    "error_code": exc.code,
                    "message": str(exc),
                }
            ), 502

        if overrides:
            raw = _apply_overrides(raw, overrides)

        try:
            data, warnings = validate_and_normalize(raw, cfg.limits)
        except ValueError as exc:
            debug_path = OUTPUTS_DIR / "debug_raw_response.txt"
            debug_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            return jsonify(
                {
                    "ok": False,
                    "status": "error",
                    "error_code": "INVALID_JSON",
                    "message": str(exc),
                }
            ), 502

        data = inject_profile_contacts(data, cfg.profile)

        basename = output_basename(
            company=data.get("company_name", "company"),
            role=data.get("role_title", "role"),
            max_chars=int(cfg.limits.get("file_name_slug_max_chars", 80)),
        )

        html_path = OUTPUTS_DIR / f"{basename}_resume.html"
        pdf_path = OUTPUTS_DIR / f"{basename}_resume.pdf"
        cover_path = OUTPUTS_DIR / f"{basename}_cover_letter.txt"
        data_path = OUTPUTS_DIR / f"{basename}_data.json"

        html = render_resume_html(profile=cfg.profile, resume=data["resume"])
        save_html(html, html_path)
        # Convenience copy for previewing the latest run.
        save_html(html, OUTPUTS_DIR / "latest_resume.html")

        cover_text = data["cover_letter"].get("body", "")
        cover_path.write_text(cover_text, encoding="utf-8")

        record = {
            "input": {
                "job_description": job_description,
                "profile_snapshot": cfg.profile,
                "limits_snapshot": cfg.limits,
                "settings_snapshot": cfg.settings,
            },
            "output": {
                "company_name": data["company_name"],
                "role_title": data["role_title"],
                "resume": data["resume"],
                "cover_letter": data["cover_letter"],
                "quality_check": data["quality_check"],
            },
            "warnings": warnings,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        data_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

        # PDF generation is deferred — the UI triggers /api/make-pdf on demand.

        status = "done_with_warnings" if warnings else "done"
        response: dict[str, object] = {
            "ok": True,
            "status": status,
            "company_name": data["company_name"],
            "role_title": data["role_title"],
            "html_path": str(html_path.relative_to(ROOT)),
            "cover_letter_path": str(cover_path.relative_to(ROOT)),
            "data_path": str(data_path.relative_to(ROOT)),
            "cover_letter": cover_text,
            "quick_copy": data["application_quick_copy"],
            "quality_check": data["quality_check"],
            "warnings": warnings,
            "basename": basename,
        }

        return jsonify(response)

    @app.post("/api/open-file")
    def api_open_file() -> object:
        payload = request.get_json(silent=True) or {}
        rel = (payload.get("path") or "").strip()
        target = _resolve_inside_root(rel)
        if target is None:
            return jsonify({"ok": False, "error": "Invalid path"}), 400
        try:
            _open_with_default_app(target)
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True})

    @app.post("/api/make-pdf")
    def api_make_pdf() -> object:
        """Generate a PDF on demand. kind=resume converts an existing HTML file;
        kind=cover renders the cover-letter text into HTML, then converts."""
        payload = request.get_json(silent=True) or {}
        kind = (payload.get("kind") or "").strip()
        basename = (payload.get("basename") or "").strip()

        if kind == "resume":
            html_rel = (payload.get("html_path") or "").strip()
            html_path = _resolve_inside_root(html_rel)
            if html_path is None or not html_path.exists():
                return jsonify({"ok": False, "error": "Resume HTML not found"}), 400
            pdf_path = html_path.with_suffix(".pdf")
        elif kind == "cover":
            text = payload.get("text") or ""
            if not text.strip():
                return jsonify({"ok": False, "error": "Empty cover letter"}), 400
            if not basename:
                return jsonify({"ok": False, "error": "Missing basename"}), 400
            html_path = OUTPUTS_DIR / f"{basename}_cover_letter.html"
            pdf_path = OUTPUTS_DIR / f"{basename}_cover_letter.pdf"
            html_path.write_text(_render_cover_letter_html(text, cfg.profile), encoding="utf-8")
        else:
            return jsonify({"ok": False, "error": "Unknown kind"}), 400

        try:
            html_to_pdf(html_path, pdf_path)
        except PdfError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

        return jsonify({"ok": True, "pdf_path": str(pdf_path.relative_to(ROOT))})

    @app.post("/api/open-folder")
    def api_open_folder() -> object:
        payload = request.get_json(silent=True) or {}
        rel = (payload.get("path") or "outputs").strip()
        target = _resolve_inside_root(rel)
        if target is None or not target.exists():
            return jsonify({"ok": False, "error": "Invalid folder"}), 400
        folder = target if target.is_dir() else target.parent
        try:
            _open_with_default_app(folder)
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True})

    return app


def _resolve_inside_root(rel: str) -> Path | None:
    """Resolve `rel` against ROOT and reject anything escaping it."""
    if not rel:
        return None
    candidate = (ROOT / rel).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return candidate


def _apply_overrides(raw: dict, overrides: dict) -> dict:
    """Replace fields in the raw LLM dict with user-provided overrides.

    Runs BEFORE schema validation so any LLM placeholders for locked fields
    get overwritten with real values that satisfy the schema.
    """
    raw = dict(raw)
    for key in ("company_name", "role_title", "file_name_slug"):
        if overrides.get(key):
            raw[key] = overrides[key]

    resume = raw.get("resume") if isinstance(raw.get("resume"), dict) else {}
    raw["resume"] = resume
    for key in ("headline", "summary"):
        if overrides.get(key):
            resume[key] = overrides[key]
    for key in ("skills", "languages", "education", "experience"):
        if overrides.get(key) is not None and overrides.get(key) != []:
            resume[key] = overrides[key]

    cl = raw.get("cover_letter") if isinstance(raw.get("cover_letter"), dict) else {}
    raw["cover_letter"] = cl
    cl_override = overrides.get("cover_letter") or {}
    if cl_override.get("subject"):
        cl["subject"] = cl_override["subject"]
    if cl_override.get("body"):
        cl["body"] = cl_override["body"]

    return raw


def _render_cover_letter_html(text: str, profile: dict) -> str:
    """Render plain cover-letter text into a printable A4 HTML page."""
    import html as _html

    paragraphs = "".join(
        f"<p>{_html.escape(p)}</p>"
        for p in text.split("\n\n")
        if p.strip()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Cover Letter - {_html.escape(profile.get('full_name', ''))}</title>
<style>
@page {{ size: A4; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #fff; }}
body {{ font-family: Inter, "Helvetica Neue", Arial, sans-serif; color: #1f2126; }}
.page {{ width: 210mm; min-height: 297mm; padding: 25mm 22mm; margin: 0 auto; }}
.header {{ margin-bottom: 14mm; }}
.header h1 {{ margin: 0 0 2mm; font-size: 18pt; font-weight: 600; }}
.header .meta {{ font-size: 10pt; color: #4a4d55; }}
.body p {{ margin: 0 0 4mm; font-size: 11pt; line-height: 1.55; white-space: pre-wrap; }}
</style>
</head>
<body>
<main class="page">
  <header class="header">
    <h1>{_html.escape(profile.get('full_name', ''))}</h1>
    <div class="meta">{_html.escape(profile.get('email', ''))} &middot; {_html.escape(profile.get('location', ''))}</div>
  </header>
  <section class="body">{paragraphs}</section>
</main>
</body>
</html>"""


def _open_with_default_app(path: Path) -> None:
    system = platform.system()
    if system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def main() -> None:
    cfg = load_config()
    app = create_app()
    port = cfg.port
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    # Open the UI in a browser once the server is up. The Flask reloader spawns
    # a second process; only open in the "real" worker to avoid double tabs.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        threading.Timer(1.0, lambda: webbrowser.open_new(url)).start()

    print(f"Resumer running at {url}", file=sys.stderr)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
