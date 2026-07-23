"""Outcome-data extraction — the arm-level numbers pooling needs (Component A).

Table 2's ``outcomes[]`` pass transcribes each study's *reported* effect (estimate +
CI + metric). Pooling from scratch wants the **raw arm-level numbers** — the 2×2
event counts (binary) or the mean/SD/N per arm (continuous) — so the pooler can
compute the effect size with a proper variance and continuity correction. This
module is that extraction pass: one model call per paper pulls, per outcome ×
comparison × timepoint, the arm data plus any reported effect and the design label.

It is the pooling analogue of ``table2_extract.py``: the ONLY place a model is
touched. It reuses the platform's PDF-aware caller (``annotator._call_with_pdf`` —
same 3-stage oversize fallback) and hands the result to the pure-Python bridge
``pooling_prep.pool_extractions`` to regroup + pool. ``pool_from_pdfs`` is the
end-to-end orchestrator: PDFs → outcome-data extraction → group into bodies → pool.

Prompt text + output schema are the framework-free contract in
``docs/shareable/pooling_meta_analysis_shareable.md``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .. import annotator as annotator_mod
from .. import helpers as helpers_mod
from .pooling_harmonize import (
    apply_canonical_map,
    clusters_to_map,
    distinct_outcome_names,
    harmonize_by_targets,
)
from .pooling_prep import pool_extractions, study_is_poolable

logger = logging.getLogger("rubricgen")

# An evidence-rich paper can report many outcomes × arms; 8192 keeps the JSON whole.
_OUTCOME_DATA_MAX_TOKENS = 8192


_OUTCOME_DATA_PROMPT = """\
You are a clinical-evidence extraction service pulling the RAW ARM-LEVEL NUMBERS
needed to meta-analyse a study. You transcribe exactly what the paper reports and
never fabricate. You never pool, average, or compute a new effect.

For EACH distinct (outcome × comparison × timepoint) reported, emit one object with:
- name: the outcome name.
- comparison: the two arms compared, "<intervention> vs <comparator>".
- timing: the timepoint/follow-up, or null.
- design: the study design label (e.g. "Randomized Controlled Trial", "Cohort Study").
- outcome_type: "binary" (events/total), "rate" (events/person-time),
  "continuous" (mean/SD), or "other".
- BINARY outcomes — fill: events_int, n_int, events_ctrl, n_ctrl (integers). These are
  the number of events and the arm total in the intervention and comparator arms.
- RATE outcomes (an incidence rate / rate ratio, i.e. events per person-time) — fill:
  events_int, time_int, events_ctrl, time_ctrl, where time_* is the ARM'S PERSON-TIME
  (e.g. person-years, patient-years at risk) as reported. Only use "rate" when the
  paper gives a person-time denominator; if it reports events and an arm SIZE (not
  person-time), that is "binary" instead. An incidence rate ratio CANNOT be computed
  from counts alone, so person-time is required here.
- CONTINUOUS outcomes — fill: mean_int, sd_int, n_int, mean_ctrl, sd_ctrl, n_ctrl. If
  only an SE is reported, convert to SD = SE × sqrt(n) and note it; if only a CI is
  reported for the mean, leave sd null.
- effect_metric + effect_estimate + ci_lower + ci_upper + p_value + p_operator: the
  paper's REPORTED effect for this outcome, AS STATED, or null. effect_metric is one of
  HR, OR, RR, IRR, MD, SMD, RD, narrative. Preserve p inequalities (p<0.001 →
  p_value=0.001, p_operator="lt").
- source_quote: a verbatim span stating these numbers. If you cannot find one, omit the row.

Hard rules:
1. Report numbers AS STATED. Set any value you cannot find to null. NEVER derive a CI
   from a p, a p from a CI, or arm counts from a percentage unless the denominator is
   explicit.
2. Prefer raw arm data (counts / person-time / mean+SD+N). Also capture the reported
   effect when present.
3. One object per (outcome × comparison × timepoint). Do not merge timepoints or arms.
4. If the study is single-arm (no comparator), set comparator fields to null and
   design accordingly — such rows are not poolable but should still be reported.

