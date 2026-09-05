"""Bridge production synthesis outputs to the platform's existing GRADE agent.

No reference labels or published values are accepted here. Meta-analysis estimates,
intervals and aligned study weights are copied exactly, with explicit scale/key
conversion; there is no second pooling pass. Both engine outputs are retained.
"""
from __future__ import annotations

import json
import logging

from backend import synthesis as syn
from backend.evidence_synthesis import grade_agent, grade_assess
from backend.evidence_synthesis.pooling_prep import _design_class

logger = logging.getLogger(__name__)
CONTEXT_FIELDS = ("population_participants", "population_intervention_exposure", "population_comparator",
                  "population_outcomes", "setting", "country_region", "primary_outcome_definition",
                  "primary_outcome_measurement", "primary_outcome_timing", "inclusion_exclusion_criteria")


def collect_context(fields):
    return {k: fields[k] for k in CONTEXT_FIELDS if fields.get(k)}


def prepare_body(snapshot, oc, contexts):
    row = next((r for r in snapshot["results"] if r["outcome_id"] == oc["id"]), {})
    pool = row.get(oc["model_choice"]) or {}
    if row.get("status") != "ok" or pool.get("status") != "ok":
        return None, ["No estimate from the requested pooling model; GRADE not computed"]
    pts = [p for p in snapshot["data_points"] if p["outcome_id"] == oc["id"]
           and p.get("included_in_pool") and p.get("yi") is not None and p.get("vi")]
    if len({p["study_id"] for p in pts}) != len(pts):
        return None, ["Dependent pooled rows from one study must be reconciled before GRADE"]
    studies = {s["id"]: s for s in snapshot["studies"]}
    classes = {_design_class(studies[p["study_id"]].get("study_type")) for p in pts}
    if len(classes) != 1 or "unknown" in classes:
        return None, ["Mixed or unsupported study designs require separate GRADE bodies"]
    weights = pool.get("weights_pct") or []
    if len(weights) != len(pts):
        return None, ["Study weights do not align with the pooled data; GRADE not computed"]
    robs = {(r["study_id"], r["outcome_id"]): r for r in snapshot.get("study_rob", [])}
    study_records, study_context, warnings = [], [], []
    for p, weight in zip(pts, weights):
        sid = p["study_id"]
        rob, _ = syn._resolve_study_rob(studies[sid], robs.get((sid, oc["id"])), "outcome")
        study_records.append({"study_id": str(sid), "weight_pct": weight, "rob": rob,
                              "design": studies[sid].get("study_type"), "yi": p["yi"], "vi": p["vi"]})
        study_context.append({"study_id": sid, "extracted_pico": contexts.get(sid, {}),
                              "outcome_context": p.get("context_label"), "source_quote": p.get("source_quote")})
    if any(not r["rob"] for r in study_records):
        warnings.append("Incomplete RoB coverage: the GRADE engine renormalizes weights over assessed studies")
    if any(not c["extracted_pico"] for c in study_context):
        warnings.append("Some pooled studies lack extracted PICO context for indirectness")
    if len(json.dumps(study_context, indent=2)) > 6000:
        warnings.append("The GRADE agent truncates study context at 6,000 characters; indirectness coverage is limited")
    totals = {}
    for dest, sources in (("n_int", ("total1", "n1")), ("n_ctrl", ("total2", "n2")),
                          ("events_int", ("events1", "a")), ("events_ctrl", ("events2", "c"))):
        vals = [next((p["raw"][k] for k in sources if p.get("raw", {}).get(k) is not None), None) for p in pts]
        # Partial totals would overstate imprecision. Unknown totals remain unknown.
        totals[dest] = sum(vals) if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals) else None
    if totals["n_int"] is None or totals["n_ctrl"] is None:
        warnings.append("Complete arm sample sizes are unavailable for the GRADE information-size check")
    het = row.get("heterogeneity") or {}
    pb = row.get("publication_bias") or {}
    egger = pb.get("egger") or None
    if egger is not None:
        egger = {**egger, "adequate_power": not egger.get("underpowered", len(pts) < 10)}
    measure = oc["effect_measure"]
    pr = {"measure": measure, "k": len(pts), "model": oc["model_choice"], "design_class": next(iter(classes)),
          "outcome_name": oc["name"], "studies": study_records, "totals": totals,
          "pooled": {"yi": pool["estimate"], "estimate": syn.stats.back_transform(pool["estimate"], measure),
                     "ci_lower": syn.stats.back_transform(pool["ci_low"], measure),
                     "ci_upper": syn.stats.back_transform(pool["ci_high"], measure)},
          "heterogeneity": {"i2": het.get("I2"), "q_p": het.get("p"), "tau2": pool.get("tau2")},
          "publication_bias": {"egger": egger, "trim_fill": pb.get("trimfill")}}
    return {"outcome_name": oc["name"], "measure": measure, "k": len(pts), "design_class": pr["design_class"],
            "pooled": pr, "warnings": warnings, "study_context": study_context}, warnings


def run_grade(conn, snapshot, contexts, user_id, is_admin, protocol):
    target = {k: v for k, v in protocol["pico"].items() if k in ("population", "intervention", "comparator")}
    gid = grade_agent.create_run(conn, user_id, name=f"Simulator synthesis {snapshot['id']}", project_id=None,
                                 target_pico=target, auto_indirectness=True, n_bodies=len(snapshot["outcomes"]))
    results, refund = {}, 0
    for index, oc in enumerate(snapshot["outcomes"]):
        body, warnings = prepare_body(snapshot, oc, contexts)
        descriptor = {"outcome_name": oc["name"], "measure": oc["effect_measure"], "grade": None, "warnings": warnings}
        if body:
            study_context = body.pop("study_context")
            try:
                parameters = {k: protocol["outcomes"][index][k] for k in ("mid_benefit", "mid_harm", "baseline_risk_per_1000")
                              if protocol["outcomes"][index].get(k) is not None}
                descriptor = grade_assess.grade_from_pooled(
                    [body], target_pico={**target, "outcome": oc["name"]}, auto_indirectness=True,
                    judgments_by_outcome={oc["name"].strip().lower(): {"study_context": study_context, **parameters}})[0]
                # Preserve engine fallback labels so evaluation can expose them, and flag missing assessment.
                if not descriptor.get("indirectness_detail"):
                    descriptor["warnings"].append("Indirectness auto-assessment failed; the engine used its zero-downgrade fallback")
                descriptor["pool_input"] = body["pooled"]
                descriptor["study_context"] = study_context
            except Exception:
                logger.exception("Simulator GRADE failed for outcome %s", oc["id"])
                descriptor["warnings"].append("GRADE agent failed for this outcome")
        if not descriptor.get("indirectness_detail"):
            refund += grade_agent.CREDIT_COST_GRADE_INDIRECTNESS
        descriptor["grade_run_id"] = gid
        grade_agent.save_result(conn, gid, descriptor)
        syn.log_event(conn, snapshot["id"], "progress",
                       f"GRADE: {oc['name']} — {(descriptor.get('grade') or {}).get('final') or 'not rated'}")
        results[oc["id"]] = descriptor
    grade_agent.finalize_run(conn, gid, "complete" if all(r.get("grade") for r in results.values()) else "partial")
    if refund and not is_admin:
        syn._refund(conn, user_id, refund, f"Simulator synthesis {snapshot['id']}: unavailable indirectness")
    return results
