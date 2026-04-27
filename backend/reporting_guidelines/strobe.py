"""STROBE — STrengthening the Reporting of OBservational studies in Epidemiology.

Source: Vandenbroucke JP, von Elm E, Altman DG, Gøtzsche PC, Mulrow CD, Pocock SJ,
et al. "Strengthening the Reporting of Observational Studies in Epidemiology
(STROBE): Explanation and Elaboration." Ann Intern Med 2007; 147:W-163–W-194.
https://doi.org/10.7326/0003-4819-147-8-200710160-00010-w1

The 22 STROBE items (with a/b/c sub-items where the checklist splits a
requirement across study designs or aspects) are encoded below. We make one
LLM call per paper asking the model to judge, for each item, whether the
observational study report adhered to it, with a short quote as evidence.
Items legitimately not applicable (e.g., 17 "subgroup analyses" for a study
with none) return ``adhered=null`` so the proportion is not deflated.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


ITEMS: list[dict[str, str]] = [
    # Title and abstract
    {"id": "1a", "section": "Title and abstract", "topic": "Title and abstract",
     "description": "Indicate the study's design with a commonly used term in the title or the abstract"},
    {"id": "1b", "section": "Title and abstract", "topic": "Title and abstract",
     "description": "Provide in the abstract an informative and balanced summary of what was done and what was found"},
    # Introduction
    {"id": "2", "section": "Introduction", "topic": "Background/rationale",
     "description": "Explain the scientific background and rationale for the investigation being reported"},
    {"id": "3", "section": "Introduction", "topic": "Objectives",
     "description": "State specific objectives, including any prespecified hypotheses"},
    # Methods
    {"id": "4", "section": "Methods", "topic": "Study design",
     "description": "Present key elements of study design early in the paper"},
    {"id": "5", "section": "Methods", "topic": "Setting",
     "description": "Describe the setting, locations, and relevant dates, including periods of recruitment, exposure, follow-up, and data collection"},
    {"id": "6a", "section": "Methods", "topic": "Participants",
     "description": "Cohort: give the eligibility criteria, and the sources and methods of selection of participants, and describe methods of follow-up. "
                    "Case-control: give the eligibility criteria, and the sources and methods of case ascertainment and control selection; give the rationale for the choice of cases and controls. "
                    "Cross-sectional: give the eligibility criteria, and the sources and methods of selection of participants"},
    {"id": "6b", "section": "Methods", "topic": "Participants",
     "description": "Cohort: for matched studies, give matching criteria and number of exposed and unexposed. "
                    "Case-control: for matched studies, give matching criteria and the number of controls per case"},
    {"id": "7", "section": "Methods", "topic": "Variables",
     "description": "Clearly define all outcomes, exposures, predictors, potential confounders, and effect modifiers. Give diagnostic criteria, if applicable"},
    {"id": "8", "section": "Methods", "topic": "Data sources/measurement",
     "description": "For each variable of interest, give sources of data and details of methods of assessment (measurement). Describe comparability of assessment methods if there is more than one group"},
    {"id": "9", "section": "Methods", "topic": "Bias",
     "description": "Describe any efforts to address potential sources of bias"},
    {"id": "10", "section": "Methods", "topic": "Study size",
     "description": "Explain how the study size was arrived at"},
    {"id": "11", "section": "Methods", "topic": "Quantitative variables",
     "description": "Explain how quantitative variables were handled in the analyses. If applicable, describe which groupings were chosen, and why"},
    {"id": "12a", "section": "Methods", "topic": "Statistical methods",
     "description": "Describe all statistical methods, including those used to control for confounding"},
    {"id": "12b", "section": "Methods", "topic": "Statistical methods",
     "description": "Describe any methods used to examine subgroups and interactions"},
    {"id": "12c", "section": "Methods", "topic": "Statistical methods",
     "description": "Explain how missing data were addressed"},
    {"id": "12d", "section": "Methods", "topic": "Statistical methods",
     "description": "Cohort: if applicable, explain how loss to follow-up was addressed. "
                    "Case-control: if applicable, explain how matching of cases and controls was addressed. "
                    "Cross-sectional: if applicable, describe analytical methods taking account of sampling strategy"},
    {"id": "12e", "section": "Methods", "topic": "Statistical methods",
     "description": "Describe any sensitivity analyses"},
    # Results
    {"id": "13a", "section": "Results", "topic": "Participants",
     "description": "Report numbers of individuals at each stage of study—eg numbers potentially eligible, examined for eligibility, confirmed eligible, included in the study, completing follow-up, and analysed"},
    {"id": "13b", "section": "Results", "topic": "Participants",
     "description": "Give reasons for non-participation at each stage"},
    {"id": "13c", "section": "Results", "topic": "Participants",
     "description": "Consider use of a flow diagram"},
    {"id": "14a", "section": "Results", "topic": "Descriptive data",
     "description": "Give characteristics of study participants (eg demographic, clinical, social) and information on exposures and potential confounders"},
    {"id": "14b", "section": "Results", "topic": "Descriptive data",
     "description": "Indicate number of participants with missing data for each variable of interest"},
    {"id": "14c", "section": "Results", "topic": "Descriptive data",
     "description": "Cohort: summarise follow-up time (eg, average and total amount)"},
    {"id": "15", "section": "Results", "topic": "Outcome data",
     "description": "Cohort: report numbers of outcome events or summary measures over time. "
                    "Case-control: report numbers in each exposure category, or summary measures of exposure. "
                    "Cross-sectional: report numbers of outcome events or summary measures"},
    {"id": "16a", "section": "Results", "topic": "Main results",
     "description": "Give unadjusted estimates and, if applicable, confounder-adjusted estimates and their precision (eg, 95% confidence interval). Make clear which confounders were adjusted for and why they were included"},
    {"id": "16b", "section": "Results", "topic": "Main results",
     "description": "Report category boundaries when continuous variables were categorized"},
    {"id": "16c", "section": "Results", "topic": "Main results",
     "description": "If relevant, consider translating estimates of relative risk into absolute risk for a meaningful time period"},
    {"id": "17", "section": "Results", "topic": "Other analyses",
     "description": "Report other analyses done—eg analyses of subgroups and interactions, and sensitivity analyses"},
    # Discussion
    {"id": "18", "section": "Discussion", "topic": "Key results",
     "description": "Summarise key results with reference to study objectives"},
    {"id": "19", "section": "Discussion", "topic": "Limitations",
     "description": "Discuss limitations of the study, taking into account sources of potential bias or imprecision. Discuss both direction and magnitude of any potential bias"},
    {"id": "20", "section": "Discussion", "topic": "Interpretation",
     "description": "Give a cautious overall interpretation of results considering objectives, limitations, multiplicity of analyses, results from similar studies, and other relevant evidence"},
    {"id": "21", "section": "Discussion", "topic": "Generalisability",
     "description": "Discuss the generalisability (external validity) of the study results"},
    # Other information
    {"id": "22", "section": "Other information", "topic": "Funding",
     "description": "Give the source of funding and the role of the funders for the present study and, if applicable, for the original study on which the present article is based"},
]


_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing adherence of an "
    "observational study report to the STROBE 2007 checklist. Read the PDF "
    "carefully. For each checklist item, decide whether the paper reports the "
    "required information. Be strict but fair: an item is adhered only if the "
    "information is actually present (not merely referenced as 'available "
    "elsewhere' unless the paper provides a usable pointer). If an item is "
    "genuinely not applicable to this study design (e.g., matching criteria "
    "for an unmatched cohort study), mark it N/A. "
    "Return ONLY a valid JSON object — no preamble, no markdown fences."
)


def build_prompt(classification: dict[str, str],
                 extracted_fields: dict[str, str] | None = None) -> str:
    """Assemble the single-call STROBE prompt covering all 32 items."""
    study_type = classification.get("study_type", "Cohort Study")
    ctx_json = json.dumps(extracted_fields or {}, indent=2) if extracted_fields else "(no pre-extracted fields)"

    item_lines = []
    for it in ITEMS:
        item_lines.append(
            f"- **{it['id']}** ({it['section']} — {it['topic']}): {it['description']}"
        )
    items_block = "\n".join(item_lines)

    shape_entries = []
    for it in ITEMS:
        shape_entries.append(
            f'  "{it["id"]}": {{"adhered": true|false|null, "evidence": "short quote or ... \'N/A\' if not applicable"}}'
        )
    shape = "{\n" + ",\n".join(shape_entries) + "\n}"

    return f"""Assess this **{study_type}** report against the STROBE 2007 checklist.

