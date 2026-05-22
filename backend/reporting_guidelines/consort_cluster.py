"""CONSORT cluster extension — reporting-guideline checklist for cluster-randomized RCTs.

Sources combined:

- Hopewell S, Chan AW, Collins GS, Hróbjartsson A, Moher D, Schulz KF, et al.
  "CONSORT 2025 Statement: updated guideline for reporting randomised trials."
  BMJ. 2025; 388:e081123. https://dx.doi.org/10.1136/bmj-2024-081123
  (Base 30 items reused from :mod:`backend.reporting_guidelines.consort2025`.)
- Campbell MK, Piaggio G, Elbourne DR, Altman DG, for the CONSORT Group.
  "Consort 2010 statement: extension to cluster randomised trials."
  BMJ 2012; 345: e5661. https://doi.org/10.1136/bmj.e5661
  (Cluster-specific extension items.)

The cluster extension to CONSORT 2010 is the authoritative reporting guideline
for cluster-randomised trials. A CONSORT 2025 cluster extension has not yet been
published; we combine the 2025 base checklist with the cluster extension items,
framing the latter in the same checklist-item shape so they fit the same scoring
pipeline (one LLM call → per-item adhered/evidence).

Items legitimately not applicable to a given trial return ``adhered=null`` so
they don't inflate or deflate the proportion.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..annotator import _call_with_pdf
from . import consort2025

logger = logging.getLogger("rubricgen")


# Cluster-specific extension items, adapted from Campbell et al. 2012.
# Numbered with a "C-" prefix so they sort visibly distinct from base items
# (and won't ever collide with future CONSORT 2025 item IDs).
CLUSTER_EXTENSION_ITEMS: list[dict[str, str]] = [
    {"id": "C-1", "section": "Title and abstract", "topic": "Cluster design identification",
     "description": "Identification as a cluster-randomised (or cluster) trial in the title and/or abstract"},
    {"id": "C-2", "section": "Introduction", "topic": "Rationale for cluster design",
     "description": "Rationale for using a cluster design (why randomisation was at the cluster rather than the individual level)"},
    {"id": "C-3", "section": "Methods", "topic": "Definition of cluster",
     "description": "Definition of the cluster and description of the cluster-randomised design (how the design was applied, including how clusters were formed)"},
    {"id": "C-4", "section": "Methods", "topic": "Eligibility — clusters and individuals",
     "description": "Eligibility criteria for clusters AND eligibility criteria for individual participants within clusters"},
    {"id": "C-5", "section": "Methods", "topic": "Intervention level",
     "description": "Whether interventions pertain to the cluster level, the individual participant level, or both"},
    {"id": "C-6", "section": "Methods", "topic": "Outcome level",
     "description": "Whether each outcome applies to (was measured at) the cluster level or the individual participant level"},
    {"id": "C-7", "section": "Methods", "topic": "Sample size — clustering",
     "description": "Sample size calculation accounting for clustering: number of clusters, cluster size, and the assumed intracluster correlation coefficient (ICC) or coefficient of variation"},
    {"id": "C-8", "section": "Methods", "topic": "Cluster as unit of randomisation",
     "description": "Clusters as the unit of randomisation — sequence generation, allocation concealment, and who generated the sequence, enrolled clusters, and assigned clusters to interventions"},
    {"id": "C-9", "section": "Methods", "topic": "Blinding at both levels",
     "description": "If done, who was blinded after assignment (those enrolling clusters, delivering the intervention, assessing outcomes, and the individual participants)"},
    {"id": "C-10", "section": "Methods", "topic": "Statistical methods — clustering",
     "description": "Statistical methods used to account for the clustered design (e.g., mixed/multilevel models, GEE, or a cluster-level summary analysis)"},
    {"id": "C-11", "section": "Results", "topic": "Flow — clusters and individuals",
     "description": "Flow diagram and counts for each arm at BOTH levels: clusters and individual participants randomised, receiving intervention, and analysed"},
    {"id": "C-12", "section": "Results", "topic": "Baseline data — both levels",
     "description": "Baseline characteristics presented for the clusters and for the individual participants in each arm"},
    {"id": "C-13", "section": "Results", "topic": "Numbers analysed + ICC",
     "description": "For each arm and each primary outcome, the number of clusters and individuals analysed, results at the appropriate level, and the estimated ICC"},
    {"id": "C-14", "section": "Discussion", "topic": "Generalisability — both levels",
     "description": "Generalisability (external validity) of the findings discussed for both clusters and individual participants"},
]


# Combined item list: base CONSORT 2025 items followed by cluster extension items.
ITEMS: list[dict[str, str]] = consort2025.ITEMS + CLUSTER_EXTENSION_ITEMS


_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing adherence of a "
    "**cluster-randomised** trial report to a combined checklist: the CONSORT "
    "2025 base items plus the CONSORT 2010 cluster-randomised-trial extension "
    "items (Campbell et al. 2012). Read the PDF carefully. For each checklist "
    "item, decide whether the trial report reports the required information. Be "
    "strict but fair: an item is adhered only if the information is actually "
    "present (not merely referenced as 'available elsewhere' unless the paper "
    "provides a usable pointer). If an item is genuinely not applicable to this "
    "trial, mark it N/A. Return ONLY a valid JSON object — no preamble, no "
    "markdown fences."
)


def build_prompt(classification: dict[str, str],
                 extracted_fields: dict[str, str] | None = None) -> str:
    """Assemble the single-call CONSORT prompt covering base + cluster items."""
    study_type = classification.get("study_type", "Cluster Randomized Trial")
    ctx_json = json.dumps(extracted_fields or {}, indent=2) if extracted_fields else "(no pre-extracted fields)"

    base_lines = []
    for it in consort2025.ITEMS:
        base_lines.append(
            f"- **{it['id']}** ({it['section']} — {it['topic']}): {it['description']}"
        )
    ext_lines = []
    for it in CLUSTER_EXTENSION_ITEMS:
        ext_lines.append(
            f"- **{it['id']}** ({it['section']} — {it['topic']}): {it['description']}"
        )

    shape_entries = []
    for it in ITEMS:
        shape_entries.append(
            f'  "{it["id"]}": {{"adhered": true|false|null, "evidence": "short quote or ... \'N/A\' if not applicable"}}'
        )
    shape = "{\n" + ",\n".join(shape_entries) + "\n}"

    return f"""Assess this **{study_type}** report against the combined CONSORT 2025 + cluster extension checklist.

