"""CONSORT 2025 — reporting-guideline checklist for randomized trials.

Source: Hopewell S, Chan AW, Collins GS, Hróbjartsson A, Moher D, Schulz KF, et al.
"CONSORT 2025 Statement: updated guideline for reporting randomised trials."
BMJ. 2025; 388:e081123. https://dx.doi.org/10.1136/bmj-2024-081123

The 30 checklist items from the editable checklist are encoded below. We make
one LLM call per paper asking the model to judge, for each item, whether the
trial report adhered to it, with a short quote as evidence. Items legitimately
not applicable to a given trial (e.g., 16b interim-analyses for a trial that
had none) return ``adhered=null`` so they don't inflate or deflate the
proportion.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


ITEMS: list[dict[str, str]] = [
    {"id": "1a", "section": "Title and abstract", "topic": "Title and structured abstract",
     "description": "Identification as a randomised trial"},
    {"id": "1b", "section": "Title and abstract", "topic": "Title and structured abstract",
     "description": "Structured summary of the trial design, methods, results, and conclusions"},
    {"id": "2", "section": "Open science", "topic": "Trial registration",
     "description": "Name of trial registry, identifying number (with URL) and date of registration"},
    {"id": "3", "section": "Open science", "topic": "Protocol and statistical analysis plan",
     "description": "Where the trial protocol and statistical analysis plan can be accessed"},
    {"id": "4", "section": "Open science", "topic": "Data sharing",
     "description": "Where and how the individual de-identified participant data (including data dictionary), statistical code and any other materials can be accessed"},
    {"id": "5a", "section": "Open science", "topic": "Funding and conflicts of interest",
     "description": "Sources of funding and other support (e.g., supply of drugs), and role of funders in the design, conduct, analysis and reporting of the trial"},
    {"id": "5b", "section": "Open science", "topic": "Funding and conflicts of interest",
     "description": "Financial and other conflicts of interest of the manuscript authors"},
    {"id": "6", "section": "Introduction", "topic": "Background and rationale",
     "description": "Scientific background and rationale"},
    {"id": "7", "section": "Introduction", "topic": "Objectives",
     "description": "Specific objectives related to benefits and harms"},
    {"id": "8", "section": "Methods", "topic": "Patient and public involvement",
     "description": "Details of patient or public involvement in the design, conduct and reporting of the trial"},
    {"id": "9", "section": "Methods", "topic": "Trial design",
     "description": "Description of trial design including type of trial (e.g., parallel group, crossover), allocation ratio, and framework (e.g., superiority, equivalence, non-inferiority, exploratory)"},
    {"id": "10", "section": "Methods", "topic": "Changes to trial protocol",
     "description": "Important changes to the trial after it commenced including any outcomes or analyses that were not prespecified, with reason"},
    {"id": "11", "section": "Methods", "topic": "Trial setting",
     "description": "Settings (e.g., community, hospital) and locations (e.g., countries, sites) where the trial was conducted"},
    {"id": "12a", "section": "Methods", "topic": "Eligibility criteria",
     "description": "Eligibility criteria for participants"},
    {"id": "12b", "section": "Methods", "topic": "Eligibility criteria",
     "description": "If applicable, eligibility criteria for sites and for individuals delivering the interventions (e.g., surgeons, physiotherapists)"},
    {"id": "13", "section": "Methods", "topic": "Intervention and comparator",
     "description": "Intervention and comparator with sufficient details to allow replication. If relevant, where additional materials describing the intervention and comparator (e.g., intervention manual) can be accessed"},
    {"id": "14", "section": "Methods", "topic": "Outcomes",
     "description": "Pre-specified primary and secondary outcomes, including the specific measurement variable, analysis metric, method of aggregation, and time point for each outcome"},
    {"id": "15", "section": "Methods", "topic": "Harms",
     "description": "How harms were defined and assessed (e.g., systematically, non-systematically)"},
    {"id": "16a", "section": "Methods", "topic": "Sample size",
     "description": "How sample size was determined, including all assumptions supporting the sample size calculation"},
    {"id": "16b", "section": "Methods", "topic": "Sample size",
     "description": "Explanation of any interim analyses and stopping guidelines"},
    {"id": "17a", "section": "Methods", "topic": "Sequence generation",
     "description": "Who generated the random allocation sequence and the method used"},
    {"id": "17b", "section": "Methods", "topic": "Sequence generation",
     "description": "Type of randomisation and details of any restriction (e.g., stratification, blocking and block size)"},
    {"id": "18", "section": "Methods", "topic": "Allocation concealment mechanism",
     "description": "Mechanism used to implement the random allocation sequence (e.g., central computer/telephone; sequentially numbered, opaque, sealed containers), describing any steps to conceal the sequence until interventions were assigned"},
    {"id": "19", "section": "Methods", "topic": "Implementation",
     "description": "Whether the personnel who enrolled and those who assigned participants to the interventions had access to the random allocation sequence"},
    {"id": "20a", "section": "Methods", "topic": "Blinding",
     "description": "Who was blinded after assignment to interventions (e.g., participants, care providers, outcome assessors, data analysts)"},
    {"id": "20b", "section": "Methods", "topic": "Blinding",
     "description": "If blinded, how blinding was achieved and description of the similarity of interventions"},
    {"id": "21a", "section": "Methods", "topic": "Statistical methods",
     "description": "Statistical methods used to compare groups for primary and secondary outcomes, including harms"},
    {"id": "21b", "section": "Methods", "topic": "Statistical methods",
     "description": "Definition of who is included in each analysis (e.g., all randomised participants), and in which group"},
    {"id": "21c", "section": "Methods", "topic": "Statistical methods",
     "description": "How missing data were handled in the analysis"},
    {"id": "21d", "section": "Methods", "topic": "Statistical methods",
     "description": "Methods for any additional analyses (e.g., subgroup and sensitivity analyses), distinguishing prespecified from post-hoc"},
    {"id": "22a", "section": "Results", "topic": "Participant flow, including flow diagram",
     "description": "For each group, the numbers of participants who were randomly assigned, received intended intervention, and were analysed for the primary outcome"},
    {"id": "22b", "section": "Results", "topic": "Participant flow, including flow diagram",
     "description": "For each group, losses and exclusions after randomisation, together with reasons"},
    {"id": "23a", "section": "Results", "topic": "Recruitment",
     "description": "Dates defining the periods of recruitment and follow-up for outcomes of benefits and harms"},
    {"id": "23b", "section": "Results", "topic": "Recruitment",
     "description": "If relevant, why the trial ended or was stopped"},
    {"id": "24a", "section": "Results", "topic": "Intervention and comparator delivery",
     "description": "Intervention and comparator as they were actually administered (e.g., where appropriate, who delivered the intervention/comparator, how participants adhered, whether they were delivered as intended [fidelity])"},
    {"id": "24b", "section": "Results", "topic": "Intervention and comparator delivery",
     "description": "Concomitant care received during the trial for each group"},
    {"id": "25", "section": "Results", "topic": "Baseline data",
     "description": "A table showing baseline demographic and clinical characteristics for each group"},
    {"id": "26", "section": "Results", "topic": "Numbers analysed, outcomes and estimation",
     "description": "For each primary and secondary outcome, by group: number of participants in analysis; number with available data at the outcome time point; result for each group and estimated effect size with 95% CI; for binary outcomes, both absolute and relative effect size"},
    {"id": "27", "section": "Results", "topic": "Harms",
     "description": "All harms or unintended events in each group"},
    {"id": "28", "section": "Results", "topic": "Ancillary analyses",
     "description": "Any other analyses performed, including subgroup and sensitivity analyses, distinguishing pre-specified from post-hoc"},
    {"id": "29", "section": "Discussion", "topic": "Interpretation",
     "description": "Interpretation consistent with results, balancing benefits and harms, and considering other relevant evidence"},
    {"id": "30", "section": "Discussion", "topic": "Limitations",
     "description": "Trial limitations, addressing sources of potential bias, imprecision, generalisability, and, if relevant, multiplicity of analyses"},
]


_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing adherence of a "
    "randomised trial report to the CONSORT 2025 checklist. Read the PDF "
    "carefully. For each checklist item, decide whether the trial report "
    "reports the required information. Be strict but fair: an item is adhered "
    "only if the information is actually present (not merely referenced as "
    "'available elsewhere' unless the paper provides a usable pointer). "
    "If an item is genuinely not applicable to this trial, mark it N/A. "
    "Return ONLY a valid JSON object — no preamble, no markdown fences."
)


def build_prompt(classification: dict[str, str],
                 extracted_fields: dict[str, str] | None = None) -> str:
    """Assemble the single-call CONSORT prompt covering all 30 items."""
    study_type = classification.get("study_type", "Randomized Controlled Trial")
    ctx_json = json.dumps(extracted_fields or {}, indent=2) if extracted_fields else "(no pre-extracted fields)"

    item_lines = []
    for it in ITEMS:
        item_lines.append(
            f"- **{it['id']}** ({it['section']} — {it['topic']}): {it['description']}"
        )
    items_block = "\n".join(item_lines)

    # JSON shape template — one entry per item
    shape_entries = []
    for it in ITEMS:
        shape_entries.append(
            f'  "{it["id"]}": {{"adhered": true|false|null, "evidence": "short quote or ... \'N/A\' if not applicable"}}'
        )
    shape = "{\n" + ",\n".join(shape_entries) + "\n}"

    return f"""Assess this **{study_type}** report against the CONSORT 2025 checklist.

