"""Print an HTML file to PDF using headless Edge or Chrome (Windows).

Returns the PDF path on success, raises PdfError otherwise. The caller is
expected to fall back to "open the HTML in a real browser" on failure."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class PdfError(RuntimeError):
    pass


def _candidate_browsers() -> list[Path]:
    paths: list[Path] = []

    # Most reliable: explicit Windows install locations.
    program_files = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LocalAppData", str(Path.home() / "AppData" / "Local")),
    ]
    known = [
        r"Microsoft\Edge\Application\msedge.exe",
        r"Google\Chrome\Application\chrome.exe",
        r"Chromium\Application\chromium.exe",
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    for base in program_files:
        if not base:
            continue
        base_p = Path(base)
        for tail in known:
            candidate = base_p / tail
            if candidate.exists():
                paths.append(candidate)

    # Fall back to whatever is on PATH.
    for name in ("msedge", "chrome", "chromium", "brave"):
        which = shutil.which(name)
        if which:
            paths.append(Path(which))

    # De-dupe while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def html_to_pdf(html_path: Path, pdf_path: Path, *, timeout_seconds: int = 60) -> Path:
    """Render html_path -> pdf_path via headless Edge/Chrome. Raises PdfError."""
    html_path = html_path.resolve()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    file_url = html_path.as_uri()

    browsers = _candidate_browsers()
    if not browsers:
        raise PdfError(
            "No supported browser found. Install Microsoft Edge or Google Chrome."
        )

    last_error: str | None = None
    for browser in browsers:
        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            file_url,
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            last_error = f"{browser}: {exc}"
            continue
        except subprocess.TimeoutExpired as exc:
            last_error = f"{browser}: timed out after {timeout_seconds}s"
            continue

        # Headless Chrome usually returns 0 even when it fails — check the file.
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return pdf_path

        last_error = (
            f"{browser}: exit={completed.returncode} "
            f"stderr={completed.stderr.strip()[:300]}"
        )
        # Try the next browser.

    raise PdfError(f"All browsers failed. Last error: {last_error}")