Context (fields already extracted from the paper):
{ctx_json}

STROBE items:
{items_block}

For each item, return:
- ``adhered = true`` if the paper reports the required information,
- ``adhered = false`` if the paper should report it but does not,
- ``adhered = null`` if the item is legitimately not applicable to this study
  (e.g., matching criteria 6b for an unmatched cohort; follow-up time 14c for
  a case-control design; subgroup analyses 17 when none were performed).
- ``evidence`` is a brief quote (≤ 25 words) from the paper, or a one-line
  reason for a false/null judgement.

Return a JSON object with exactly this shape:
{shape}

Return only the JSON object."""


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str]) -> dict[str, Any]:
    """Run STROBE adherence check. Returns
    ``{items: {id: {adhered, evidence}}, adhered, applicable, proportion}``.
    """
    prompt = build_prompt(classification, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    items_out: dict[str, dict[str, Any]] = {}
    for it in ITEMS:
        entry = raw.get(it["id"]) or {}
        adhered = entry.get("adhered")
        if isinstance(adhered, str):
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
        "guideline": "STROBE 2007",
        "citation": "Vandenbroucke JP et al. Ann Intern Med 2007; 147:W-163. https://doi.org/10.7326/0003-4819-147-8-200710160-00010-w1",
        "system_prompt": _SYSTEM_PROMPT,
        "items": ITEMS,
        "prompt_template": build_prompt(
            {"study_type": "Cohort Study"},
            {"(example field)": "<value>"},
        ),
        "scoring_code": inspect.getsource(run),
    }
