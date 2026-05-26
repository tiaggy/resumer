"use strict";

const MIN_LEN = 200;

const el = {
  textarea: document.getElementById("job-description"),
  charCount: document.getElementById("char-count"),
  generateBtn: document.getElementById("generate-btn"),
  status: document.getElementById("status"),
  resultCard: document.getElementById("result-card"),
  warnings: document.getElementById("warnings"),
  quickCopy: document.getElementById("quick-copy"),
  coverCard: document.getElementById("cover-card"),
  coverLetter: document.getElementById("cover-letter"),
  toast: document.getElementById("toast"),
};

let state = {
  result: null,
};

function setStatus(text, kind) {
  el.status.textContent = text;
  el.status.classList.remove("is-error", "is-done");
  if (kind === "error") el.status.classList.add("is-error");
  if (kind === "done") el.status.classList.add("is-done");
}

function showToast(text) {
  el.toast.textContent = text;
  el.toast.hidden = false;
  setTimeout(() => {
    el.toast.hidden = true;
  }, 1400);
}

async function copyText(value) {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    showToast("Copied");
  } catch {
    showToast("Copy failed");
  }
}

function updateGenerateState() {
  const len = el.textarea.value.length;
  el.charCount.textContent = `${len} chars`;
  el.generateBtn.disabled = len < MIN_LEN;
}

el.textarea.addEventListener("input", updateGenerateState);

async function fetchProfile() {
  try {
    const res = await fetch("/api/profile");
    if (!res.ok) return;
    renderQuickCopy(await res.json());
  } catch {
    // No-op; quick copy will populate after a successful generation too.
  }
}

