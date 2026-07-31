"""GRADE imprecision — single-trial assessment.

Source: Murad, Neumann, Brozek, Langendam, Dahm, Schünemann, GRADE handbook
chapter on imprecision (https://book.gradepro.org/guideline/imprecision).
GRADE is conventionally body-of-evidence, but per-trial imprecision can be
assessed via CI width vs decision thresholds + sample / event adequacy +
fragility — which is what this module does.

Four subdomains assessed (parallel shape to ``backend/indirectness.py``):
  CI    Confidence-interval width vs decision thresholds
  N     Sample-size adequacy (single-trial OIS heuristic)
  E     Event count (binary outcomes only; N/A for continuous)
  F     Fragility / robustness

Per-subdomain judgements (4-level, blue→red gradient):
  precise                   — sufficiently precise for decision-making
  probably_precise          — probably sufficiently precise
  probably_not_precise      — probably not sufficiently precise
  not_precise               — not sufficiently precise

Overall severity → GRADE downgrade:
  none                — 0 levels
  serious             — 1 level   (any single not_precise, OR ≥2 probably_not_precise)
  very_serious        — 2 levels  (2 not_precise)
  extremely_serious   — 3 levels  (≥3 not_precise)

Optional run-level input — ``thresholds = {"mid_benefit": "...", "mid_harm": "..."}``
follows the GRADE 2-threshold framing (one MID for benefit, one for harm).
When absent, the LLM falls back to the line-of-no-effect + clinical-importance
reasoning.

Out of scope (v1, single-trial): six-threshold EtD framing,
machine-readable threshold-crossing arithmetic, formal Optimal Information
Size / Walsh fragility-index computation, very-low-baseline-risk auto-override
(guardrail in rationale only), random-effects double-counting (meta-analysis
concern only).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


JUDGEMENT_OPTIONS = ("precise", "probably_precise", "probably_not_precise", "not_precise")
SEVERITY_LEVELS = ("none", "serious", "very_serious", "extremely_serious")


# ─────────────────────────────────────────────
# Subdomain definitions
# ─────────────────────────────────────────────
SUBDOMAINS: list[dict[str, Any]] = [
    {
        "id": "ci_width",
        "label": "Confidence-interval width",
        "guidance": (
            "Per the GRADE handbook, imprecision is judged primarily by "
            "whether the 95% confidence interval around the absolute effect "
            "estimate crosses clinical-decision thresholds. Default thresholds "
            "are the line of no effect plus the minimal important difference "
            "(MID) for benefit and harm if supplied. A CI that does not cross "
            "any threshold → 'precise'. A CI crossing one threshold → "
            "'probably_not_precise' (1-level concern). A CI crossing two or "
            "more thresholds → 'not_precise'. If no effect estimate / CI is "
            "reported, return 'probably_not_precise' and explain in rationale."
        ),
    },
    {
        "id": "sample_size",
        "label": "Sample-size adequacy",
        "guidance": (
            "Is the enrolled N large enough that the result is unlikely to "
            "flip with a few more participants? This is a single-trial "
            "surrogate for the GRADE Optimal Information Size (we do not "
            "compute formal RIS). Rule-of-thumb thresholds for a clinically "
            "important effect: <100 total participants → 'not_precise'; "
            "100–300 → 'probably_not_precise'; 300–1000 → 'probably_precise'; "
            ">1000 → 'precise'. Adjust for outcome type and observed effect "
            "size; underpowered trials with extreme effects warrant concern."
        ),
    },
    {
        "id": "event_count",
        "label": "Event count (binary outcomes)",
        "guidance": (
            "For binary primary outcomes: are there enough events to support "
            "the observed effect? Rule-of-thumb: <100 total events across "
            "arms → 'not_precise'; 100–300 → 'probably_not_precise'; "
            "300–1000 → 'probably_precise'; >1000 → 'precise'. Pay extra "
            "attention to the smaller arm — significance driven by ≤10 "
            "events in one arm is fragile. **Mark this subdomain N/A for "
            "continuous outcomes** (return ``\"n_a\"`` or ``\"not_applicable\"``); "
            "the normalizer treats N/A as 'precise' so it never contributes "
            "to severity counting."
        ),
    },
    {
        "id": "fragility",
        "label": "Fragility / robustness",
        "guidance": (
            "Could a small number of additional events change the conclusion? "
            "Per the GRADE handbook: small studies that produce large "
            "relative effects on dichotomous outcomes can appear precise via "
            "narrow CIs but be fragile because CIs for odds ratios / relative "
            "risks tend to narrow as effects grow. Flag: extreme effect "
            "sizes from few events, p-values barely under 0.05 with small N, "
            "single-event-driven significance, or large relative effects "
            "(RRR > 50%) with sparse data. Continuous outcomes: judge "
            "robustness from observed variance + sample size."
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

    "reds" = ``not_precise``; "oranges" = ``probably_not_precise``.
    GRADE guidance: don't rate down unless concerns are likely to lead to
    meaningful, systematic uncertainty in the effect estimate — a single
    borderline ('probably not precise') subdomain is treated as inherent
    uncertainty, not a reason to downgrade.

    N/A subdomains (e.g. event_count for continuous outcomes) are normalized
    to 'precise' upstream so they never contribute to reds/oranges.
    """
    reds = sum(1 for v in judgements.values() if v == "not_precise")
    oranges = sum(1 for v in judgements.values() if v == "probably_not_precise")
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
        return ("No serious imprecision: confidence intervals, sample size, "
                "and event counts are sufficient for the target question.")
    drivers = [SUBDOMAIN_IDS_TO_LABEL.get(sid, sid)
               for sid, j in per_subdomain.items()
               if j in ("not_precise", "probably_not_precise")]
    drivers_text = ", ".join(drivers) if drivers else "imprecision concerns"
    if severity == "serious":
        return (f"Serious imprecision: concerns in {drivers_text} "
                f"({counts['reds']} not-precise, {counts['oranges']} probably-not-precise).")
    if severity == "very_serious":
        return (f"Very serious imprecision: 2 subdomains not sufficiently "
                f"precise ({drivers_text}).")
    return (f"Extremely serious imprecision: {counts['reds']} subdomains "
            f"not sufficiently precise ({drivers_text}).")


