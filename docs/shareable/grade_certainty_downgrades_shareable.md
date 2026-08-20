# GRADE Certainty (Body of Evidence) — Sharable Methodology Reference

> **Status: DRAFT — downgrade domains only. Rating up is not yet written (§5).**
> This document is being written in two passes. The five downgrade domains, the combiner,
> the hand-off contract, and the absolute-effects formulas are complete and verified on AiTheia's platform.

A self-contained reference for the **GRADE agent** — the body-of-evidence engine that rates the **certainty of evidence** for **one outcome** and computes its **anticipated absolute effects**. This is "Component D" of the evidence-synthesis pipeline: it consumes the pooled numbers from the pooling agent (Component B, `pooling_meta_analysis_shareable.md`), the per-study risk-of-bias ratings from a quality-appraisal step, and a few human judgments, and produces the GRADE certainty (⊕⊕⊕⊕ High → ⊕⊝⊝⊝ Very low) plus the Summary-of-Findings absolute-effect row ("Tom Table 5"). Contains:

- The **starting-certainty rule** by study design (RCT = High, non-randomized = Low, single-arm = Very low).
- The **five downgrade domains** — risk of bias, inconsistency, indirectness, imprecision, publication bias — each as a plain-Python decision function with its exact numeric thresholds.
- *(Pending — §5.)* The three upgrade domains and the gate that restricts them to non-randomized evidence.
- The **certainty combiner** (start + Σdowngrades, clamped to [Very low, High]) and the per-domain `overrides` mechanism.
- The **anticipated absolute-effects** formulas (assumed risk → risk with intervention for RR/OR/RD/IRR → risk difference + 95% CI → NNT).
- The **pooling → GRADE hand-off contract** (which pooled numbers feed which domain).
- The **hybrid indirectness** path — reviewer value wins; otherwise one LLM call auto-assesses PICO directness — with the exact prompt.
- A **turnkey, dependency-free reference implementation** (`llm_call` injected only for the optional indirectness pass) and plain-`assert` test sketches.


**Scope.** This document covers the GRADE **downgrade** half of the certainty rating, plus absolute effects, for one pooled body of evidence. It consumes numbers; it never pools and never extracts. Out of scope:

- **Rating up** (large effect, dose-response gradient, opposing plausible confounding) — **pending, not absent from the engine.** See §5.

