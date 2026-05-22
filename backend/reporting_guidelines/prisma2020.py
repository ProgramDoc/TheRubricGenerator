"""PRISMA 2020 — Preferred Reporting Items for Systematic reviews and Meta-Analyses.

Source: Page MJ, McKenzie JE, Bossuyt PM, Boutron I, Hoffmann TC, Mulrow CD,
et al. "The PRISMA 2020 statement: an updated guideline for reporting
systematic reviews." BMJ 2021;372:n71. https://doi.org/10.1136/bmj.n71

The 27 PRISMA 2020 items (with a/b/c… sub-items where the checklist splits a
requirement) are encoded below — 42 entries across 7 sections. We make one LLM
call per paper asking the model to judge, for each item, whether the systematic
review report adhered to it, with a short quote as evidence. Items legitimately
not applicable (e.g. heterogeneity exploration for a review with a single study)
return ``adhered=null`` so the proportion is not deflated.

Same shape as the other reporting-guideline modules (``strobe.py`` etc.):
``run(pdf_bytes, fields, classification)`` →
``{items, adhered, applicable, total, proportion}``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


ITEMS: list[dict[str, str]] = [
    # Title
    {"id": "1", "section": "Title", "topic": "Title",
     "description": "Identify the report as a systematic review"},
    # Abstract
    {"id": "2", "section": "Abstract", "topic": "Abstract",
     "description": "See the PRISMA 2020 for Abstracts checklist — provide a structured "
                    "abstract summarising the objectives, methods, results, and conclusions"},
    # Introduction
    {"id": "3", "section": "Introduction", "topic": "Rationale",
     "description": "Describe the rationale for the review in the context of existing knowledge"},
    {"id": "4", "section": "Introduction", "topic": "Objectives",
     "description": "Provide an explicit statement of the objective(s) or question(s) the review addresses"},
    # Methods
    {"id": "5", "section": "Methods", "topic": "Eligibility criteria",
     "description": "Specify the inclusion and exclusion criteria for the review and how studies "
                    "were grouped for the syntheses"},
    {"id": "6", "section": "Methods", "topic": "Information sources",
     "description": "Specify all databases, registers, websites, organisations, reference lists "
                    "and other sources searched or consulted to identify studies; specify the date "
                    "when each source was last searched or consulted"},
    {"id": "7", "section": "Methods", "topic": "Search strategy",
     "description": "Present the full search strategies for all databases, registers and websites, "
                    "including any filters and limits used"},
    {"id": "8", "section": "Methods", "topic": "Selection process",
     "description": "Specify the methods used to decide whether a study met the inclusion criteria, "
                    "including how many reviewers screened each record and report retrieved, whether "
                    "they worked independently, and details of any automation tools used"},
    {"id": "9", "section": "Methods", "topic": "Data collection process",
     "description": "Specify the methods used to collect data from reports, including how many "
                    "reviewers collected data from each report, whether they worked independently, "
                    "any processes for obtaining or confirming data from investigators, and details "
                    "of any automation tools used"},
    {"id": "10a", "section": "Methods", "topic": "Data items",
     "description": "List and define all outcomes for which data were sought; specify whether all "
                    "results compatible with each outcome domain in each study were sought, and if "
                    "not, the methods used to decide which results to collect"},
    {"id": "10b", "section": "Methods", "topic": "Data items",
     "description": "List and define all other variables for which data were sought (e.g. participant "
                    "and intervention characteristics, funding sources); describe any assumptions made "
                    "about any missing or unclear information"},
    {"id": "11", "section": "Methods", "topic": "Study risk of bias assessment",
     "description": "Specify the methods used to assess risk of bias in the included studies, "
                    "including the tool(s) used, how many reviewers assessed each study and whether "
                    "they worked independently, and details of any automation tools used"},
    {"id": "12", "section": "Methods", "topic": "Effect measures",
     "description": "Specify for each outcome the effect measure(s) (e.g. risk ratio, mean difference) "
                    "used in the synthesis or presentation of results"},
    {"id": "13a", "section": "Methods", "topic": "Synthesis methods",
     "description": "Describe the processes used to decide which studies were eligible for each "
                    "synthesis (e.g. tabulating study intervention characteristics against the "
                    "planned groups for each synthesis)"},
    {"id": "13b", "section": "Methods", "topic": "Synthesis methods",
     "description": "Describe any methods required to prepare the data for presentation or synthesis, "
                    "such as handling of missing summary statistics or data conversions"},
    {"id": "13c", "section": "Methods", "topic": "Synthesis methods",
     "description": "Describe any methods used to tabulate or visually display results of individual "
                    "studies and syntheses"},
    {"id": "13d", "section": "Methods", "topic": "Synthesis methods",
     "description": "Describe any methods used to synthesise results and provide a rationale for the "
                    "choice(s); if meta-analysis was performed, describe the model(s), method(s) to "
                    "identify statistical heterogeneity, and software package(s) used"},
    {"id": "13e", "section": "Methods", "topic": "Synthesis methods",
     "description": "Describe any methods used to explore possible causes of heterogeneity among "
                    "study results (e.g. subgroup analysis, meta-regression)"},
    {"id": "13f", "section": "Methods", "topic": "Synthesis methods",
     "description": "Describe any sensitivity analyses conducted to assess robustness of the "
                    "synthesised results"},
    {"id": "14", "section": "Methods", "topic": "Reporting bias assessment",
     "description": "Describe any methods used to assess risk of bias due to missing results in a "
                    "synthesis (arising from reporting biases)"},
    {"id": "15", "section": "Methods", "topic": "Certainty assessment",
     "description": "Describe any methods used to assess certainty (or confidence) in the body of "
                    "evidence for an outcome"},
    # Results
    {"id": "16a", "section": "Results", "topic": "Study selection",
     "description": "Describe the results of the search and selection process, from the number of "
                    "records identified to the number of studies included, ideally using a flow diagram"},
    {"id": "16b", "section": "Results", "topic": "Study selection",
     "description": "Cite studies that might appear to meet the inclusion criteria but were excluded, "
                    "and explain why they were excluded"},
    {"id": "17", "section": "Results", "topic": "Study characteristics",
     "description": "Cite each included study and present its characteristics"},
    {"id": "18", "section": "Results", "topic": "Risk of bias in studies",
     "description": "Present assessments of risk of bias for each included study"},
    {"id": "19", "section": "Results", "topic": "Results of individual studies",
     "description": "For all outcomes, present for each study summary statistics for each group "
                    "(where appropriate) and an effect estimate and its precision (e.g. "
                    "confidence/credible interval), ideally using structured tables or plots"},
    {"id": "20a", "section": "Results", "topic": "Results of syntheses",
     "description": "For each synthesis, briefly summarise the characteristics and risk of bias "
                    "among contributing studies"},
    {"id": "20b", "section": "Results", "topic": "Results of syntheses",
     "description": "Present results of all statistical syntheses conducted; if meta-analysis was "
                    "done, present for each the summary estimate and its precision and measures of "
                    "statistical heterogeneity; if comparing groups, describe the direction of effect"},
    {"id": "20c", "section": "Results", "topic": "Results of syntheses",
     "description": "Present results of all investigations of possible causes of heterogeneity among "
                    "study results"},
    {"id": "20d", "section": "Results", "topic": "Results of syntheses",
     "description": "Present results of all sensitivity analyses conducted to assess the robustness "
                    "of the synthesised results"},
    {"id": "21", "section": "Results", "topic": "Reporting biases",
     "description": "Present assessments of risk of bias due to missing results (arising from "
                    "reporting biases) for each synthesis assessed"},
    {"id": "22", "section": "Results", "topic": "Certainty of evidence",
     "description": "Present assessments of certainty (or confidence) in the body of evidence for "
                    "each outcome assessed"},
    # Discussion
    {"id": "23a", "section": "Discussion", "topic": "Discussion",
     "description": "Provide a general interpretation of the results in the context of other evidence"},
    {"id": "23b", "section": "Discussion", "topic": "Discussion",
     "description": "Discuss any limitations of the evidence included in the review"},
    {"id": "23c", "section": "Discussion", "topic": "Discussion",
     "description": "Discuss any limitations of the review processes used"},
    {"id": "23d", "section": "Discussion", "topic": "Discussion",
     "description": "Discuss implications of the results for practice, policy, and future research"},
    # Other information
    {"id": "24a", "section": "Other information", "topic": "Registration and protocol",
     "description": "Provide registration information for the review, including the register name "
                    "and registration number, or state that the review was not registered"},
    {"id": "24b", "section": "Other information", "topic": "Registration and protocol",
     "description": "Indicate where the review protocol can be accessed, or state that a protocol "
                    "was not prepared"},
    {"id": "24c", "section": "Other information", "topic": "Registration and protocol",
     "description": "Describe and explain any amendments to information provided at registration or "
                    "in the protocol"},
    {"id": "25", "section": "Other information", "topic": "Support",
     "description": "Describe sources of financial or non-financial support for the review, and the "
                    "role of the funders or sponsors in the review"},
    {"id": "26", "section": "Other information", "topic": "Competing interests",
     "description": "Declare any competing interests of review authors"},
    {"id": "27", "section": "Other information", "topic": "Availability of data, code and other materials",
     "description": "Report which of the following are publicly available and where they can be "
                    "found: template data collection forms; data extracted from included studies; "
                    "data used for all analyses; analytic code; any other materials used in the review"},
]


_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing adherence of a "
    "systematic review report to the PRISMA 2020 checklist. Read the PDF "
    "carefully. For each checklist item, decide whether the review reports the "
    "required information. Be strict but fair: an item is adhered only if the "
    "information is actually present. If an item is genuinely not applicable to "
    "this review (e.g. heterogeneity exploration 13e when only one study was "
    "included; sensitivity analyses 13f / 20d when none were planned), mark it "
    "N/A. Return ONLY a valid JSON object — no preamble, no markdown fences."
)


def build_prompt(classification: dict[str, str],
                 extracted_fields: dict[str, str] | None = None) -> str:
    """Assemble the single-call PRISMA 2020 prompt covering all 42 entries."""
    study_type = classification.get("study_type", "SR with Meta-Analysis")
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
            f'  "{it["id"]}": {{"adhered": true|false|null, "evidence": "short quote or \'N/A\' if not applicable"}}'
        )
    shape = "{\n" + ",\n".join(shape_entries) + "\n}"

    return f"""Assess this **{study_type}** report against the PRISMA 2020 checklist.