function renderQuickCopy(profile) {
  el.quickCopy.innerHTML = "";
  let firstName = profile.first_name || "";
  let lastName = profile.last_name || "";
  if (!firstName && !lastName && profile.full_name) {
    const idx = profile.full_name.lastIndexOf(" ");
    if (idx > 0) {
      firstName = profile.full_name.slice(0, idx);
      lastName = profile.full_name.slice(idx + 1);
    } else {
      firstName = profile.full_name;
    }
  }
  const fields = [
    ["First Name", firstName],
    ["Last Name", lastName],
    ["Email", profile.email],
    ["Phone", profile.phone],
    ["Location", profile.location],
    ["LinkedIn", profile.linkedin],
    ["Portfolio", profile.portfolio],
    ["Telegram", profile.telegram],
  ];
  for (const [label, value] of fields) {
    if (!value) continue;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.innerHTML = `<span class="label">${label}</span><span class="value">${escapeHtml(value)}</span>`;
    btn.addEventListener("click", () => copyText(value));
    el.quickCopy.appendChild(btn);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function generate() {
  el.generateBtn.disabled = true;
  setStatus("Generating tailored resume...");
  el.warnings.innerHTML = "";
  el.resultCard.hidden = true;
  el.coverCard.hidden = true;

  try {
    const masterPromptEl = document.getElementById("master-prompt");
    const languageEl = document.getElementById("language");
    const masterPrompt = masterPromptEl?.value || "";
    const language = languageEl?.value?.trim() || "Always English";
    try {
      localStorage.setItem("resumer.master_prompt", masterPrompt);
      localStorage.setItem("resumer.language", language);
    } catch {}

    const overrides = collectOverrides();
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_description: el.textarea.value,
        master_prompt: masterPrompt,
        language: language,
        overrides,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      setStatus(`Error: ${data.message || data.error_code || "unknown"}`, "error");
      return;
    }
    state.result = data;
    renderResult(data);
    setStatus(
      data.status === "done_with_warnings"
        ? "Generated with warnings"
        : "Done",
      "done",
    );
  } catch (err) {
    setStatus(`Error: ${err.message}`, "error");
  } finally {
    updateGenerateState();
  }
}

function renderResult(data) {
  el.resultCard.hidden = false;
  el.warnings.innerHTML = "";
  for (const w of data.warnings || []) {
    const li = document.createElement("li");
    li.textContent = w;
    el.warnings.appendChild(li);
  }
  setActionEnabled("generate-resume-pdf", Boolean(data.html_path));
  setActionEnabled("open-html", Boolean(data.html_path));

  if (data.quick_copy) renderQuickCopy(data.quick_copy);

  if (data.cover_letter) {
    el.coverCard.hidden = false;
    el.coverLetter.textContent = data.cover_letter;
  }
}

function setActionEnabled(action, enabled) {
  const btn = document.querySelector(`button[data-action="${action}"]`);
  if (btn) btn.disabled = !enabled;
}

document.querySelectorAll("button[data-action]").forEach((btn) => {
  btn.addEventListener("click", () => onAction(btn.dataset.action));
});

async function onAction(action) {
  const data = state.result;
  switch (action) {
    case "generate-resume-pdf":
      if (data?.html_path) await generatePdf("resume", { html_path: data.html_path });
      break;
    case "generate-cover-pdf":
      if (data?.cover_letter) await generatePdf("cover", {
        text: data.cover_letter,
        basename: data.basename || "cover",
      });
      break;
    case "open-html":
      if (data?.html_path) await openPath(data.html_path);
      break;
    case "open-folder":
      await openFolder(data?.html_path || "outputs");
      break;
    case "copy-cover":
      if (data?.cover_letter) await copyText(data.cover_letter);
      break;
  }
}

async function generatePdf(kind, extras) {
  const btn = document.querySelector(
    `button[data-action="generate-${kind === "resume" ? "resume" : "cover"}-pdf"]`,
  );
  if (btn) btn.disabled = true;
  showToast("Generating PDF...");
  try {
    const res = await fetch("/api/make-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, ...extras }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast(data.error || "PDF failed");
      return;
    }
    await openPath(data.pdf_path);
  } catch (err) {
    showToast(err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function openPath(path) {
  try {
    const res = await fetch("/api/open-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!data.ok) showToast(data.error || "Open failed");
  } catch (err) {
    showToast(err.message);
  }
}

async function openFolder(path) {
  try {
    const res = await fetch("/api/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!data.ok) showToast(data.error || "Open failed");
  } catch (err) {
    showToast(err.message);
  }
}

function collectOverrides() {
  const get = (id) => document.getElementById(id)?.value ?? "";
  const trim = (s) => s.trim();
  const lines = (s) => s.split(/\r?\n/).map(trim).filter(Boolean);

  const out = {};
  const scalarMap = {
    company_name: "ovr-company_name",
    role_title: "ovr-role_title",
    file_name_slug: "ovr-file_name_slug",
    headline: "ovr-headline",
    summary: "ovr-summary",
  };
  for (const [key, id] of Object.entries(scalarMap)) {
    const v = trim(get(id));
    if (v) out[key] = v;
  }

  const skills = lines(get("ovr-skills"));
  if (skills.length) out.skills = skills;

  const langs = lines(get("ovr-languages"))
    .map((line) => {
      const m = line.match(/^(.*?)\s*[-–]\s*(.+)$/);
      return m ? { name: trim(m[1]), level: trim(m[2]) } : { name: line, level: "" };
    });
  if (langs.length) out.languages = langs;

  const edus = lines(get("ovr-education"))
    .map((line) => {
      const parts = line.split("|").map(trim);
      return {
        dates: parts[0] || "",
        degree: parts[1] || "",
        school: parts[2] || "",
      };
    });
  if (edus.length) out.education = edus;

  const expText = trim(get("ovr-experience"));
  if (expText) {
    try {
      const parsed = JSON.parse(expText);
      if (Array.isArray(parsed)) out.experience = parsed;
    } catch {
      showToast("Experience override is not valid JSON — ignored");
    }
  }

  const coverSubject = trim(get("ovr-cover_subject"));
  const coverBody = trim(get("ovr-cover_body"));
  if (coverSubject || coverBody) {
    out.cover_letter = {};
    if (coverSubject) out.cover_letter.subject = coverSubject;
    if (coverBody) out.cover_letter.body = coverBody;
  }

  return out;
}

el.generateBtn.addEventListener("click", generate);

(function restoreInputs() {
  try {
    const mp = localStorage.getItem("resumer.master_prompt");
    if (mp !== null) {
      const el2 = document.getElementById("master-prompt");
      if (el2) el2.value = mp;
    }
    const lang = localStorage.getItem("resumer.language");
    if (lang !== null) {
      const el3 = document.getElementById("language");
      if (el3) el3.value = lang;
    }
  } catch {}
})();

fetchProfile();
updateGenerateState();
