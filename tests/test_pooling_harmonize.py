"""Tests for outcome harmonization (pooling_harmonize + the LLM clusterer).

Covers the pure dictionary/alias layer (no model): alias-index building, fuzzy
matching, applying a canonical map, target-driven harmonization end-to-end into
grouping, and the LLM clusterer's parsing (mocked text caller).
"""

from __future__ import annotations

from backend.evidence_synthesis import pooling_harmonize as ph
from backend.evidence_synthesis.pooling_prep import group_into_bodies, pool_extractions


def _rct(author, name, est=0.6):
    return {"citation_authors": author, "study_type": "RCT", "population_comparator": "placebo",
            "outcomes": [{"name": name, "comparison": "drug vs placebo", "timing": "12m",
                          "effect_metric": "RR", "effect_estimate": est,
                          "ci_lower": est * 0.7, "ci_upper": est * 1.3}]}


# ─────────────────────────────────────────────
# Alias index + matching
# ─────────────────────────────────────────────
class TestAliasIndex:
    def test_build_from_strings_and_dicts(self):
        idx = ph.build_alias_index([
            "Overall survival",
            {"canonical": "All-cause mortality", "aliases": ["death from any cause", "overall mortality"]},
        ])
        assert idx["overall survival"] == "Overall survival"
        assert idx["death from any cause"] == "All-cause mortality"
        assert idx["all cause mortality"] == "All-cause mortality"   # canonical maps to itself

    def test_exact_normalized_match(self):
        idx = ph.build_alias_index([{"canonical": "All-cause mortality", "aliases": []}])
        assert ph.match_outcome_name("all-cause mortality", idx) == "All-cause mortality"

    def test_alias_match(self):
        idx = ph.build_alias_index(
            [{"canonical": "All-cause mortality", "aliases": ["death from any cause"]}])
        assert ph.match_outcome_name("Death from any cause", idx) == "All-cause mortality"

    def test_fuzzy_subset_match(self):
        idx = ph.build_alias_index([{"canonical": "All-cause mortality",
                                     "aliases": ["all cause mortality"]}])
        assert ph.match_outcome_name("all-cause mortality (any)", idx) == "All-cause mortality"

    def test_no_false_merge_of_distinct_outcomes(self):
        idx = ph.build_alias_index(["Overall survival", "Progression-free survival"])
        # "progression free survival" must not resolve to "overall survival".
        assert ph.match_outcome_name("progression free survival", idx) == "Progression-free survival"

    def test_unmatched_returns_none(self):
        idx = ph.build_alias_index([{"canonical": "All-cause mortality", "aliases": []}])
        assert ph.match_outcome_name("tumor response rate", idx) is None

    def test_fuzzy_off(self):
        idx = ph.build_alias_index([{"canonical": "All-cause mortality",
                                     "aliases": ["all cause mortality"]}])
        assert ph.match_outcome_name("all-cause mortality extended", idx, fuzzy=False) is None


# ─────────────────────────────────────────────
# Applying a map + distinct names
# ─────────────────────────────────────────────
class TestApply:
    def test_apply_annotates_without_mutating(self):
        studies = [_rct("Smith", "Death from any cause")]
        out = ph.apply_canonical_map(studies, {"death from any cause": "All-cause mortality"})
        assert out[0]["outcomes"][0]["canonical_outcome"] == "All-cause mortality"
        assert "canonical_outcome" not in studies[0]["outcomes"][0]   # original untouched

    def test_distinct_names_skips_resolved(self):
        studies = [_rct("Smith", "Death from any cause")]
        studies = ph.apply_canonical_map(studies, {"death from any cause": "All-cause mortality"})
        assert ph.distinct_outcome_names(studies) == {}    # already canonical -> not pending

    def test_clusters_to_map(self):
        m = ph.clusters_to_map([{"canonical": "All-cause mortality",
                                 "members": ["death from any cause", "overall mortality"]}])
        assert m["death from any cause"] == "All-cause mortality"
        assert m["all cause mortality"] == "All-cause mortality"   # canonical seeded too


# ─────────────────────────────────────────────
# End-to-end: harmonize then group/pool
# ─────────────────────────────────────────────
class TestHarmonizeEndToEnd:
    STUDIES = [_rct("Smith", "All-cause mortality", 0.6),
               _rct("Jones", "Death from any cause", 0.7),
               _rct("Lee", "Overall mortality", 0.65)]

    def test_without_harmonization_three_bodies(self):
        assert len(pool_extractions(self.STUDIES)) == 3

    def test_targets_merge_into_one_body(self):
        targets = [{"canonical": "All-cause mortality",
                    "aliases": ["death from any cause", "overall mortality"]}]
        harm, report = ph.harmonize_by_targets(self.STUDIES, targets)
        bodies = pool_extractions(harm)
        assert len(bodies) == 1 and bodies[0]["k"] == 3
        assert bodies[0]["outcome_name"] == "All-cause mortality"
        assert all(r["canonical"] == "All-cause mortality" for r in report)

    def test_grouping_prefers_canonical(self):
        studies = ph.apply_canonical_map(
            self.STUDIES, {"death from any cause": "All-cause mortality",
                           "overall mortality": "All-cause mortality"})
        bodies = group_into_bodies(studies)
        assert len(bodies) == 1

    def test_unmatched_kept_separate_and_reported(self):
        studies = self.STUDIES + [_rct("Park", "Tumor response rate")]
        targets = [{"canonical": "All-cause mortality",
                    "aliases": ["death from any cause", "overall mortality"]}]
        harm, report = ph.harmonize_by_targets(studies, targets)
        bodies = pool_extractions(harm)
        assert len(bodies) == 2           # mortality body + the unmatched response-rate body
        assert any(r["name"] == "Tumor response rate" and r["canonical"] is None for r in report)


