"""Validate the JSON returned by the LLM against the expected shape + limits.

Returns:
  - parsed dict (possibly with bullets trimmed)
  - list of human-readable warnings (limit violations, missing optional fields)
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class ExperienceItem(BaseModel):
    company: str
    title: str
    location: str = ""
    dates: str = ""
    description: str = ""
    bullets: list[str] = Field(default_factory=list)
    core_skills: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str
    description: str = ""
    skills: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    dates: str = ""


class LanguageItem(BaseModel):
    name: str = ""
    level: str = ""


class ResumeBody(BaseModel):
    headline: str
    summary: str
    experience: list[ExperienceItem]
    skills: list[str]
    projects: list[ProjectItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)


class CoverLetter(BaseModel):
    subject: str = ""
    body: str


class QuickCopy(BaseModel):
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    portfolio: str = ""
    telegram: str = ""


class QualityCheck(BaseModel):
    job_focus: str = ""
    most_relevant_matches: list[str] = Field(default_factory=list)
    unsupported_job_requirements_not_claimed: list[str] = Field(default_factory=list)
    possible_risks: list[str] = Field(default_factory=list)


class LLMOutput(BaseModel):
    file_name_slug: str
    company_name: str
    role_title: str
    resume: ResumeBody
    cover_letter: CoverLetter
    application_quick_copy: QuickCopy = Field(default_factory=QuickCopy)
    quality_check: QualityCheck = Field(default_factory=QualityCheck)

    @field_validator("company_name", "role_title", "file_name_slug")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


def _coerce_shape(raw: dict[str, Any]) -> dict[str, Any]:
    """Map common LLM-invented shapes onto our schema.

    Real-world deviations observed:
      - Top-level `meta` carrying {job_title, company_name, ...}
      - Top-level `match_assessment` instead of `quality_check`
      - `cover_letter` returned as a bare string instead of {subject, body}
      - Missing `file_name_slug` (derive from company + role)
    Be conservative: only fill in fields that aren't already present.
    """
    data = dict(raw)

    # Lift well-known nested containers up to the root.
    for container_key in ("meta", "metadata", "header"):
        container = data.get(container_key)
        if isinstance(container, dict):
            for k, v in container.items():
                if k not in data and v not in (None, ""):
                    data[k] = v

    # Common synonyms for top-level scalars.
    synonyms = {
        "company_name": ("company", "employer", "organization", "company_title"),
        "role_title": ("title", "position", "job_title", "role"),
        "file_name_slug": ("slug", "filename_slug", "filename"),
    }
    for canonical, alts in synonyms.items():
        if canonical not in data or not data.get(canonical):
            for alt in alts:
                if alt in data and data[alt]:
                    data[canonical] = data[alt]
                    break

    # quality_check synonyms.
    if "quality_check" not in data:
        for alt in ("match_assessment", "assessment", "analysis"):
            if alt in data and isinstance(data[alt], dict):
                data["quality_check"] = data[alt]
                break

    # quality_check returned as a bare string (some models do this when they
    # want to refuse or comment instead of filling the schema). Wrap the string
    # into possible_risks so it still surfaces in the UI warnings.
    qc = data.get("quality_check")
    if isinstance(qc, str):
        data["quality_check"] = {"possible_risks": [qc.strip()]} if qc.strip() else {}
    elif isinstance(qc, list):
        data["quality_check"] = {"possible_risks": [str(x) for x in qc if x]}

    # resume synonyms / unwrapping.
    if "resume" not in data or not isinstance(data.get("resume"), dict):
        for alt in (
            "tailored_resume",
            "adapted_resume",
            "cv",
            "resume_content",
            "candidate_resume",
            "resume_body",
        ):
            if alt in data and isinstance(data[alt], dict):
                data["resume"] = data[alt]
                break

    # If the model put resume fields directly at the root, wrap them.
    if "resume" not in data or not isinstance(data.get("resume"), dict):
        resume_fields = (
            "headline",
            "summary",
            "experience",
            "skills",
            "projects",
            "education",
            "languages",
        )
        if any(k in data for k in resume_fields):
            data["resume"] = {k: data[k] for k in resume_fields if k in data}

    # Per-experience-item normalization: some models emit `responsibilities`
    # or `achievements` instead of `bullets`, or `role`/`position` for title.
    resume_block = data.get("resume")
    if isinstance(resume_block, dict):
        experience = resume_block.get("experience")
        if isinstance(experience, list):
            for item in experience:
                if not isinstance(item, dict):
                    continue
                if "bullets" not in item:
                    for alt in ("responsibilities", "achievements", "highlights", "points"):
                        if alt in item and isinstance(item[alt], list):
                            item["bullets"] = item[alt]
                            break
                if "title" not in item or not item.get("title"):
                    for alt in ("role", "position", "job_title"):
                        if alt in item and item[alt]:
                            item["title"] = item[alt]
                            break
                if "company" not in item or not item.get("company"):
                    for alt in ("employer", "organization"):
                        if alt in item and item[alt]:
                            item["company"] = item[alt]
                            break

    # cover_letter: bare string -> {subject, body}.
    cover = data.get("cover_letter")
    if isinstance(cover, str):
        data["cover_letter"] = {"subject": "", "body": cover}
    elif isinstance(cover, dict):
        # Some models use `text` instead of `body`.
        if "body" not in cover and "text" in cover:
            cover["body"] = cover.pop("text")

    # Fallback for missing top-level company_name / role_title (the prompt
    # requires them, but models sometimes omit). Use placeholders the prompt
    # itself instructs models to emit; warn the caller in validate_and_normalize.
    if not data.get("company_name"):
        data["company_name"] = "Unknown Company"
    if not data.get("role_title"):
        data["role_title"] = "Unknown Role"

    # Fill file_name_slug from company + role if still missing.
    if not data.get("file_name_slug"):
        company = str(data.get("company_name", "")).strip()
        role = str(data.get("role_title", "")).strip()
        if company or role:
            from file_utils import slugify

            data["file_name_slug"] = slugify(f"{company}-{role}")

    return data


def validate_and_normalize(
    raw: dict[str, Any], limits: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Validate `raw` against LLMOutput schema, then check length/count limits.

    Mutates a copy: extra bullets above the per-job limit are trimmed (with warning),
    extra skills are trimmed (with warning).
    """
    warnings: list[str] = []

    raw = _coerce_shape(raw)

    try:
        parsed = LLMOutput.model_validate(raw)
    except ValidationError as exc:
        # Bubble up as a clear error — the caller treats this as INVALID_JSON.
        raise ValueError(f"LLM output failed schema validation: {exc}") from exc

    data = parsed.model_dump()

    if data.get("company_name") == "Unknown Company":
        warnings.append("LLM did not emit top-level company_name; using 'Unknown Company' placeholder")
    if data.get("role_title") == "Unknown Role":
        warnings.append("LLM did not emit top-level role_title; using 'Unknown Role' placeholder")

    headline_max = int(limits.get("headline_max_chars", 85))
    summary_max = int(limits.get("summary_max_chars", 460))
    desc_max = int(limits.get("experience_description_max_chars", 260))
    bullet_max = int(limits.get("experience_bullet_max_chars", 165))
    bullets_per_job = int(limits.get("experience_bullets_per_job", 3))
    core_skills_per_job = int(limits.get("experience_core_skills_per_job", 6))
    core_skill_max = int(limits.get("experience_core_skill_max_chars", 32))
    title_max = int(limits.get("experience_title_max_chars", 80))
    company_max = int(limits.get("experience_company_max_chars", 60))
    exp_location_max = int(limits.get("experience_location_max_chars", 60))
    dates_max = int(limits.get("experience_dates_max_chars", 40))
    skills_max = int(limits.get("skills_max_count", 18))
    skill_max = int(limits.get("skill_max_chars", 32))
    education_max = int(limits.get("education_max_count", 4))
    edu_school_max = int(limits.get("education_school_max_chars", 90))
    edu_degree_max = int(limits.get("education_degree_max_chars", 90))
    edu_dates_max = int(limits.get("education_dates_max_chars", 40))
    languages_max = int(limits.get("languages_max_count", 6))
    lang_name_max = int(limits.get("language_name_max_chars", 30))
    lang_level_max = int(limits.get("language_level_max_chars", 30))
    cover_max = int(limits.get("cover_letter_max_chars", 1400))
    cover_subject_max = int(limits.get("cover_letter_subject_max_chars", 120))

    headline = data["resume"]["headline"]
    if len(headline) > headline_max:
        warnings.append(f"headline is {len(headline)} chars, limit is {headline_max}")

    summary = data["resume"]["summary"]
    if len(summary) > summary_max:
        warnings.append(f"summary is {len(summary)} chars, limit is {summary_max}")

    for idx, job in enumerate(data["resume"]["experience"]):
        if len(job["title"]) > title_max:
            warnings.append(
                f"experience[{idx}].title is {len(job['title'])} chars, limit is {title_max}"
            )
        if len(job["company"]) > company_max:
            warnings.append(
                f"experience[{idx}].company is {len(job['company'])} chars, limit is {company_max}"
            )
        if len(job["location"]) > exp_location_max:
            warnings.append(
                f"experience[{idx}].location is {len(job['location'])} chars, limit is {exp_location_max}"
            )
        if len(job["dates"]) > dates_max:
            warnings.append(
                f"experience[{idx}].dates is {len(job['dates'])} chars, limit is {dates_max}"
            )
        if len(job["description"]) > desc_max:
            warnings.append(
                f"experience[{idx}].description is {len(job['description'])} "
                f"chars, limit is {desc_max}"
            )
        bullets = job["bullets"]
        if len(bullets) < bullets_per_job:
            warnings.append(
                f"experience[{idx}] has {len(bullets)} bullets, expected {bullets_per_job}"
            )
        if len(bullets) > bullets_per_job:
            warnings.append(
                f"experience[{idx}] has {len(bullets)} bullets, trimmed to {bullets_per_job}"
            )
            job["bullets"] = bullets[:bullets_per_job]
        for b_idx, bullet in enumerate(job["bullets"]):
            if len(bullet) > bullet_max:
                warnings.append(
                    f"experience[{idx}].bullets[{b_idx}] is {len(bullet)} "
                    f"chars, limit is {bullet_max}"
                )
        core = job.get("core_skills", [])
        if len(core) > core_skills_per_job:
            warnings.append(
                f"experience[{idx}] has {len(core)} core_skills, trimmed to {core_skills_per_job}"
            )
            job["core_skills"] = core[:core_skills_per_job]
        for c_idx, cs in enumerate(job["core_skills"]):
            if len(cs) > core_skill_max:
                warnings.append(
                    f"experience[{idx}].core_skills[{c_idx}] is {len(cs)} "
                    f"chars, limit is {core_skill_max}"
                )

    skills = data["resume"]["skills"]
    if len(skills) > skills_max:
        warnings.append(
            f"skills count is {len(skills)}, trimmed to {skills_max}"
        )
        data["resume"]["skills"] = skills[:skills_max]
    for s_idx, skill in enumerate(data["resume"]["skills"]):
        if len(skill) > skill_max:
            warnings.append(
                f"skills[{s_idx}] is {len(skill)} chars, limit is {skill_max}"
            )

    education = data["resume"].get("education", [])
    if len(education) > education_max:
        warnings.append(
            f"education count is {len(education)}, trimmed to {education_max}"
        )
        data["resume"]["education"] = education[:education_max]
    for e_idx, edu in enumerate(data["resume"]["education"]):
        if len(edu["school"]) > edu_school_max:
            warnings.append(
                f"education[{e_idx}].school is {len(edu['school'])} chars, limit is {edu_school_max}"
            )
        if len(edu["degree"]) > edu_degree_max:
            warnings.append(
                f"education[{e_idx}].degree is {len(edu['degree'])} chars, limit is {edu_degree_max}"
            )
        if len(edu["dates"]) > edu_dates_max:
            warnings.append(
                f"education[{e_idx}].dates is {len(edu['dates'])} chars, limit is {edu_dates_max}"
            )

    languages = data["resume"].get("languages", [])
    if len(languages) > languages_max:
        warnings.append(
            f"languages count is {len(languages)}, trimmed to {languages_max}"
        )
        data["resume"]["languages"] = languages[:languages_max]
    for l_idx, lang in enumerate(data["resume"]["languages"]):
        if len(lang["name"]) > lang_name_max:
            warnings.append(
                f"languages[{l_idx}].name is {len(lang['name'])} chars, limit is {lang_name_max}"
            )
        if len(lang["level"]) > lang_level_max:
            warnings.append(
                f"languages[{l_idx}].level is {len(lang['level'])} chars, limit is {lang_level_max}"
            )

    cover_subject = data["cover_letter"].get("subject", "")
    if len(cover_subject) > cover_subject_max:
        warnings.append(
            f"cover_letter.subject is {len(cover_subject)} chars, limit is {cover_subject_max}"
        )
    cover_body = data["cover_letter"]["body"]
    if len(cover_body) > cover_max:
        warnings.append(
            f"cover_letter.body is {len(cover_body)} chars, limit is {cover_max}"
        )

    return data, warnings


def inject_profile_contacts(
    data: dict[str, Any], profile: dict[str, Any]
) -> dict[str, Any]:
    """profile.json is the source of truth for contact info — overwrite whatever
    the LLM put in application_quick_copy with the real values."""
    full_name = profile.get("full_name", "")
    first_name = profile.get("first_name") or ""
    last_name = profile.get("last_name") or ""
    if not first_name and not last_name and full_name:
        parts = full_name.rsplit(" ", 1)
        if len(parts) == 2:
            first_name, last_name = parts
        else:
            first_name = parts[0]
    data["application_quick_copy"] = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "location": profile.get("location", ""),
        "linkedin": profile.get("linkedin", ""),
        "portfolio": profile.get("portfolio", ""),
        "telegram": profile.get("telegram", ""),
    }
    return data
