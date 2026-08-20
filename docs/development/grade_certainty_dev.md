# GRADE Certainty (Body of Evidence) — Development Notes

Internal companion to [`../shareable/grade_certainty_shareable.md`](../shareable/grade_certainty_shareable.md). The shareable
document is the methodology, framework-free and history-free, for readers implementing on another
stack. This document holds what is specific to *this* codebase: where the code lives, what state it
is in, and the revision history.

**Backing modules**

- `backend/evidence_synthesis/grade.py` — `grade_body()`, the domain decision functions, `absolute_effects()`, `GradeConfig`
- `backend/evidence_synthesis/grade_prep.py` — body ↔ judgment matching, `apply_rob_to_pooled()`
- `backend/evidence_synthesis/grade_assess.py` — hybrid indirectness resolution, the servable entry points
- `backend/evidence_synthesis/grade_agent.py` — HTTP glue + persistence (`grade_results`)
- `tests/test_grade.py`, `tests/test_grade_api.py`, `tests/test_rob_routing.py`

**Implementation status.** Merged to `main`. The engine lives in
`backend/evidence_synthesis/grade.py` (renamed from `backend/synthesis/` so it can coexist with the
Synthesis review app's `backend/synthesis.py` module). The parallel engine in
`backend/synthesis_stats.py` is *not* a duplicate to be reconciled away — it serves the review app and
now applies the same risk-of-bias rules; the two are kept in agreement deliberately (see the
2026-07-31 entries).

**Related documents**

- `grade_certainty_downgrades_shareable.md` — the downgrade-only draft cut of the same methodology, with §5 (rating up) held back as a placeholder. It tracks this same history; changes to shared sections must be applied to both.

---

## Revision notes

Substantive changes to the methodology, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

This history lives here rather than in the shareable document: the shareable document is the methodology as a reader on another stack should implement it, with no history and no repo-internal references. Everything about *this* codebase — where the code lives, what state it is in, what changed when — belongs on this side.

### 2026-08-19 — Upgrade gate keys on the design; stale "Some concerns" note removed from §11

**What changed.** Two corrections prompted by an external implementer's review questions.

1. **Upgrade eligibility is now derived from the body's design, not the starting certainty.**
   `grade_body` previously computed `is_randomized = (initial == "High")`, which collapsed the
   gate's two conditions into `initial == "Low"` alone: a randomized body whose caller pinned
   `initial="Low"` via the `initial` parameter silently became upgrade-eligible. A new
   `_randomized_design(design_class, studies)` helper (shared with `_initial_from_design`) reads
   `design_class` when the pooler supplies one (`"rct"` → randomized, `"nrs"` → not), else infers
   conservatively from the study design labels, else defaults to non-randomized — so bodies with no
   design information keep the previous behaviour. GRADE 9 restricts rating up to observational
   evidence *by design*, whatever the starting certainty.
2. **The stale §11 note claiming a missing RoB label "defaults to 'Some concerns' severity" is
   removed from both shareable cuts.** It contradicted §4.1, the reference implementation, and the
   2026-07-31 revision below (which retired exactly that default in favour of drop-and-renormalize);
   it was leftover text missed in that revision. §4.1 is and was authoritative. The corrected bullet
   also states the adjacent rule that *was* true: only an unrecognized **non-blank** label falls back
   to severity 1.

**Why.** (1) closes a gate bypass reported as a technical weakness; (2) resolves a genuine internal
contradiction that sent a downstream implementer down the wrong path.

**Impact.** (1) Logic change, deliberately narrow: results change **only** for bodies whose design is
randomized while their starting certainty was pinned to Low — previously such a body could be rated
up, now it cannot. No stored production results are known to hit this combination; default-derived
`initial` values are unaffected because `_initial_from_design` and the gate now share the same design
test. The Synthesis review app's engine (`synthesis_stats.grade_body_of_evidence`) is downgrade-only
and is untouched. (2) Documentation-only — no code implemented the stale note.

**Sections touched:** interpretation callouts, §3 (starting certainty — `randomized_design` helper),
§5.4 (the gate), §9 (reference implementation), §10 (test sketches), §11 (both bullets) of the full
cut; §11's risk-of-bias bullet of the downgrade-only cut. Also §4.5, which now states explicitly that
an omitted indirectness level scores 0 ("not assessed") and therefore does not close the upgrade gate
— the caller contract is to resolve indirectness upstream for any body intended to rate up.

### 2026-07-31 — Unassessed studies dropped, not defaulted; AMSTAR-2 excluded

**What changed.** Two corrections to the risk-of-bias domain, applied identically here and in the
Synthesis review app's engine so the two cannot drift.

1. A study with **no** risk-of-bias label is now **dropped** from the domain and the weights
   renormalized over the studies that carry one, with the excluded count named in the reason string.
   It was previously scored severity 1 ("Some concerns").
2. **AMSTAR-2 labels must never reach the severity map**, and the map documents why: AMSTAR-2 rates a
   review's *confidence*, where "High" is good, so its labels invert the domain. `"Critically low"` is
   deliberately absent so it cannot quietly take the severity-1 default.

**Why.** "Some concerns" for an unappraised study looks conservative but states a finding about a
study nobody looked at, and because unappraised studies are common it pushes the `frac_some` share
past its threshold on its own — manufacturing a downgrade out of missing data. Scoring them "Low"
would inflate certainty instead. Dropping and renormalizing is the only option that adds no
information. The all-missing case was already refused by `require_rob`; this fixes the partial case.

**Impact.** Logic change; **stored results change on re-rating** for any body that mixes appraised and
unappraised studies. A body of two *High* studies at 20% weight and eight unappraised at 80% moves
from a 1-level downgrade (`frac_some` = 1.0 across defaulted labels) to a 2-level one
(`frac_serious` = 1.0 across the assessed weight) — a different answer, and the correct one. Bodies
where every study is appraised are unaffected.

**Sections touched:** the severity-map and risk-of-bias-across-studies sections of both shareable
cuts, plus their reference implementations.

### 2026-07-31 — Per-outcome labels keyed on the outcome name, not the prompt label

**What changed.** The sourcing adapter that reads appraisal rows now keys `rob_by_outcome` on the
outcome's short `name`, not on the composed assessed-outcome string.

**Why.** The assessed-outcome string is composed for prompt quality — *"Quality of life — measured as
KCCQ total symptom score — at 8 months"* — while a body of evidence is keyed on the short name
("Quality of life"). Keying on the composed form made every per-outcome lookup miss, so a
**fully-appraised body resolved as unappraised** and was then refused outright by `require_rob`. The
existing tests passed only because they supplied bare, uncomposed outcome strings.

**Impact.** Bug fix. Before it, the per-(study × outcome) path could not deliver a single label to any
body whose appraisal carried a measure or timepoint. No stored appraisal changes; what changes is
whether the label is found.

**Sections touched:** none of the methodology — this is an adapter/join-key fix. Implementers should
note the rule: **compose for the prompt, join on the name.**

### 2026-07-31 — Risk of bias rides on the study records, per (study × outcome)

**What changed.** Risk-of-bias labels now reach this engine attached to each pooled study record — `studies[].rob`, alongside `weight_pct` — instead of as a positional list supplied by the caller. `studies[].rob_source` records the provenance (`user_outcome` / `user_study` / `tool` / `missing`). `per_study_rob` remains as an explicit override but must match `studies[]` exactly; a length mismatch now raises instead of silently discarding the weights. A body with no risk-of-bias input at all raises unless the caller passes `require_rob=False`. The weighted tree reports the share of weight that is unappraised. Sections touched below.

**Why.** The RoB domain is weight-driven, so the labels and the weights have to describe the same studies in the same order — and a parallel list silently does not: the pooler drops studies without usable data, so the pooled order is not the input order, and every label after a dropped study shifted by one. A length mismatch fell back to equal weights, producing an unweighted judgement with an identical reason string. Attaching the label to the record it describes removes both failure modes structurally rather than by validation. Separately, RoB 2 and ROBINS-I are outcome-specific (domain 4 measurement of the outcome, domain 5 selection of the reported result) and GRADE rates risk of bias per outcome, so one label per study was wrong in principle as well as fragile in practice — the label is now resolved per (study × outcome).

**Impact.** **Breaking for implementations that pass `per_study_rob` positionally against a pooled body**: previously a mismatched or misordered list was accepted, now it raises. Bodies graded with no RoB input previously came out as though risk of bias were clean; they now raise, so any caller grading before appraisal completes must set `require_rob=False` and present the domain as not assessed. Ratings themselves are unchanged wherever the labels were already correctly aligned. Logic change, not a prompt change; no re-run needed for correctly-aligned historical results, but results produced with a misaligned list were wrong and should be re-rated.

**Sections touched:** §2 (hand-off contract), §4.1 (risk of bias), §9 (reference implementation).

### 2026-07-22 — Initial publication

**What changed.** First publication of the body-of-evidence GRADE certainty methodology: starting certainty by design (§3), the five downgrade domains with their `GradeConfig` thresholds (§4), the three upgrade domains and the upgrade gate (§5), the certainty combiner + overrides (§6), the hybrid indirectness auto-assessor prompt (§7), the anticipated absolute-effects formulas (§8), the pooling→GRADE hand-off contract (§2), a turnkey dependency-free reference implementation (§9), and plain-`assert` tests (§10).
**Why.** Establish the shareable contract so forks (e.g. OVID) implement GRADE certainty identically and pick up future threshold/logic changes here rather than from production Python.
**Impact.** New document — no prior results affected. Logic + prompt.
**Sections touched:** all (genesis).

---