Study context: {study_context}
Intervention arm(s): {intervention}
Comparator arm(s): {comparator}
Outcomes of interest (extract ALL reported; guidance, not a limit): {outcomes_of_interest}

Return ONLY a single JSON object of exactly this shape. No prose, no markdown:

{
  "study_type": null,
  "citation_authors": null,
  "citation_year": null,
  "population_comparator": null,
  "outcomes": [
    {
      "name": "",
      "comparison": "",
      "timing": null,
      "design": null,
      "outcome_type": "binary",
      "events_int": null,
      "n_int": null,
      "events_ctrl": null,
      "n_ctrl": null,
      "time_int": null,
      "time_ctrl": null,
      "mean_int": null,
      "sd_int": null,
      "mean_ctrl": null,
      "sd_ctrl": null,
      "effect_metric": null,
      "effect_estimate": null,
      "ci_lower": null,
      "ci_upper": null,
      "p_value": null,
      "p_operator": "eq",
      "source_quote": ""
    }
  ]
}
"""


def _fill(template: str, **kwargs: str) -> str:
    """Substitute {placeholder} markers without disturbing the literal JSON braces."""
    out = template
    for key, val in kwargs.items():
        out = out.replace("{" + key + "}", val)
    return out


def _ctx(tags: Optional[dict[str, Any]]) -> dict[str, str]:
    tags = tags or {}
    bits = [str(tags[k]) for k in ("citation_title", "study_type", "population_participants")
            if tags.get(k)]
    ooi = []
    for k in ("population_outcomes", "primary_outcome_definition", "secondary_outcomes"):
        v = tags.get(k)
        if v:
            ooi.append(", ".join(v) if isinstance(v, (list, tuple)) else str(v))
    return {
        "study_context": " — ".join(bits) or "not provided",
        "intervention": str(tags.get("population_intervention_exposure") or "as reported"),
        "comparator": str(tags.get("population_comparator") or "as reported"),
        "outcomes_of_interest": "; ".join(ooi) or "all reported outcomes",
    }


def extract_outcome_data(
    pdf_bytes: bytes,
    *,
    injected: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Run the outcome-data pass — one model call. Returns a study dict shaped for
    ``pooling_prep`` (study-level fields + an ``outcomes`` list of arm-level objects).

    ``injected`` study-level tags (title/design/arms) prime the prompt context and
    fill study-level fields the model omits. Never raises on an odd response —
    returns ``{"outcomes": []}`` so a batch survives one bad paper. Oversized-PDF
    handling is inherited from ``_call_with_pdf``.
    """
    prompt = _fill(_OUTCOME_DATA_PROMPT, **_ctx(injected))
    result = annotator_mod._call_with_pdf(
        pdf_bytes, prompt, max_tokens=_OUTCOME_DATA_MAX_TOKENS)
    study: dict[str, Any] = dict(injected or {})
    if isinstance(result, dict):
        for k, v in result.items():
            if k == "outcomes":
                continue
            if v is not None and not (isinstance(v, str) and not v.strip()):
                study.setdefault(k, v)
        outcomes = result.get("outcomes")
        study["outcomes"] = [o for o in outcomes if isinstance(o, dict)] if isinstance(outcomes, list) else []
    else:
        study.setdefault("outcomes", [])
    return study


# ---------------------------------------------------------------------------
# Outcome harmonization — cluster differently-worded outcomes into one canonical
# ---------------------------------------------------------------------------

_CLUSTER_MAX_TOKENS = 2048

_CLUSTER_SYSTEM = (
    "You harmonize outcome names from multiple studies in a systematic review. You "
    "cluster names that denote the SAME clinical outcome and give each cluster one "
    "canonical label. You never merge genuinely different outcomes."
)

