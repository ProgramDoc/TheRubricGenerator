"""Tests for the extraction -> pooling bridge (pooling_prep + pooling_extract).

Covers the pure regroup-and-route layer (no model): outcome-object -> study input
mapping, body grouping (design + timepoint separation, fuzzy outcome matching),
measure selection + exclusion of unreconcilable metrics, and the end-to-end
``pool_extractions``. The model-wired ``extract_outcome_data`` is tested with a
mocked PDF caller so it proves it shapes one study dict per paper.
"""

from __future__ import annotations

import pytest

from backend.synthesis import pooling_prep as pp


def _rct(author, year, oc):
    return {"citation_authors": author, "citation_year": year,
            "study_type": "Randomized Controlled Trial",
            "population_comparator": "placebo", "outcomes": [oc]}


# ─────────────────────────────────────────────
# Outcome object -> study input
# ─────────────────────────────────────────────
class TestOutcomeToStudyInput:
    def test_raw_binary_arms_preferred(self):
        si = pp.outcome_to_study_input(
            {"study_type": "RCT", "study_id": "S"},
            {"name": "death", "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100,
             "effect_metric": "RR", "effect_estimate": 0.6}, "RR")
        assert si["events_int"] == 12 and si["n_ctrl"] == 100
        assert "estimate" not in si          # raw arms win over the reported effect

    def test_reported_effect_when_no_arms(self):
        si = pp.outcome_to_study_input(
            {"study_id": "S"},
            {"name": "death", "effect_metric": "RR", "effect_estimate": 0.6,
             "ci_lower": 0.4, "ci_upper": 0.9}, "RR")
        assert si["estimate"] == 0.6 and si["ci_lower"] == 0.4

    def test_metric_mismatch_excluded(self):
        si = pp.outcome_to_study_input(
            {"study_id": "S"},
            {"name": "death", "effect_metric": "OR", "effect_estimate": 0.55}, "RR")
        assert si is None                    # OR can't join an RR body without counts

    def test_continuous_arms(self):
        si = pp.outcome_to_study_input(
            {"study_id": "S"},
            {"name": "bp", "mean_int": 10, "sd_int": 2, "n_int": 30,
             "mean_ctrl": 12, "sd_ctrl": 2.5, "n_ctrl": 30}, "MD")
        assert si["mean_int"] == 10 and si["sd_ctrl"] == 2.5

    def test_irr_maps_person_time_arms(self):
        si = pp.outcome_to_study_input(
            {"study_id": "S"},
            {"name": "infection", "events_int": 10, "time_int": 500,
             "events_ctrl": 20, "time_ctrl": 480}, "IRR")
        assert si["time_int"] == 500 and si["events_ctrl"] == 20
        assert "n_int" not in si                 # rate shape, not a 2x2

    def test_irr_body_rejects_2x2_counts(self):
        # events + total (no person-time) can't make an IRR -> not mapped for target IRR.
        si = pp.outcome_to_study_input(
            {"study_id": "S"},
            {"name": "infection", "events_int": 5, "n_int": 100,
             "events_ctrl": 9, "n_ctrl": 100}, "IRR")
        assert si is None

    def test_rate_arms_infer_irr_measure(self):
        assert pp._choose_measure(
            [({}, {"events_int": 5, "time_int": 100, "events_ctrl": 8, "time_ctrl": 90})],
            None) == "IRR"


# ─────────────────────────────────────────────
# Design classification
# ─────────────────────────────────────────────
class TestDesignClass:
    def test_randomized_variants(self):
        for d in ("Randomized Controlled Trial", "RCT", "Cluster Randomized Trial",
                  "Crossover Trial"):
            assert pp._design_class(d) == "rct"

    def test_non_randomized_variants(self):
        for d in ("Cohort Study", "Case-Control", "Single-Arm Trial", "observational"):
            assert pp._design_class(d) == "nrs"

    def test_unknown(self):
        assert pp._design_class(None) == "unknown"
        assert pp._design_class("Editorial") == "unknown"