SUBDOMAIN_IDS_TO_LABEL = {s["id"]: s["label"] for s in SUBDOMAINS}


# ─────────────────────────────────────────────
# Outcome-type heuristic
# ─────────────────────────────────────────────
_BINARY_HINTS = (
    "binary", "dichotom", "event", "incidence", "mortalit", "death",
    "rate", "proportion", "frequency", "occurrence",
)
_CONTINUOUS_HINTS = (
    "continuous", "mean", "score", "scale", "concentration",
    "level", "change from baseline",
)


def infer_outcome_is_binary(extracted_fields: dict[str, str],
                              primary_outcome: str,
                              outcome_is_primary: bool = True,
                              outcome_type: str = "") -> bool | None:
    """Best-effort guess at whether the assessed outcome is binary.

    Returns ``True`` for binary, ``False`` for continuous, ``None`` if
    indeterminate. The LLM is told the inferred answer and asked to confirm
    or override (and is instructed to mark event_count N/A for continuous).

    ``outcome_type`` is a per-outcome answer supplied by the caller (the outcome
    extractor produces one) and wins outright when present.

    ``outcome_is_primary`` gates every paper-level field, because they all
    describe the paper's *primary* outcome. When rating a secondary outcome,
    ``primary_outcome_type`` / ``_measurement`` / ``_definition`` describe a
    different outcome entirely — a trial with binary mortality and a continuous
    6-minute-walk secondary would otherwise type the secondary as binary and
    fire the event-count subdomain that should be N/A. For a secondary we judge
    from the assessed-outcome string alone, and return ``None`` (indeterminate,
    so the LLM decides) rather than guess from the wrong outcome's description.
    """
    explicit = (outcome_type or "").strip().lower()
    if not explicit and outcome_is_primary:
        explicit = (extracted_fields.get("primary_outcome_type") or "").lower()
    if any(h in explicit for h in ("binary", "dichotom")):
        return True
    if "continuous" in explicit:
        return False

    if outcome_is_primary:
        measurement = (extracted_fields.get("primary_outcome_measurement") or "")
        definition = (extracted_fields.get("primary_outcome_definition") or "")
    else:
        measurement = definition = ""
    haystack = " ".join([primary_outcome or "", measurement, definition]).lower()
    if any(h in haystack for h in _BINARY_HINTS):
        return True
    if any(h in haystack for h in _CONTINUOUS_HINTS):
        return False
    return None


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing the GRADE "
    "imprecision domain for a single trial. Read the PDF carefully. For each "
    "of the four subdomains (CI width, sample size, event count, fragility), "
    "judge how precise the primary-outcome evidence is on a 4-level scale: "
    "'precise' (sufficiently precise), 'probably_precise' (probably "
    "sufficiently precise), 'probably_not_precise' (probably not "
    "sufficiently precise), or 'not_precise' (not sufficiently precise). "
    "Provide a 1-2 sentence rationale per subdomain, quoting the paper "
    "where possible. Per the GRADE handbook, the primary tool is whether "
    "the 95% CI for the absolute effect crosses decision thresholds — the "
    "line of no effect, plus minimal important difference (MID) thresholds "
    "for benefit and harm if supplied. Mark event_count as 'n_a' for "
    "continuous outcomes (it will be excluded from severity counting). Be "
    "alert to single-trial fragility: large relative effects on few events "
    "may appear precise but be unreliable. If baseline risk is very low "
    "(<5%) and the absolute-risk CI is narrow despite a wide relative-risk "
    "CI, briefly note this in rationale rather than rating down. Return "
    "ONLY a valid JSON object — no preamble, no markdown fences."
)


