"""STARD 2015 — Standards for Reporting of Diagnostic Accuracy Studies.

Source: Bossuyt PM, Reitsma JB, Bruns DE, Gatsonis CA, Glasziou PP, Irwig L,
et al. "STARD 2015: An Updated List of Essential Items for Reporting
Diagnostic Accuracy Studies." BMJ 2015;351:h5527.
https://doi.org/10.1136/bmj.h5527

The 30 STARD 2015 items are encoded below grouped by section (Title/Abstract,
Abstract, Introduction, Methods, Results, Discussion, Other). We make one
LLM call per paper asking the model to judge, for each item, whether the
diagnostic accuracy study report adhered to it, with a short quote as
evidence. Items legitimately not applicable (e.g., 13b "uninterpretable
results" for a study with none) return ``adhered=null`` so the proportion
is not deflated.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


ITEMS: list[dict[str, str]] = [
    # Title or abstract
    {"id": "1", "section": "Title or abstract", "topic": "Title",
     "description": "Identification as a study of diagnostic accuracy using at least one measure of accuracy (such as sensitivity, specificity, predictive values, or AUC)"},
    # Abstract
    {"id": "2", "section": "Abstract", "topic": "Abstract",
     "description": "Structured summary of study design, methods, results, and conclusions (for specific guidance, see STARD for Abstracts)"},
    # Introduction
    {"id": "3", "section": "Introduction", "topic": "Background",
     "description": "Scientific and clinical background, including the intended use and clinical role of the index test"},
    {"id": "4", "section": "Introduction", "topic": "Objectives",
     "description": "Study objectives and hypotheses"},
    # Methods — Study design
    {"id": "5", "section": "Methods", "topic": "Study design",
     "description": "Whether data collection was planned before the index test and reference standard were performed (prospective study) or after (retrospective study)"},
    # Methods — Participants
    {"id": "6", "section": "Methods", "topic": "Participants — Eligibility",
     "description": "Eligibility criteria"},
    {"id": "7", "section": "Methods", "topic": "Participants — Identification",
     "description": "On what basis potentially eligible participants were identified (such as symptoms, results from previous tests, inclusion in registry)"},
    {"id": "8", "section": "Methods", "topic": "Participants — Sampling",
     "description": "Where and when potentially eligible participants were identified (setting, location and dates)"},
    {"id": "9", "section": "Methods", "topic": "Participants — Recruitment",
     "description": "Whether participants formed a consecutive, random or convenience series"},
    # Methods — Test methods
    {"id": "10a", "section": "Methods", "topic": "Test methods — Index test",
     "description": "Index test, in sufficient detail to allow replication"},
    {"id": "10b", "section": "Methods", "topic": "Test methods — Reference standard",
     "description": "Reference standard, in sufficient detail to allow replication"},
    {"id": "11", "section": "Methods", "topic": "Test methods — Rationale",
     "description": "Rationale for choosing the reference standard (if alternatives exist)"},
    {"id": "12a", "section": "Methods", "topic": "Test methods — Index test thresholds",
     "description": "Definition of and rationale for test positivity cut-offs or result categories of the index test, distinguishing pre-specified from exploratory"},
    {"id": "12b", "section": "Methods", "topic": "Test methods — Reference standard thresholds",
     "description": "Definition of and rationale for test positivity cut-offs or result categories of the reference standard, distinguishing pre-specified from exploratory"},
    {"id": "13a", "section": "Methods", "topic": "Test methods — Index test blinding",
     "description": "Whether clinical information and reference standard results were available to the performers/readers of the index test"},
    {"id": "13b", "section": "Methods", "topic": "Test methods — Reference standard blinding",
     "description": "Whether clinical information and index test results were available to the assessors of the reference standard"},
    # Methods — Analysis
    {"id": "14", "section": "Methods", "topic": "Analysis — Estimates",
     "description": "Methods for estimating or comparing measures of diagnostic accuracy"},
    {"id": "15", "section": "Methods", "topic": "Analysis — Indeterminate results",
     "description": "How indeterminate index test or reference standard results were handled"},
    {"id": "16", "section": "Methods", "topic": "Analysis — Missing data",
     "description": "How missing data on the index test and reference standard were handled"},
    {"id": "17", "section": "Methods", "topic": "Analysis — Subgroups",
     "description": "Any analyses of variability in diagnostic accuracy, distinguishing pre-specified from exploratory"},
    {"id": "18", "section": "Methods", "topic": "Analysis — Sample size",
     "description": "Intended sample size and how it was determined"},
    # Results — Participants
    {"id": "19", "section": "Results", "topic": "Participants — Flow",
     "description": "Flow of participants, using a diagram"},
    {"id": "20", "section": "Results", "topic": "Participants — Baseline",
     "description": "Baseline demographic and clinical characteristics of participants"},
    {"id": "21a", "section": "Results", "topic": "Participants — Distribution by reference standard",
     "description": "Distribution of severity of disease in those with the target condition"},
    {"id": "21b", "section": "Results", "topic": "Participants — Distribution of alternative diagnoses",
     "description": "Distribution of alternative diagnoses in those without the target condition"},
    {"id": "22", "section": "Results", "topic": "Participants — Time interval",
     "description": "Time interval and any clinical interventions between index test and reference standard"},
    # Results — Test results
    {"id": "23", "section": "Results", "topic": "Test results — Cross-tabulation",
     "description": "Cross tabulation of the index test results (or their distribution) by the results of the reference standard"},
    {"id": "24", "section": "Results", "topic": "Test results — Estimates",
     "description": "Estimates of diagnostic accuracy and their precision (such as 95% confidence intervals)"},
    {"id": "25", "section": "Results", "topic": "Test results — Adverse events",
     "description": "Any adverse events from performing the index test or the reference standard"},
    # Discussion
    {"id": "26", "section": "Discussion", "topic": "Limitations",
     "description": "Study limitations, including sources of potential bias, statistical uncertainty, and generalisability"},
    {"id": "27", "section": "Discussion", "topic": "Implications",
     "description": "Implications for practice, including the intended use and clinical role of the index test"},
    # Other information
    {"id": "28", "section": "Other information", "topic": "Registration",
     "description": "Registration number and name of registry"},
    {"id": "29", "section": "Other information", "topic": "Protocol",
     "description": "Where the full study protocol can be accessed"},
    {"id": "30", "section": "Other information", "topic": "Funding",
     "description": "Sources of funding and other support; role of funders"},
]


_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing adherence of a "
    "diagnostic test accuracy study report to the STARD 2015 checklist. Read "
    "the PDF carefully. For each checklist item, decide whether the paper "
    "reports the required information. Be strict but fair: an item is "
    "adhered only if the information is actually present (not merely "
    "referenced as 'available elsewhere' unless the paper provides a usable "
    "pointer). If an item is genuinely not applicable to this study (e.g., "
    "item 25 'adverse events' for a non-invasive imaging study where adverse "
    "events would be impossible), mark it N/A. "
    "Return ONLY a valid JSON object — no preamble, no markdown fences."
)


def build_prompt(classification: dict[str, str],
                 extracted_fields: dict[str, str] | None = None) -> str:
    """Assemble the single-call STARD 2015 prompt covering all 32 items."""
    study_type = classification.get("study_type", "Diagnostic Accuracy")
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

    return f"""Assess this **{study_type}** report against the STARD 2015 checklist.

Context (fields already extracted from the paper):
{ctx_json}

STARD 2015 items:
{items_block}

For each item, return:
- ``adhered = true`` if the paper reports the required information,
- ``adhered = false`` if the paper should report it but does not,
- ``adhered = null`` if the item is legitimately not applicable to this study
  (e.g., item 25 'adverse events' for an entirely non-invasive imaging study;
  item 28 'registration' for a retrospective records review where registration
  would not have been possible).
- ``evidence`` is a brief quote (≤ 25 words) from the paper, or a one-line
  reason for a false/null judgement.

Return a JSON object with exactly this shape:
{shape}

Return only the JSON object."""


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str]) -> dict[str, Any]:
    """Run STARD 2015 adherence check. Returns
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
        "guideline": "STARD 2015",
        "citation": "Bossuyt PM et al. BMJ 2015;351:h5527. https://doi.org/10.1136/bmj.h5527",
        "system_prompt": _SYSTEM_PROMPT,
        "items": ITEMS,
        "prompt_template": build_prompt(
            {"study_type": "Diagnostic Accuracy"},
            {"(example field)": "<value>"},
        ),
        "scoring_code": inspect.getsource(run),
    }