# ─────────────────────────────────────────────
# Grouping into bodies
# ─────────────────────────────────────────────
class TestGrouping:
    def test_fuzzy_outcome_name_groups_together(self):
        studies = [
            _rct("Smith", 2019, {"name": "All-cause mortality", "comparison": "d vs p",
                                 "timing": "12m", "effect_metric": "RR", "effect_estimate": 0.6}),
            _rct("Jones", 2020, {"name": "all cause mortality", "comparison": "d vs p",
                                 "timing": "12m", "effect_metric": "RR", "effect_estimate": 0.7}),
        ]
        bodies = pp.group_into_bodies(studies)
        assert len(bodies) == 1 and len(bodies[0]["members"]) == 2

    def test_design_splits_bodies(self):
        studies = [
            _rct("Smith", 2019, {"name": "death", "comparison": "d vs p", "timing": "12m",
                                 "effect_metric": "RR", "effect_estimate": 0.6}),
            {"citation_authors": "Park", "citation_year": 2018, "study_type": "Cohort Study",
             "population_comparator": "placebo",
             "outcomes": [{"name": "death", "comparison": "d vs p", "timing": "12m",
                           "effect_metric": "RR", "effect_estimate": 0.7}]},
        ]
        bodies = pp.group_into_bodies(studies)
        assert {b["design_class"] for b in bodies} == {"rct", "nrs"}

    def test_timepoint_splits_by_default_but_can_merge(self):
        studies = [
            _rct("Smith", 2019, {"name": "death", "comparison": "d vs p", "timing": "6m",
                                 "effect_metric": "RR", "effect_estimate": 0.6}),
            _rct("Jones", 2020, {"name": "death", "comparison": "d vs p", "timing": "12m",
                                 "effect_metric": "RR", "effect_estimate": 0.7}),
        ]
        assert len(pp.group_into_bodies(studies)) == 2
        assert len(pp.group_into_bodies(studies, include_timepoint=False)) == 1


# ─────────────────────────────────────────────
# Measure selection
# ─────────────────────────────────────────────
class TestMeasureSelection:
    def test_majority_reported_metric(self):
        members = [
            ({}, {"effect_metric": "RR"}), ({}, {"effect_metric": "RR"}),
            ({}, {"effect_metric": "OR"}),
        ]
        assert pp._choose_measure(members, None) == "RR"

    def test_override_wins(self):
        members = [({}, {"effect_metric": "RR"})]
        assert pp._choose_measure(members, "odds ratio") == "OR"

    def test_inferred_from_binary_arms(self):
        members = [({}, {"events_int": 5, "n_int": 50, "events_ctrl": 8, "n_ctrl": 50})]
        assert pp._choose_measure(members, None) == "RR"