Context (fields already extracted from the paper):
{ctx_json}

CONSORT 2025 items:
{items_block}

For each item, return:
- ``adhered = true`` if the paper reports the required information,
- ``adhered = false`` if the paper should report it but does not,
- ``adhered = null`` if the item is legitimately not applicable to this trial
  (e.g., sub-item 12b "eligibility for sites/deliverers" if there is only one
  site and no special deliverer criteria; 16b interim analyses if none were
  performed).
- ``evidence`` is a brief quote (≤ 25 words) from the paper, or a one-line
  reason for a false/null judgement.

Return a JSON object with exactly this shape:
{shape}

Return only the JSON object."""


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str]) -> dict[str, Any]:
    """Run CONSORT 2025 adherence check. Returns
    ``{items: {id: {adhered, evidence}}, adhered, applicable, proportion}``.
    """
    prompt = build_prompt(classification, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    items_out: dict[str, dict[str, Any]] = {}
    for it in ITEMS:
        entry = raw.get(it["id"]) or {}
        adhered = entry.get("adhered")
        if isinstance(adhered, str):
            # Be generous with non-boolean returns
            low = adhered.strip().lower()
            if low in ("true", "yes", "y", "1"):
                adhered = True
            elif low in ("false", "no", "n", "0"):
                adhered = False
            elif low in ("na", "n/a", "null", "none", ""):
                adhered = None
            else:
                adhered = None
        evidence = str(entry.get("evidence") or "").strip()
        items_out[it["id"]] = {"adhered": adhered, "evidence": evidence,
                                "section": it["section"], "topic": it["topic"],
                                "description": it["description"]}

    applicable = [v for v in items_out.values() if v["adhered"] is not None]
    adhered_count = sum(1 for v in applicable if v["adhered"] is True)
    applicable_count = len(applicable)
    proportion = (adhered_count / applicable_count) if applicable_count else 0.0

    return {
        "items": items_out,
        "adhered": adhered_count,
        "applicable": applicable_count,
        "total": len(ITEMS),
        "proportion": round(proportion, 3),
    }


def prompt_catalog() -> dict[str, Any]:
    """Return the prompt template + items table for the developer icon."""
    import inspect
    return {
        "guideline": "CONSORT 2025",
        "citation": "Hopewell S et al. BMJ 2025; 388:e081123. https://dx.doi.org/10.1136/bmj-2024-081123",
        "system_prompt": _SYSTEM_PROMPT,
        "items": ITEMS,
        "prompt_template": build_prompt(
            {"study_type": "Randomized Controlled Trial"},
            {"(example field)": "<value>"},
        ),
        "scoring_code": inspect.getsource(run),
    }