def _format_thresholds(thresholds: dict[str, str] | None) -> str:
    """Render the threshold block for the prompt, or a fallback when blank."""
    if not thresholds or not any(
            (thresholds.get(k) or "").strip() for k in ("mid_benefit", "mid_harm")):
        return (
            "(No MID thresholds supplied — assess CI width against the line "
            "of no effect plus your judgement of clinically important "
            "effect sizes for this outcome. Default to 'probably_precise' "
            "rather than 'precise' when CI width is uncertain.)"
        )
    lines = []
    for key, label in (("mid_benefit", "MID for benefit"),
                       ("mid_harm", "MID for harm")):
        val = (thresholds.get(key) or "").strip()
        lines.append(f"  {label}: {val if val else '(unspecified — use line-of-no-effect only)'}")
    return "Decision thresholds (a priori):\n" + "\n".join(lines)


def _format_outcome_type(outcome_is_binary: bool | None) -> str:
    if outcome_is_binary is True:
        return ("Outcome type (inferred): BINARY. Judge event_count using "
                "the rule-of-thumb thresholds in the guidance.")
    if outcome_is_binary is False:
        return ("Outcome type (inferred): CONTINUOUS. Mark event_count as "
                "'n_a' (it will be excluded from severity counting). Judge "
                "fragility from sample size and observed variance instead.")
    return ("Outcome type (inferred): UNCERTAIN. Determine binary vs "
            "continuous from the paper; if continuous, mark event_count as "
            "'n_a'.")


def build_prompt(thresholds: dict[str, str] | None,
                 study_type: str,
                 primary_outcome: str,
                 extracted_fields: dict[str, str],
                 outcome_is_binary: bool | None = None) -> str:
    """Build the per-paper imprecision prompt."""
    relevant_keys = [
        "primary_outcome_definition", "primary_outcome_measurement",
        "primary_outcome_type",
        "effect_size", "effect_estimate", "confidence_interval",
        "p_value", "statistical_test",
        "sample_size", "sample_size_intervention", "sample_size_comparator",
        "events_intervention", "events_comparator",
        "follow_up_duration", "baseline_risk",
        "population_outcomes",
    ]
    relevant = {k: extracted_fields[k]
                for k in relevant_keys if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    threshold_block = _format_thresholds(thresholds)
    outcome_block = _format_outcome_type(outcome_is_binary)

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
        if sid == "event_count":
            shape += f'  "{sid}": "precise|probably_precise|probably_not_precise|not_precise|n_a",\n'
        else:
            shape += f'  "{sid}": "precise|probably_precise|probably_not_precise|not_precise",\n'
        shape += f'  "{sid}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "outcome_is_binary": true|false,\n'
    shape += '  "sample_size_total": <integer or null>,\n'
    shape += '  "events_total": <integer or null>,\n'
    shape += '  "ci_summary": "Brief description of the reported 95% CI for the primary outcome (e.g., \\"RR 0.78, 95% CI 0.62 to 0.98\\"), or null if not reported."\n'
    shape += "}"

    return f"""Assess **GRADE imprecision** for the trial described in the attached PDF.

Study type: {study_type}
As-conducted primary outcome: {primary_outcome}

{outcome_block}

{threshold_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether imprecision is likely to leave the truth uncertain for clinical decision-making. Default to 'probably_precise' rather than 'precise' when there is meaningful uncertainty. Reserve 'not_precise' for clear, substantial imprecision concerns. Rationales must quote the paper verbatim where possible (effect estimates, CIs, sample sizes, event counts)."""


def _normalize_judgement(raw: str) -> str:
    """Coerce LLM output to one of the four allowed values; default to probably_precise.

    N/A aliases (``n_a`` / ``not_applicable`` / ``na``) map to ``precise`` so
    they don't contribute to severity counting — used for ``event_count`` on
    continuous outcomes.
    """
    val = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if val in JUDGEMENT_OPTIONS:
        return val
    aliases = {
        "sufficiently_precise": "precise",
        "probably_sufficiently_precise": "probably_precise",
        "probably_not_sufficiently_precise": "probably_not_precise",
        "not_sufficiently_precise": "not_precise",
        "yes": "precise",
        "no": "not_precise",
        "n_a": "precise",
        "na": "precise",
        "not_applicable": "precise",
        "n/a": "precise",
    }
    if val in aliases:
        return aliases[val]
    logger.warning("Imprecision: unknown judgement %r — defaulting to probably_precise", raw)
    return "probably_precise"


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        thresholds: dict[str, str] | None = None,
        outcome_is_primary: bool = True,
        outcome_type: str = "",
        ) -> tuple[dict[str, Any], str, int, str]:
    """Run the imprecision assessment on a single paper, for one outcome.

    Returns ``(per_subdomain_results, severity_label, downgrade_levels, explanation)``.

    - ``per_subdomain_results``: ``{ci_width: {judgement, rationale, label}, ...,
      outcome_is_binary: bool | None, sample_size_total: int | None,
      events_total: int | None, ci_summary: str | None}``.
    - ``severity_label``: one of ``SEVERITY_LEVELS``.
    - ``downgrade_levels``: 0 / 1 / 2 / 3.
    - ``explanation``: 1-sentence rationale for the severity tier.
    """
    study_type = classification.get("study_type", "")
    inferred_binary = infer_outcome_is_binary(
        extracted_fields, primary_outcome,
        outcome_is_primary=outcome_is_primary, outcome_type=outcome_type)
    prompt = build_prompt(thresholds, study_type, primary_outcome,
                          extracted_fields, outcome_is_binary=inferred_binary)
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

    # Outcome-type confirmation from LLM (overrides the heuristic if present)
    raw_binary = raw.get("outcome_is_binary")
    if isinstance(raw_binary, bool):
        outcome_is_binary: bool | None = raw_binary
    else:
        outcome_is_binary = inferred_binary
    per_sub["outcome_is_binary"] = outcome_is_binary

    def _coerce_int(v: Any) -> int | None:
        try:
            if v is None or v == "":
                return None
            return int(v)
        except (TypeError, ValueError):
            return None

    per_sub["sample_size_total"] = _coerce_int(raw.get("sample_size_total"))
    per_sub["events_total"] = _coerce_int(raw.get("events_total"))
    per_sub["ci_summary"] = str(raw.get("ci_summary", "") or "").strip() or None

    severity, levels, counts = _judgement_severity(judgements)
    per_sub["counts"] = counts
    explanation = severity_explanation(severity, counts, judgements)
    return per_sub, severity, levels, explanation