Context (fields already extracted from the review):
{ctx_json}

PRISMA 2020 items:
{items_block}

For each item, return:
- ``adhered = true`` if the review reports the required information,
- ``adhered = false`` if the review should report it but does not,
- ``adhered = null`` if the item is legitimately not applicable to this review
  (e.g. exploring causes of heterogeneity 13e/20c when only one study was
  included; sensitivity analyses 13f/20d when none were conducted).
- ``evidence`` is a brief quote (≤ 25 words) from the review, or a one-line
  reason for a false/null judgement.

Return a JSON object with exactly this shape:
{shape}

Return only the JSON object."""


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str]) -> dict[str, Any]:
    """Run the PRISMA 2020 adherence check. Returns
    ``{items: {id: {adhered, evidence}}, adhered, applicable, total, proportion}``.
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
            else:  # na / n/a / null / none / ""
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
        "guideline": "PRISMA 2020",
        "citation": ("Page MJ, McKenzie JE, Bossuyt PM, et al. The PRISMA 2020 "
                     "statement. BMJ 2021;372:n71. https://doi.org/10.1136/bmj.n71"),
        "system_prompt": _SYSTEM_PROMPT,
        "items": ITEMS,
        "prompt_template": build_prompt(
            {"study_type": "SR with Meta-Analysis"},
            {"(example field)": "<value>"},
        ),
        "scoring_code": inspect.getsource(run),
    }