_CLUSTER_PROMPT = """\
Below are distinct outcome names extracted verbatim from different studies. Some are
the same outcome worded differently (e.g. "All-cause mortality", "Death from any
cause", "Overall mortality" are one outcome). Cluster names that refer to the SAME
outcome/construct and give each cluster a single canonical label.

Rules:
- Only merge names that measure the SAME thing. Do NOT merge different outcomes —
  e.g. "overall survival" vs "progression-free survival", or "systolic BP" vs
  "diastolic BP", are DIFFERENT and must stay in separate clusters.
- Preserve distinctions in what is measured (different scales/instruments for the
  same construct MAY cluster; different constructs may not).
- A name you are unsure about goes in its own single-member cluster.
- Every input name must appear in exactly one cluster's "members".
{targets_clause}
Outcome names:
{names}

Return ONLY a single JSON object of exactly this shape. No prose, no markdown:

{
  "clusters": [
    { "canonical": "", "members": ["", ""] }
  ]
}
"""


def cluster_outcome_names_map(
    names: list[str],
    *,
    targets: Optional[list[Any]] = None,
) -> dict[str, str]:
    """One LLM call: cluster distinct outcome names into canonical outcomes.

    Returns a normalized-name -> canonical map (the shape ``apply_canonical_map``
    consumes). ``targets`` (reviewer-defined canonical outcomes) are offered as
    preferred labels. Text-only call; never raises — returns ``{}`` on any failure so
    the batch still pools on raw names.
    """
    names = [n for n in names if n and str(n).strip()]
    if not names:
        return {}
    targets_clause = ""
    if targets:
        labels = [t if isinstance(t, str) else (t.get("canonical") or t.get("name"))
                  for t in targets]
        labels = [str(x) for x in labels if x]
        if labels:
            targets_clause = ("- Prefer these reviewer-defined canonical labels when a "
                              "name fits one of them: " + "; ".join(labels) + ".\n")
    prompt = (_CLUSTER_PROMPT
              .replace("{targets_clause}", targets_clause)
              .replace("{names}", "\n".join(f"- {n}" for n in names)))
    try:
        raw = helpers_mod.call_anthropic(
            [{"role": "user", "content": prompt}], _CLUSTER_SYSTEM,
            max_tokens=_CLUSTER_MAX_TOKENS)
        if isinstance(raw, tuple):              # thinking mode returns (answer, thinking)
            raw = raw[0]
        parsed = helpers_mod.parse_json_response(raw)
        return clusters_to_map(parsed.get("clusters") if isinstance(parsed, dict) else None)
    except Exception:                           # noqa: BLE001 — harmonization is best-effort
        logger.exception("pooling: outcome-name clustering failed; pooling on raw names")
        return {}


def harmonize_outcomes(
    studies: list[dict[str, Any]],
    *,
    targets: Optional[list[Any]] = None,
    use_llm: bool = False,
) -> list[dict[str, Any]]:
    """Annotate studies' outcomes with a canonical label so synonyms group together.

    Deterministic-first: if ``targets`` are supplied, dictionary/alias matching runs
    (pure, zero model calls). If ``use_llm`` is set, an LLM clusters whatever names
    the dictionary left unresolved (one batch call), constrained to ``targets`` when
    given. Returns harmonized copies; safe to call with neither (returns as-is).
    """
    if targets:
        studies, _report = harmonize_by_targets(studies, targets)
    if use_llm:
        pending = sorted(distinct_outcome_names(studies).keys())   # skips already-canonical
        if pending:
            mapping = cluster_outcome_names_map(pending, targets=targets)
            studies = apply_canonical_map(studies, mapping)
    return studies


