"""GRADE indirectness — single-trial PICO assessment.

Source: Schünemann et al., GRADE handbook chapter on indirectness
(https://book.gradepro.org/guideline/indirectness). Figure 1 in that chapter
explicitly supports per-trial indirectness tables — judgements per PICO
component for a single study.

Four PICO subdomains assessed:
  P  Population
  I  Intervention
  C  Comparison
  O  Outcomes

Per-subdomain judgements (4-level, matching the green/yellow/orange/red colour
scheme from the GRADE handbook):
  direct                — sufficiently direct
  probably_direct       — probably sufficiently direct
  probably_not_direct   — probably not sufficiently direct
  not_direct            — not sufficiently direct

Overall severity → GRADE downgrade:
  none                — 0 levels
  serious             — 1 level   (any single not_direct, OR ≥2 probably_not_direct)
  very_serious        — 2 levels  (2 not_direct)
  extremely_serious   — 3 levels  (≥3 not_direct)

Out of scope: indirect comparisons / network meta-analysis (body-of-evidence
only — not applicable to a single study); baseline-risk indirectness (needs
external longitudinal data); ICEMAN credibility check.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


JUDGEMENT_OPTIONS = ("direct", "probably_direct", "probably_not_direct", "not_direct")
SEVERITY_LEVELS = ("none", "serious", "very_serious", "extremely_serious")


# ─────────────────────────────────────────────
# Subdomain definitions
# ─────────────────────────────────────────────
SUBDOMAINS: list[dict[str, Any]] = [
    {
        "id": "population",
        "label": "Population",
        "guidance": (
            "Assess how closely the study population matches the population of "
            "interest in the target question (age, sex, comorbidities, severity, "
            "setting, geographic context). Highly selected, narrow, or atypical "
            "populations limit generalisability and warrant 'probably not "
            "sufficiently direct' or stronger."
        ),
    },
    {
        "id": "intervention",
        "label": "Intervention",
        "guidance": (
            "Assess how closely the studied intervention matches the intervention "
            "of interest (dose, formulation, mode of delivery, intensity, "
            "duration, provider type). Substantial differences in delivery "
            "context (\"too ideal\" trial conditions, specialised provider, "
            "non-translatable infrastructure) warrant downgrading."
        ),
    },
    {
        "id": "comparator",
        "label": "Comparator",
        "guidance": (
            "Assess how closely the studied comparator matches the comparator of "
            "interest. Active comparators that include potentially effective "
            "co-interventions, or 'usual care' that varies markedly across "
            "settings, warrant downgrading. Placebo controls when the question "
            "is about head-to-head comparison are not direct."
        ),
    },
    {
        "id": "outcome",
        "label": "Outcomes",
        "guidance": (
            "Assess whether outcome measures capture what matters to patients. "
            "Per the GRADE handbook: 'surrogate outcomes should be rated down "
            "for indirectness unless there is a strong and well-established "
            "correlation with meaningful, patient-important outcomes — a "
            "criterion that is rarely fulfilled.' Examples of surrogates: "
            "HbA1c (diabetes complications), LDL cholesterol (cardiovascular "
            "events), bone mineral density (fractures), progression-free "
            "survival (overall survival), tumour response rate (survival)."
        ),
    },
]

SUBDOMAIN_IDS = [s["id"] for s in SUBDOMAINS]


# ─────────────────────────────────────────────
# Severity decision tree (pure-Python)
# ─────────────────────────────────────────────
def _judgement_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """Aggregate per-subdomain judgements into an overall severity tier.

    Returns ``(severity_label, downgrade_levels, counts)``.

    Rule:
      reds=0 and oranges<=1                              → none (0 levels)
      reds=0 and oranges>=2                              → serious (1)
      reds=1 (regardless of oranges)                     → serious (1)
      reds=2                                             → very_serious (2)
      reds>=3                                            → extremely_serious (3)

    "reds" = ``not_direct``; "oranges" = ``probably_not_direct``.
    GRADE guidance: don't rate down unless concerns are likely to lead to
    meaningful, systematic differences — a single borderline ('probably not
    sufficiently direct') subdomain is treated as inherent indirectness, not
    a reason to downgrade.
    """
    reds = sum(1 for v in judgements.values() if v == "not_direct")
    oranges = sum(1 for v in judgements.values() if v == "probably_not_direct")
    counts = {"reds": reds, "oranges": oranges}

    if reds >= 3:
        return "extremely_serious", 3, counts
    if reds == 2:
        return "very_serious", 2, counts
    if reds == 1 or oranges >= 2:
        return "serious", 1, counts
    return "none", 0, counts


def severity_explanation(severity: str, counts: dict[str, int],
                         per_subdomain: dict[str, str]) -> str:
    """One-sentence rationale for the chosen severity tier."""
    if severity == "none":
        return ("No serious indirectness: PICO components are sufficiently "
                "direct for the target question.")
    # Pick the "drivers" — non-direct subdomains
    drivers = [SUBDOMAIN_IDS_TO_LABEL.get(sid, sid)
               for sid, j in per_subdomain.items()
               if j in ("not_direct", "probably_not_direct")]
    drivers_text = ", ".join(drivers) if drivers else "PICO mismatch"
    if severity == "serious":
        return (f"Serious indirectness: concerns in {drivers_text} "
                f"({counts['reds']} not-direct, {counts['oranges']} probably-not-direct).")
    if severity == "very_serious":
        return (f"Very serious indirectness: 2 PICO components not sufficiently "
                f"direct ({drivers_text}).")
    return (f"Extremely serious indirectness: {counts['reds']} PICO components "
            f"not sufficiently direct ({drivers_text}).")


SUBDOMAIN_IDS_TO_LABEL = {s["id"]: s["label"] for s in SUBDOMAINS}


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing the GRADE "
    "indirectness domain for a single study. Read the PDF carefully. For each "
    "of the four PICO subdomains (Population, Intervention, Comparison, "
    "Outcome), judge how directly the study's evidence applies to the "
    "specified target question on a 4-level scale: 'direct' (sufficiently "
    "direct), 'probably_direct' (probably sufficiently direct), "
    "'probably_not_direct' (probably not sufficiently direct), or 'not_direct' "
    "(not sufficiently direct). Provide a 1-2 sentence rationale per "
    "subdomain, quoting the paper where possible. Per GRADE guidance, do NOT "
    "rate down unless there are compelling reasons to believe the mismatch "
    "would lead to meaningful, systematic differences in the effect estimate. "
    "Surrogate outcomes (HbA1c, LDL, bone density, progression-free survival, "
    "etc.) should be rated 'probably_not_direct' or worse unless a strong, "
    "well-established correlation with patient-important outcomes is "
    "documented in the paper. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)


def _format_target_pico(target_pico: dict[str, str] | None) -> str:
    """Render the target PICO block for the prompt, or a fallback when blank."""
    if not target_pico or not any(
            (target_pico.get(k) or "").strip() for k in ("population", "intervention", "comparator", "outcome")):
        return (
            "(No target PICO supplied — assess against the as-conducted PICO "
            "of the study itself. Focus the OUTCOME judgement on whether the "
            "primary outcome is a surrogate vs. a patient-important outcome. "
            "For Population, Intervention, and Comparator, default to "
            "'probably_direct' unless the study's selection is unusually "
            "narrow or atypical for routine clinical use.)"
        )
    lines = []
    for key, label in (("population", "Population"),
                       ("intervention", "Intervention"),
                       ("comparator", "Comparator"),
                       ("outcome", "Outcome")):
        val = (target_pico.get(key) or "").strip()
        lines.append(f"  {label}: {val if val else '(unspecified — judge based on as-conducted PICO)'}")
    return "Target question (PICO):\n" + "\n".join(lines)


def build_prompt(target_pico: dict[str, str] | None,
                 study_type: str,
                 primary_outcome: str,
                 extracted_fields: dict[str, str]) -> str:
    """Build the per-paper indirectness prompt."""
    # Extracted-field context most relevant for indirectness
    relevant_keys = [
        "population_description", "population_age", "population_sex",
        "population_comorbidities", "population_setting", "geography",
        "inclusion_criteria", "exclusion_criteria",
        "intervention_description", "intervention_dose",
        "intervention_duration", "intervention_provider",
        "comparator_description", "comparator_type",
        "primary_outcome_definition", "primary_outcome_measurement",
        "follow_up_duration",
    ]
    relevant = {k: extracted_fields[k]
                for k in relevant_keys if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    target_block = _format_target_pico(target_pico)

    sub_lines = []
    for sub in SUBDOMAINS:
        sub_lines.append(
            f"\n**{sub['label']} ({sub['id']})**\n"
            f"Guidance: {sub['guidance']}"
        )
    subdomains_block = "\n".join(sub_lines)

    shape = "{\n"
    for sub in SUBDOMAINS:
        sid = sub["id"]
        shape += f'  "{sid}": "direct|probably_direct|probably_not_direct|not_direct",\n'
        shape += f'  "{sid}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "primary_outcome_is_surrogate": true|false,\n'
    shape += '  "surrogate_rationale": "If outcome is a surrogate, briefly explain (e.g., \\"HbA1c is a surrogate for diabetes complications\\")."\n'
    shape += "}"

    return f"""Assess **GRADE indirectness** for the study described in the attached PDF.

Study type: {study_type}
As-conducted primary outcome: {primary_outcome}

{target_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether the mismatch is likely to produce systematic differences in effect estimates. Default to 'probably_direct' rather than 'direct' when there is any meaningful uncertainty. Reserve 'not_direct' for clear, substantial mismatches. Rationales must quote the paper verbatim where possible."""


def _normalize_judgement(raw: str) -> str:
    """Coerce LLM output to one of the four allowed values; default to probably_direct."""
    val = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if val in JUDGEMENT_OPTIONS:
        return val
    aliases = {
        "sufficiently_direct": "direct",
        "probably_sufficiently_direct": "probably_direct",
        "probably_not_sufficiently_direct": "probably_not_direct",
        "not_sufficiently_direct": "not_direct",
        "yes": "direct",
        "no": "not_direct",
    }
    if val in aliases:
        return aliases[val]
    logger.warning("Indirectness: unknown judgement %r — defaulting to probably_direct", raw)
    return "probably_direct"


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        target_pico: dict[str, str] | None = None,
        ) -> tuple[dict[str, Any], str, int, str]:
    """Run the indirectness assessment on a single paper.

    Returns ``(per_subdomain_results, severity_label, downgrade_levels, explanation)``.

    - ``per_subdomain_results``: ``{population: {judgement, rationale}, ...,
      primary_outcome_is_surrogate: bool, surrogate_rationale: str}``.
    - ``severity_label``: one of ``SEVERITY_LEVELS``.
    - ``downgrade_levels``: 0 / 1 / 2 / 3.
    - ``explanation``: 1-sentence rationale for the severity tier.
    """
    study_type = classification.get("study_type", "")
    prompt = build_prompt(target_pico, study_type, primary_outcome, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=4096)

    per_sub: dict[str, Any] = {}
    judgements: dict[str, str] = {}
    for sub in SUBDOMAINS:
        sid = sub["id"]
        judgement = _normalize_judgement(str(raw.get(sid, "")))
        rationale = str(raw.get(f"{sid}_rationale", "")).strip()
        per_sub[sid] = {"judgement": judgement, "rationale": rationale,
                         "label": sub["label"]}
        judgements[sid] = judgement

    is_surrogate = bool(raw.get("primary_outcome_is_surrogate", False))
    surrogate_rationale = str(raw.get("surrogate_rationale", "")).strip()
    per_sub["primary_outcome_is_surrogate"] = is_surrogate
    per_sub["surrogate_rationale"] = surrogate_rationale

    severity, levels, counts = _judgement_severity(judgements)
    per_sub["counts"] = counts
    explanation = severity_explanation(severity, counts, judgements)
    return per_sub, severity, levels, explanation


# ─────────────────────────────────────────────
# Developer-view exposure
# ─────────────────────────────────────────────
def prompt_catalog() -> dict[str, Any]:
    """Return the indirectness prompts + decision-tree source for the developer view."""
    import inspect
    sample_target = {
        "population": "<target population, e.g. adults aged 40-75 with type 2 diabetes>",
        "intervention": "<target intervention, e.g. once-daily SGLT2 inhibitor>",
        "comparator": "<target comparator, e.g. standard care without SGLT2>",
        "outcome": "<target outcome, e.g. major adverse cardiovascular events>",
    }
    return {
        "tool": "GRADE Indirectness — single-study PICO assessment",
        "system_prompt": _SYSTEM_PROMPT,
        "judgement_options": list(JUDGEMENT_OPTIONS),
        "severity_levels": list(SEVERITY_LEVELS),
        "subdomains": SUBDOMAINS,
        "prompt_template_with_target_pico": build_prompt(
            sample_target, "Randomized Controlled Trial",
            "<primary outcome here>",
            {"population_description": "<extracted value>"},
        ),
        "prompt_template_no_target_pico": build_prompt(
            None, "Randomized Controlled Trial",
            "<primary outcome here>",
            {"population_description": "<extracted value>"},
        ),
        "severity_decision_tree_code": inspect.getsource(_judgement_severity),
        "severity_explanation_code": inspect.getsource(severity_explanation),
        "downgrade_table": {
            "none":              "0 levels — all subdomains direct or probably_direct (≤1 borderline allowed)",
            "serious":           "1 level  — exactly 1 not_direct, OR ≥2 probably_not_direct",
            "very_serious":      "2 levels — 2 not_direct subdomains",
            "extremely_serious": "3 levels — 3 or more not_direct subdomains",
        },
        "out_of_scope": [
            "Indirect comparisons / network meta-analysis (body-of-evidence only)",
            "Baseline-risk indirectness (needs external longitudinal data)",
            "ICEMAN subgroup-credibility check",
        ],
    }
