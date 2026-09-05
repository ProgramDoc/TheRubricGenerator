"""Published-data checkpoints; these test the numerical path, not AI accuracy.

Version-pinned Cochrane sources: CD000980.pub4 Analysis 1.1 subgroup 2;
CD007094.pub5 Analysis 1.3.1. Honey's displayed SEs are rounded: the CI
endpoint discrepancy must remain visible rather than be fitted away.
"""
import json
import math

import pytest

from backend import synthesis as syn, synthesis_stats as ss, synthesis_codegen as cg


VITAMIN_C = [(7, 44, 19, 47), (6, 56, 14, 56), (4, 13, 13, 19),
             (14, 43, 28, 41), (17, 139, 31, 140)]


def vitamin_rows():
    return [dict(zip(("events1", "total1", "events2", "total2"), r)) for r in VITAMIN_C]


def persisted_pool(monkeypatch, measure, rows, **options):
    written = {}
    monkeypatch.setattr(syn, "_write_result_row", lambda *a, **kw: written.update(kw))
    points = [dict(study_id=i, raw_json=json.dumps(raw),
                   **syn.compute_effect_for_point(measure, raw)) for i, raw in enumerate(rows)]
    outcome = dict(id=1, name="Published checkpoint", effect_measure=measure,
                   model_choice="fixed", **options)
    studies = {i: {"filename": str(i), "study_type": "Randomized Controlled Trial"}
               for i in range(len(rows))}
    result = syn.pool_outcome(None, 1, outcome, points, studies)
    assert result["status"] == "ok"
    return written


def test_persisted_binary_counts_pool_with_mh_and_keep_sample_sizes(monkeypatch):
    out = persisted_pool(monkeypatch, "RR", vitamin_rows(), fe_method="MH")
    assert math.exp(out["fixed"]["estimate"]) == pytest.approx(0.475242155, abs=1e-9)
    assert math.exp(out["fixed"]["ci_low"]) == pytest.approx(0.354907261, abs=1e-9)
    assert math.exp(out["fixed"]["ci_high"]) == pytest.approx(0.636377812, abs=1e-9)
    assert out["fixed"]["weights_pct"] == pytest.approx(
        [17.92694395, 13.65964509, 10.30571438, 27.96974947, 30.13794711], abs=1e-7)
    assert sum(r["n"] for r in out["forest"]["studies"]) == 598


@pytest.mark.parametrize("measure, expected", [
    ("OR", [4.5, 8]), ("RR", [5, 10]), ("RD", [50, 50]),
])
def test_mh_weights_follow_measure_and_reach_forest(measure, expected):
    effects = [ss.effect_size(measure, r) for r in [
        dict(events1=10, total1=100, events2=10, total2=100),
        dict(events1=20, total1=100, events2=20, total2=100)]]
    out = ss.pool(effects, measure, model="fixed", fe_method="MH")
    assert out["fixed"]["weights"] == pytest.approx(expected)
    assert out["fixed"]["weights_pct"] == pytest.approx([100*x/sum(expected) for x in expected])


def test_honey_reported_md_se_survives_extraction_storage_and_pool(monkeypatch):
    raw_rows = [dict(estimate=-0.97, se=0.282, n1=35, n2=39),
                dict(estimate=-1.18, se=0.345, n1=40, n2=40)]
    monkeypatch.setattr(syn.annotator_mod, "_call_with_pdf", lambda *a, **k: {"data_points": raw_rows})
    extracted = syn.extract_outcome_data(b"pdf", "cough frequency", "MD", {})
    assert not any(r["needs_review"] for r in extracted)
    assert all(r["raw"]["sd1"] is None for r in extracted)
    out = persisted_pool(monkeypatch, "MD", [r["raw"] for r in extracted], tau2_method="DL")
    assert out["fixed"]["estimate"] == pytest.approx(-1.054110421, abs=1e-9)
    assert out["fixed"]["ci_high"] == pytest.approx(-0.626170855, abs=1e-9)
    assert round(out["fixed"]["ci_high"], 2) != -0.62  # published rounded endpoint
    assert sum(r["n"] for r in out["forest"]["studies"]) == 154
    assert "m <- metagen(" in out["r_code"]
    assert 'sm = "MD"' in out["r_code"]
    assert "metacont(" not in out["r_code"]


@pytest.mark.parametrize("se", [None, 0, -0.1, float("inf"), float("nan")])
def test_generic_md_rejects_missing_or_invalid_precision(se):
    assert ss.effect_size("MD", {"estimate": -1, "se": se}) is None


def test_complete_arm_md_takes_precedence_and_mixed_export_uses_metagen():
    row = dict(m1=10, sd1=2, n1=50, m2=8, sd2=3, n2=50, estimate=99, se=0.1)
    effect = ss.effect_size("MD", row)
    assert effect.yi == 2
    assert effect.vi == pytest.approx(0.26)
    studies = [dict(raw=row, yi=effect.yi, vi=effect.vi),
               dict(raw={"estimate": 1, "se": 0.2}, yi=1, vi=0.04)]
    code = cg.r_code_for({"effect_measure": "MD"}, studies)
    assert "m <- metagen(" in code
    assert "TE = c(2.0, 1.0)" in code