# ─────────────────────────────────────────────
# End-to-end pool_extractions
# ─────────────────────────────────────────────
class TestPoolExtractions:
    STUDIES = [
        _rct("Smith", 2019, {"name": "All-cause mortality", "comparison": "d vs p", "timing": "12m",
                             "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}),
        _rct("Jones", 2020, {"name": "all cause mortality", "comparison": "d vs p", "timing": "12m",
                             "effect_metric": "RR", "effect_estimate": 0.6, "ci_lower": 0.4, "ci_upper": 0.9}),
        _rct("Lee", 2021, {"name": "All cause mortality", "comparison": "d vs p", "timing": "12m",
                           "events_int": 8, "n_int": 90, "events_ctrl": 18, "n_ctrl": 95}),
        _rct("Kim", 2022, {"name": "all-cause mortality", "comparison": "d vs p", "timing": "12m",
                           "effect_metric": "OR", "effect_estimate": 0.55}),
    ]

    def test_pools_rr_body_and_excludes_or(self):
        bodies = pp.pool_extractions(self.STUDIES)
        assert len(bodies) == 1
        b = bodies[0]
        assert b["measure"] == "RR" and b["k"] == 3
        assert 0.4 < b["pooled"]["random"]["estimate"] < 0.8
        assert any("Kim" in e and "OR" in e for e in b["excluded"])

    def test_min_studies_filters_singletons(self):
        one = [self.STUDIES[0]]
        assert pp.pool_extractions(one, min_studies=2) == []

    def test_forced_measure_via_default(self):
        bodies = pp.pool_extractions(self.STUDIES, default_measure="RR")
        assert bodies[0]["measure"] == "RR"


# ─────────────────────────────────────────────
# Conservative unknown-design handling + favorable_direction
# ─────────────────────────────────────────────
class TestUnknownDesignAndDirection:
    def _study(self, design, name="death", fd=None, est=0.6):
        oc = {"name": name, "comparison": "d vs p", "timing": "12m",
              "effect_metric": "RR", "effect_estimate": est,
              "ci_lower": est * 0.7, "ci_upper": est * 1.3}
        if fd:
            oc["favorable_direction"] = fd
        return {"citation_authors": "X", "study_type": design,
                "population_comparator": "placebo", "outcomes": [oc]}

    def test_unknown_design_flagged(self):
        studies = [self._study("Registry analysis"), self._study("Registry analysis")]
        b = pp.pool_extractions(studies)[0]
        assert b["design_class"] == "unknown"
        assert b["warnings"] and "unclassified study design" in b["warnings"][0]
        assert "Registry analysis" in b["warnings"][0]

    def test_unknown_never_merges_with_rct(self):
        studies = [_rct("Smith", 2019, {"name": "death", "comparison": "d vs p", "timing": "12m",
                                        "effect_metric": "RR", "effect_estimate": 0.6}),
                   self._study("Registry analysis")]
        classes = {b["design_class"] for b in pp.pool_extractions(studies)}
        assert classes == {"rct", "unknown"}

    def test_known_designs_have_no_unknown_warning(self):
        studies = [self._study("Randomized Controlled Trial"),
                   self._study("Randomized Controlled Trial")]
        b = pp.pool_extractions(studies)[0]
        assert b["warnings"] == []

    def test_favorable_direction_propagates(self):
        studies = [self._study("RCT", fd="higher"), self._study("RCT", fd="higher")]
        b = pp.pool_extractions(studies)[0]
        assert b["favorable_direction"] == "higher"
        assert b["pooled"]["favorable_direction"] == "higher"

    def test_favorable_direction_defaults_to_lower(self):
        b = pp.pool_extractions([self._study("RCT"), self._study("RCT")])[0]
        assert b["favorable_direction"] == "lower"


# ─────────────────────────────────────────────
# Model-wired outcome-data extraction (mocked PDF caller)
# ─────────────────────────────────────────────
class TestOutcomeDataExtraction:
    def test_shapes_one_study_dict(self, monkeypatch):
        import backend.synthesis.pooling_extract as pe

        def fake_call(pdf_bytes, prompt, max_tokens=None):
            return {"study_type": "Randomized Controlled Trial", "citation_authors": "Smith",
                    "outcomes": [{"name": "death", "comparison": "d vs p",
                                  "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}]}
        monkeypatch.setattr(pe.annotator_mod, "_call_with_pdf", fake_call)
        study = pe.extract_outcome_data(b"%PDF-fake", injected={"citation_year": 2019})
        assert study["study_type"] == "Randomized Controlled Trial"
        assert study["citation_year"] == 2019          # injected fills through
        assert len(study["outcomes"]) == 1 and study["outcomes"][0]["events_int"] == 12

    def test_bad_response_yields_empty_outcomes(self, monkeypatch):
        import backend.synthesis.pooling_extract as pe
        monkeypatch.setattr(pe.annotator_mod, "_call_with_pdf",
                            lambda *a, **k: "not a dict")
        study = pe.extract_outcome_data(b"%PDF-fake")
        assert study["outcomes"] == []

    def test_pool_from_pdfs_end_to_end(self, monkeypatch):
        import backend.synthesis.pooling_extract as pe

        def fake_call(pdf_bytes, prompt, max_tokens=None):
            return {"study_type": "RCT", "outcomes": [
                {"name": "death", "comparison": "d vs p", "timing": "12m",
                 "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}]}
        monkeypatch.setattr(pe.annotator_mod, "_call_with_pdf", fake_call)
        papers = [{"pdf_bytes": b"%PDF-1", "injected": {"citation_authors": "A", "citation_year": 2019}},
                  {"pdf_bytes": b"%PDF-2", "injected": {"citation_authors": "B", "citation_year": 2020}}]
        bodies = pe.pool_from_pdfs(papers)
        assert len(bodies) == 1 and bodies[0]["k"] == 2
        assert bodies[0]["pooled"]["measure"] == "RR"


# ─────────────────────────────────────────────
# Dual-mode: injected-first, self-extract the gaps
# ─────────────────────────────────────────────
class TestDualMode:
    def test_study_is_poolable(self):
        assert pp.study_is_poolable(_rct("A", 2019, {"name": "d", "effect_metric": "RR", "effect_estimate": 0.6})) is True
        assert pp.study_is_poolable(_rct("A", 2019, {"name": "d", "events_int": 5, "n_int": 50, "events_ctrl": 8, "n_ctrl": 50})) is True
        # No arm data and no reported estimate -> not poolable.
        assert pp.study_is_poolable(_rct("A", 2019, {"name": "d", "effect_metric": "RR"})) is False
        assert pp.study_is_poolable({"outcomes": []}) is False

    def test_injected_study_uses_no_model_call(self, monkeypatch):
        import backend.synthesis.pooling_extract as pe
        calls = {"n": 0}

        def spy(*a, **k):
            calls["n"] += 1
            return {}
        monkeypatch.setattr(pe.annotator_mod, "_call_with_pdf", spy)
        item = _rct("Smith", 2019, {"name": "death", "comparison": "d vs p", "timing": "12m",
                              "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100})
        study = pe.prepare_study(item)
        assert calls["n"] == 0                          # injected -> ZERO model calls
        assert study["outcomes"][0]["events_int"] == 12

    def test_missing_extraction_triggers_self_extract(self, monkeypatch):
        import backend.synthesis.pooling_extract as pe
        calls = {"n": 0}

        def fake_call(pdf_bytes, prompt, max_tokens=None):
            calls["n"] += 1
            return {"study_type": "RCT", "outcomes": [
                {"name": "death", "comparison": "d vs p", "timing": "12m",
                 "events_int": 9, "n_int": 80, "events_ctrl": 16, "n_ctrl": 82}]}
        monkeypatch.setattr(pe.annotator_mod, "_call_with_pdf", fake_call)
        # No outcomes, but a PDF is available -> the agent self-extracts.
        item = {"citation_authors": "Jones", "study_type": "RCT", "pdf_bytes": b"%PDF"}
        study = pe.prepare_study(item)
        assert calls["n"] == 1
        assert study["outcomes"][0]["events_int"] == 9
        assert study["citation_authors"] == "Jones"     # priming tags carried in

    def test_force_extract_overrides_injected(self, monkeypatch):
        import backend.synthesis.pooling_extract as pe
        calls = {"n": 0}
        monkeypatch.setattr(pe.annotator_mod, "_call_with_pdf",
                            lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {"outcomes": []})
        item = _rct("Smith", 2019, {"name": "death", "effect_metric": "RR", "effect_estimate": 0.6})
        item["pdf_bytes"] = b"%PDF"
        pe.prepare_study(item, force_extract=True)
        assert calls["n"] == 1                          # re-extract even though injected was poolable

    def test_pool_studies_mixed_batch(self, monkeypatch):
        import backend.synthesis.pooling_extract as pe

        def fake_call(pdf_bytes, prompt, max_tokens=None):
            return {"study_type": "RCT", "outcomes": [
                {"name": "All-cause mortality", "comparison": "d vs p", "timing": "12m",
                 "events_int": 9, "n_int": 80, "events_ctrl": 16, "n_ctrl": 82}]}
        monkeypatch.setattr(pe.annotator_mod, "_call_with_pdf", fake_call)
        items = [
            # injected (no PDF) — used directly
            _rct("Smith", 2019, {"name": "All-cause mortality", "comparison": "d vs p", "timing": "12m",
                           "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}),
            # only a PDF — self-extracted
            {"citation_authors": "Jones", "study_type": "RCT", "population_comparator": "placebo",
             "pdf_bytes": b"%PDF"},
        ]
        bodies = pe.pool_studies(items)
        assert len(bodies) == 1 and bodies[0]["k"] == 2   # one from injected, one self-extracted

    def test_prompt_includes_person_time_fields(self):
        import backend.synthesis.pooling_extract as pe
        # The extraction prompt schema must carry person-time so IRR self-extracts.
        assert "time_int" in pe._OUTCOME_DATA_PROMPT
        assert "time_ctrl" in pe._OUTCOME_DATA_PROMPT
        assert "person-time" in pe._OUTCOME_DATA_PROMPT

    def test_self_extracted_rate_outcome_pools_as_irr(self, monkeypatch):
        import backend.synthesis.pooling_extract as pe

        def fake_call(pdf_bytes, prompt, max_tokens=None):
            return {"study_type": "RCT", "outcomes": [
                {"name": "Infection rate", "comparison": "d vs p", "timing": "12m",
                 "outcome_type": "rate",
                 "events_int": 10, "time_int": 500, "events_ctrl": 20, "time_ctrl": 480}]}
        monkeypatch.setattr(pe.annotator_mod, "_call_with_pdf", fake_call)
        papers = [{"pdf_bytes": b"%PDF-1", "injected": {"citation_authors": "A"}},
                  {"pdf_bytes": b"%PDF-2", "injected": {"citation_authors": "B"}}]
        bodies = pe.pool_from_pdfs(papers)
        assert len(bodies) == 1
        assert bodies[0]["measure"] == "IRR" and bodies[0]["k"] == 2