# ─────────────────────────────────────────────
# Developer-view exposure
# ─────────────────────────────────────────────
def prompt_catalog() -> dict[str, Any]:
    """Return the imprecision prompts + decision-tree source for the developer view."""
    import inspect
    sample_thresholds = {
        "mid_benefit": "<MID for benefit, e.g. 5% absolute risk reduction>",
        "mid_harm": "<MID for harm, e.g. 5% absolute risk increase>",
    }
    return {
        "tool": "GRADE Imprecision — single-trial assessment",
        "system_prompt": _SYSTEM_PROMPT,
        "judgement_options": list(JUDGEMENT_OPTIONS),
        "severity_levels": list(SEVERITY_LEVELS),
        "subdomains": SUBDOMAINS,
        "prompt_template_with_thresholds": build_prompt(
            sample_thresholds, "Randomized Controlled Trial",
            "<primary outcome here>",
            {"effect_size": "<extracted value>"},
            outcome_is_binary=True,
        ),
        "prompt_template_no_thresholds": build_prompt(
            None, "Randomized Controlled Trial",
            "<primary outcome here>",
            {"effect_size": "<extracted value>"},
            outcome_is_binary=None,
        ),
        "severity_decision_tree_code": inspect.getsource(_judgement_severity),
        "severity_explanation_code": inspect.getsource(severity_explanation),
        "outcome_type_heuristic_code": inspect.getsource(infer_outcome_is_binary),
        "downgrade_table": {
            "none":              "0 levels — all subdomains precise or probably_precise (≤1 borderline allowed)",
            "serious":           "1 level  — exactly 1 not_precise, OR ≥2 probably_not_precise",
            "very_serious":      "2 levels — 2 not_precise subdomains",
            "extremely_serious": "3 levels — 3 or more not_precise subdomains",
        },
        "out_of_scope": [
            "Six-threshold EtD framing (only 2-threshold MID-benefit + MID-harm in v1)",
            "Machine-readable threshold-crossing arithmetic (LLM judges qualitatively)",
            "Formal Optimal Information Size / Review Information Size computation",
            "Walsh fragility-index computation",
            "Very-low-baseline-risk auto-override (guardrail in rationale only)",
            "Random-effects double-counting caveat (meta-analysis only — single-trial here)",
        ],
    }