Context (fields already extracted from the paper):
{ctx_json}

CONSORT 2025 base items:
{chr(10).join(base_lines)}

CONSORT cluster-randomised extension items (Campbell et al. 2012):
{chr(10).join(ext_lines)}

For each item, return:
- ``adhered = true`` if the paper reports the required information,
- ``adhered = false`` if the paper should report it but does not,
- ``adhered = null`` if the item is legitimately not applicable to this trial.
- ``evidence`` is a brief quote (≤ 25 words) from the paper, or a one-line
  reason for a false/null judgement.

Return a JSON object with exactly this shape:
{shape}

Return only the JSON object."""


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str]) -> dict[str, Any]:
    """Run combined CONSORT 2025 + cluster extension adherence check.

    Returns ``{items: {id: {adhered, evidence}}, adhered, applicable,
    proportion, total}``.
    """
    prompt = build_prompt(classification, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=12288)

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
        "guideline": "CONSORT 2025 + cluster-randomised extension (Campbell et al. 2012)",
        "citation": (
            "Hopewell S et al. BMJ 2025; 388:e081123 (base); "
            "Campbell MK, Piaggio G, Elbourne DR, Altman DG. BMJ 2012; 345: e5661 "
            "(cluster-randomised extension)."
        ),
        "system_prompt": _SYSTEM_PROMPT,
        "base_items": consort2025.ITEMS,
        "cluster_extension_items": CLUSTER_EXTENSION_ITEMS,
        "items": ITEMS,
        "prompt_template": build_prompt(
            {"study_type": "Cluster Randomized Trial"},
            {"(example field)": "<value>"},
        ),
        "scoring_code": inspect.getsource(run),
    }