- **The meta-analysis math** (effect sizes, inverse-variance pooling, heterogeneity, Egger/trim-and-fill) — that is the separate **pooling agent** (`pooling_meta_analysis_shareable.md`). This agent reads the pooled result; it does not recompute it.
- **Per-study risk-of-bias assessment** (RoB 2 / ROBINS-I / QUADAS) — the per-study *overall* RoB labels arrive from a quality-appraisal step (see the RoB shareable docs). This agent only **aggregates** those labels across studies, weighted by pooled weight.
- **Effect-size extraction** — the per-study numbers arrive upstream (see `table2_evidence_table_shareable.md`).
- **GRADE domains that need a body of evidence beyond a single pooled estimate** at the depth GRADE ultimately expects — e.g. inconsistency is assessed here from I²/Q on the pooled set, and publication bias from Egger/trim-and-fill, but formal indirect-comparison / network reasoning, baseline-risk indirectness, and the full six-threshold imprecision EtD framing are not modeled.
- **Mixing designs.** GRADE rates randomized and non-randomized evidence as **separate bodies** — call this agent once per body. Do not mix them in one estimate (the pooling agent's `design_class` already separates them).

**Interpretation callouts.**

- **Body-of-evidence, never single-paper.** A GRADE rating is computed *across* the pooled set of studies, never read off one paper. This is structurally unlike per-study extraction or per-study RoB.
- **Natural scale in, natural scale out.** The pooling engine hands over the relative effect + 95% CI already **back-transformed to the natural (display) scale** (RR/OR/HR ratios; MD/SMD/RD differences). Every GRADE decision here — imprecision CI-vs-null, imprecision CI-vs-MID, large-effect magnitude, absolute effects — reads that natural-scale CI directly. The analysis-scale (log) values live in the pooled result too (`pooled.yi`) but this agent does not need them. **The null is 1.0 for ratio measures and 0.0 for difference measures.**
- **Deterministic trees; one optional prompt.** Every downgrade/upgrade decision is deterministic arithmetic over the pooled numbers + human judgments — no model. The agent's *only* LLM touch is the **optional indirectness auto-assessment** (§7), used when the reviewer did not supply an indirectness level and a target PICO is available. With a reviewer-supplied indirectness level, the whole agent runs with **zero model calls**.
- **Conservative-tree note.** Where GRADE's narrative allows reviewer judgement (e.g. "consider −2 for I² > 75%"), the deterministic tree takes the **more transparent, single-level** reading and surfaces the per-domain reason so a human can override via the `overrides` mechanism (§6). The tree is stricter/plainer than the prose so the logic is inspectable and testable; it is not a claim that judgement is unnecessary.
- **A body rated Low here may not be final.** Because the upgrade half is not yet written (§5), non-randomized evidence that GRADE would rate *up* — a large effect, a dose-response gradient, or confounding that could only have attenuated the result — reads as Low or Very low from this document. Randomized evidence is unaffected: GRADE never rates it up, so an RCT body's certainty is complete here.

---

## 1. The pipeline position

```
   PER STUDY                                 BODY OF EVIDENCE
 ┌──────────────┐   (yi, vi)     ┌───────────────────────────────┐
 │  Extraction  ├───────────────►│   Pooling (Component B)        │
 └──────────────┘                │   • pooled effect + 95% CI     │
 ┌──────────────┐                │   • heterogeneity (I², Q, τ²)  │
 │  RoB (per    ├──overall RoB─┐ │   • Egger + trim-and-fill      │
 │  study)      │              │ └───────────────┬───────────────┘
 └──────────────┘              │    pooled stats │
   human inputs                ▼                 ▼
   (baseline risk, MIDs, ────►┌────────────────────────────────────┐
    indirectness, NRS         │        GRADE (Component D — HERE)   │
    up-rating judgments)      │  start-by-design                   │
                              │  − 5 downgrade domains              │
                              │  → certainty  +  absolute effects   │
                              └──────────────────┬─────────────────┘
                                                 ▼
                                        Summary-of-Findings row
```

One call = **one outcome = one body of evidence**. Randomized and non-randomized evidence are graded as separate bodies.

---

## 2. The pooling → GRADE hand-off contract

The GRADE agent reads a single **pooled result dict** (the pooling agent's `pool_outcome(...)` output). Only these fields are consumed — everything else is ignored:

| Pooled field | Type | Feeds |
|---|---|---|
| `measure` | `"RR"/"OR"/"RD"/"IRR"/"HR"/"MD"/"SMD"` | scale + which domains apply |
| `k` | int | inconsistency (single study → n/a), publication-bias gate |
| `design_class` | `"rct"/"nrs"/"unknown"` (optional) | starting certainty |
| `pooled.estimate` | float, **natural scale** | absolute effects *(also large-effect — §5)* |
| `pooled.ci_lower`, `pooled.ci_upper` | float, **natural scale** | imprecision, absolute effects *(also large-effect — §5)* |
| `heterogeneity.i2` | float % | inconsistency |
| `heterogeneity.q_p` | float (Cochran-Q p) | inconsistency |
| `publication_bias.egger.p` | float | publication bias |
| `publication_bias.egger.adequate_power` | bool (k ≥ 10) | publication-bias gate |
| `publication_bias.trim_fill.n_imputed` | int | publication bias |
| `studies[].study_id`, `studies[].design`, `studies[].weight_pct` | — | weighting; single-arm detection |
| `studies[].rob`, `studies[].rob_source` | str / str | **risk of bias** — the per-study label, attached to the study record |
| `totals.n_int`, `totals.n_ctrl` | float | imprecision OIS, absolute-effects context |
| `totals.events_int`, `totals.events_ctrl` | float | imprecision OIS (binary) |

**Risk of bias arrives on the study records, not as a parallel list.** Each entry of `studies[]` carries its own `rob` label next to its `weight_pct`. This matters because the risk-of-bias domain is *weight-driven*: a list supplied alongside has to stay aligned with `studies[]`, and it silently will not be — the pooler drops studies without usable data, so the pooled order is not the input order, and every label after a dropped study shifts by one. Attaching the label to the record it describes makes reordering and dropping harmless.

`rob_source` records where the label came from — `user_outcome` (reviewer, for this specific outcome), `user_study` (reviewer, study-level), `tool` (injected by the risk-of-bias instrument), or `missing`.

**Risk of bias is per (study × outcome).** RoB 2 and ROBINS-I are outcome-specific — domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between outcomes — and GRADE rates risk of bias per outcome. One trial can be Low for mortality and High for an unblinded subjective outcome, so the label is resolved per body, not once per study.

> **This is a breaking change for implementations built on an earlier version of this document.** A positional `per_study_rob` list is still accepted as an explicit override, but it must now match `studies[]` exactly — a length mismatch raises instead of silently falling back to equal weights, which previously produced an unweighted judgement indistinguishable from a weighted one. Bodies with no risk-of-bias input at all now raise rather than rating as though the domain were clean.

**Human judgments** supplied alongside: `baseline_risk_per_1000`, `mid_benefit`, `mid_harm`, `indirectness_levels` (optional → auto), and `overrides`. (`dose_response` and `opposing_confounding` are upgrade inputs — §5.)

---

## 3. Starting certainty by design (GRADE 3)

```python
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]  # index 0..3

def initial_from_design(design_class, measure, studies):
    """RCT = High, non-randomized = Low, single-arm = Very low."""
    designs = " ".join(str((s or {}).get("design") or "") for s in (studies or [])).lower()
    if "single-arm" in designs or "single arm" in designs or "dose-escalation" in designs:
        return "Very low"
    dc = (design_class or "").lower()
    if dc == "rct":
        return "High"
    if dc == "nrs":
        return "Low"
    non_random = "non-random" in designs or "nonrandom" in designs
    randomized = "randomized" in designs or "randomised" in designs or "rct" in designs
    return "High" if (randomized and not non_random) else "Low"
```

Single-arm (uncontrolled) evidence starts at **Very low**: no comparator is more severe than a confounded comparison, so it starts one level below other non-randomized designs.

---

## 4. The five downgrade domains

All thresholds live in one config object so a methodologist can tune them without editing the trees:

```python
from dataclasses import dataclass

@dataclass
class GradeConfig:
    # Imprecision — Optimal Information Size rules of thumb.
    ois_binary_events: int = 300   # binary: total events (or N) < 300 -> OIS unmet
    ois_total_n: int = 400         # continuous: < 400 participants -> OIS unmet
    # Inconsistency — I^2 / Cochran-Q.
    i2_serious: float = 50.0       # I^2 > 50% (with Q p<threshold) -> -1
    i2_very_serious: float = 75.0  # I^2 > 75% -> considerable
    q_p_threshold: float = 0.10
    # Risk of bias — share of pooled weight in studies with concerns.
    rob_high_weight_2: float = 0.50  # >=50% weight at high/serious -> -2
    rob_high_weight_1: float = 0.25  # >=25% high OR >=50% some -> -1
    rob_some_weight_1: float = 0.50
    # Publication bias — only meaningful with enough studies.
    pubbias_min_studies: int = 10  # GRADE: do not assess below ~10 studies
    egger_p: float = 0.10
    trimfill_min_imputed: int = 2
    # Upgrade (non-randomized evidence only).
    large_effect_1: float = 2.0    # RR/OR >=2 or <=0.5 -> +1
    large_effect_2: float = 5.0    # RR/OR >=5 or <=0.2 -> +2
    require_ci_for_large_effect: bool = True
    upgrade_requires_no_downgrade: bool = True
```

### 4.1 Risk of bias (GRADE 4) — weighted across studies, not averaged

Per-study overall RoB labels map to a 0/1/2 severity; the downgrade is driven by the **share of pooled weight** sitting in studies with concerns (GRADE says *do not average*):

```python
_ROB_SEVERITY = {
    "low": 0,
    "low (except for concerns about uncontrolled confounding)": 0,
    "low (except for concerns about uncontrolled benchmarking)": 0,
    "some concerns": 1, "moderate": 1,
    "high": 2, "serious": 2, "critical": 2,
    "no information": 1, "insufficient information": 1, "unclear": 1,
}

def rob_across_studies(per_study_rob, weights, cfg):
    labels = list(per_study_rob)
    if weights is not None and len(weights) != len(labels):
        # Falling back to equal weights here produced an unweighted judgement
        # indistinguishable from a weighted one. Refuse instead.
        raise ValueError("risk-of-bias labels and pooled weights differ in length")
    # Unappraised studies are DROPPED and the weights renormalized over the rest.
    # Scoring them "some concerns" asserts a finding about a study nobody looked
    # at, and on its own pushes frac_some past its threshold; scoring them "low"
    # inflates certainty. A body with no labels at all is refused by require_rob.
    kept = [i for i, r in enumerate(labels) if (r or "").strip()]
    if not kept:
        return 0, "no risk-of-bias judgements available"
    sev = [_ROB_SEVERITY.get(labels[i].strip().lower(), 1) for i in kept]
    w = [float(weights[i]) for i in kept] if weights is not None else [1.0] * len(kept)
    total = sum(w) or 1.0
    frac_serious = sum(wi for wi, s in zip(w, sev) if s >= 2) / total
    frac_some    = sum(wi for wi, s in zip(w, sev) if s >= 1) / total
    n_missing = len(labels) - len(kept)
    tail = ("" if not n_missing else
            f"; {n_missing} of {len(labels)} pooled studies had no assessable "
            "judgement and are excluded from this domain")
    if frac_serious >= cfg.rob_high_weight_2:
        return 2, f"most of the weight ({frac_serious:.0%}) is in studies at high/serious risk of bias{tail}"
    if frac_serious >= cfg.rob_high_weight_1 or frac_some >= cfg.rob_some_weight_1:
        return 1, f"a substantial share of weight ({frac_some:.0%}) is in studies with risk-of-bias concerns{tail}"
    return 0, f"most weight is in low risk-of-bias studies{tail}"
```

A study with **no** RoB entry is **dropped** from this domain and the weights renormalized over the studies that do carry one; the number excluded is named in the reason string. Scoring an unappraised study "Some concerns" looks conservative but asserts a finding about a study nobody looked at, and — because unappraised studies are common — pushes the `frac_some` share past its threshold on its own; scoring it "Low" inflates certainty. Dropping is the only option that adds no information. A body where *nothing* is appraised is refused outright by `require_rob` rather than rated (§ risk-of-bias inputs).
**Only risk-of-bias instruments may be mapped here.** AMSTAR-2 rates a systematic review's *confidence*, where "High" is **good** — the opposite polarity to every risk-of-bias scale. Its labels hit `"high": 2` and downgrade a well-conducted review two levels, and `"Critically low"` is deliberately **absent** from the map so it cannot quietly land on the severity-1 default. Exclude AMSTAR-2-appraised studies from this domain at the source instead; adding severity entries for its vocabulary would legitimize pooling systematic reviews into a body of primary-study evidence, which is a separate methodological error.

**A body with no risk-of-bias input at all is an error, not a clean body.** Scoring the domain 0 in that case reports "no serious concerns" for something nobody assessed. The engine raises; a caller that deliberately grades before appraisal has finished must opt out explicitly (`require_rob=False`) and present the domain as not assessed.

### 4.2 Inconsistency (GRADE 7) — I² + Cochran-Q, subgroup-aware

```python
def inconsistency_downgrade(k, i2, q_p, subgroup, cfg):
    if k < 2:
        return 0, "single study — inconsistency not assessable"
    i2 = i2 or 0.0
    p = 1.0 if q_p is None else q_p
    explained = bool(subgroup and subgroup.get("p_between") is not None and subgroup["p_between"] < 0.05)
    if explained:
        return 0, f"heterogeneity (I²={i2:.0f}%) explained by subgroup differences"
    if i2 > cfg.i2_very_serious and p < cfg.q_p_threshold:
        return 1, f"considerable heterogeneity (I²={i2:.0f}%, p={p:.3f})"
    if i2 > cfg.i2_serious and p < cfg.q_p_threshold:
        return 1, f"substantial unexplained heterogeneity (I²={i2:.0f}%)"
    return 0, f"acceptable consistency (I²={i2:.0f}%)"
```

### 4.3 Imprecision (GRADE 6) — CI vs null / MIDs + Optimal Information Size

`ci_lower`/`ci_upper` are **natural scale**; the null is 1.0 for ratio measures and 0.0 for difference measures. When both MIDs are supplied, the two-threshold GRADE framing applies; otherwise fall back to line-of-no-effect + OIS.

```python
_RATIO_MEASURES  = {"OR", "RR", "IRR", "HR"}
_BINARY_MEASURES = {"OR", "RR", "RD", "IRR"}

def imprecision_downgrade(measure, ci_lower, ci_upper, total_n, mid_benefit, mid_harm, is_binary, cfg):
    null = 1.0 if measure in _RATIO_MEASURES else 0.0
    crosses_null = (ci_lower is not None and ci_upper is not None and ci_lower <= null <= ci_upper)
    ois_cap = cfg.ois_binary_events if is_binary else cfg.ois_total_n
    ois_fail = total_n is not None and total_n < ois_cap
    if mid_benefit is not None and mid_harm is not None and ci_lower is not None and ci_upper is not None:
        lo, hi = min(mid_benefit, mid_harm), max(mid_benefit, mid_harm)
        if ci_lower <= lo and ci_upper >= hi:
            return 2, "CI spans both the benefit and harm thresholds"
        if crosses_null:
            return 1, "CI crosses the line of no effect"
        if ois_fail:
            return 1, "sample size below the optimal information size"
        return 0, "CI excludes clinically important effects in one direction"
    if crosses_null and ois_fail:
        return 2, "wide CI crossing no effect with sample size below OIS"
    if crosses_null:
        return 1, "CI crosses the line of no effect"
    if ois_fail:
        return 1, "sample size below the optimal information size"
    return 0, "adequately precise"
```

For binary outcomes the OIS check prefers **total events** across arms (the tighter GRADE signal), falling back to total N when events are absent; for continuous outcomes it uses total N. The OIS thresholds (300 events / 400 participants) are rules of thumb — a formal Optimal/Review Information Size is out of scope.

### 4.4 Publication bias (GRADE 5) — small-study tests, gated at k ≥ 10

```python
def pubbias_downgrade(k, egger, trim_fill, cfg):
    adequate = None if egger is None else egger.get("adequate_power")
    if adequate is False or (adequate is None and k and k < cfg.pubbias_min_studies):
        return 0, f"not formally assessed (<{cfg.pubbias_min_studies} studies)"
    ep = None if egger is None else egger.get("p")
    if ep is not None and ep < cfg.egger_p:
        return 1, f"funnel asymmetry (Egger p={ep:.3f})"
    n_imp = 0 if trim_fill is None else int(trim_fill.get("n_imputed") or 0)
    if n_imp >= cfg.trimfill_min_imputed:
        return 1, f"trim-and-fill imputed {n_imp} missing studies"
    return 0, "no strong evidence of publication bias"
```

### 4.5 Indirectness (GRADE 8) — a supplied integer (0/1/2)

Indirectness is a judgement, not a computation from the pooled numbers. The engine consumes an **integer level**; §7 describes how a hybrid path fills it in (reviewer value, else one LLM call). When omitted it defaults to 0.

---

## 5. Rating up (GRADE 9) — NOT YET DOCUMENTED

**This section is a placeholder.** The three GRADE rating-up domains — **large effect**, **dose-response gradient**, and **opposing plausible confounding** — are not written up yet.

Read the following before implementing from this document, because the omission is not neutral:

- **The engine already implements them.** This is a gap in the documentation, not in the code. An implementation built from this document alone will **diverge from the reference engine** for one specific class of body: non-randomized evidence with no rate-down factors.
- **Who is affected.** Rating up applies *only* to non-randomized evidence starting at Low, and *only* when nothing was rated down. Randomized bodies are never rated up under GRADE 9, so an RCT body graded from this document is complete and correct.
- **Direction of the error.** Omitting the upgrades is **conservative** — it can only under-rate certainty, never over-rate it. A non-randomized body that should have reached Moderate reads as Low.
- **Do not reconstruct the gate from the GRADE 9 paper and assume you match.** It is an AND of three conditions — *non-randomized*, *starts at Low*, and *zero downgrades* — and the third is the one implementations usually miss. Wait for this section, or read it off the engine.

What this section will contain when written: the large-effect thresholds (≥2-fold → +1, ≥5-fold → +2) and whether the CI must exclude no-effect; the dose-response path via assessor judgement or a meta-regression moderator; the opposing-confounding flag; the gate; and the corresponding `GradeConfig` knobs (`large_effect_1`, `large_effect_2`, `require_ci_for_large_effect`, `upgrade_requires_no_downgrade`).

Until then the combiner in §6 sums downgrades only, and every code block in this document — including the reference implementation in §9 — omits the upgrade path entirely rather than half-implementing it.

---

## 6. Certainty combiner + overrides

```python
def grade_index(level):
    try: return GRADE_LEVELS.index(level)
    except ValueError: return 0

# start + Σdowngrades, clamped to [Very low (idx 3), High (idx 0)]
# (The − Σupgrades term arrives with §5; see the note below.)
start = grade_index(initial)
final_idx = max(0, min(len(GRADE_LEVELS) - 1, start + total_downgrade))
final = GRADE_LEVELS[final_idx]
```

The lower clamp (`max(0, …)`) has nothing to do yet — with downgrades only, the index can never go below the starting level. It is kept because it is load-bearing the moment §5 lands, and removing it would be a silent trap for whoever adds the upgrade term.

**Overrides** let an assessor pin any domain by key — `{"imprecision": 2, "indirectness": 0}`. Keys available in this draft: `risk_of_bias`, `inconsistency`, `indirectness`, `imprecision`, `publication_bias`. A pinned value replaces the computed level and the reason gets a `[overridden]` suffix. The three upgrade keys (`large_effect`, `dose_response`, `opposing_confounding`) arrive with §5.

### 6.1 The explanation string

The record carries a human-readable narrative assembled from the per-domain reasons. Only domains that actually fired appear, so the sentence stays short for a clean body and enumerates every contributor for a downgraded one:

```python
fired = [f"{d['domain'].lower()} (−{d['downgrade']}: {d['reason']})"
         for d in domains if d["downgrade"] > 0]
parts = [f"Initial certainty {initial}"]
if fired:
    parts.append(f"downgraded {total_down} level(s) for " + "; ".join(fired))
if not fired:
    parts.append("no serious concerns across GRADE domains")
explanation = ". ".join(parts) + f". Final certainty: {final}."
```

Worked output, from the scenarios in §10:

```text
Initial certainty High. no serious concerns across GRADE domains. Final certainty: High.

Initial certainty High. downgraded 6 level(s) for risk of bias (−2: most of the weight (70%)
is in studies at high/serious risk of bias); inconsistency (−1: considerable heterogeneity
(I²=82%, p=0.002)); indirectness (−1: indirectness concerns); imprecision (−2: wide CI
crossing no effect with sample size below OIS). Final certainty: Very low.

```

> **Note for §5.** When rating up is documented, this builder gains a matching `raised` list and an
> "upgraded N level(s) for …" clause between the downgrade clause and the final-certainty sentence.
> A non-randomized body that qualifies today produces only the "Initial certainty Low." opening,
> which is accurate but incomplete.

> **The explanation reports the computed downgrade, not the applied one.** The second example totals 6 levels while the ladder only has 3 below High. The clamp in the combiner absorbs the excess, and the sentence deliberately keeps the full reasoning rather than hiding it. Expect "downgraded 6 level(s)" next to a 3-level drop; that is correct output, not a bug.

---

## 7. Hybrid indirectness (the only optional model call)

The engine takes `indirectness_levels` as an integer. The orchestrator resolves it:

1. **Reviewer supplied a value** → use it verbatim. No model call.
2. **Omitted, and a target PICO is available** → one LLM call judges the four PICO subdomains (Population / Intervention / Comparison / Outcome) for the *pooled body*, and a count-based severity tree maps them to 0/1/2/3.

The severity tree is shared with the single-study indirectness tool (see `indirectness`/GRADE 8):

```python
def indirectness_severity(judgements):
    """reds = 'not_direct'; oranges = 'probably_not_direct'."""
    reds    = sum(1 for v in judgements.values() if v == "not_direct")
    oranges = sum(1 for v in judgements.values() if v == "probably_not_direct")
    if reds >= 3:            return "extremely_serious", 3
    if reds == 2:            return "very_serious", 2
    if reds == 1 or oranges >= 2: return "serious", 1
    return "none", 0
```

**Exact prompt (body-level).** `{...}` are filled at call time; `llm_call(system, user) -> str` is injected.

System:
```
You are an evidence-synthesis methodologist assessing the GRADE indirectness
domain for a BODY OF EVIDENCE (a pooled meta-analytic estimate across several
studies for one outcome), not a single study. For each of the four PICO
subdomains (Population, Intervention, Comparison, Outcome), judge how directly the
pooled evidence applies to the specified target question on a 4-level scale:
'direct', 'probably_direct', 'probably_not_direct', or 'not_direct'. Per GRADE
guidance, do NOT rate down unless the mismatch is likely to lead to meaningful,
systematic differences in the effect estimate. Surrogate outcomes (HbA1c, LDL,
bone density, progression-free survival, etc.) should be 'probably_not_direct' or
worse unless a strong, well-established correlation with patient-important
outcomes is documented. Also flag whether the outcome is a surrogate. Return ONLY
a valid JSON object — no preamble, no markdown fences.
```

User:
```
Assess GRADE indirectness for this pooled body of evidence.

Body of evidence:
{body_context_json}     # {outcome_name, comparison, measure, favorable_direction, k}

Target question (PICO):
  Population: {population}
  Intervention: {intervention}
  Comparator: {comparator}
  Outcome: {outcome}

Return a JSON object with exactly this shape:
{
  "population": "direct|probably_direct|probably_not_direct|not_direct",
  "population_rationale": "1-2 sentences",
  "intervention": "...",  "intervention_rationale": "...",
  "comparator": "...",    "comparator_rationale": "...",
  "outcome": "...",       "outcome_rationale": "...",
  "primary_outcome_is_surrogate": true|false,
  "surrogate_rationale": "If the outcome is a surrogate, briefly explain."
}
```

When the target PICO is blank, replace the "Target question" block with an instruction to judge surrogate-outcome directness only and default the other three subdomains to `probably_direct` unless the as-conducted PICO is unusually narrow.

---

## 8. Anticipated absolute effects (GRADE 12)

For a dichotomous outcome with an assumed (control) risk, compute the absolute effects per 1000. `estimate`/`ci_lower`/`ci_upper` are the **natural-scale** relative effect + CI.

```python
_ABSOLUTE_MEASURES = {"RR", "OR", "RD", "IRR"}

def absolute_effects(measure, estimate, ci_lower, ci_upper, baseline_per_1000):
    if baseline_per_1000 is None or measure not in _ABSOLUTE_MEASURES:
        return None
    acr = baseline_per_1000 / 1000.0
    if measure != "RD" and not (0.0 < acr < 1.0):
        return None

    def apply(rel):
        if rel is None: return None
        if measure == "RD":            return acr + rel            # RD is already absolute
        if measure in ("RR", "IRR"):   return acr * rel
        odds = acr / (1.0 - acr) * rel                             # OR -> absolute via odds
        return odds / (1.0 + odds)

    est, lo, hi = apply(estimate), apply(ci_lower), apply(ci_upper)
    if est is None: return None
    rd = est - acr
    return {
        "baseline_per_1000": round(baseline_per_1000, 1),
        "intervention_per_1000": round(est * 1000, 1),
        "risk_difference_per_1000": round(rd * 1000, 1),
        "rd_ci_per_1000": [None if lo is None else round((lo - acr) * 1000, 1),
                           None if hi is None else round((hi - acr) * 1000, 1)],
        "nnt": None if rd == 0 else round(1.0 / abs(rd)),
        "favours": "intervention" if rd < 0 else ("comparator" if rd > 0 else "neither"),
    }
```

Worked example: **RR = 0.5**, assumed risk **200/1000** → intervention `200 × 0.5 = 100`/1000, risk difference **−100**/1000, **NNT = 10**, favours intervention. Continuous measures (MD/SMD) have no per-1000 column — the relative column carries the MD/SMD instead.

---

## 9. Reference implementation (turnkey, dependency-free)

A single self-contained module. Pure standard library. `llm_call` is injected **only** for the optional indirectness auto-assessment (§7); with a reviewer-supplied `indirectness_levels`, nothing calls a model. Paste and run.

```python
"""grade_certainty.py — body-of-evidence GRADE certainty + absolute effects.
Stdlib only. Consumes a pooled result dict (see pooling_meta_analysis_shareable.md).
"""
from dataclasses import dataclass

GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]
_ROB_SEVERITY = {
    "low": 0, "low (except for concerns about uncontrolled confounding)": 0,
    "low (except for concerns about uncontrolled benchmarking)": 0,
    "some concerns": 1, "moderate": 1, "high": 2, "serious": 2, "critical": 2,
    "no information": 1, "insufficient information": 1, "unclear": 1,
}
_RATIO_MEASURES  = {"OR", "RR", "IRR", "HR"}
_BINARY_MEASURES = {"OR", "RR", "RD", "IRR"}
_ABSOLUTE_MEASURES = {"RR", "OR", "RD", "IRR"}

@dataclass
class GradeConfig:
    ois_binary_events: int = 300; ois_total_n: int = 400
    i2_serious: float = 50.0; i2_very_serious: float = 75.0; q_p_threshold: float = 0.10
    rob_high_weight_2: float = 0.50; rob_high_weight_1: float = 0.25; rob_some_weight_1: float = 0.50
    pubbias_min_studies: int = 10; egger_p: float = 0.10; trimfill_min_imputed: int = 2
    # Upgrade knobs (large_effect_1/2, require_ci_for_large_effect,
    # upgrade_requires_no_downgrade) arrive with section 5.

_CFG = GradeConfig()

def _num(v):
    if v is None or isinstance(v, bool): return None
    try: f = float(v)
    except (TypeError, ValueError): return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None

def _grade_index(level):
    try: return GRADE_LEVELS.index(level)
    except ValueError: return 0

def _initial_from_design(design_class, measure, studies):
    designs = " ".join(str((s or {}).get("design") or "") for s in (studies or [])).lower()
    if "single-arm" in designs or "single arm" in designs or "dose-escalation" in designs:
        return "Very low"
    dc = (design_class or "").lower()
    if dc == "rct": return "High"
    if dc == "nrs": return "Low"
    non_random = "non-random" in designs or "nonrandom" in designs
    randomized = "randomized" in designs or "randomised" in designs or "rct" in designs
    return "High" if (randomized and not non_random) else "Low"

def _rob_across_studies(per_study_rob, weights, cfg):
    labels = list(per_study_rob)
    if weights is not None and len(weights) != len(labels):
        raise ValueError("risk-of-bias labels and pooled weights differ in length")
    # Unappraised studies are dropped and the weights renormalized over the rest.
    kept = [i for i, r in enumerate(labels) if (r or "").strip()]
    if not kept: return 0, "no risk-of-bias judgements available"
    sev = [_ROB_SEVERITY.get(labels[i].strip().lower(), 1) for i in kept]
    w = [float(weights[i]) for i in kept] if weights is not None else [1.0] * len(kept)
    total = sum(w) or 1.0
    fs = sum(wi for wi, s in zip(w, sev) if s >= 2) / total
    fm = sum(wi for wi, s in zip(w, sev) if s >= 1) / total
    n_miss = len(labels) - len(kept)
    tail = (f"; {n_miss} of {len(labels)} pooled studies had no assessable judgement "
            "and are excluded from this domain") if n_miss else ""
    if fs >= cfg.rob_high_weight_2:
        return 2, f"most of the weight ({fs:.0%}) is in studies at high/serious risk of bias{tail}"
    if fs >= cfg.rob_high_weight_1 or fm >= cfg.rob_some_weight_1:
        return 1, f"a substantial share of weight ({fm:.0%}) is in studies with risk-of-bias concerns{tail}"
    return 0, f"most weight is in low risk-of-bias studies{tail}"

def _inconsistency(k, i2, q_p, subgroup, cfg):
    if k < 2: return 0, "single study — inconsistency not assessable"
    i2 = i2 or 0.0; p = 1.0 if q_p is None else q_p
    if subgroup and subgroup.get("p_between") is not None and subgroup["p_between"] < 0.05:
        return 0, f"heterogeneity (I²={i2:.0f}%) explained by subgroup differences"
    if i2 > cfg.i2_very_serious and p < cfg.q_p_threshold:
        return 1, f"considerable heterogeneity (I²={i2:.0f}%, p={p:.3f})"
    if i2 > cfg.i2_serious and p < cfg.q_p_threshold:
        return 1, f"substantial unexplained heterogeneity (I²={i2:.0f}%)"
    return 0, f"acceptable consistency (I²={i2:.0f}%)"

def _imprecision(measure, lo, hi, total_n, mid_b, mid_h, is_binary, cfg):
    null = 1.0 if measure in _RATIO_MEASURES else 0.0
    crosses = lo is not None and hi is not None and lo <= null <= hi
    cap = cfg.ois_binary_events if is_binary else cfg.ois_total_n
    ois_fail = total_n is not None and total_n < cap
    if mid_b is not None and mid_h is not None and lo is not None and hi is not None:
        blo, bhi = min(mid_b, mid_h), max(mid_b, mid_h)
        if lo <= blo and hi >= bhi: return 2, "CI spans both the benefit and harm thresholds"
        if crosses:  return 1, "CI crosses the line of no effect"
        if ois_fail: return 1, "sample size below the optimal information size"
        return 0, "CI excludes clinically important effects in one direction"
    if crosses and ois_fail: return 2, "wide CI crossing no effect with sample size below OIS"
    if crosses:  return 1, "CI crosses the line of no effect"
    if ois_fail: return 1, "sample size below the optimal information size"
    return 0, "adequately precise"

def _pubbias(k, egger, trim_fill, cfg):
    adequate = None if egger is None else egger.get("adequate_power")
    if adequate is False or (adequate is None and k and k < cfg.pubbias_min_studies):
        return 0, f"not formally assessed (<{cfg.pubbias_min_studies} studies)"
    ep = None if egger is None else _num(egger.get("p"))
    if ep is not None and ep < cfg.egger_p: return 1, f"funnel asymmetry (Egger p={ep:.3f})"
    n = 0 if trim_fill is None else int(trim_fill.get("n_imputed") or 0)
    if n >= cfg.trimfill_min_imputed: return 1, f"trim-and-fill imputed {n} missing studies"
    return 0, "no strong evidence of publication bias"

# NOTE: the three upgrade helpers (_large_effect, _dose_response, _opposing)
# arrive with section 5. They are omitted here rather than half-implemented.

def absolute_effects(measure, est, lo, hi, baseline_per_1000):
    b = _num(baseline_per_1000)
    if b is None or measure not in _ABSOLUTE_MEASURES: return None
    acr = b / 1000.0
    if measure != "RD" and not (0.0 < acr < 1.0): return None
    def apply(rel):
        if rel is None: return None
        if measure == "RD": return acr + rel
        if measure in ("RR", "IRR"): return acr * rel
        odds = acr / (1.0 - acr) * rel
        return odds / (1.0 + odds)
    e, l, h = apply(est), apply(lo), apply(hi)
    if e is None: return None
    rd = e - acr
    return {"baseline_per_1000": round(b, 1), "intervention_per_1000": round(e * 1000, 1),
            "risk_difference_per_1000": round(rd * 1000, 1),
            "rd_ci_per_1000": [None if l is None else round((l - acr) * 1000, 1),
                               None if h is None else round((h - acr) * 1000, 1)],
            "nnt": None if rd == 0 else round(1.0 / abs(rd)),
            "favours": "intervention" if rd < 0 else ("comparator" if rd > 0 else "neither")}

def grade_body(pool_result, *, initial=None, per_study_rob=None, weights=None, require_rob=True,
               indirectness_levels=None, indirectness_reason="", mid_benefit=None, mid_harm=None,
               baseline_risk_per_1000=None, subgroup=None, overrides=None, cfg=None):
    # `dose_response`, `opposing_confounding`, and `metaregression` are upgrade
    # inputs and arrive with section 5.
    cfg = cfg or _CFG; overrides = overrides or {}
    measure = (pool_result.get("measure") or "").upper()
    k = int(pool_result.get("k") or 0)
    pooled = pool_result.get("pooled") or {}
    het = pool_result.get("heterogeneity") or {}
    pb = pool_result.get("publication_bias") or {}
    studies = pool_result.get("studies") or []
    totals = pool_result.get("totals") or {}
    est, lo, hi = _num(pooled.get("estimate")), _num(pooled.get("ci_lower")), _num(pooled.get("ci_upper"))
    is_binary = measure in _BINARY_MEASURES
    n_int, n_ctrl = _num(totals.get("n_int")) or 0.0, _num(totals.get("n_ctrl")) or 0.0
    total_n = (n_int + n_ctrl) or None
    if is_binary:
        e_i, e_c = _num(totals.get("events_int")), _num(totals.get("events_ctrl"))
        ev = None if (e_i is None and e_c is None) else (e_i or 0.0) + (e_c or 0.0)
        ois = ev if ev is not None else total_n
    else:
        ois = total_n
    if initial is None:
        initial = _initial_from_design(pool_result.get("design_class"), measure, studies)
    is_randomized = (initial == "High")
    # RoB rides on the study records (studies[].rob), attached by the pooling
    # layer; per_study_rob is an explicit positional override.
    if per_study_rob is None:
        per_study_rob = [(s.get("rob") or "") for s in studies]
    else:
        per_study_rob = list(per_study_rob)
        if per_study_rob and studies and len(per_study_rob) != len(studies):
            raise ValueError("per_study_rob must match the pooled studies exactly")
        if not per_study_rob:
            per_study_rob = [(s.get("rob") or "") for s in studies]
    if not any((r or "").strip() for r in per_study_rob):
        if require_rob:
            raise ValueError("no risk-of-bias judgements for this body of evidence")
        per_study_rob = []
    if weights is None:
        weights = [s.get("weight_pct") for s in studies if s.get("weight_pct") is not None]
        if len(weights) != len(per_study_rob): weights = None

    def pin(key, lv, reason):
        if key in overrides: return max(0, int(overrides[key])), (reason + " [overridden]").strip()
        return lv, reason

    rob_lv, rob_r = pin("risk_of_bias", *_rob_across_studies(per_study_rob, weights, cfg))
    inc_lv, inc_r = pin("inconsistency", *_inconsistency(k, _num(het.get("i2")), _num(het.get("q_p")), subgroup, cfg))
    imp_lv, imp_r = pin("imprecision", *_imprecision(measure, lo, hi, ois, _num(mid_benefit), _num(mid_harm), is_binary, cfg))
    pub_lv, pub_r = pin("publication_bias", *_pubbias(k, pb.get("egger"), pb.get("trim_fill"), cfg))
    ind_in = 0 if indirectness_levels is None else max(0, int(indirectness_levels))
    ind_lv, ind_r = pin("indirectness", ind_in, indirectness_reason or ("no serious indirectness" if ind_in == 0 else "indirectness concerns"))

    domains = [
        {"domain": "Risk of bias", "kind": "downgrade", "downgrade": rob_lv, "upgrade": 0, "reason": rob_r},
        {"domain": "Inconsistency", "kind": "downgrade", "downgrade": inc_lv, "upgrade": 0, "reason": inc_r},
        {"domain": "Indirectness", "kind": "downgrade", "downgrade": ind_lv, "upgrade": 0, "reason": ind_r},
        {"domain": "Imprecision", "kind": "downgrade", "downgrade": imp_lv, "upgrade": 0, "reason": imp_r},
        {"domain": "Publication bias", "kind": "downgrade", "downgrade": pub_lv, "upgrade": 0, "reason": pub_r},
    ]
    total_down = sum(d["downgrade"] for d in domains)
    # SECTION 5 PENDING: the upgrade block goes here — gated on
    # (not is_randomized and initial == "Low" and total_down == 0), appending
    # Large effect / Dose-response gradient / Opposing plausible confounding
    # domain records and accumulating total_up. `total_up` is reported as 0 so
    # the record shape stays stable for consumers.
    total_up = 0
    final = GRADE_LEVELS[max(0, min(len(GRADE_LEVELS) - 1, _grade_index(initial) + total_down))]
    fired = [f"{d['domain'].lower()} (−{d['downgrade']}: {d['reason']})"
             for d in domains if d["downgrade"] > 0]
    parts = [f"Initial certainty {initial}"]
    if fired:
        parts.append(f"downgraded {total_down} level(s) for " + "; ".join(fired))
    if not fired:
        parts.append("no serious concerns across GRADE domains")
    explanation = ". ".join(parts) + f". Final certainty: {final}."
    return {"initial": initial, "final": final, "total_downgrade": total_down, "total_upgrade": total_up,
            "domains": domains,
            "explanation": explanation,
            "absolute_effects": absolute_effects(measure, est, lo, hi, baseline_risk_per_1000)}
```

---

## 10. Quick test sketches (plain assert, no framework)

```python
def _rr_body(est, lo, hi, i2=5.0, q_p=0.8, n=2000, ev=800, k=3, dc=None, studies=None):
    return {"measure": "RR", "k": k, "design_class": dc,
            "pooled": {"estimate": est, "ci_lower": lo, "ci_upper": hi},
            "heterogeneity": {"i2": i2, "q_p": q_p},
            "publication_bias": {"egger": None, "trim_fill": None},
            "studies": studies or [], "totals": {"n_int": n, "n_ctrl": n, "events_int": ev, "events_ctrl": ev}}

# Clean RCT -> High, no downgrades.
g = grade_body(_rr_body(0.7, 0.55, 0.9), initial="High", per_study_rob=["Low", "Low", "Low"])
assert g["final"] == "High" and g["total_downgrade"] == 0

# Absolute effects: RR 0.5, baseline 200/1000 -> 100, RD -100, NNT 10.
g = grade_body(_rr_body(0.5, 0.4, 0.62), initial="High", per_study_rob=["Low"]*3, baseline_risk_per_1000=200)
ae = g["absolute_effects"]
assert ae["intervention_per_1000"] == 100.0 and ae["risk_difference_per_1000"] == -100.0 and ae["nnt"] == 10

# Downgrades stack and clamp at Very low.
g = grade_body(_rr_body(0.9, 0.6, 1.4, i2=85.0, q_p=0.001, n=60, ev=40), initial="High",
               per_study_rob=["High", "High", "Some concerns"])
assert g["final"] == "Very low"

# SECTION 5 PENDING — the upgrade tests belong here. Until then this draft
# asserts only that nothing rates up, which is the honest statement of what
# the code above does. An NRS body with a large effect stays at Low here and
# reaches Moderate in the full engine; that gap is the subject of section 5.
g = grade_body(_rr_body(3.0, 2.0, 4.5), initial="Low", per_study_rob=["Low"]*3)
assert g["total_upgrade"] == 0 and g["final"] == "Low"

# RCT bodies are unaffected by the missing section — GRADE never rates them up.
g = grade_body(_rr_body(6.0, 3.0, 12.0), initial="High", per_study_rob=["Low", "Low"])
assert g["total_upgrade"] == 0 and g["final"] == "High"

# Override pins indirectness -> High minus 2 = Low.
g = grade_body(_rr_body(1.2, 1.05, 1.4), initial="High", per_study_rob=["Low", "Low"], overrides={"indirectness": 2})
assert g["final"] == "Low"

# Single study -> inconsistency not assessed.
g = grade_body(_rr_body(0.8, 0.6, 0.95, k=1), initial="High", per_study_rob=["Low"])
assert next(d for d in g["domains"] if d["domain"] == "Inconsistency")["downgrade"] == 0

# Single-arm design -> starts Very low.
sa = _rr_body(0.7, 0.5, 0.95, k=1, studies=[{"design": "Single-Arm Trial", "weight_pct": 100.0}])
assert grade_body(sa, per_study_rob=["Low"])["initial"] == "Very low"
```

---

## 11. Implementation notes for other platforms

- **Natural-scale CI is the interface.** This engine assumes the pooling layer already back-transformed ratio measures to the natural scale. If your pooler hands over log-scale CIs, back-transform (`exp`) before calling, or the null-crossing / MID / large-effect / absolute-effect logic will be wrong. The null is **1.0 for ratios, 0.0 for differences**.
- **RoB labels are joined by study id, not by position, upstream.** Align `per_study_rob` to the pooled `studies[]` order (which carries `weight_pct`). A study with a **missing** (blank) label is **dropped from the domain and the weights renormalized** over the rest, exactly as the risk-of-bias section specifies — it is *not* defaulted to "Some concerns" (an earlier revision of this document did that; it was retired because it asserts a finding about a study nobody appraised). Only an *unrecognized non-blank* label falls back to severity 1, and a body with no labels at all is refused via `require_rob` rather than rated.
- **Weighting matters.** Risk of bias is weighted by pooled weight, not averaged; if your pooler doesn't expose per-study weights, the engine falls back to equal weights (a coarser approximation).
- **OIS is a rule of thumb.** 300 events / 400 participants are defaults, not a computed Optimal Information Size. If you can compute a formal OIS/RIS for your outcome, override the imprecision domain (§6) or adjust `GradeConfig`.
- **Rating up is missing from this draft (§5), and that is a behavioural difference, not just a documentation one.** If you ship an implementation built from this document, non-randomized bodies with no rate-down factors will be rated one or two levels lower than the reference engine rates them. Decide deliberately whether to ship conservative-and-consistent-with-this-document, or to wait for §5. Randomized bodies are identical either way.
- **Indirectness is deliberately a scalar.** The hybrid auto-assessor (§7) is optional and project-specific; the canonical GRADE input is a reviewer judgement. If you skip the LLM path entirely, always pass `indirectness_levels` explicitly.
- **Absolute effects need a baseline.** Without `baseline_risk_per_1000` (or for continuous measures) the SoF absolute column is `None` — the relative effect carries the row instead.

## Companion documents

- **The complete version of this document:** `grade_certainty_shareable.md` — same component, with §5 (rating up) written and the upgrade path present in the combiner, the reference implementation, and the tests. Prefer it unless you specifically want the downgrade-only draft.

- **Pooling (Component B):** `pooling_meta_analysis_shareable.md` — produces the pooled result this agent consumes.
- **Evidence table (Component A/T2):** `table2_evidence_table_shareable.md` — the per-study extraction feeding the pooler.
- **Per-study risk of bias:** `robins_i_v2_shareable.md`, `robins_i_v1_shareable.md`, `rob2_cluster_shareable.md`, `rob2_crossover_shareable.md`, `quadas2_shareable.md` — produce the `per_study_rob` labels the risk-of-bias domain aggregates.