# ─────────────────────────────────────────────
# LLM clusterer (mocked text caller)
# ─────────────────────────────────────────────
class TestLLMClusterer:
    def test_cluster_map_from_llm(self, monkeypatch):
        import backend.evidence_synthesis.pooling_extract as pe

        def fake_call(messages, system, max_tokens=None):
            return ('{"clusters":[{"canonical":"All-cause mortality",'
                    '"members":["death from any cause","overall mortality"]}]}')
        monkeypatch.setattr(pe.helpers_mod, "call_anthropic", fake_call)
        m = pe.cluster_outcome_names_map(["death from any cause", "overall mortality"])
        assert m["death from any cause"] == "All-cause mortality"

    def test_cluster_failure_returns_empty(self, monkeypatch):
        import backend.evidence_synthesis.pooling_extract as pe
        monkeypatch.setattr(pe.helpers_mod, "call_anthropic",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert pe.cluster_outcome_names_map(["a", "b"]) == {}

    def test_harmonize_llm_merges(self, monkeypatch):
        import backend.evidence_synthesis.pooling_extract as pe

        def fake_call(messages, system, max_tokens=None):
            return ('{"clusters":[{"canonical":"All-cause mortality",'
                    '"members":["all-cause mortality","death from any cause","overall mortality"]}]}')
        monkeypatch.setattr(pe.helpers_mod, "call_anthropic", fake_call)
        harm = pe.harmonize_outcomes(TestHarmonizeEndToEnd.STUDIES, use_llm=True)
        assert len(pool_extractions(harm)) == 1

    def test_pool_studies_with_llm_harmonization(self, monkeypatch):
        import backend.evidence_synthesis.pooling_extract as pe

        def fake_call(messages, system, max_tokens=None):
            return ('{"clusters":[{"canonical":"All-cause mortality",'
                    '"members":["all-cause mortality","death from any cause","overall mortality"]}]}')
        monkeypatch.setattr(pe.helpers_mod, "call_anthropic", fake_call)
        bodies = pe.pool_studies(list(TestHarmonizeEndToEnd.STUDIES), harmonize_llm=True)
        assert len(bodies) == 1 and bodies[0]["k"] == 3

    def test_dictionary_first_then_llm_only_for_gaps(self, monkeypatch):
        import backend.evidence_synthesis.pooling_extract as pe
        seen = {"names": None}

        def fake_call(messages, system, max_tokens=None):
            seen["names"] = messages[0]["content"]
            return '{"clusters":[{"canonical":"Tumor response","members":["response rate"]}]}'
        monkeypatch.setattr(pe.helpers_mod, "call_anthropic", fake_call)
        studies = list(TestHarmonizeEndToEnd.STUDIES) + [_rct("Park", "response rate")]
        targets = [{"canonical": "All-cause mortality",
                    "aliases": ["death from any cause", "overall mortality"]}]
        pe.pool_studies(studies, outcome_targets=targets, harmonize_llm=True)
        # The dictionary already resolved the 3 mortality names, so only "response rate"
        # is sent to the LLM.
        assert "response rate" in seen["names"]
        assert "death from any cause" not in seen["names"]


# ─────────────────────────────────────────────
# Risk-of-bias keys are outcome names too
# ─────────────────────────────────────────────

class TestRobKeysAreCanonicalized:
    """Canonicalizing outcomes but not the RoB keys is worse than not harmonizing:
    the body takes the canonical name, the key keeps its alias, every lookup misses,
    and a fully-appraised body arrives at GRADE looking unappraised."""

    def _studies(self):
        return [
            {"study_id": "S1", "study_type": "RCT",
             "rob_by_outcome": {"Death from any cause": "Low"},
             "outcomes": [{"name": "Death from any cause", "comparison": "d vs p",
                           "timing": "12m", "events_int": 12, "n_int": 100,
                           "events_ctrl": 20, "n_ctrl": 100}]},
            {"study_id": "S2", "study_type": "RCT",
             "rob_by_outcome": {"Overall mortality": "High"},
             "outcomes": [{"name": "Overall mortality", "comparison": "d vs p",
                           "timing": "12m", "events_int": 8, "n_int": 80,
                           "events_ctrl": 25, "n_ctrl": 90}]},
        ]

    _TARGETS = [{"canonical": "All-cause mortality",
                 "aliases": ["Death from any cause", "Overall mortality"]}]

    def test_map_keys_are_rewritten_to_canonical(self):
        out, _report = ph.harmonize_by_targets(self._studies(), self._TARGETS)
        assert list(out[0]["rob_by_outcome"]) == ["All-cause mortality"]
        assert list(out[1]["rob_by_outcome"]) == ["All-cause mortality"]

    def test_labels_survive_into_the_pooled_body(self):
        out, _report = ph.harmonize_by_targets(self._studies(), self._TARGETS)
        bodies = pool_extractions(out)
        assert len(bodies) == 1
        assert bodies[0]["outcome_name"] == "All-cause mortality"
        pooled = bodies[0]["pooled"]["studies"]
        assert {s["study_id"]: s["rob"] for s in pooled} == {"S1": "Low", "S2": "High"}
        assert all(s["rob_source"] == "user_outcome" for s in pooled)

    def test_rob_only_names_reach_the_clustering_input(self):
        # A name that appears ONLY as a RoB key must still be offered to the clusterer.
        counts = ph.distinct_outcome_names(
            [{"outcomes": [], "rob_by_outcome": {"Overall mortality": "High"}}])
        assert counts.get("Overall mortality") == 1

    def test_studies_without_a_rob_map_are_untouched(self):
        out, _ = ph.harmonize_by_targets(
            [{"study_id": "S3", "outcomes": []}], self._TARGETS)
        assert "rob_by_outcome" not in out[0]
