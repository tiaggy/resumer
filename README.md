# Resumer

Local Flask app that adapts a tailored, single-page A4 resume + cover letter to any job description with one click. The output mirrors a designed CV layout (two-column, photo, sidebar with Skills / Education / Languages, timeline-style Experience with per-job core-skills tags) and is rendered to PDF by headless Edge/Chrome.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env          # fill OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL
notepad data\profile.json
python app.py
```

The UI opens at `http://127.0.0.1:17321`.

## How it works

1. You paste a vacancy into the **Job Description** box.
2. Optionally fill the **Master Prompt** (hard rules, work-history lock) and **Language** field (default `Always English`).
3. Optionally fill any of the **Manual Overrides** fields — anything filled is locked and the LLM is told to skip generating it.
4. One LLM call returns JSON containing the tailored resume body, a cover letter, contact quick-copy, and a quality-check report.
5. `validation.py` validates against a pydantic schema and warns on length-limit violations.
6. `renderer.py` fills ~60 absolute-positioned text slots in `templates/resume.html`, base64-inlines the profile photo, and saves the self-contained HTML.
7. Click **Generate PDF** on the resume or cover-letter card → headless Chrome/Edge prints to PDF, output opens in your default viewer.

## UI overview

| Section | Purpose |
|---|---|
| Master Prompt | Highest-priority instructions sent to the LLM (work-history lock, tone, never-invent rules). Saved to `localStorage`. |
| Language | Free-text language directive, default `Always English`. Overrides `settings.json` `language_mode`. |
| Job Description | Vacancy text. Min 200 chars. |
| Manual Overrides | One field per LLM output. Anything filled bypasses generation and is merged into the response before validation. |
| Result card | **Generate PDF**, Open HTML, Open Folder. |
| Cover Letter card | The letter body, with **Copy** and **Generate PDF** buttons. |
| Quick Copy sidebar | First/last name, email, location, links, telegram — one-click copy. |

## Layout source

`templates/resume.html` is a Jinja-ized copy of a PDF-converted single-page CV. Every text line is an absolute-mm-positioned `<div class="t ...">` slot. The renderer fills `~60` named slots:

- `name`, `tagline_1..3`, `about_1..3`, `email`, `behance`, `linkedin`, `location`
- `skill_1..9`, `edu_dates`, `edu_degree`, `edu_faculty`, `edu_school`, `lang_1..4`
- For each of three job blocks: `jobN_date`, `jobN_role`, `jobN_company`, `jobN_b1..6`, `jobN_core`

Contact icons and the education block are flex containers so missing fields collapse gracefully. Bullet dots and job timeline dots are wrapped in `{% if slots.X %}` guards so empty experiences don't leave orphan markers.

## Provider compatibility

Uses the official `openai` SDK against `OPENAI_BASE_URL`, so any OpenAI-compatible endpoint works: api.openai.com, OpenRouter, DeepSeek, Groq, or a local server. Pick a model that supports JSON output reliably; if your provider rejects `response_format`, set `data/settings.json` → `response_format_mode` to `"none"`.

## Output files

Each run writes a date + company-role slug to `outputs/`:

```
outputs/
  2026-05-14_acme-product-designer_resume.html
  2026-05-14_acme-product-designer_resume.pdf      (created on demand)
  2026-05-14_acme-product-designer_cover_letter.txt
  2026-05-14_acme-product-designer_cover_letter.pdf (created on demand)
  2026-05-14_acme-product-designer_data.json
```

`data.json` contains the full inputs + outputs + warnings and is the place to look when something looks off. PDF generation is deferred — the page renders fast and PDFs are produced only when you click **Generate PDF**.

## Privacy

`phone` is stripped from the profile before the LLM request and re-injected from `data/profile.json` afterwards, so the model never sees your phone number even though the rendered resume includes it.

## Project layout

```
.
├── app.py                  # Flask server: /api/generate, /api/make-pdf, /api/profile, ...
├── llm.py                  # Single OpenAI-compatible call + prompt builder
├── renderer.py             # Slot filler + photo base64 inliner
├── validation.py           # Pydantic schema + length-limit warnings + override pre-merge
├── pdf.py                  # Headless Edge/Chrome → PDF
├── config.py               # Loads data/ + .env
├── file_utils.py           # Slugify + filename helpers
├── prompts/
│   ├── system.md           # The system prompt (full ruleset)
│   └── user_message.txt    # User-message template with placeholder slots
├── templates/
│   ├── index.html          # Main UI
│   ├── resume.html         # Jinja resume template
│   └── default_portrait.jpg
├── static/
│   ├── app.js
│   └── styles.css
├── data/
│   ├── profile.json        # Your name / email / links / photo path
│   ├── settings.json       # max_tokens, temperature, language_mode, ...
│   └── limits.json         # Character / count caps per resume field
└── outputs/                # Generated artifacts (git-ignored)
```

## Customizing the prompt

`prompts/system.md` holds the entire ruleset. Edit it freely — it's re-read each time the server starts. Use the **Master Prompt** field in the UI to inject per-request rules without touching the file.

## Customizing the photo

Drop a JPEG/PNG into `data/` (e.g. `data/me.jpg`) and set `"photo": "data/me.jpg"` in `data/profile.json`. With an empty `photo` the portrait area renders as a blank gray circle. `data/*.jpg|jpeg|png|webp` are git-ignored, so your real photo never reaches the repo.