def prepare_study(
    item: dict[str, Any],
    *,
    force_extract: bool = False,
) -> dict[str, Any]:
    """Dual-mode: return one pooling-ready study, preferring INJECTED extraction
    elements and self-extracting from the PDF only when they are missing.

    Mirrors Table 2's injected-vs-isolation contract. ``item`` is a flat study dict
    that may carry study-level fields, an ``outcomes`` list (from the extraction
    agent), and/or ``pdf_bytes``:

    * **Injected mode (zero model calls):** if the item already carries poolable
      outcomes (``study_is_poolable``), it is used as-is — the pooling comes straight
      from the extraction agent's output.
    * **Isolation / self-extract mode (one model call):** if it has no poolable
      outcomes but does carry ``pdf_bytes``, the pooling agent runs its OWN
      outcome-data extraction, primed with whatever study-level tags were supplied.
    * Otherwise the item is returned unchanged (it will contribute nothing).

    ``force_extract=True`` always re-extracts from the PDF even when injected
    outcomes exist (e.g. to pull raw arm counts when only a reported effect was
    injected).
    """
    study = {k: v for k, v in item.items() if k != "pdf_bytes"}
    if not force_extract and study_is_poolable(study):
        return study                            # injected — from the extraction agent
    pdf_bytes = item.get("pdf_bytes")
    if pdf_bytes:
        priming = {k: v for k, v in study.items() if k != "outcomes"} or None
        return extract_outcome_data(pdf_bytes, injected=priming)  # self-extract fallback
    return study


def pool_studies(
    items: list[dict[str, Any]],
    *,
    force_extract: bool = False,
    outcome_targets: Optional[list[Any]] = None,
    harmonize_llm: bool = False,
    measures: Optional[dict[str, str]] = None,
    default_measure: Optional[str] = None,
    include_timepoint: bool = True,
    min_studies: int = 1,
    model: str = "random",
    tau2_method: str = "REML",
) -> list[dict[str, Any]]:
    """Dual-mode pooling over a mixed batch → one pooled result per body of evidence.

    This is the primary entry point. Each ``item`` is a flat study dict (study-level
    fields + optional ``outcomes`` + optional ``pdf_bytes``). Per item, extraction
    elements from the extraction agent are used when present; otherwise the pooling
    agent self-extracts from ``pdf_bytes`` (see ``prepare_study``). A study that
    neither carries outcomes nor a PDF, or that fails extraction, contributes no
    outcomes rather than aborting the batch.

    Between assembly and pooling, **outcome harmonization** clusters differently-worded
    outcomes into one canonical label so they group together (see
    ``harmonize_outcomes``): ``outcome_targets`` (reviewer-defined canonical outcomes
    + aliases) drive the deterministic pass; ``harmonize_llm=True`` adds an LLM
    clustering pass for whatever the dictionary left unresolved. With neither, grouping
    falls back to normalized verbatim names.
    """
    studies: list[dict[str, Any]] = []
    for item in items:
        try:
            studies.append(prepare_study(item, force_extract=force_extract))
        except Exception:                       # noqa: BLE001 — one bad paper must not kill the batch
            logger.exception("pooling: preparing a study failed (extraction error?)")
    if outcome_targets or harmonize_llm:
        studies = harmonize_outcomes(studies, targets=outcome_targets, use_llm=harmonize_llm)
    return pool_extractions(
        studies, measures=measures, default_measure=default_measure,
        include_timepoint=include_timepoint, min_studies=min_studies,
        model=model, tau2_method=tau2_method)


def pool_from_pdfs(
    papers: list[dict[str, Any]],
    *,
    measures: Optional[dict[str, str]] = None,
    default_measure: Optional[str] = None,
    include_timepoint: bool = True,
    min_studies: int = 1,
    model: str = "random",
    tau2_method: str = "REML",
) -> list[dict[str, Any]]:
    """Always-extract convenience wrapper: PDFs → outcome-data extraction → pool.

    ``papers`` is a list of ``{pdf_bytes, injected?}`` dicts. Thin shim over
    ``pool_studies`` that flattens ``injected`` into the item and forces extraction —
    use it when you know none of the papers carry pre-extracted outcomes. For the
    dual-mode (injected-first, self-extract-the-gaps) path, call ``pool_studies``.
    """
    items = [{**(p.get("injected") or {}), "pdf_bytes": p.get("pdf_bytes")} for p in papers]
    return pool_studies(
        items, force_extract=True, measures=measures, default_measure=default_measure,
        include_timepoint=include_timepoint, min_studies=min_studies,
        model=model, tau2_method=tau2_method)
