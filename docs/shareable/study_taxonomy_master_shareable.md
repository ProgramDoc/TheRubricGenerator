# Study Taxonomy & Agent Pipeline — Sharable Methodology Reference

A self-contained master reference for the full agent pipeline: the **study-design taxonomy**, the **classification agent**, the **extraction agent**, every **quality-appraisal agent** (risk-of-bias tools, reporting guidelines, GRADE domains), and the **evidence-synthesis agents**. It unifies two previously separate bodies of work — the OGAI study-design taxonomy and classification rubric, and the appraisal platform's deployed agent suite — into one atlas. Contains:

- The unified study-design taxonomy (union of OGAI taxonomy v1.9 and platform taxonomy v2.1) with a per-type routing table: risk-of-bias tool → reporting guideline → initial GRADE certainty → deployment status
- The classification agent in full: methods-over-labels, the three layers of study identity, the primacy hierarchy (Rules 1, 2, 2b, 3, 4), the master decision flowchart, the exogeneity test, the before-after spectrum, the 11 design features with the feature-to-type consistency matrix, the confusion-pair disambiguation catalog, the cluster-subtype decision tree — plus the deployed classification prompt verbatim
- The extraction agent in full: the three-layer field catalog (universal / type-specific / design modifiers), the deployed extraction prompt verbatim, the selective-assembly contract, and the three-stage large-document pipeline
- A methodology digest of **every** quality-appraisal agent — RoB 2 (parallel, cross-over, cluster), ROBINS-I V1 and V2, QUADAS-2 and QUADAS-3, AMSTAR-2, the six reporting-guideline checkers, GRADE indirectness, GRADE imprecision, the per-paper GRADE combiner, and outcome extraction — each with a pointer to its full standalone companion document where one exists
- Digests of the evidence-synthesis agents: pooling / meta-analysis, the per-study evidence table (Table 2), the body-of-evidence GRADE agent, and the systematic-review synthesis pipeline
- The cross-agent engineering conventions every implementation should preserve
- An appendix consolidating, verbatim, every reference implementation and test sketch from the companion documents
- Implementation notes for other platforms

**Sources.** This document consolidates two lineages. (1) The **OGAI study-design taxonomy and classification rubric** — the OGAI Pipeline v3 reference site (Taxonomy v1.9, 32 types, March 2026; Classification Rubric v1.8; Extraction Fields Reference v1.6) and the *OGAI AI-CEA Pipeline v3.1 — AI Classification, Extraction & Appraisal Rubric* (February 2026), published at <https://programdoc.github.io/StudyTaxonomy/>. (2) The **appraisal platform's deployed agents** (platform taxonomy v2.1, 33 types), whose per-instrument methodologies are transcribed in the companion documents listed in §1.3 — each companion carries the full academic citation for its instrument (RoB 2: Sterne 2019; ROBINS-I: Sterne 2016 / 20 Nov 2025 cribsheet; QUADAS-2: Whiting 2011; AMSTAR-2: Shea 2017; GRADE handbook chapters; etc.).

**Scope.** This is the *atlas*: the taxonomy, the routing, the classification and extraction methodologies in full, and a working digest of every downstream agent. For agents that have a standalone companion document, the companion is the document of record — the digest here states what the agent is, its unit of assessment, its judgement scales, and how it plugs into the pipeline, and defers signaling-question text, decision trees, prompts, and reference implementations to the companion. For agents that do **not** yet have a companion (RoB 2 parallel-group, QUADAS-3, AMSTAR-2, and the six reporting-guideline checkers), the digest here is richer and is marked *standalone document pending*.

Explicitly **out of scope**: verbatim signaling-question and checklist-item text for instruments whose companion carries it; the numerical statistics engines (effect sizes, pooling models, heterogeneity — see `pooling_meta_analysis_shareable.md` and `synthesis_meta_analysis_shareable.md`); rubric generation and answer judging for LLM benchmarking (a separate subsystem, not part of this pipeline).

> **Deployment-status convention — read this first.**
> This document deliberately mixes two kinds of content, and every section is tagged so a reader always knows which they are looking at:
>
> - **Deployed** — behavior a working implementation exhibits today. Prompts tagged *deployed* are transcribed verbatim from the production agents.
> - **Reference** — methodology specified by the OGAI rubric that a *complete* implementation should exhibit, but which the current deployed agents implement only partially or not at all. Reference material is not speculative — it is the design target — but a reader replicating "what the platform does" should implement the deployed parts first and treat reference parts as the roadmap.
>
> The flagship example: the deployed classification agent (§3.11, deployed) is a single taxonomy-constrained prompt returning three keys, while the full classification rubric (§3.1–3.10, reference) adds primacy rules, design-feature cross-validation, confusion-pair disambiguation, and mismatch documentation.

---

## 1. The pipeline at a glance

### 1.1 Flow

```
PAPER (PDF)
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│ CLASSIFICATION AGENT (§3)                                    │
│ study type within the unified taxonomy (§2)                  │
│ deployed: taxonomy-constrained prompt →                      │
│    {major_category, subcategory, study_type}                 │
│ reference adds: primacy rules, 11 design features,           │
│    confusion pairs, author-label concordance,                │
│    confidence + alternative                                  │
└──────────┬───────────────────────────────────────────────────┘
           │
     ┌─────┴──────┐
     │  ROUTING    │ ← unified routing table (§2.3):
     └─────┬──────┘   type → RoB tool + reporting guideline + initial GRADE
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│ EXTRACTION AGENT (§4)                                        │
│ Layer 1 universal fields (32, 8 groups)                      │
│ Layer 2 type-specific fields (per study type, RoB-aligned)   │
│ Layer 3 design modifiers (cross-cutting overlays)            │
│ reference adds: classification-validation block,             │
│    red-flag re-routing                                       │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│ QUALITY-APPRAISAL AGENTS — per (study × outcome) unit (§5)   │
│ • Outcome extraction → the outcome axis (§8.4)               │
│ • Risk-of-bias tool matched to the study type (§6)           │
│ • Reporting-guideline adherence check (§7)                   │
│ • GRADE indirectness + imprecision (§8.1–8.2)                │
│ • Per-paper GRADE combiner (§8.3)                            │
└──────────┬───────────────────────────────────────────────────┘
           │  many appraised studies
           ▼
┌──────────────────────────────────────────────────────────────┐
│ EVIDENCE-SYNTHESIS AGENTS — per body of evidence (§9)        │
│ • Per-study evidence table (Table 2)                         │
│ • Pooling / meta-analysis                                    │
│ • Body-of-evidence GRADE agent (all 5 + 3 domains)           │
│ • Systematic-review synthesis pipeline                       │
└──────────────────────────────────────────────────────────────┘
```

Two design principles run through the whole pipeline:

1. **Classification exists to drive correct appraisal.** The study type is not an end in itself — it selects the extraction template, the risk-of-bias instrument, the reporting guideline, and the starting GRADE certainty. When classification is ambiguous, the tiebreaker is always: *which risk-of-bias tool must be used to properly evaluate this study?*
2. **Misclassification is caught and corrected rather than silently propagated** (reference). Downstream stages carry validation hooks — design-feature consistency checks at classification time, red-flag blocks at extraction time, domain-applicability checks at appraisal time — that can send a study back for re-routing or human review.

### 1.2 The agent roster

| # | Agent | Unit of work | Status | Methodology of record |
|---|-------|--------------|--------|----------------------|
| 1 | Study classification | one paper → study type | deployed + reference rubric | **this document** §3 |
| 2 | Field extraction (three-layer) | one paper → structured fields | deployed + reference validation | **this document** §4 |
| 3 | Outcome extraction | one paper → appraisable outcome list | deployed | `outcome_extraction_shareable.md` |
| 4 | RoB 2 (parallel-group RCT) | (study × outcome) | deployed | **this document** §6.1 *(standalone pending)* |
| 5 | RoB 2 cross-over extension | (study × outcome) | deployed | `rob2_crossover_shareable.md` |
| 6 | RoB 2 cluster extension (CRT) | (study × outcome) | deployed | `rob2_cluster_shareable.md` |
| 7 | ROBINS-I V2 (incl. single-arm variant) | (study × outcome) | deployed | `robins_i_v2_shareable.md` |
| 8 | ROBINS-I V1 (opt-in; incl. single-arm variant) | (study × outcome) | deployed | `robins_i_v1_shareable.md` |
| 9 | QUADAS-2 | (study × estimate) | deployed | `quadas2_shareable.md` |
| 10 | QUADAS-3 v1.2 | (study × estimate) | deployed | **this document** §6.6 *(standalone pending)* |
| 11 | AMSTAR-2 | one systematic review | deployed | **this document** §6.8 *(standalone pending)* |
| 12 | Reporting-guideline checkers (CONSORT 2025, CONSORT cross-over, CONSORT cluster, STROBE, STARD 2015, PRISMA 2020) | one paper | deployed | **this document** §7 *(standalones pending; cross-over/cluster companions exist — see §7.3)* |
| 13 | GRADE indirectness (per-paper) | (study × outcome) | deployed | `quality_appraisal_grade_shareable.md` §4 |
| 14 | GRADE imprecision (per-paper) | (study × outcome) | deployed | `quality_appraisal_grade_shareable.md` §5 |
| 15 | Per-paper GRADE combiner | (study × outcome) | deployed | `quality_appraisal_grade_shareable.md` §§1–3, 6 |
| 16 | Pooling / meta-analysis agent | body of evidence (outcome × comparison × design class) | deployed | `pooling_meta_analysis_shareable.md` |
| 17 | Per-study evidence table (Table 2) | study × outcome × comparison × timepoint rows | deployed | `table2_evidence_table_shareable.md` |
| 18 | Body-of-evidence GRADE agent | one pooled outcome | deployed | `grade_certainty_shareable.md` |
| 19 | Systematic-review synthesis pipeline | one review (screen → extract → RoB → pool → GRADE) | deployed | `synthesis_meta_analysis_shareable.md` |
| — | EPOC, NOS/ROBINS-E, QUIPS, PROBAST, JBI, AXIS, CASP, MMAT, CHEC, STROBE-MR, SCCS-checklist tools | (study × outcome) | reference (routed, not built) | routing rows in §2.3 |

### 1.3 Companion documents

All companions are self-contained sibling documents distributed alongside this one: `outcome_extraction_shareable.md`, `rob2_crossover_shareable.md`, `rob2_cluster_shareable.md`, `robins_i_v1_shareable.md`, `robins_i_v2_shareable.md`, `quadas2_shareable.md`, `quality_appraisal_grade_shareable.md`, `grade_certainty_shareable.md` (and its downgrades-only draft variant `grade_certainty_downgrades_shareable.md`), `pooling_meta_analysis_shareable.md`, `table2_evidence_table_shareable.md`, `synthesis_meta_analysis_shareable.md`. Where this document and a companion disagree, **the companion wins** for that agent's internals; this document wins for taxonomy, routing, and cross-agent contracts.

---

## 2. The unified study-design taxonomy

### 2.1 Two lineages, one union

Two taxonomy versions were maintained in parallel and have now converged to near-identity:

- **OGAI taxonomy v1.9** (32 types, March 2026) — deliberately *consolidated by RoB-tool and reporting-guideline utility*: types that route to the same instrument were merged (prospective/retrospective/ambidirectional cohort → Cohort Study; case-control/nested/case-cohort → Case-Control; case report + case series → Case Report / Series; SR ± meta-analysis kept split because AMSTAR-2 items 11/12/15 differ; guideline + consensus statement → Guideline / Consensus). Includes **Controlled Before-After**.
- **Platform taxonomy v2.1** (33 types) — the deployed classification agent's taxonomy. Identical structure, but adds two uncontrolled experimental designs the appraisal platform supports (**Single-Arm Trial**, **Dose-Escalation Study**) and omits Controlled Before-After.

This document canonicalizes the **union: 34 study types** across 5 major categories. Each type below carries its deployment status:

- **A — appraisable (deployed)**: classification + extraction + full quality-appraisal pipeline deployed (the 13 registry types).
- **C — classify/extract (deployed)**: the deployed classifier can assign the type and (for most) type-specific extraction fields exist, but appraisal routing is reference-only (the study is marked *skipped* by the appraisal orchestrator, with the charge refunded).
- **T — taxonomy-only (reference)**: in the unified tree, but not yet in the deployed classifier's taxonomy prompt.

### 2.2 The unified tree (34 types)

```
Study Designs
├── Primary Studies
│   ├── Randomized Controlled
│   │   ├── Randomized Controlled Trial        [A]
│   │   ├── Cluster Randomized Trial           [A]  (parallel-cluster; subtypes §2.4)
│   │   ├── Stepped-Wedge Cluster RCT          [C]  (appraisal reference-only — see §2.4)
│   │   └── Crossover Trial                    [A]
│   ├── Non-Randomized Controlled
│   │   └── Non-Randomized Trial               [A]
│   ├── Non-Randomized Uncontrolled
│   │   ├── Single-Arm Trial                   [A]  (platform extension, absent from v1.9)
│   │   └── Dose-Escalation Study              [A]  (platform extension, absent from v1.9)
│   ├── Quasi-Experimental
│   │   ├── Interrupted Time Series            [C]
│   │   ├── Controlled Before-After            [T]  (v1.9 type, absent from deployed classifier)
│   │   ├── Uncontrolled Before-After          [C]
│   │   ├── Difference-in-Differences          [C]
│   │   └── Regression Discontinuity           [C]
│   └── Qualitative & Mixed Methods
│       ├── Qualitative Research               [C]
│       └── Mixed Methods                      [C]
├── Observational Studies
│   ├── Descriptive
│   │   ├── Case Report / Series               [C]
│   │   ├── Cross-Sectional (Descriptive)      [C]
│   │   └── Ecological Study                   [C]
│   ├── Analytical
│   │   ├── Case-Control                       [A]  (incl. nested case-control, case-cohort)
│   │   ├── Cohort Study                       [A]  (prospective, retrospective, ambidirectional)
│   │   ├── Cross-Sectional (Analytical)       [A]
│   │   ├── Self-Controlled Case Series        [C]
│   │   ├── Case-Crossover                     [A]
│   │   └── Mendelian Randomization            [C]
│   └── Diagnostic / Prognostic
│       ├── Diagnostic Accuracy                [A]
│       ├── Prognostic Factor Study            [C]
│       └── Prediction Model Study             [C]
├── Evidence Synthesis
│   └── Reviews
│       ├── SR without Meta-Analysis           [A]
│       ├── SR with Meta-Analysis              [A]
│       ├── Umbrella Review                    [C]
│       ├── Network Meta-Analysis              [C]
│       ├── Scoping Review                     [C]
│       └── Narrative Review                   [C]
├── Guidance / Consensus
│   └── Guidelines & Consensus
│       └── Guideline / Consensus              [C]
└── Economic & Decision Models
    └── Economic Evaluation                    [C]
```

**Consolidation rationale (v1.9, adopted here).** The tree is intentionally coarser than a methodological textbook's. The test for whether two designs are one type or two is: *do they route to a different RoB tool, reporting guideline, extraction template, or GRADE starting level?* Prospective and retrospective cohorts share ROBINS-I + STROBE + Low, so they are one type — the prospective/retrospective distinction is captured as an extraction field (temporal framework, one of the 11 design features of §3.8), not a taxonomy split. Nested case-control and case-cohort likewise fold into Case-Control. SR with vs. without meta-analysis stays split because AMSTAR-2's meta-analysis items and the extraction template genuinely differ.

### 2.3 The master routing table

One row per unified type: risk-of-bias tool → reporting guideline → initial GRADE certainty → status. Deployed rows are exact; reference rows carry the OGAI routing map's assignment for implementers who extend coverage.

| Study type | RoB tool | Reporting guideline | Initial GRADE | Status |
|---|---|---|---|---|
| Randomized Controlled Trial | RoB 2 (2019) | CONSORT 2025 | High | **A (deployed)** |
| Cluster Randomized Trial | RoB 2 cluster ext. (2021) | CONSORT + cluster ext. (Campbell 2012) | High | **A (deployed)** |
| Crossover Trial | RoB 2 cross-over ext. | CONSORT + cross-over ext. (Dwan 2019) | High | **A (deployed)** |
| Stepped-Wedge Cluster RCT | RoB 2 cluster + SW considerations (reference) | CONSORT stepped-wedge ext. (reference) | High | C (appraisal reference-only — §2.4) |
| Non-Randomized Trial | ROBINS-I V2 (V1 opt-in) | STROBE | Low | **A (deployed)** |
| Single-Arm Trial | ROBINS-I V2/V1 single-arm variant | STROBE (pragmatic reuse) | Very low | **A (deployed)** |
| Dose-Escalation Study | ROBINS-I V2/V1 single-arm variant | STROBE (pragmatic reuse) | Very low | **A (deployed)** |
| Interrupted Time Series | EPOC criteria (reference) | EPOC / STROBE (reference) | Low | C |
| Controlled Before-After | EPOC criteria (reference) | EPOC / STROBE (reference) | Low | T (reference) |
| Uncontrolled Before-After | EPOC criteria (reference) | EPOC / STROBE (reference) | Very Low | C |
| Difference-in-Differences | ROBINS-I (reference) | STROBE (reference) | Low | C |
| Regression Discontinuity | ROBINS-I (reference) | STROBE (reference) | Low | C |
| Cohort Study | ROBINS-I V2 (V1 opt-in) | STROBE | Low | **A (deployed)** |
| Case-Control | ROBINS-I V2 (V1 opt-in) | STROBE | Low | **A (deployed)** |
| Cross-Sectional (Analytical) | ROBINS-I V2 (approximation) | STROBE | Low | **A (deployed)** |
| Case-Crossover | ROBINS-I V2 (approximation) | STROBE | Low | **A (deployed)** |
| Self-Controlled Case Series | adapted ROBINS-I / SCCS checklist (reference) | SCCS guidelines (reference) | Low | C |
| Mendelian Randomization | STROBE-MR checklist (reference) | STROBE-MR (reference) | Low | C |
| Case Report / Series | JBI / CARE checklist (reference) | CARE / PROCESS (reference) | Very Low / N-A | C |
| Cross-Sectional (Descriptive) | AXIS / JBI (reference) | STROBE (reference) | N/A | C |
| Ecological Study | adapted NOS (reference) | STROBE (reference) | Very Low | C |
| Diagnostic Accuracy | QUADAS-3 v1.2 (default) or QUADAS-2 (per-run toggle) | STARD 2015 | High (accuracy framework) | **A (deployed)** |
| Prognostic Factor Study | QUIPS (reference) | REMARK (reference) | Low (modified GRADE) | C |
| Prediction Model Study | PROBAST (reference) | TRIPOD (reference) | separate framework | C |
| SR with Meta-Analysis | AMSTAR-2 | PRISMA 2020 | none (confidence rating instead) | **A (deployed)** |
| SR without Meta-Analysis | AMSTAR-2 | PRISMA 2020 | none (confidence rating instead) | **A (deployed)** |
| Umbrella Review | AMSTAR-2 (reference) | PRISMA (reference) | depends on included reviews | C |
| Network Meta-Analysis | AMSTAR-2 + CINeMA (reference) | PRISMA-NMA (reference) | NMA framework | C |
| Scoping Review | none (typically) | PRISMA-ScR (reference) | N/A | C |
| Narrative Review | none | none standardized | N/A | C |
| Guideline / Consensus | AGREE II (reference) | — | N/A | C |
| Qualitative Research | CASP Qualitative (reference) | COREQ / SRQR (reference) | GRADE-CERQual (reference) | C |
| Mixed Methods | MMAT (reference) | GRAMMS (reference) | per component | C |
| Economic Evaluation | CHEC / Drummond (reference) | CHEERS (reference) | separate framework | C |

Notes on the deployed rows:

- **Uncontrolled designs start at Very low**, one step below confounded-comparison designs: the absence of *any* comparator is a more severe limitation than a confounded comparison, and the GRADE combiner clamps further downgrades at Very low.
- **Diagnostic Accuracy starts at High** per the GRADE handbook's treatment of cross-sectional accuracy designs; case-control accuracy designs are downgraded through the participant-selection domain of the QUADAS tools rather than through a lower starting level. PICO-style indirectness and imprecision are *skipped* for accuracy studies — those modules assume treatment trials, not PIRT (Patient / Index test / Reference standard / Target condition) questions.
- **AMSTAR-2 emits a confidence rating, not a GRADE certainty** (High / Moderate / Low / Critically low). The initial-GRADE column is empty by design and the GRADE-domain agents are skipped for review papers.
- **ROBINS-I V2 for Case-Control, Cross-Sectional (Analytical), and Case-Crossover is a best-available approximation** — V2 is published for follow-up (cohort) studies; these designs use it pending design-specific tooling.

### 2.4 Cluster-randomized subtypes

The cluster family has three subtypes; the two lineages handle them differently, and the union keeps both views coherent:

- **Parallel cluster RCT** — clusters randomized once to an arm and stay there. This is the deployed **Cluster Randomized Trial** type: RoB 2 cluster extension (Domain 1a randomization + the cluster-specific Domain 1b identification/recruitment-timing) + CONSORT cluster extension. Deployed.
- **Stepped-Wedge Cluster RCT** — all clusters begin in control and cross over to intervention at *randomized* time points. Kept as its own classify-able type (the deployed classifier can assign it; extraction reuses the cluster field set), but **appraisal is deliberately not routed**: the published RoB 2 CRT cribsheet covers only parallel cluster trials, and stepped-wedge needs an additional time-trend / time-period-confounding treatment. Reference guidance: assess whether crossover *timing* was truly randomized (Domain 1); recruitment practices may differ between control and intervention periods within a cluster (1b); awareness of upcoming crossover may change behavior in late control periods (Domain 2); multiple plausible correlation-structure / time-trend model specifications inflate selective-reporting risk (Domain 5); consider GRADE downgrades for time-period confounding and learning-curve indirectness.
- **Cluster crossover RCT** — clusters receive both/all interventions in randomized sequence with washout; switching is *bidirectional* (unlike stepped-wedge). Not a separate type in either lineage's classifier; reference material for a future subtype.

**Subtype decision tree** (reference, for classifiers that go finer than the deployed one):

```
Cluster-level randomization confirmed
│
├─ Do ALL clusters eventually receive the intervention?
│  ├─ YES → Cross over control→intervention at randomized time points
│  │        (sequential rollout)?
│  │   ├─ YES → stepped_wedge
│  │   └─ NO  → Both/all interventions in randomized sequence with washout?
│  │       ├─ YES → cluster_crossover
│  │       └─ NO  → parallel (flag for review if contradictory)
│  └─ NO  → Clusters remain in one arm throughout?
│      ├─ YES → parallel
│      └─ NO  → Switch between arms with washout?
│          ├─ YES → cluster_crossover
│          └─ NO  → flag for manual review
```

Disambiguation signals: "all sites eventually received the intervention", "sequential rollout", "staggered implementation", "randomized to early or late implementation" → stepped-wedge; "washout period between interventions" at cluster level, "each hospital received both protocols" → cluster crossover; "clusters randomized to A or B" with no crossover → parallel. Two traps: **phased rollout without randomized timing is not stepped-wedge** (classify as controlled/uncontrolled before-after); **staggered recruitment** (clusters *enter* at different times but stay in their arm) is not stepped-wedge — stepped-wedge requires intervention status to change over time.

---

## 3. The classification agent

The classification agent assigns each paper one study type from the unified taxonomy. §§3.1–3.10 give the full OGAI classification rubric — the reference methodology, with the elements the deployed agent already implements noted inline. §3.11 gives the deployed classification profile verbatim.

### 3.1 Core principle: methods over labels

Authors routinely mislabel their own studies. Empirical audits report error rates of **97%** for "case-control" labels in rehabilitation journals, **72%** for cohort/case-series confusion, and **34%** in general medical journals.

**Rule: read the Methods section and classify based on what was actually done.** Capture the author's stated label separately (`author_stated_design`, verbatim) and flag discordance (`author_label_concordant`) rather than letting the label drive the classification. The label remains a useful *signal* — the deployed prompt (§3.11) uses an explicitly stated design as the primary signal precisely because most papers are correctly labelled — but the reference methodology treats it as one input to be checked against the design features (§3.8), never as the answer.

### 3.2 The three layers of study identity

Every clinical study decomposes into three independent layers, and classification is only trivial when they agree:

| Layer | Definition | Examples |
|---|---|---|
| **1 — Data architecture** | How data were collected and structured | Prospective cohort, retrospective database, cross-sectional survey, RCT, administrative claims |
| **2 — Analytic framework** | The statistical/methodological approach | Segmented regression (ITS), regression discontinuity, Cox regression, sensitivity/specificity, prediction modelling |
| **3 — Research question** | What the study seeks to answer | Diagnostic accuracy, prognostic factor, prediction model, intervention effect, etiologic association |

When the layers diverge (a retrospective database analyzed with segmented regression to answer a causal question; an RCT re-analyzed for prognostic factors), the primacy hierarchy resolves the conflict. The overriding principle: **classification exists to drive correct appraisal** — ask which RoB tool must be used to properly evaluate the study, and classify so that routing selects it.

### 3.3 The primacy hierarchy (five resolution rules)

Work through the rules sequentially; the first that fires decides the classification.

**Rule 1 — Diagnostic accuracy primacy.** If the research question is fundamentally about test performance (sensitivity, specificity, PPV/NPV, likelihood ratios against a reference standard; 2×2-table derivations), classify as **Diagnostic Accuracy** regardless of the data architecture. Rationale: the QUADAS tools evaluate patient selection, index-test conduct, reference-standard validity, and flow/timing — concerns no treatment-trial RoB tool covers. → QUADAS-2/3, STARD.

**Rule 2 — Prediction model primacy.** If the study develops, validates, or updates a *formal prediction model for individualized risk estimation* (risk score, nomogram, algorithm; discrimination via c-statistic/AUC; calibration; internal/external validation), classify as **Prediction Model Study** regardless of architecture. → PROBAST, TRIPOD. *Boundary test:* does the study produce a deployable tool intended for individualized prediction? If not, fall to Rule 2b.

**Rule 2b — Prognostic factor primacy.** If the primary research question is identifying *factors that predict an outcome* — without formalizing a prediction instrument — classify as **Prognostic Factor Study** regardless of architecture. → QUIPS, REMARK. *Boundary vs. Rule 2:* no nomogram/score/discrimination-calibration package → 2b. *Boundary vs. cohort:* prognostic-factor identification must be the *primary stated objective* with analyses structured around factor–outcome associations; a study primarily examining an etiologic exposure–outcome association ("does smoking cause lung cancer?") stays a cohort under Rule 4.

**Rule 3 — Quasi-experimental design primacy.** If the analytic framework constitutes a quasi-experimental causal-inference strategy exploiting *genuinely exogenous* variation (policy change, regulatory threshold, natural disaster, algorithmic cutoff), classify by quasi-experimental type (ITS, RDD, DiD, natural experiment, CBA/UBA). → EPOC (ITS/CBA/UBA) or ROBINS-I (other quasi-experimental). *Critical qualifier:* the method must exploit exogenous variation. Physician-selected exposure analyzed with DiD or propensity scores remains observational (Rule 4).

**Rule 4 — Data architecture as default.** When the question is etiologic/associational and Rules 1–3 do not fire, classify by how the data were collected. *Sub-rule 4a (cross-sectional within a cohort):* a study drawing on a longitudinal cohort but analyzing a single time point with no follow-up is classified cross-sectional (descriptive if no hypothesis is tested).

### 3.4 Master decision flowchart

| Step | Question | If YES | If NO |
|---|---|---|---|
| 1 | Index test vs. reference standard with sensitivity/specificity outputs? | → Diagnostic Accuracy (QUADAS, STARD) | → 2 |
| 2 | Formal prediction model with c-statistic, calibration, validation? | → Prediction Model Study (PROBAST, TRIPOD) | → 3 |
| 3 | Primary objective is identifying prognostic factors, without formalizing a model? | → Prognostic Factor Study (QUIPS, REMARK) | → 4 |
| 4 | Quasi-experimental causal inference exploiting genuinely exogenous variation? | → classify by quasi-experimental type (EPOC or ROBINS-I) | → 5 |
| 5 | Advanced analytics (DiD, propensity scores, IV) *without* exogenous variation? | → classify by data architecture; record the method as a modifier | → 6 |
| 6 | All three layers align? | → standard classification | → document the mismatch; classify by primary analytic contribution |

### 3.5 The exogeneity test

The determining factor for quasi-experimental vs. observational-with-advanced-analytics:

| Feature | Quasi-experimental | Observational + advanced analytics |
|---|---|---|
| Source of exposure variation | Exogenous: policy change, regulatory threshold, natural disaster, algorithm cutoff | Endogenous: physician decision, patient preference |
| Can the exposed unit manipulate assignment? | No | Yes |
| Does the analytic method exploit the exogenous variation? | Yes — the discontinuity/interruption *is* the identification strategy | Method reduces confounding but exploits no natural experiment |
| Example | RDD using a D-dimer ≥ 500 cutoff | DiD comparing CGM vs. no-CGM with physician-selected initiation |

### 3.6 The before-after spectrum

Disambiguation for system-level interventions evaluated over time:

| Feature | Interrupted Time Series | Controlled Before-After | Uncontrolled Before-After |
|---|---|---|---|
| Data points per period | ≥ 3 per segment (EPOC minimum) | 1 per period | 1 per period |
| Control group | Optional (controlled ITS has one) | Concurrent non-equivalent control | None |
| Analytic method | Segmented regression, ARIMA | Pre-post in both groups | Simple pre-post |
| Can model secular trends? | Yes | No (control partially compensates) | No |
| Initial GRADE | Low | Low | Very Low |

**Test:** (1) ≥ 3 data points per segment with time-series analytics → ITS. (2) Concurrent non-equivalent control group → CBA. (3) Neither → UBA.

### 3.7 Top-down decision tree

The full walkthrough a classifier follows (type names are the unified taxonomy's):

```
STEP 1: Does the study synthesize existing studies?
├── YES → Evidence Synthesis
│   ├── Systematic search, no pooling → SR without Meta-Analysis
│   ├── Statistical pooling → SR with Meta-Analysis
│   ├── Network of indirect comparisons → Network Meta-Analysis
│   ├── Review of reviews → Umbrella Review
│   ├── Maps the evidence landscape → Scoping Review
│   └── No systematic search → Narrative Review
│
└── NO → Primary study
    │
    STEP 2: Did the investigator assign an intervention?
    ├── YES → Experimental
    │   STEP 3: Was allocation random?
    │   ├── YES → individual randomization, parallel arms → Randomized Controlled Trial
    │   │        individual randomization, all arms sequentially → Crossover Trial
    │   │        cluster-level randomization → Cluster Randomized Trial
    │   │          (sequential randomized rollout → Stepped-Wedge Cluster RCT — §2.4)
    │   └── NO  → concurrent non-equivalent control → Non-Randomized Trial
    │            (or Controlled Before-After for site-level pre/post with control)
    │            no comparator at all → Single-Arm Trial
    │              (dose-finding with DLT/MTD machinery → Dose-Escalation Study)
    │            pre/post only, ≥3 points per segment → Interrupted Time Series
    │            pre/post only, <3 points per segment → Uncontrolled Before-After
    │            policy/threshold cutoff → Regression Discontinuity
    │            treated vs. untreated across a change point → Difference-in-Differences
    │
    └── NO → Observational or other
        STEP 4: Is there an analytical exposure–outcome comparison?
        ├── YES → sampled by outcome (cases + controls) → Case-Control
        │        sampled by exposure, followed for outcome → Cohort Study
        │        population sample, measured once → Cross-Sectional (Analytical)
        │        group-level data only → Ecological Study
        │        within-person exposed vs. unexposed windows → Self-Controlled Case Series
        │        within-person hazard vs. control windows (transient exposure) → Case-Crossover
        │        genetic instruments for causal inference → Mendelian Randomization
        ├── NO (descriptive) → single patient or series without comparison → Case Report / Series
        │        prevalence survey, no hypothesis → Cross-Sectional (Descriptive)
        └── Diagnostic / prognostic / other →
            index test vs. reference standard → Diagnostic Accuracy
            factors predicting an outcome → Prognostic Factor Study
            multivariable model development/validation → Prediction Model Study
            non-numeric data (interviews, ethnography) → Qualitative Research
            quantitative + qualitative combined → Mixed Methods
            costs vs. outcomes → Economic Evaluation
            recommendation development → Guideline / Consensus
```

### 3.8 The 11 design features (cross-validation) — reference

Alongside the type, the classifier extracts 11 design features. They are an internal consistency check: a feature pattern that contradicts the assigned type is a warning, not a silent pass.

| # | Feature | Values |
|---|---|---|
| 1 | Investigator-assigned intervention | yes / no |
| 2 | Random allocation | yes / no / unclear |
| 3 | Comparison group present | yes / no / implied |
| 4 | Direction of inquiry | exposure→outcome / outcome→exposure / simultaneous / within-person |
| 5 | Temporal framework | prospective / retrospective / ambidirectional / cross-sectional |
| 6 | Unit of randomization/analysis | individual / cluster / population / within-person |
| 7 | Sampling basis | exposure / outcome / population / convenience / genetic |
| 8 | Data collection timing | prospective / retrospective / both |
| 9 | Primary outcome measure type | (free text) |
| 10 | Individual-level exposure–outcome analysis | yes / no |
| 11 | Synthesizes existing studies | yes / no |

Feature-to-type consistency matrix (any mismatch → `_feature_consistency_warnings`):

| Feature pattern | Expected type |
|---|---|
| investigator-assigned + random + individual unit | RCT / Crossover |
| investigator-assigned + random + cluster unit | Cluster RCT |
| investigator-assigned + NOT random | Non-Randomized Trial / quasi-experimental |
| not investigator + outcome sampling + no follow-up | Case-Control |
| not investigator + exposure sampling + follow-up | Cohort Study |
| not investigator + population sampling + single time point | Cross-Sectional |
| not investigator + group-level unit + no individual data | Ecological Study |
| no comparison + outcome sampling | Case Report / Series |
| synthesizes existing + systematic search | SR ± MA / NMA |

*Deployment note:* the deployed platform records a compatible reduced set on each annotation for audit — `rule1_pass` … `rule3_pass`, `natural_experiment_flag`, `author_stated_design`, `author_label_discordance`, `reviewer_action` — populated by the reviewer or a downstream pipeline rather than by the classification call itself.

### 3.9 Confusion pairs — disambiguation catalog — reference

The empirically common misclassifications, each with the single discriminating feature:

1. **Case-control ↔ retrospective cohort.** Sampling strategy: selected because they *had the outcome*, matched to those who did not → case-control; selected by *exposure status* with outcomes from records → cohort. *Red flag:* a "case-control" reporting risk ratios, hazard ratios, or incidence rates is likely a retrospective cohort.
2. **Cross-sectional ↔ case-control.** Deliberately selected and matched controls → case-control; a population sample stratified after the fact → cross-sectional.
3. **Case series ↔ cohort.** Can the study calculate incidence? Patients selected because they already had the condition → case series; selected by exposure and followed → cohort, even without a named comparison group.
4. **Prospective ↔ retrospective cohort.** When was the cohort assembled relative to the outcomes? (In the unified taxonomy both are Cohort Study; the answer lands in the *temporal framework* feature.)
5. **Non-randomized trial ↔ prospective cohort.** Did the *investigator* assign the exposure? Investigator decides → NRT; routine-practice choice by clinician/patient → cohort.
6. **Controlled ↔ uncontrolled before-after.** Concurrent non-equivalent control site/ward present → CBA; temporal comparison only → UBA.
7. **ITS ↔ uncontrolled before-after.** ≥ 3 data points per segment enabling segmented regression → ITS; 1 pre / 1–2 post → UBA.
8. **Nested case-control ↔ prediction model.** Analytical purpose: multivariable prediction equation with AUC/calibration → Prediction Model Study regardless of sampling; odds ratios for exposure–outcome associations → case-control.

Plus the cluster-subtype pairs in §2.4.

### 3.10 Reference output schema

The complete Stage-1 output a full implementation emits:

```json
{
  "classification": {
    "major_category": "string",
    "subcategory": "string",
    "study_type": "string — one of the unified types",
    "cluster_subtype": "parallel | stepped_wedge | cluster_crossover | null",
    "confidence": "high | moderate | low",
    "alternative_classification": "string or null",
    "alternative_rationale": "string or null",
    "author_stated_design": "string or null — verbatim from the article",
    "author_label_concordant": "boolean",
    "primacy_rule_applied": "rule_1 | rule_2 | rule_2b | rule_3 | rule_4 | none",
    "data_architecture": "string",
    "analytic_framework": "string",
    "research_question_type": "string"
  },
  "design_features": { "…the 11 features of §3.8…": "…" },
  "reasoning_chain": ["Step 1: [decision and evidence]", "…"],
  "_feature_consistency_warnings": ["string"]
}
```

Low-confidence classifications with a non-null alternative trigger **dual extraction** downstream (§4.9).

### 3.11 The deployed classification profile

The production classifier is deliberately minimal: one LLM call, the taxonomy inline, three output keys, deterministic post-filtering. Everything in §§3.3–3.10 beyond this is reference methodology.

**Prompt (verbatim; `{TAXONOMY}` is the §2.2 tree flattened to the category → subcategory → type listing):**

```
You are a clinical research methodologist. Read this PDF and classify it using the taxonomy below.

Return ONLY a valid JSON object with exactly these three keys:
- "major_category": one of the major category names
- "subcategory": the subcategory within that major category
- "study_type": the specific study type

Taxonomy:
{TAXONOMY}

Rules:
- Choose the single best-fitting classification based on the study design described in the paper.
- If the paper explicitly states its design, use that as your primary signal.
- If uncertain between two options, choose the more specific one.
- Return ONLY the JSON object — no explanation, no markdown fences.

Example output:
{"major_category": "Primary Studies", "subcategory": "Randomized Controlled", "study_type": "Randomized Controlled Trial"}
```

The deployed taxonomy block:

```
Major categories and their subcategories and study types:

Primary Studies:
  Randomized Controlled → Randomized Controlled Trial, Cluster Randomized Trial, Stepped-Wedge Cluster RCT, Crossover Trial
  Non-Randomized Controlled → Non-Randomized Trial
  Non-Randomized Uncontrolled → Single-Arm Trial, Dose-Escalation Study
  Quasi-Experimental → Interrupted Time Series, Uncontrolled Before-After, Difference-in-Differences, Regression Discontinuity
  Qualitative & Mixed Methods → Qualitative Research, Mixed Methods

Observational Studies:
  Descriptive → Case Report / Series, Cross-Sectional (Descriptive), Ecological Study
  Analytical → Case-Control, Cohort Study, Cross-Sectional (Analytical), Self-Controlled Case Series, Case-Crossover, Mendelian Randomization
  Diagnostic / Prognostic → Diagnostic Accuracy, Prognostic Factor Study, Prediction Model Study

Evidence Synthesis:
  Reviews → SR without Meta-Analysis, SR with Meta-Analysis, Umbrella Review, Network Meta-Analysis, Scoping Review, Narrative Review

Guidance / Consensus:
  Guidelines & Consensus → Guideline / Consensus

Economic & Decision Models:
  Economic Evaluation → Economic Evaluation
```

**Post-processing contract:**

- The response is parsed as JSON (with markdown-fence stripping tolerance) and **whitelist-filtered to exactly the three keys**; values are stringified. Any extra keys the model volunteers are dropped.
- A missing `study_type` is a hard failure (the call errors; it is never silently defaulted).
- The `study_type` string must match a taxonomy leaf **verbatim** — it is the join key into the extraction field catalog (§4.3) and the appraisal routing table (§2.3).

**Classification and oversized documents:** study design is a paper-level property stated early (title, abstract, methods). When a paper is too large to attach whole and the text-fallback path chunks it (§4.7), classification runs in *classification mode*: only the **first chunk** (abstract + introduction + early methods) is sent, and no cross-chunk merge is attempted — a holistic single-output judgement cannot be map-reduced.

---

## 4. The extraction agent

The extraction agent fills a structured annotation form from the paper. Its field catalog is **three-layered**, and the layering is the load-bearing design decision: universal fields make every study comparable, type-specific fields capture what the risk-of-bias instrument for that design will need, and modifier tags capture cross-cutting features that change interpretation without changing classification or routing.

### 4.1 Three-layer architecture

```
Layer 1 — UNIVERSAL (every study, 32 fields in 8 groups)
          citation, objective, population/PICO, sample, setting,
          outcomes, key findings, admin
Layer 2 — TYPE-SPECIFIC (selected by the classification)
          fields aligned to the routed RoB tool's domains
Layer 3 — DESIGN MODIFIERS (cross-cutting overlays, 11 deployed)
          phase, regulatory context, data source, adaptive/pragmatic, …
```

A key property carried over from the OGAI design: **Layer-2 fields map onto the routed RoB tool's signaling questions**, so extraction feeds appraisal instead of running parallel to it. An RCT's Layer-2 fields cover the five RoB 2 domains; a diagnostic-accuracy study's cover the QUADAS domains; a cohort's cover ROBINS-I's confounding/selection/classification concerns. The appraisal agents receive these fields as pre-extracted context alongside the PDF.

### 4.2 Layer 1 — universal fields (32, in 8 groups) — deployed

| Group | Fields |
|---|---|
| `citation` (5) | `citation_authors`, `citation_year`, `citation_title`, `citation_journal`, `citation_doi` |
| `objective` (1) | `study_objective` |
| `population` (4) | `population_participants`, `population_intervention_exposure`, `population_comparator`, `population_outcomes` |
| `sample` (3) | `sample_size_total`, `sample_size_per_group`, `power_calculation_reported` |
| `setting` (5) | `setting`, `country_region`, `study_period_enrollment_start`, `study_period_enrollment_end`, `follow_up_duration` |
| `outcomes` (4) | `primary_outcome_definition`, `primary_outcome_measurement`, `primary_outcome_timing`, `secondary_outcomes` |
| `results` (6) | `key_findings_effect_estimate`, `key_findings_metric`, `key_findings_ci_lower`, `key_findings_ci_upper`, `key_findings_pvalue`, `key_findings_direction` |
| `admin` (4) | `funding_source`, `conflicts_of_interest`, `limitations_stated`, `protocol_registration` |

Group selection is part of the request contract: a caller may extract any subset of the 8 groups; an empty/absent selection means *all* universal fields.

### 4.3 Layer 2 — type-specific fields — deployed

26 of the 34 unified types carry type-specific fields; the remaining 8 (Case Report / Series, Cross-Sectional (Descriptive), Ecological Study, Self-Controlled Case Series, Mixed Methods, Scoping Review, Narrative Review, and the taxonomy-only Controlled Before-After) have none — Layer 1 suffices. The full deployed catalog:

| Study type | Type-specific fields |
|---|---|
| Randomized Controlled Trial | `randomization_method`, `allocation_concealment`, `allocation_ratio`, `stratification_factors`, `baseline_balance`, `blinding_participants`, `blinding_personnel`, `blinding_outcome_assessors`, `protocol_deviations`, `analysis_framework`, `attrition_rate`, `missing_data_handling`, `outcome_measurement_method`, `protocol_available`, `outcomes_match_protocol`, `consort_flow_diagram` |
| Cluster Randomized Trial | `cluster_unit`, `n_clusters`, `icc_reported`, `recruitment_after_randomization`, `clustering_in_analysis`, `contamination_risk` |
| Stepped-Wedge Cluster RCT | (shares the Cluster Randomized Trial set) |
| Crossover Trial | `washout_period`, `carryover_assessment`, `period_effects`, `sequence_order`, `paired_analysis` |
| Non-Randomized Trial | `concurrent_control_confirmed`, `allocation_mechanism`, `baseline_comparability`, `confounding_control`, `blinding` |
| Single-Arm Trial | `primary_endpoint_prespecified`, `inclusion_exclusion_criteria`, `comparator_historical_reference`, `consecutive_enrolment` |
| Dose-Escalation Study | `escalation_scheme`, `dlt_definition`, `dose_levels`, `mtd_declared`, `rp2d`, `expansion_cohort`, `pk_pd_reported` |
| Interrupted Time Series | `n_data_points_pre`, `n_data_points_post`, `intervention_date`, `control_series`, `statistical_method`, `level_change`, `slope_change`, `autocorrelation_addressed`, `seasonality_adjustment`, `concurrent_events` |
| Uncontrolled Before-After | `pre_measurement`, `post_measurement`, `secular_trend_risk`, `regression_to_mean_risk`, `concurrent_events` |
| Difference-in-Differences | `exogenous_event`, `parallel_trends_evidence`, `n_pre_period_points`, `interaction_term`, `common_shocks`, `staggered_adoption` |
| Regression Discontinuity | `running_variable`, `cutoff_value`, `sharp_vs_fuzzy`, `bandwidth_selection`, `manipulation_testing`, `continuity_plots` |
| Cohort Study | `exposure_definition`, `exposure_measurement`, `comparator_group`, `outcome_ascertainment`, `confounders_measured`, `adjustment_method`, `loss_to_follow_up`, `immortal_time_bias` |
| Case-Control | `case_definition`, `case_source`, `control_selection`, `matching`, `exposure_ascertainment`, `recall_bias_risk` |
| Case-Crossover | `case_definition_ccx`, `exposure_definition_ccx`, `hazard_period`, `control_period`, `induction_period`, `temporal_direction`, `exposure_variability`, `conditional_logistic`, `self_selection_bias` |
| Cross-Sectional (Analytical) | `sampling_method`, `response_rate`, `exposure_outcome_simultaneity`, `adjustment_method` |
| Mendelian Randomization | `instrument_variants`, `f_statistic`, `mr_design`, `sample_overlap`, `pleiotropy_tests`, `exclusion_restriction` |
| Diagnostic Accuracy | `index_test`, `reference_standard`, `blinding_index_to_reference`, `blinding_reference_to_index`, `two_by_two_table`, `spectrum_of_patients`, `verification_bias`, `threshold_effects`, `flow_and_timing` |
| Prognostic Factor Study | `prognostic_factor`, `outcome_definition`, `study_participation`, `study_attrition`, `pf_measurement`, `confounding_control`, `statistical_analysis` |
| Prediction Model Study | `predictors_candidate`, `predictor_selection_method`, `model_type`, `discrimination`, `calibration`, `model_presentation`, `model_stage` |
| SR without Meta-Analysis | `search_strategy`, `inclusion_criteria`, `study_selection`, `data_extraction`, `included_studies_n`, `rob_tool_used`, `synthesis_method`, `grade_assessment`, `prisma_flow` |
| SR with Meta-Analysis | `search_strategy`, `inclusion_criteria`, `included_studies_n`, `effect_measure`, `pooled_estimate`, `pooling_model`, `heterogeneity`, `publication_bias`, `sensitivity_analyses`, `subgroup_analyses`, `grade_assessment`, `prisma_flow` |
| Umbrella Review | `search_strategy`, `inclusion_criteria`, `included_studies_n`, `rob_tool_used`, `synthesis_method`, `grade_assessment`, `prisma_flow` |
| Network Meta-Analysis | `search_strategy`, `inclusion_criteria`, `included_studies_n`, `effect_measure`, `pooled_estimate`, `heterogeneity`, `publication_bias`, `sensitivity_analyses`, `grade_assessment`, `prisma_flow` |
| Economic Evaluation | `evaluation_type`, `perspective`, `time_horizon`, `discount_rate`, `model_type`, `cost_inputs`, `effectiveness_source`, `icer`, `sensitivity_analysis` |
| Guideline / Consensus | `guideline_organization`, `panel_composition`, `evidence_base`, `grade_used`, `recommendations`, `updating_plan` |
| Qualitative Research | `methodology`, `data_collection`, `sampling_strategy`, `data_saturation`, `reflexivity`, `themes` |

### 4.4 Layer 3 — design modifiers — deployed (+ reference extended catalog)

Eleven cross-cutting modifier fields are deployed; they apply to any base type without changing classification or routing:

`clinical_trial_phase` · `regulatory_context` · `registration_number` · `industry_sponsored` · `data_source_type` · `database_name` · `adaptive_design` · `pragmatic_vs_explanatory` · `trial_framework` (superiority / non-inferiority / equivalence) · `target_trial_emulation` · `pilot_or_feasibility`

The OGAI extraction reference specifies a richer modifier catalog (reference) that a full implementation should adopt as structured objects rather than flat strings: adaptive-design detail (`adaptation_type`: sample-size re-estimation / arm dropping / dose finding / biomarker-adaptive / seamless phase; interim-analysis count), PRECIS-2 spectrum position for pragmatic-vs-explanatory, master-protocol type (umbrella / basket / platform), factorial-design factor matrix, Bayesian framework with prior specification, registry-based-trial flag, `natural_experiment_flag` with the exogenous-event description and the author's exogeneity argument, and data-source detail (linkage method, code-validation PPV).

### 4.5 The deployed extraction prompt

One LLM call, PDF attached, selective field list assembled per request. Prompt verbatim (`{study_type}` from classification; `{field_list}` is the assembled ids, one `  - field_id` per line):

```
You are a clinical research data extractor. Extract information from this PDF to fill a structured annotation form for a {study_type} study.

Return ONLY a valid JSON object — no preamble, no markdown fences, no explanation. Keys must be exactly the field IDs below.

Rules:
- Short factual values (1–3 sentences max). Omit fields not found (do not include null or empty string).
- Do not invent values. Extract only what is explicitly stated.
- Numeric fields: return just the number/value as a string.
- DOI: return the DOI string only, without "https://doi.org/".

Fields:
{field_list}

Return only the JSON object.
```

**Selective-assembly contract.** The three layers are selected independently, and the None-vs-empty distinction is part of the API:

| Parameter | `None` / absent | `[]` (explicit empty) | list |
|---|---|---|---|
| `groups` (Layer 1) | all 8 groups | all 8 groups (empty = all) | named groups only |
| `type_fields` (Layer 2) | all fields for the study type | **none** | intersection with the type's catalog |
| `modifier_fields` (Layer 3) | all 11 modifiers | **none** | intersection with the modifier catalog |

Unknown group names and out-of-catalog field ids are silently dropped, preserving order and de-duplicating. The final id list is `universal + type_specific + modifiers`.

**Extraction rules worth preserving in any port:**

- **Absence is omission, not null.** The model is told to *omit* fields it cannot find. Downstream, returned values equal to `None`, `""`, or `[]` are filtered out, so an absent key and a filtered key are indistinguishable — which is the point: the form shows blank, never "null".
- **No invention.** Only explicitly stated content; short factual values (1–3 sentences).
- **Numbers as strings.** All values are strings end-to-end; numeric coercion happens later, only in analytics (a defined subset of fields is float-coerced for min/median/max summaries, with non-numeric cells silently dropped).
- Classification is whitelist-filtered (§3.11); extraction output is *not* whitelisted against the requested ids — a model volunteering an extra field is tolerated. A stricter port may whitelist; the reference behavior is documented here for fidelity.

### 4.6 Custom-schema extraction — deployed

Besides the fixed catalog, the pipeline supports reviewer-defined schemas: a schema can be **parsed from an uploaded document** (a codebook PDF, DOCX, CSV, or pasted text — one LLM call proposes `{field_id, label, description}` entries), **refined** conversationally (one call rewrites the schema per instruction), and then **run** over a batch of papers with the same extraction prompt shape as §4.5, substituting the custom field list. Custom runs reuse the entire large-document pipeline below and the same omission/no-invention rules. Optional extended thinking can be enabled per run, in which case the model's reasoning is captured per paper alongside the extraction.

### 4.7 The three-stage large-document pipeline — deployed

Every extraction-family call (classify / prefill / custom / schema-parse — and every appraisal agent, which reuses this entry point) funnels through one degradation pipeline:

```
0. HARD GATE  — reject files > 32 MB up front (PDF APIs cap there;
                fail fast with an actionable message, refund any charge)
1. PDF-AS-DOCUMENT (fast path, ~95% of papers)
                base64-encode, attach as a document block, one call
     └─ escalate ONLY on a context-window/input-limit rejection;
        any other error is real and propagates
2. TEXT FALLBACK
                extract plain text (per-page markers); image-heavy papers
                that were ~200k tokens as PDF are usually well under 100k
                as text. Empty text → "scanned image-only PDF" error
                suggesting OCR — do not silently return nothing
3. CHUNKED MAP-REDUCE (only if the text still exceeds one window)
                overlapping ~300k-char windows (≈75k tokens), 8k overlap,
                ≤ 8 chunks, ≤ 4 parallel workers; one extraction call per
                chunk; per-field merge = FIRST NON-EMPTY WINS
                (earliest chunk wins on disagreement — abstract/methods/
                results live early, which is where authoritative values are)
                failed chunks are logged and skipped; all-fail → clean error
```

Chunked calls prepend a context note telling the model it is seeing plain text (figures invisible) and, when chunked, *which* chunk — with the instruction to omit fields not present in *this* chunk, which is what makes first-non-empty-wins a sound merge.

**Classification mode** (§3.11): holistic single-judgement tasks (classify, schema proposal) set first-chunk-only — stage 3's merge is meaningless for them.

**Vendor neutrality:** user-facing errors from this pipeline speak of "the AI model" / "the extraction service", never a vendor name. Vendor detail belongs in server logs.

### 4.8 Analytics metadata — deployed

Two field-type sets drive downstream analytics without affecting extraction: a **numeric set** (e.g. `sample_size_total`, `attrition_rate`, `included_studies_n`, `f_statistic`, CI bounds) coerced to float for min/median/max/mean summaries, and a **categorical set** (e.g. `study_type`, `funding_source`, `clinical_trial_phase`, `trial_framework`) drawn from small discrete vocabularies for distribution charts. Ports that add fields should classify them into one of numeric / categorical / free-text — the reference heuristic for unknown fields: ≥ 80% float-parsable → numeric; ≤ 8 unique values and ≤ 60-char max → categorical; else text.

### 4.9 Validation and re-routing — reference

The OGAI Stage-2 design adds a feedback loop the deployed extractor does not yet implement:

- **Classification-validation block.** Every extraction output carries `{author_stated_design, classified_design_confirmed, confidence_in_classification, red_flags[], suggested_reclassification, reclassification_reason}`. The extractor — which reads the paper more deeply than the classifier — is positioned to catch misclassification.
- **Red-flag re-routing.** If `classified_design_confirmed` is false with a non-null suggestion, the orchestrator re-routes to the suggested type's template and re-extracts. Red flags with a confirmed classification are logged, not acted on. A re-routed extraction that also fails validation → human review.
- **Low-confidence dual extraction.** When classification confidence is *low* with an alternative: run extraction under both templates, compare completeness (count of non-null fields), keep the more complete, log both with rationale.
- **Human-review flags.** Both templates fail validation; unresolvable feature-consistency warnings; low confidence with no alternative; dual extraction ties.

---

## 5. Appraisal orchestration — deployed

How one paper flows through the appraisal agents. All of this is deployed behavior.

**The unit of assessment is a (study × outcome) pair** — never the bare paper. Risk-of-bias instruments are outcome-specific (the same trial can be Low for objectively-ascertained mortality and High for an unblinded symptom score), and GRADE rates certainty per outcome. The orchestrator therefore builds a list of assessment *units* per paper:

- **Outcome axis** (treatment designs): the reviewer selects one or more outcomes — from the outcome-extraction agent's candidate list (§8.4), a free-text override, or by default the paper's auto-picked primary outcome (fallback chain: primary-outcome definition → measurement → population outcomes). Each selected outcome becomes one unit and one result row.
- **Estimate axis** (diagnostic accuracy): the unit is an accuracy *estimate* — one 2×2 table (subgroup × index test × threshold × reference standard × unit of analysis). A dedicated extractor lists every numeric sens/spec tuple in the paper; the reviewer picks; no selection falls back to the paper's headline estimate.
- The two axes are **mutually exclusive per paper**. Review papers (AMSTAR-2) collapse to exactly one unit — the tool rates the review itself, not an outcome.

**Per-paper call anatomy** (≈ 10 LLM calls for an RCT; ≈ 12 for non-randomized designs):

1. Classify study design (§3.11) — once per paper.
2. Extract Layer-1 + Layer-2 + Layer-3 fields (§4) — once per paper.
3. Dispatch on `study_type` through the routing table (§2.3). Unsupported type → the paper is marked *skipped* and any charge refunded; never a partial appraisal.
4. Per unit: one LLM call **per RoB domain** (the tool's pure-Python decision tree maps the returned signal answers to the domain judgement — §10), plus the tool's preflight call where it has one.
5. Reporting-guideline adherence (§7) — a single call, once per paper, run *lazily* (a paper whose every RoB call fails never pays for it).
6. Per unit: GRADE indirectness (one call), GRADE imprecision (one call) — skipped for diagnostic accuracy (`skip_grade_extras`) and for reviews (`skip_grade`).
7. Per unit: the per-paper GRADE combiner (§8.3) — pure arithmetic, no LLM.

**Per-run tool toggles**, resolved at dispatch by shallow-copying the routed config (the registry itself is never mutated — batch runs are concurrent):

- Diagnostic accuracy: QUADAS-3 (default) ↔ QUADAS-2. Gated so the toggle cannot reroute a non-accuracy paper.
- ROBINS-I designs: V2 (default) ↔ V1 opt-in. Applies wherever the routed tool is ROBINS-I — cohort *and* single-arm types.
- Cluster RCTs: Domain 2 aim — `assignment` (ITT, default) ↔ `adhering` (per-protocol).

**Transparency contract.** Every prompt template, signaling question, and decision tree is exposed through a developer view — the trees are plain Python functions whose source is shown verbatim, so a reviewer can see exactly how any judgement was produced. This is a design requirement, not a debugging convenience: keep judgement logic in inspectable code, never buried in prompts (§10).

---

## 6. Risk-of-bias agents

Common contract (all tools): input = PDF bytes + the extracted fields (§4) + the classification + the unit's outcome/estimate; per-domain LLM calls answer signaling questions on the tool's scale; a **pure-Python decision tree** maps signal answers to the domain judgement; an aggregator derives the overall judgement; output = per-domain `{judgement, signal answers, rationale, evidence quotes}` + overall. Evidence quotes are verbatim snippets, which downstream UIs can locate in the source PDF.

### 6.1 RoB 2 — parallel-group randomized trials — deployed *(standalone document pending — this digest is the current sharable reference)*

- **Source:** the revised Cochrane risk-of-bias tool for randomized trials, RoB 2 (Sterne JAC et al., BMJ 2019;366:l4898), per the 22 August 2019 cribsheet for parallel-group trials. Assesses the **effect of assignment to intervention** (the ITT question).
- **Structure:** 5 domains, 22 signaling questions (3 / 7 / 4 / 5 / 3):
  1. Bias arising from the randomization process (1.1 sequence random; 1.2 allocation concealed; 1.3 baseline imbalances suggesting a problem)
  2. Bias due to deviations from intended interventions — effect of assignment (2.1–2.7: awareness of assignment; trial-context deviations; deviations balanced/likely to affect outcome; appropriate ITT-style analysis; impact of any mis-analysis)
  3. Bias due to missing outcome data (3.1–3.4: data availability; evidence result not biased; missingness dependence on true value)
  4. Bias in measurement of the outcome (4.1–4.5: inappropriate measurement; differential measurement; assessor awareness; assessment influenced by awareness)
  5. Bias in selection of the reported result (5.1–5.3: pre-specified plan; multiple measurements; multiple analyses)
- **Signal scale:** `Y / PY / PN / N / NI` (Yes / Probably yes / Probably no / No / No information). Conditional ("If Y/PY/NI to X…") questions are answered by the LLM but **NA is derived in code** when the precondition fails — the cascade-enforcement pattern (§10).
- **Judgement scale:** Low / Some concerns / High per domain, via the cribsheet's per-domain decision trees transcribed as pure functions.
- **Overall:** Low if all domains Low; High if any domain High **or** several domains have Some concerns; Some concerns otherwise.
- **GRADE hand-off:** Low → 0; Some concerns → −1; High → −1 (−2 if ≥ 2 domains High). See §8.3.
- Extraction fields feeding it: the RCT Layer-2 set (§4.3) — randomization/concealment/balance for D1, blinding + analysis framework for D2, attrition for D3, measurement method for D4, protocol/registration for D5.
- The cross-over (§6.2) and cluster (§6.3) extensions reuse these domains and trees where unchanged; their companions document only the deltas in full.

### 6.2 RoB 2 cross-over extension — deployed → `rob2_crossover_shareable.md`

For individually randomized trials where each participant receives all interventions sequentially (AB/BA). **6 domains / 23 signals**: the parallel-group five plus **Domain S — bias arising from period and carryover effects** (washout adequacy, carryover assessment, period effects), and a fourth Domain-5 question (5.4) for selective first-period-only reporting on the basis of a carryover test. Domains 1–4 reuse the parallel-group signal trees. Scales and overall rule as §6.1. The companion also carries the CONSORT cross-over reporting checklist (§7.3).

### 6.3 RoB 2 cluster extension (RoB 2 CRT) — deployed → `rob2_cluster_shareable.md`

For **parallel** cluster-randomized trials (18 March 2021 cribsheet; stepped-wedge is explicitly out of scope — §2.4). **6 domains**: 1a randomization; **1b — bias arising from the timing of identification or recruitment of participants** (the cluster-specific domain: were individuals identified/recruited after cluster allocation was known?); 2–5 as in RoB 2. **Domain 2 has two variants** selected per run: `assignment` (ITT, 8 signals) and `adhering` (per-protocol, 6 signals). Decision trees are transcribed independently from the CRT cribsheet and *diverge* from parallel RoB 2 in places (e.g. concealed-but-non-random allocation is *Some concerns* in D1a); only D5 and the overall rule are shared. Signal 3.2 has no NI option; conditional NA is derived in code. Companion carries the CONSORT cluster checklist (§7.3).

### 6.4 ROBINS-I V2 — deployed → `robins_i_v2_shareable.md`

The default tool for every non-randomized intervention design (20 Nov 2025 cribsheet). **6 domains** (V2 retired V1's separate deviations domain): confounding; classification of interventions; selection of participants; missing data; outcome measurement; selection of the reported result. **Preflight call** answers screening questions B1/B2/B3 + C4: B2 or B3 = Y/PY short-circuits the whole assessment to **Critical**; C4 (does the analysis account for post-baseline deviations?) dispatches **Domain 1 Variant A** (ITT-like, baseline confounding) vs **Variant B** (per-protocol, adds time-varying confounding). **Single-arm variant** (project extension for Single-Arm Trial / Dose-Escalation Study): pinned by study type before preflight; replaces B1/B2 with benchmark-pre-specification questions, reframes D1 as benchmark adequacy + prognostic-mix comparability (1S.*) and D2 as intervention fidelity + intent-vs-received cohort definition; D3–D6 unchanged. Signal scale adds strength tokens (`SY/WY/WN/SN`) on designated questions; judgements are 4-level **Low / Moderate / Serious / Critical** (the V1 "No information" judgement is retired), with Domain 1's Low labelled "Low (except for concerns about uncontrolled confounding/benchmarking)" — normalized to plain Low before GRADE mapping.

### 6.5 ROBINS-I V1 — deployed, opt-in per run → `robins_i_v1_shareable.md`

The 1 Aug 2016 original, kept co-resident for teams standardized on V1 vocabularies. **7 domains** (confounding; selection into the study; classification of interventions; **deviations from intended interventions** — aim-gated; missing data; outcome measurement; selective reporting), 5-token signal scale, 5-level judgement scale (adds "No information"). An **aim preflight** determines whether the study estimates the effect of *assignment* (ITT) or of *starting and adhering* (per-protocol), which gates Domain 4's signal path. Its own single-arm adaptation mirrors V2's (D1-SA benchmark signals 1S.1–1S.5, D2-SA signals 2S.1–2S.3, D4 = NA in code with no LLM call). The companion's migration notes map V1 ↔ V2 vocabulary conservatively.

### 6.6 QUADAS-3 v1.2 — diagnostic test accuracy — deployed *(standalone document pending — this digest is the current sharable reference)*

- **Source:** QUADAS-3 v1.2 (the successor to QUADAS-2, restructured around the "ideal test accuracy trial" target). Assesses **risk of bias and applicability per accuracy estimate**, not per paper.
- **Structure:** 4 domains, 20 signaling questions; domains 1–3 carry an **applicability** judgement alongside RoB, domain 4 is RoB-only:
  1. **Participants** (4 signals: single-gate design; prospective enrolment; consecutive/random sampling; intended-use representativeness) + applicability
  2. **Index Test** (4: recommended instructions; blinding to reference standard; in-practice information available; threshold pre-specified) + applicability
  3. **Target Condition** (8: reference-standard adequacy; complete vs. partial verification; differential verification; incorporation bias; reference-standard conduct, blinding, threshold, interval) + applicability
  4. **Analysis** (4: all participants analyzed; missing-data handling; unit of analysis; sens/spec calculation) — RoB only
- **Scales:** signals `Y / PY / PN / N / NI`; judgements 3-level **Low / High / Insufficient information**. Applicability judged as concern that the conducted study does not match the *ideal trial* the reviewer defines (an optional free-text review-context input conditions these prompts; without it, applicability is judged against a generic intended-use baseline).
- **Decision tree** (conservative reading of the instrument's Phase 5): all signals Y/PY → Low; any N/PN → High; otherwise → Insufficient information. The instrument narratively allows reviewer judgement to hold a domain at Low despite an N — baking that into a deterministic tree would be arbitrary, so the tree stays conservative and the per-signal rationales support a human override in the write-up.
- **Overall** (Phase 6): the same 3-level rule applied across the 4 domain RoB judgements → overall RoB, and across the 3 applicability-bearing domains → overall applicability. Direction-of-bias is reported NA — a treatment-trial concept.
- **Per-estimate path:** classify + extract + STARD run once per paper; QUADAS-3 + GRADE run once per estimate; each estimate is its own result row. The estimate extractor (one call listing every sens/spec tuple) is tool-agnostic and shared with QUADAS-2.
- **GRADE hand-off:** Low → 0; High → −1 (−2 if ≥ 2 High domains); Insufficient information → −1 (conservative). Indirectness/imprecision skipped (PICO modules don't fit PIRT questions); initial certainty High (§2.3).
- Out of scope in the current version: the per-estimate domain-difference shortcut (every estimate runs all 4 domains); QUADAS-C comparative accuracy; PIRT-aware indirectness/imprecision.

### 6.7 QUADAS-2 — deployed, per-run alternative → `quadas2_shareable.md`

The classic 2011 tool most published reviews still use (Whiting et al., Ann Intern Med 2011;155:529-536). **4 domains / 11 signals** (Patient Selection 3; Index Test 2; Reference Standard 2; Flow & Timing 4), signal scale `Y / N / U`, judgements **Low / High / Unclear**, dual RoB + applicability on domains 1–3. Applicability is framed against the **review question in PIRT terms** (vs. QUADAS-3's ideal-trial framing) — same free-text context input, different meaning. Decision tree: all Y → Low; any N → High; else Unclear. Shares QUADAS-3's estimate extractor and per-estimate path. GRADE: Unclear → −1 conservative; Low/High as above.

### 6.8 AMSTAR-2 — systematic reviews — deployed *(standalone document pending — this digest is the current sharable reference)*

- **Source:** Shea BJ et al., "AMSTAR 2: a critical appraisal tool for systematic reviews," BMJ 2017;358:j4008. Registered for SR with and without meta-analysis (items 11/12/15 carry a "No meta-analysis conducted" path).
- **Structurally unlike the primary-study tools.** It scores **16 checklist items** (not bias domains), each rated **Yes / Partial Yes / No** (some Yes/No only), and its headline output is an **overall confidence rating** — High / Moderate / Low / Critically low — *not* a GRADE certainty. GRADE, indirectness, and imprecision are all skipped for review papers.
- **The 16 items** (critical items — the published default set {2, 4, 7, 9, 11, 13, 15} — shown in bold): 1 PICO components in the research question · **2** protocol established before the review · 3 explanation of study-design selection · **4** comprehensive literature search · 5 study selection in duplicate · 6 data extraction in duplicate · **7** list of excluded studies with justification · 8 adequate description of included studies · **9** satisfactory risk-of-bias technique · 10 funding sources of included studies · **11** appropriate meta-analysis methods · 12 impact of RoB on the meta-analysis · **13** accounting for RoB when interpreting results · 14 explanation and discussion of heterogeneity · **15** investigation of publication bias · 16 conflicts of interest of the review.
- **Per-item scoring:** the LLM answers each item's Y/N *sub-criteria* (transcribed from the checklist + guidance document); a pure decision function derives the item rating. Logic types: `all_required` (Yes iff every sub-criterion Y), `one_of`, `tiered` (Partial Yes = the partial-tier sub-criteria; Yes = those plus the yes-tier), `rob_design` (item 9 — evaluated per included design; a both-designs review takes the lower rating), `meta_design` (item 11 — design-aware Yes/No).
- **Preflight:** one call determines `review_includes` (rct / nrsi / both — items 9 and 11 are design-aware) and `meta_analysis` (was quantitative synthesis performed). When no synthesis was performed, items 11/12/15 are set to "No meta-analysis conducted" **in code, with no LLM call** (the NA-cascade pattern, §10). Calls per paper: 1 preflight + ≤ 16 item calls.
- **Overall confidence** (the published algorithm): a *critical flaw* = a critical item rated No; a *non-critical weakness* = a non-critical item rated No (Partial Yes and "No meta-analysis conducted" are not flaws). **High** = 0 critical flaws, ≤ 1 weakness · **Moderate** = 0 critical, > 1 weakness · **Low** = exactly 1 critical flaw · **Critically low** = ≥ 2 critical flaws.
- **Display caution:** AMSTAR-2's labels collide with the RoB vocabulary with opposite polarity — "High" is *good* here and *bad* for RoB tools. Any UI or export must key badge/colour semantics on the tool, not the label string.
- Out of scope in the current version: per-run custom critical-item sets; umbrella reviews / NMA (routed as reference, §2.3); reviewer override of item ratings.

---

## 7. Reporting-guideline agents

### 7.1 The shared contract — deployed *(standalone documents pending — this digest is the current sharable reference)*

Reporting-guideline adherence is a *reporting* signal, deliberately separate from the risk-of-bias judgement: poor adherence does not prove poor methods, and perfect adherence does not prove rigor — but unreported methods cannot be appraised, and missing items correlate empirically with methodological weakness.

Every guideline checker follows one contract:

- A module-level **item catalog**: `{id, section, text, …}` per checklist entry, transcribed from the published statement. Sub-items (10a/10b…) are separate entries.
- **One LLM call per paper** (not per item): the model receives the PDF + the item catalog and returns, per item, `{adhered: true|false|"n/a", evidence: "verbatim or near-verbatim snippet"}`.
- **Score** = adhered ÷ applicable. Items judged not applicable (e.g. adverse-event items for non-invasive imaging; registration items for retrospective records reviews) are excluded from **both** numerator and denominator — an N/A never penalizes.
- Run **once per paper** (outcome units share it), and lazily — only after at least one RoB domain call has succeeded.

### 7.2 The deployed guidelines

| Guideline | For | Entries | Source |
|---|---|---|---|
| CONSORT 2025 | parallel-group RCTs | 30 items → 42 entries with sub-items | the 2025 update of the CONSORT statement |
| CONSORT + cross-over extension | crossover trials | base + 16 `X-` items | Dwan K et al., 2019 (CONSORT extension for randomised crossover trials) |
| CONSORT + cluster extension | cluster RCTs | base + 14 `C-` items | Campbell MK et al., 2012 (CONSORT extension for cluster randomised trials) |
| STROBE 2007 | observational designs (and pragmatically, single-arm) | 22 items → 34 entries | von Elm E et al., STROBE statement 2007 |
| STARD 2015 | diagnostic accuracy | 30 items → 34 entries (a/b sub-items) | Bossuyt PM et al., BMJ 2015;351:h5527 |
| PRISMA 2020 | systematic reviews | 27 items → 42 entries | Page MJ et al., BMJ 2021;372:n71 |

The extension checkers **import the base CONSORT items** and append the extension entries with prefixed ids — one call still covers the combined checklist.

### 7.3 Extension companions

The cross-over and cluster extension checklists are additionally documented, item by item, as the reporting-guideline companion sections of their RoB siblings: CONSORT cross-over in `rob2_crossover_shareable.md` §10, CONSORT cluster in `rob2_cluster_shareable.md` §11.

### 7.4 Critical-item tiering and RoB integration — reference

The deployed checkers score items flat; the OGAI Stage-3 reference goes further, and implementers extending the reporting layer should adopt it:

- **Three item tiers.** *Critical* items — absence raises internal-validity concerns (for CONSORT: sequence generation 8a, allocation concealment 9, implementation 10, blinding 11a, primary-outcome statistical methods 12a, participant flow 13a/13b/16, primary results with effect size and precision 17a). *Important* items — absence reduces interpretability/reproducibility. *Minor* items — administrative detail.
- **Adherence quality categories:** Complete (all critical, ≤ 1 important missing) / Adequate (all critical, 2–3 important missing) / Partial (1–2 critical missing) / Inadequate (≥ 3 critical missing — the study cannot be fully appraised, and GRADE should consider a risk-of-bias downgrade on the basis that unreported methods are more likely deficient).
- **Reporting-gap → RoB coupling.** A RoB domain that depends on unreported information cannot be judged Low: unreported allocation concealment caps RoB 2 Domain 1 at *Some concerns*; unreported STARD blinding items map onto the QUADAS index-test domain; PRISMA vs. AMSTAR-2 overlap is resolved by letting AMSTAR-2 own the *quality* judgement while the PRISMA check records what was *reported* (no double-counting).

---

## 8. GRADE-domain agents (per-paper)

These three agents produce the appraisal platform's per-(paper × outcome) certainty rating. **Do not confuse this with the body-of-evidence GRADE agent** (§9.3) — the disambiguation table in `quality_appraisal_grade_shareable.md` is the canonical statement of the difference. In one line: this path rates *one appraised study* on the three domains a single paper can support (risk of bias, indirectness, imprecision); the GRADE agent rates *one pooled outcome* on all five downgrade + three upgrade domains.

### 8.1 Indirectness — deployed → `quality_appraisal_grade_shareable.md` §4

One LLM call judges the four PICO subdomains — population, intervention, comparator, outcome — each on a 4-level scale (`direct / probably_direct / probably_not_direct / not_direct`), plus a surrogate-outcome flag. Judged **against the reviewer's target PICO** when supplied; otherwise falls back to outcome-surrogacy assessment (the other three subdomains default toward `probably_direct` unless the as-conducted PICO is unusually narrow). The GRADE handbook's surrogate rule is baked into the prompt: surrogates rate down unless a strong, well-established correlation with patient-important outcomes exists — a criterion rarely fulfilled. A pure severity tree aggregates: none (0) / serious (−1: one `not_direct` or ≥ 2 `probably_not_direct`) / very serious (−2: two `not_direct`) / extremely serious (−3: ≥ 3 `not_direct`).

### 8.2 Imprecision — deployed → `quality_appraisal_grade_shareable.md` §5

One LLM call judges four subdomains on the mirror-image scale (`precise / probably_precise / probably_not_precise / not_precise`): **CI width** vs. decision thresholds (the reviewer's optional MID-benefit/MID-harm pair when supplied, else line-of-no-effect + clinical importance), **sample size** adequacy, **event count** (binary outcomes only — N/A for continuous, normalized so it never contributes to severity), and **fragility** (large relative effects from few events; p just under 0.05 with small N). The same severity tree yields 0/−1/−2/−3. The call also reports the inferred outcome type and the extracted N / events / CI summary for display.

### 8.3 The per-paper GRADE combiner — deployed → `quality_appraisal_grade_shareable.md` §§1–3, 6

Pure arithmetic — no LLM. The ladder is `High → Moderate → Low → Very low`. Total downgrade = RoB levels + indirectness levels + imprecision levels, applied to the design's initial certainty (§2.3) and **capped at Very low**.

The RoB → levels mapping must handle five instruments whose overall-judgement vocabularies overlap (order of branch evaluation matters — "Low" and "High" are shared strings):

| Overall judgement | Instrument | Downgrade |
|---|---|---|
| Low | all | 0 |
| Some concerns | RoB 2 family | −1 |
| High | RoB 2 family / QUADAS | −1, or −2 if ≥ 2 domains High |
| Moderate | ROBINS-I | −1 |
| Serious | ROBINS-I | −1, or −2 if ≥ 2 domains Serious |
| Critical | ROBINS-I | −2 (always) |
| No information | ROBINS-I V1 (legacy) | −1 (conservative) |
| Insufficient information | QUADAS-3 | −1 (conservative) |
| Unclear | QUADAS-2 | −1 (conservative) |
| *anything else* | fallback | −1 |

ROBINS-I Domain 1's "Low (except for concerns about uncontrolled confounding/benchmarking)" labels are normalized to plain "Low" by the tool's aggregator *before* this mapping — skipping that normalization silently costs a level via the fallback branch. The combiner emits a deterministic explanation string naming every contributor ("Downgraded 2 levels: 1 level for Some concerns in risk of bias + 1 level for serious indirectness — surrogate primary outcome (HbA1c)."). The companion carries the exact string grammar, the fail-open ladder caveat, and the turnkey implementation.

### 8.4 Outcome extraction — deployed → `outcome_extraction_shareable.md`

The agent that produces the outcome axis of §5: one call per paper returns a list of *separately appraisable* outcomes (`{name, description, measure, timing, outcome_type, is_primary}`), conservatively split — one outcome with several statistics (HR + KM curve + median survival for overall survival) is **one** outcome, because over-splitting costs a full appraisal pass per spurious entry. Advisory and fully optional: every consumer must fall back to appraising the primary outcome alone.

---

## 9. Evidence-synthesis agents

Downstream of per-study appraisal: many appraised studies → bodies of evidence. Digests only; each companion is the document of record.

### 9.1 Pooling / meta-analysis agent — deployed → `pooling_meta_analysis_shareable.md`

The model-free pooling engine plus its extraction bridge. Per-study effect sizes (OR / RR / RD from 2×2; MD / SMD from means; **IRR from events + person-time, never a count table**; HR from reported estimates; any measure from a pre-computed estimate + CI), inverse-variance fixed/random-effects pooling with DL / REML / Paule-Mandel τ², heterogeneity (Q, I², τ², prediction interval), Egger + trim-and-fill. The bridge groups many studies' extracted outcomes into bodies of evidence — **one body per outcome × comparison × timepoint × design class; randomized and non-randomized studies never pool together** — picks the measure, maps raw arm data or reported effects, and quarantines what cannot be reconciled with named warnings. Outcome-name harmonization (dictionary-first, LLM-for-the-gaps) makes differently-worded outcomes group. Hand-off to GRADE is raw numbers only — no certainty decisions.

### 9.2 Per-study evidence table (Table 2) — deployed → `table2_evidence_table_shareable.md`

The guideline-panel evidence table: **one row = study × outcome × comparison × timepoint**, transcribing each study's *reported* results — explicitly no pooling. Dual-mode: assemble from already-extracted tags (zero model calls) or extract in isolation. Covers study-id building, metric canonicalization, direction-of-benefit inference, statistical reconciliation, and quality-rating mapping.

### 9.3 Body-of-evidence GRADE agent — deployed → `grade_certainty_shareable.md`

The GRADE agent proper: consumes one pooled outcome and rates certainty across **all five downgrade domains** (risk of bias aggregated across studies by pooled weight; inconsistency from I²/Q; indirectness — reviewer-supplied with an optional LLM assist; imprecision from the pooled CI vs. null/MIDs + optimal information size; publication bias from Egger/trim-fill, gated at k ≥ 10) **plus the three upgrade domains** (large effect, dose-response, opposing plausible confounding — gated to non-randomized bodies with no downgrades), then anticipated absolute effects and Summary-of-Findings rows. Rate randomized and non-randomized evidence as separate bodies. A downgrades-only draft variant exists (`grade_certainty_downgrades_shareable.md`); it under-rates non-randomized bodies that qualify for rating up — share the full document unless the draft is specifically wanted.

### 9.4 Systematic-review synthesis pipeline — deployed → `synthesis_meta_analysis_shareable.md`

The end-to-end review workflow: screening (LLM title/abstract + full-text decisions with reasons), structured effect-size extraction, per-(study × outcome) risk of bias reusing the §6 tools, pooling per outcome (the §9.1 engine, embedded), PRISMA flow accounting, and the body-of-evidence GRADE combiner. Also emits runnable R (`meta`/`metafor`) and Python code per calculation so every pooled number is independently reproducible.

---

## 10. Cross-agent engineering conventions

Conventions every agent above observes, and any port should preserve:

1. **Decision trees live in code, not prompts.** The LLM answers signaling questions; deterministic, inspectable pure functions map answers to judgements. This keeps judgements auditable (the tree source can be displayed verbatim), reproducible (same answers → same judgement), and conservatively faithful to the instruments (where a published tool narratively permits reviewer discretion, the tree takes the conservative branch and the per-signal rationales support human override — the "conservative-tree" pattern flagged in each companion).
2. **NA is derived in code, never requested from the model.** Conditional signaling questions ("If Y/PY to 1.2 …") are all answered by the LLM; cascade-enforcement functions overwrite answers with NA when the precondition fails, after the call. Same pattern gates AMSTAR-2's meta-analysis items and ROBINS-I V1's retired D4. Asking a model to output NA correctly is unreliable; deriving it is free.
3. **One LLM call per domain** (not per signal, not per tool). Per-signal calls multiply cost and lose within-domain context; single-call-per-tool overloads the response schema and degrades answer quality on 20+ questions.
4. **JSON-only outputs with fence-stripping tolerance.** Every prompt demands a bare JSON object; every parser still strips markdown fences before parsing. Never feed raw model output to a strict JSON parser.
5. **`llm_call` is injected.** Reference implementations take the model caller as a parameter — no HTTP client, framework, or vendor SDK in the methodology layer. This is what makes every companion's turnkey module portable.
6. **The unit of assessment is (study × outcome)** — or (study × estimate) for accuracy studies, or the review itself for AMSTAR-2. Paper-level steps (classify, extract, reporting guideline) run once; unit-level steps (RoB, indirectness, imprecision, GRADE) run per unit.
7. **Fail whole, refund, never partially appraise.** An unsupported study type, a failed classification, or an all-domains failure marks the unit skipped/errored and releases any charge; a half-assessed study is worse than none.
8. **Verbatim evidence quotes.** Every judgement and adherence call returns short verbatim snippets, enabling source-linked display (quote-to-highlight in the PDF) and human verification. Quotes are best-effort locators, not offsets — do not require character positions from the model.
9. **Registry-driven routing with immutable dispatch.** One table maps study type → tool + guideline + initial certainty + flags (§2.3). Per-run overrides shallow-copy the routed entry; the registry is never mutated at runtime (batch runs are concurrent). Registry keys must match the classifier's emitted type strings and the extraction catalog's keys exactly — enforce with a test.
10. **Vendor-neutral user-facing language.** Errors and UI text say "the AI model" / "the extraction service"; vendor names stay in server logs.
11. **Tool-aware label semantics.** Judgement vocabularies collide across instruments ("High" is bad for RoB 2, good for AMSTAR-2; "Low" the reverse). Key display and mapping logic on (tool, label), never label alone — the §8.3 mapping table shows why branch order matters.
12. **Documents degrade gracefully.** All agents share the §4.7 pipeline: attach-as-PDF → text fallback → chunked map-reduce, with holistic judgements pinned to the first chunk.

---

## 11. Implementation notes for other platforms

- **Build order.** The dependency chain is: taxonomy + classification (§2–3) → extraction (§4) → orchestration (§5) → one RoB tool end-to-end (RoB 2, §6.1, is the best first target: smallest, best documented) → reporting guideline + GRADE domains (§7–8) → the remaining tools → synthesis (§9). Every companion document's turnkey module is independently runnable once §4's extraction output shape exists.
- **Start with the deployed subset.** The deployed pipeline is a complete, working system with the *simple* classifier and *flat* reporting scores. The reference layers (primacy rules as structured output, design-feature cross-validation, red-flag re-routing, critical-item tiering, stepped-wedge appraisal) each bolt onto a defined seam — none require re-architecting.
- **Guard the type strings.** The study-type string is the join key across classifier → extraction catalog → routing registry → UI. A single mismatch ("Cross-sectional (analytical)" vs. "Cross-Sectional (Analytical)") silently drops a design to *skipped*. Pin the vocabulary in one place and test membership in all three tables.
- **Respect the mutually-exclusive unit axes.** Reject a paper carrying both outcome and estimate selections at request time — charging happens before classification, so the conflict must be caught early.
- **Per-domain calls are parallelizable per unit;** paper-level calls are not repeated per unit. A 3-outcome cohort paper is: 1 classify + 1 extract + 1 guideline + 3 × (preflight-dependent domain calls + indirectness + imprecision) + 3 combiner runs.
- **Attach the PDF when you can; degrade deliberately when you cannot** (§4.7). Never chunk a holistic judgement; never merge chunked thinking traces; treat an empty text extraction as "scanned PDF, needs OCR", not as an empty paper.
- **Expose the machinery.** A read-only developer view returning every prompt template, item catalog, and decision-tree source is cheap and is what makes an AI appraisal defensible to methodologists. Transparency is part of the methodology, not an accessory.
- **Mirroring.** Like the companions, this document is designed to be distributed verbatim outside its home repository. Cross-references are to sibling shareable filenames only; keep the set together.

---

## 12. Appendix — consolidated reference code from the companion documents

Every reference implementation and test sketch from the companion shareable documents, transcribed **verbatim** for readers who want the whole agent suite's code in one place. The companion documents remain the documents of record — if this appendix and a companion ever drift, the companion wins, and the surrounding narrative (prompt rationale, decision-tree derivations, sample data) lives only there. All modules follow the same portability contract: single-file, dependency-free unless stated (the pooling engine uses numpy/scipy, with a dependency-free variant described in its companion), with `llm_call` injected — no framework, HTTP, or database code.

Two notes on coverage:

- The agents documented **only** in this master document — the classification agent, the extraction agent, RoB 2 parallel-group, QUADAS-3, AMSTAR-2, and the reporting-guideline checkers — have no shareable reference module yet (their standalone documents are pending). Their code-level contract today is the verbatim deployed prompts and post-processing rules in §§3.11, 4.5, and the digests of §§6–7.
- The downgrades-only draft variant of the body-of-evidence GRADE agent (`grade_certainty_downgrades_shareable.md`) carries a strict subset of the full agent's code (§12.10 below, minus the rating-up functions); it is not duplicated here.

### 12.1 Outcome extraction — from `outcome_extraction_shareable.md`

**From that document's §5. Reference implementation:**

```python
"""Outcome extraction — the list of outcomes a paper can be appraised for."""
from typing import Any, Callable

NAME_CAP, DESCRIPTION_CAP, MEASURE_CAP, TIMING_CAP, LABEL_CAP = 120, 200, 120, 80, 200
OUTCOME_TYPES = ("binary", "continuous", "time-to-event")

PROMPT_HEADER = """Identify every distinct outcome the attached study reports that could be separately appraised for risk of bias.

Risk-of-bias instruments (RoB 2, ROBINS-I) are outcome-specific: domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between outcomes in the same paper. List the outcomes a reviewer would appraise separately.

For each outcome return:
- ``name`` — short label suitable for a UI checkbox (<= 80 chars), e.g. "All-cause mortality"
- ``description`` — the outcome as the paper defines it (<= 200 chars)
- ``measure`` — how it was measured; the instrument, scale, or metric used
- ``timing`` — the timepoint or follow-up window, as reported
- ``outcome_type`` — one of "binary", "continuous", "time-to-event", or "" if unclear
- ``is_primary`` — true only for the paper's stated primary outcome(s)

Rules:
- Do NOT split one outcome into the several statistics reported for it. A hazard ratio, a Kaplan-Meier curve, and a median survival for overall survival are ONE outcome.
- Composite outcomes are one outcome. Do not decompose them into components unless the components are themselves pre-specified outcomes.
- Do NOT list every adverse-event tally as a separate outcome. Include a safety outcome only where the paper pre-specifies a named one.
- List the primary outcome first.
- If the paper states no outcomes at all, return an empty list.

Return ONLY a JSON object of the shape:
{
  "outcomes": [
    {"name": "...", "description": "...", "measure": "...", "timing": "...", "outcome_type": "...", "is_primary": true}
  ]
}
"""


def extract_outcomes(pdf_bytes: bytes,
                     llm_call: Callable[[bytes, str], dict],
                     context: str = "(no pre-extracted fields)",
                     ) -> list[dict[str, Any]]:
    """Every appraisable outcome in the paper. Returns [] rather than raising
    when the model gives back nothing usable — the caller falls back to the
    paper's primary outcome."""
    raw = llm_call(pdf_bytes, PROMPT_HEADER +
                   "\n\nContext (fields already extracted from the paper):\n" + context)
    items = raw.get("outcomes")
    if not isinstance(items, list):
        return []

    out: list[dict[str, Any]] = []
    for oc in items:
        if not isinstance(oc, dict):
            continue
        # Numbered over the entries we keep, so a malformed entry leaves no gap
        # in ids that get stored and exported.
        idx = len(out) + 1
        otype = str(oc.get("outcome_type") or "").strip().lower()
        clean = {
            "id": idx,
            "name": str(oc.get("name") or "").strip()[:NAME_CAP],
            "description": str(oc.get("description") or "").strip()[:DESCRIPTION_CAP],
            "measure": str(oc.get("measure") or "").strip()[:MEASURE_CAP],
            "timing": str(oc.get("timing") or "").strip()[:TIMING_CAP],
            "outcome_type": otype if otype in OUTCOME_TYPES else "",
            "is_primary": bool(oc.get("is_primary")),
            "source": "extracted",
        }
        if not clean["name"]:
            bits = [b for b in (clean["measure"], clean["timing"]) if b]
            clean["name"] = (clean["description"] or " — ".join(bits)
                             or f"Outcome {idx}")[:NAME_CAP]
        out.append(clean)
    return out


def outcome_label(outcome: dict[str, Any]) -> str:
    """The prompt string — NOT the join key. Group studies by outcome["name"]."""
    name = (outcome.get("name") or outcome.get("description") or "").strip()
    bits = [name]
    measure = (outcome.get("measure") or "").strip()
    timing = (outcome.get("timing") or "").strip()
    if measure:
        bits.append(f"measured as {measure}")
    if timing:
        bits.append(f"at {timing}")
    return " — ".join(b for b in bits if b)[:LABEL_CAP]
```

**From that document's §6. Test sketches:**

```python
def _stub(payload):
    return lambda pdf, prompt: payload

# ids are contiguous over the entries kept, so a malformed entry leaves no gap
out = extract_outcomes(b"", _stub({"outcomes": [
    "not a dict", {"name": "Mortality"}, None, {"name": "Quality of life"}]}))
assert [o["id"] for o in out] == [1, 2]
assert [o["name"] for o in out] == ["Mortality", "Quality of life"]

# anything unusable returns [] so the caller can fall back
assert extract_outcomes(b"", _stub({"outcomes": "mortality"})) == []
assert extract_outcomes(b"", _stub({"something_else": []})) == []

# fields are coerced to stripped strings; a missing name is synthesized
out = extract_outcomes(b"", _stub({"outcomes": [
    {"name": "  Mortality  ", "measure": None, "timing": 12},
    {"description": "Death from any cause"},
    {"measure": "6-minute walk distance", "timing": "12 weeks"},
    {}]}))
assert out[0]["name"] == "Mortality" and out[0]["measure"] == "" and out[0]["timing"] == "12"
assert out[1]["name"] == "Death from any cause"
assert out[2]["name"] == "6-minute walk distance — 12 weeks"
assert out[3]["name"] == "Outcome 4"

# outcome_type is normalized to the closed set; unknown degrades to ""
out = extract_outcomes(b"", _stub({"outcomes": [
    {"name": "A", "outcome_type": "  BINARY "},
    {"name": "B", "outcome_type": "ordinal"}]}))
assert [o["outcome_type"] for o in out] == ["binary", ""]

# the label composes; the name stays clean for joining
o = {"name": "Quality of life", "measure": "KCCQ score", "timing": "8 months"}
assert outcome_label(o) == "Quality of life — measured as KCCQ score — at 8 months"
assert outcome_label({"name": "Mortality"}) == "Mortality"
assert outcome_label({}) == ""
assert len(outcome_label({"name": "N" * 150, "measure": "M" * 150})) == 200

print("all outcome-extraction self-checks passed")
```

### 12.2 RoB 2 cross-over extension — from `rob2_crossover_shareable.md`

**From that document's §7. Reference implementation as a single Python file:**

```python
"""rob2_crossover_assessor.py — reference implementation.

Public API:
    assess_crossover_trial(pdf_bytes, study_type, assessed_outcome,
                            extracted_fields, call_llm,
                            outcome_is_override=False) -> dict

`call_llm` is a callable you provide:
    call_llm(system_prompt: str, user_prompt: str, pdf_bytes: bytes) -> dict
It must return the parsed JSON object the model produced.
"""

import json

SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")


def _yes(ans):  return ans in ("Y", "PY")
def _no(ans):   return ans in ("N", "PN")


# ── Decision trees ─────────────────────────────────────────────

def domain1_judge(s):
    q11, q12, q13 = (s.get(k, "NI") for k in ("1.1", "1.2", "1.3"))
    if _no(q12): return "High"
    if q12 == "NI":
        return "High" if _yes(q13) else "Some concerns"
    if _no(q11): return "High"
    return "Some concerns" if _yes(q13) else "Low"


def domain2_judge(s):
    q21, q22, q23, q24, q25, q26, q27 = (s.get(k, "NI") for k in
        ("2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"))
    aware = (not _no(q21)) or (not _no(q22))
    if not aware:
        part1 = "Low"
    elif _no(q23) or q23 == "NI":
        part1 = "Some concerns"
    elif _no(q24):
        part1 = "Some concerns"
    else:
        part1 = "High" if not _yes(q25) else "Some concerns"
    if _yes(q26):
        part2 = "Low"
    else:
        part2 = "Some concerns" if _no(q27) else "High"
    if part1 == "High" or part2 == "High": return "High"
    if part1 == "Low" and part2 == "Low":  return "Low"
    return "Some concerns"


def domainS_judge(s):
    s1, s2, s3, s4 = (s.get(k, "NI") for k in ("S.1", "S.2", "S.3", "S.4"))
    if _yes(s1) and _yes(s4): return "Low"
    if _no(s1) and _no(s2) and _no(s3): return "High"
    if _no(s1) and _no(s4): return "High"
    if _no(s4) and not _yes(s1): return "High"
    return "Some concerns"


def domain3_judge(s):
    q31, q32, q33, q34 = (s.get(k, "NI") for k in ("3.1", "3.2", "3.3", "3.4"))
    if _yes(q31): return "Low"
    if _yes(q32): return "Low"
    if _no(q33):  return "Low"
    if _yes(q34) or q34 == "NI": return "High"
    return "Some concerns"


def domain4_judge(s):
    q41, q42, q43, q44, q45 = (s.get(k, "NI") for k in
        ("4.1", "4.2", "4.3", "4.4", "4.5"))
    if _yes(q41): return "High"
    if _yes(q42): return "High"
    if _no(q43):  base = "Low"
    elif _no(q44): base = "Low"
    elif _yes(q45) or q45 == "NI": base = "High"
    else: base = "Some concerns"
    if q42 == "NI" and base == "Low":
        return "Some concerns"
    return base


def domain5_judge(s):
    q51, q52, q53, q54 = (s.get(k, "NI") for k in ("5.1", "5.2", "5.3", "5.4"))
    if _yes(q52) or _yes(q53) or _yes(q54): return "High"
    if _no(q52) and _no(q53) and _no(q54):
        return "Low" if _yes(q51) else "Some concerns"
    return "Some concerns"


def overall(judgements):
    if any(j == "High" for j in judgements): return "High"
    some = sum(1 for j in judgements if j == "Some concerns")
    if some >= 2: return "High"
    if some >= 1: return "Some concerns"
    return "Low"


# ── Domain definitions ─────────────────────────────────────────
# Each domain: id, name, list of {id, text, elaboration}, and the judge.
# (Question text + elaborations are condensed here; full text is in §2 above.)

DOMAINS = [
    {"id": 1,   "judge": domain1_judge, "relevant_fields": ["randomization_method", "allocation_concealment", "allocation_ratio", "stratification_factors", "baseline_balance", "sequence_order"], "name": "Bias arising from the randomization process",                       "signals": [
        {"id": "1.1", "text": "Was the allocation sequence random?", "elaboration": "<see §2.1>"},
        {"id": "1.2", "text": "Was the allocation sequence concealed until participants were enrolled and assigned to intervention sequences?", "elaboration": "<see §2.1>"},
        {"id": "1.3", "text": "Did baseline differences between intervention groups suggest a problem with the randomization process?", "elaboration": "<see §2.1>"},
    ]},
    {"id": 2,   "judge": domain2_judge, "relevant_fields": ["blinding_participants", "blinding_personnel", "protocol_deviations", "analysis_framework", "missing_data_handling"], "name": "Bias due to deviations from intended interventions (effect of assignment)", "signals": [
        {"id": "2.1", "text": "Were participants aware of their assigned intervention during the trial?", "elaboration": "<see §2.2>"},
        {"id": "2.2", "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?", "elaboration": "<see §2.2>"},
        {"id": "2.3", "text": "If Y/PY/NI to 2.1 or 2.2: Were there deviations from the intended intervention that arose because of the trial context?", "elaboration": "<see §2.2>"},
        {"id": "2.4", "text": "If Y/PY to 2.3: Were these deviations likely to have affected the outcome?", "elaboration": "<see §2.2>"},
        {"id": "2.5", "text": "If Y/PY/NI to 2.4: Were these deviations from intended intervention balanced between groups?", "elaboration": "<see §2.2>"},
        {"id": "2.6", "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?", "elaboration": "<see §2.2>"},
        {"id": "2.7", "text": "If N/PN/NI to 2.6: Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?", "elaboration": "<see §2.2>"},
    ]},
    {"id": "S", "judge": domainS_judge, "relevant_fields": ["washout_period", "carryover_assessment", "period_effects", "paired_analysis"], "name": "Bias arising from period and carryover effects", "signals": [
        {"id": "S.1", "text": "Were carryover effects unlikely to occur in this trial, given the nature of the interventions and the outcome?", "elaboration": "<see §2.3>"},
        {"id": "S.2", "text": "If carryover effects could occur, was there a suitable washout period between treatment periods?", "elaboration": "<see §2.3>"},
        {"id": "S.3", "text": "For trials with potential for carryover effects, were unbiased data available for the analysis (e.g., from periods unaffected by carryover, or via methods that adjust for carryover)?", "elaboration": "<see §2.3>"},
        {"id": "S.4", "text": "Were the data analysed using an appropriate paired analysis that takes the cross-over design into account?", "elaboration": "<see §2.3>"},
    ]},
    {"id": 3,   "judge": domain3_judge, "relevant_fields": ["attrition_rate", "missing_data_handling"], "name": "Bias due to missing outcome data", "signals": [
        {"id": "3.1", "text": "Were data for this outcome available for all, or nearly all, participants randomized?", "elaboration": "<see §2.4>"},
        {"id": "3.2", "text": "If N/PN/NI to 3.1: Is there evidence that the result was not biased by missing outcome data?", "elaboration": "<see §2.4>"},
        {"id": "3.3", "text": "If N/PN to 3.2: Could missingness in the outcome depend on its true value?", "elaboration": "<see §2.4>"},
        {"id": "3.4", "text": "If Y/PY/NI to 3.3: Is it likely that missingness in the outcome depended on its true value?", "elaboration": "<see §2.4>"},
    ]},
    {"id": 4,   "judge": domain4_judge, "relevant_fields": ["blinding_outcome_assessors", "outcome_measurement_method"], "name": "Bias in measurement of the outcome", "signals": [
        {"id": "4.1", "text": "Was the method of measuring the outcome inappropriate?", "elaboration": "<see §2.5>"},
        {"id": "4.2", "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?", "elaboration": "<see §2.5>"},
        {"id": "4.3", "text": "If N/PN/NI to 4.1 and 4.2: Were outcome assessors aware of the intervention received by study participants?", "elaboration": "<see §2.5>"},
        {"id": "4.4", "text": "If Y/PY/NI to 4.3: Could assessment of the outcome have been influenced by knowledge of intervention received?", "elaboration": "<see §2.5>"},
        {"id": "4.5", "text": "If Y/PY/NI to 4.4: Is it likely that assessment of the outcome was influenced by knowledge of intervention received?", "elaboration": "<see §2.5>"},
    ]},
    {"id": 5,   "judge": domain5_judge, "relevant_fields": ["protocol_available", "outcomes_match_protocol", "paired_analysis", "carryover_assessment"], "name": "Bias in selection of the reported result", "signals": [
        {"id": "5.1", "text": "Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?", "elaboration": "<see §2.6>"},
        {"id": "5.2", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements (e.g., scales, definitions, time points) within the outcome domain?", "elaboration": "<see §2.6>"},
        {"id": "5.3", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?", "elaboration": "<see §2.6>"},
        {"id": "5.4", "text": "Is a result based on data from both periods sought, but unavailable on the basis of carryover having been identified?", "elaboration": "<see §2.6>"},
    ]},
]


# ── Prompt building ────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "**cross-over** randomized trial using the Cochrane RoB 2 tool (cross-over "
    "extension). Read the PDF carefully. Answer each signaling question with "
    "one of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no "
    "information). Provide a 1-2 sentence rationale for each answer, quoting "
    "the paper where possible. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)

OVERRIDE_NOTE = (
    "\\n\\nNote: this assessment is scoped to one specific outcome, "
    "selected by the reviewer. Domain 1 "
    "signaling questions concern the randomization process for the trial as a "
    "whole, not the specific outcome — answer accordingly."
)


def build_domain_prompt(domain, study_type, assessed_outcome,
                        extracted_fields, outcome_is_override=False):
    relevant = {k: extracted_fields[k]
                for k in domain["relevant_fields"] if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"
    q_lines = []
    for sig in domain["signals"]:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: Y/PY/PN/N/NI."
        )
    questions_block = "\n".join(q_lines)
    shape = "{\n"
    for sig in domain["signals"]:
        shape += f'  "{sig["id"]}": "Y|PY|PN|N|NI",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"
    override_note = OVERRIDE_NOTE if (outcome_is_override and domain["id"] == 1) else ""
    return (
        f"Assess **Domain {domain['id']} — {domain['name']}** for the "
        f"**cross-over** trial described in the attached PDF.\n\n"
        f"Study type: {study_type}\n"
        f"Outcome being assessed: {assessed_outcome}{override_note}\n\n"
        f"Context (fields already extracted from the paper):\n{ctx_json}\n\n"
        f"Signaling questions:\n{questions_block}\n\n"
        f"Return a JSON object with exactly this shape:\n{shape}\n\n"
        f"Answer N (or PN) when the paper gives enough information to rule "
        f"out the problem, and NI only when the paper is silent. Rationales "
        f"must be short (1-2 sentences) and quote the paper verbatim where "
        f"possible."
    )


# ── Per-domain LLM call + parse ────────────────────────────────

def assess_domain(pdf_bytes, domain, study_type, assessed_outcome,
                   extracted_fields, call_llm, outcome_is_override=False):
    prompt = build_domain_prompt(
        domain, study_type, assessed_outcome,
        extracted_fields, outcome_is_override,
    )
    raw = call_llm(SYSTEM_PROMPT, prompt, pdf_bytes)
    signals, rationales = {}, {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        if ans not in SIGNAL_OPTIONS:
            ans = "NI"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()
    return {
        "id": domain["id"],
        "name": domain["name"],
        "signals": signals,
        "rationales": rationales,
        "judgement": domain["judge"](signals),
        "direction": str(raw.get("direction_of_bias", "NA")).strip() or "NA",
    }


# ── Top-level entry point ──────────────────────────────────────

def assess_crossover_trial(pdf_bytes, study_type, assessed_outcome,
                            extracted_fields, call_llm,
                            outcome_is_override=False):
    """Run all 6 domains; return per-domain results + overall judgement."""
    domain_results = {}
    for domain in DOMAINS:
        domain_results[str(domain["id"])] = assess_domain(
            pdf_bytes, domain, study_type, assessed_outcome,
            extracted_fields, call_llm, outcome_is_override,
        )
    overall_j = overall([d["judgement"] for d in domain_results.values()])
    # Aggregate direction — most-common non-NA, ties → Unpredictable
    from collections import Counter
    dirs = [d["direction"] for d in domain_results.values()
            if d["direction"] not in ("", "NA")]
    if not dirs:
        overall_d = "NA"
    else:
        counts = Counter(dirs).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            overall_d = "Unpredictable"
        else:
            overall_d = counts[0][0]
    return {
        "domains": domain_results,
        "overall_judgement": overall_j,
        "overall_direction": overall_d,
    }
```

### 12.3 RoB 2 cluster extension (RoB 2 CRT) — from `rob2_cluster_shareable.md`

**From that document's §8. Reference implementation as a single Python file:**

```python
"""rob2_cluster_assessor.py — reference implementation.

Public API:
    assess_cluster_trial(pdf_bytes, study_type, assessed_outcome,
                         extracted_fields, call_llm,
                         outcome_is_override=False, aim="assignment") -> dict

`call_llm(system_prompt, user_prompt, pdf_bytes) -> dict` must return the
parsed JSON object the model produced.

`aim` selects the Domain 2 variant: "assignment" (intention-to-treat, the
default) or "adhering" (per-protocol).
"""

import json
from collections import Counter

SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
_BASE = ["Y", "PY", "PN", "N", "NI"]
_NO_NI = ["Y", "PY", "PN", "N"]            # signaling question 3.2 only


def _yes(a):  return a in ("Y", "PY")
def _no(a):   return a in ("N", "PN")


# ── Decision trees ─────────────────────────────────────────────

def domain1a_judge(s):
    q1, q2, q3 = s.get("1a.1", "NI"), s.get("1a.2", "NI"), s.get("1a.3", "NI")
    if _no(q2): return "High"
    if q2 == "NI":
        return "High" if _yes(q3) else "Some concerns"
    if _no(q1): return "Some concerns"
    return "Some concerns" if _yes(q3) else "Low"


def domain1b_judge(s):
    q1, q2, q3 = s.get("1b.1", "NI"), s.get("1b.2", "NI"), s.get("1b.3", "NI")
    if _yes(q1): return "Low"
    if _yes(q2): return "High"
    if _no(q2):
        return "Some concerns" if _yes(q3) else "Low"
    return "High" if _yes(q3) else "Some concerns"


def domain2_assignment_judge(s):
    q21a, q21b, q22 = s.get("2.1a", "NI"), s.get("2.1b", "NI"), s.get("2.2", "NI")
    q23, q24, q25 = s.get("2.3", "NI"), s.get("2.4", "NI"), s.get("2.5", "NI")
    q26, q27 = s.get("2.6", "NI"), s.get("2.7", "NI")
    if _no(q21a):
        aware = not _no(q22)
    else:
        aware = not (_no(q21b) and _no(q22))
    if not aware:
        part1 = "Low"
    elif _no(q23):
        part1 = "Low"
    elif q23 == "NI":
        part1 = "Some concerns"
    elif _no(q24):
        part1 = "Low"
    else:
        part1 = "Some concerns" if _yes(q25) else "High"
    if _yes(q26):
        part2 = "Low"
    elif _no(q27):
        part2 = "Some concerns"
    else:
        part2 = "High"
    if part1 == "High" or part2 == "High": return "High"
    if part1 == "Low" and part2 == "Low":  return "Low"
    return "Some concerns"


def domain2_adhering_judge(s):
    q21, q22, q23 = s.get("2.1", "NI"), s.get("2.2", "NI"), s.get("2.3", "NI")
    q24, q25, q26 = s.get("2.4", "NI"), s.get("2.5", "NI"), s.get("2.6", "NI")

    def _node_2425():
        if _no(q24) and _no(q25): return "Low"
        return "Some concerns" if _yes(q26) else "High"

    if _no(q21) and _no(q22):
        return _node_2425()
    if _yes(q23):
        return _node_2425()
    return "Some concerns" if _yes(q26) else "High"


def domain3_judge(s):
    q31a, q31b = s.get("3.1a", "NI"), s.get("3.1b", "NI")
    q32, q33, q34 = s.get("3.2", "N"), s.get("3.3", "NI"), s.get("3.4", "NI")
    if _yes(q31a) and _yes(q31b): return "Low"
    if _yes(q32): return "Low"
    if _no(q33):  return "Low"
    if _no(q34):  return "Some concerns"
    return "High"


def domain4_judge(s):
    q41, q42 = s.get("4.1", "NI"), s.get("4.2", "NI")
    q43a, q43b = s.get("4.3a", "NI"), s.get("4.3b", "NI")
    q44, q45 = s.get("4.4", "NI"), s.get("4.5", "NI")
    if _yes(q41): return "High"
    if _yes(q42): return "High"
    floor = "Low" if _no(q42) else "Some concerns"
    if _no(q43a): return floor
    if _no(q43b): return floor
    if _no(q44):  return floor
    if _no(q45):  return "Some concerns"
    return "High"


def domain5_judge(s):
    q51, q52, q53 = s.get("5.1", "NI"), s.get("5.2", "NI"), s.get("5.3", "NI")
    if _yes(q52) or _yes(q53): return "High"
    if _no(q52) and _no(q53):
        return "Low" if _yes(q51) else "Some concerns"
    return "Some concerns"


def overall(judgements):
    if any(j == "High" for j in judgements): return "High"
    some = sum(1 for j in judgements if j == "Some concerns")
    if some >= 2: return "High"
    if some >= 1: return "Some concerns"
    return "Low"


# ── Cascade enforcement (Python-derived NA) ────────────────────

def enforce_cascade_1b(s):
    out = dict(s)
    if out.get("1b.1", "NI") in ("Y", "PY"):
        out["1b.2"] = "NA"
    return out


def enforce_cascade_2_assignment(s):
    out = dict(s)
    if out.get("2.1a", "NI") in ("N", "PN"):
        out["2.1b"] = "NA"
    aware = (out.get("2.1b", "NA") in ("Y", "PY", "NI")
             or out.get("2.2", "NI") in ("Y", "PY", "NI"))
    if not aware:
        out["2.3"] = "NA"
    if out.get("2.3", "NA") not in ("Y", "PY"):
        out["2.4"] = "NA"
    if out.get("2.4", "NA") not in ("Y", "PY", "NI"):
        out["2.5"] = "NA"
    if out.get("2.6", "NI") in ("Y", "PY"):
        out["2.7"] = "NA"
    return out


def enforce_cascade_2_adhering(s):
    out = dict(s)
    aware = (out.get("2.1", "NI") in ("Y", "PY", "NI")
             or out.get("2.2", "NI") in ("Y", "PY", "NI"))
    if not aware:
        out["2.3"] = "NA"
    need_26 = (out.get("2.3", "NA") in ("N", "PN", "NI")
               or out.get("2.4", "NI") in ("Y", "PY", "NI")
               or out.get("2.5", "NI") in ("Y", "PY", "NI"))
    if not need_26:
        out["2.6"] = "NA"
    return out


def enforce_cascade_3(s):
    out = dict(s)
    missing = (out.get("3.1a", "NI") in ("N", "PN", "NI")
               or out.get("3.1b", "NI") in ("N", "PN", "NI"))
    if not missing:
        out["3.2"] = "NA"
    if out.get("3.2", "NA") not in ("N", "PN"):
        out["3.3"] = "NA"
    if out.get("3.3", "NA") not in ("Y", "PY", "NI"):
        out["3.4"] = "NA"
    return out


def enforce_cascade_4(s):
    out = dict(s)
    if out.get("4.1", "NI") in ("Y", "PY") or out.get("4.2", "NI") in ("Y", "PY"):
        for sid in ("4.3a", "4.3b", "4.4", "4.5"):
            out[sid] = "NA"
        return out
    if out.get("4.3a", "NI") not in ("Y", "PY", "NI"):
        out["4.3b"] = "NA"
    if out.get("4.3b", "NA") not in ("Y", "PY", "NI"):
        out["4.4"] = "NA"
    if out.get("4.4", "NA") not in ("Y", "PY", "NI"):
        out["4.5"] = "NA"
    return out


def enforce_cascade(domain_id, signals, aim="assignment"):
    if domain_id == "1b": return enforce_cascade_1b(signals)
    if domain_id == 2:
        return (enforce_cascade_2_adhering(signals) if aim == "adhering"
                else enforce_cascade_2_assignment(signals))
    if domain_id == 3: return enforce_cascade_3(signals)
    if domain_id == 4: return enforce_cascade_4(signals)
    return dict(signals)  # 1a, 5 — no conditional questions


# ── Domain definitions ─────────────────────────────────────────
# Each domain: id, name, judge, list of {id, text, elaboration, options}.
# (Question text + elaborations condensed here; full text is in §2 above.)

_DOMAIN_1A = {"id": "1a", "judge": domain1a_judge,
    "relevant_fields": ["cluster_unit", "n_clusters", "icc_reported",
                        "allocation_concealment", "baseline_balance"],
    "name": "Bias arising from the randomization process", "signals": [
    {"id": "1a.1", "text": "Was the allocation sequence random?", "options": _BASE, "elaboration": "<see §2.1>"},
    {"id": "1a.2", "text": "Was the allocation sequence concealed until clusters were enrolled and assigned to interventions?", "options": _BASE, "elaboration": "<see §2.1>"},
    {"id": "1a.3", "text": "Did baseline differences between intervention groups suggest a problem with the randomization process?", "options": _BASE, "elaboration": "<see §2.1>"},
]}

_DOMAIN_1B = {"id": "1b", "judge": domain1b_judge,
    "relevant_fields": ["recruitment_after_randomization", "contamination_risk", "n_clusters", "cluster_unit"],
    "name": "Bias arising from the timing of identification or recruitment of participants", "signals": [
    {"id": "1b.1", "text": "Were all the individual participants identified and recruited (if appropriate) before randomization of clusters?", "options": _BASE, "elaboration": "<see §2.2>"},
    {"id": "1b.2", "text": "Is it likely that selection of individual participants was affected by knowledge of the intervention assigned to the cluster?", "options": _BASE, "elaboration": "<see §2.2>"},
    {"id": "1b.3", "text": "Were there baseline imbalances that suggest differential identification or recruitment of individual participants between intervention groups?", "options": _BASE, "elaboration": "<see §2.2>"},
]}

_DOMAIN_2_ASSIGNMENT = {"id": 2, "judge": domain2_assignment_judge,
    "relevant_fields": ["contamination_risk", "clustering_in_analysis",
                        "blinding_participants", "blinding_personnel",
                        "protocol_deviations", "analysis_framework"],
    "name": "Bias due to deviations from intended interventions (effect of assignment to intervention)", "signals": [
    {"id": "2.1a", "text": "Were participants aware that they were in a trial?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.1b", "text": "Were participants aware of their assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.2", "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.3", "text": "Were there deviations from the intended intervention that arose because of the trial context?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.4", "text": "Were these deviations likely to have affected the outcome?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.5", "text": "Were these deviations from intended intervention balanced between groups?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.6", "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.7", "text": "Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?", "options": _BASE, "elaboration": "<see §2.3>"},
]}

_DOMAIN_2_ADHERING = {"id": 2, "judge": domain2_adhering_judge,
    "relevant_fields": ["contamination_risk", "clustering_in_analysis",
                        "blinding_participants", "blinding_personnel",
                        "protocol_deviations", "analysis_framework"],
    "name": "Bias due to deviations from intended interventions (effect of adhering to intervention)", "signals": [
    {"id": "2.1", "text": "Were participants aware of their assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.2", "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.3", "text": "Were important non-protocol interventions balanced across intervention groups?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.4", "text": "Were there failures in implementing the intervention that could have affected the outcome?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.5", "text": "Was there non-adherence to the assigned intervention regimen that could have affected participants' outcomes?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.6", "text": "Was an appropriate analysis used to estimate the effect of adhering to the intervention?", "options": _BASE, "elaboration": "<see §2.4>"},
]}

_DOMAIN_3 = {"id": 3, "judge": domain3_judge,
    "relevant_fields": ["clustering_in_analysis", "n_clusters", "attrition_rate", "missing_data_handling"],
    "name": "Bias due to missing outcome data", "signals": [
    {"id": "3.1a", "text": "Were data for this outcome available for all clusters that recruited participants?", "options": _BASE, "elaboration": "<see §2.5>"},
    {"id": "3.1b", "text": "Were data for this outcome available for all, or nearly all, participants within clusters?", "options": _BASE, "elaboration": "<see §2.5>"},
    {"id": "3.2", "text": "Is there evidence that the result was not biased by missing outcome data?", "options": _NO_NI, "elaboration": "<see §2.5>"},
    {"id": "3.3", "text": "Could missingness in the outcome depend on its true value?", "options": _BASE, "elaboration": "<see §2.5>"},
    {"id": "3.4", "text": "Is it likely that missingness in the outcome depended on its true value?", "options": _BASE, "elaboration": "<see §2.5>"},
]}

_DOMAIN_4 = {"id": 4, "judge": domain4_judge,
    "relevant_fields": ["blinding_outcome_assessors", "outcome_measurement_method", "clustering_in_analysis"],
    "name": "Bias in measurement of the outcome", "signals": [
    {"id": "4.1", "text": "Was the method of measuring the outcome inappropriate?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.2", "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.3a", "text": "Were outcome assessors aware that a trial was taking place?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.3b", "text": "Were outcome assessors aware of the intervention received by study participants?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.4", "text": "Could assessment of the outcome have been influenced by knowledge of intervention received?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.5", "text": "Is it likely that assessment of the outcome was influenced by knowledge of intervention received?", "options": _BASE, "elaboration": "<see §2.6>"},
]}

_DOMAIN_5 = {"id": 5, "judge": domain5_judge,
    "relevant_fields": ["protocol_available", "outcomes_match_protocol"],
    "name": "Bias in selection of the reported result", "signals": [
    {"id": "5.1", "text": "Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?", "options": _BASE, "elaboration": "<see §2.7>"},
    {"id": "5.2", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements within the outcome domain?", "options": _BASE, "elaboration": "<see §2.7>"},
    {"id": "5.3", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?", "options": _BASE, "elaboration": "<see §2.7>"},
]}


def domains_for_aim(aim):
    d2 = _DOMAIN_2_ADHERING if aim == "adhering" else _DOMAIN_2_ASSIGNMENT
    return [_DOMAIN_1A, _DOMAIN_1B, d2, _DOMAIN_3, _DOMAIN_4, _DOMAIN_5]


# ── Prompt building ────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "**cluster-randomized** trial using the Cochrane RoB 2 tool (cluster-randomized "
    "trial extension, RoB 2 CRT). Read the PDF carefully. Answer every signaling "
    "question on the Y/PY/PN/N/NI scale based on what the paper reports. Do NOT "
    "decide whether a question is 'not applicable' — the cribsheet's conditional "
    "structure is resolved in code after you answer. Provide a 1-2 sentence "
    "rationale for each answer. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)

OVERRIDE_NOTE = (
    "\\n\\nNote: this assessment is scoped to one specific outcome, "
    "selected by the reviewer. Domain 1a "
    "signaling questions concern the randomization process for the trial as a "
    "whole, not the specific outcome — answer accordingly."
)


def build_domain_prompt(domain, study_type, assessed_outcome,
                        extracted_fields, outcome_is_override=False):
    relevant = {k: extracted_fields[k]
                for k in domain["relevant_fields"] if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"
    q_lines = []
    for sig in domain["signals"]:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: {'/'.join(sig['options'])}."
        )
    questions_block = "\n".join(q_lines)
    shape = "{\n"
    for sig in domain["signals"]:
        shape += f'  "{sig["id"]}": "{"|".join(sig["options"])}",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"
    override_note = OVERRIDE_NOTE if (outcome_is_override and str(domain["id"]) == "1a") else ""
    return (
        f"Assess **Domain {domain['id']} — {domain['name']}** for the "
        f"**cluster-randomized** trial described in the attached PDF.\n\n"
        f"Study type: {study_type}\n"
        f"Outcome being assessed: {assessed_outcome}{override_note}\n\n"
        f"Context (fields already extracted from the paper):\n{ctx_json}\n\n"
        f"Signaling questions:\n{questions_block}\n\n"
        f"Return a JSON object with exactly this shape:\n{shape}\n\n"
        f"Answer each question using only its listed response options, on its "
        f"own merits — do not mark a question not-applicable; the tool resolves "
        f"the conditional structure in code."
    )


# ── Per-domain LLM call + parse + cascade ──────────────────────

def assess_domain(pdf_bytes, domain, aim, study_type, assessed_outcome,
                   extracted_fields, call_llm, outcome_is_override=False):
    prompt = build_domain_prompt(
        domain, study_type, assessed_outcome,
        extracted_fields, outcome_is_override,
    )
    raw = call_llm(SYSTEM_PROMPT, prompt, pdf_bytes)
    signals, rationales = {}, {}
    for sig in domain["signals"]:
        sid = sig["id"]
        allowed = sig["options"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        if ans not in allowed:
            ans = "NI" if "NI" in allowed else "N"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()
    # Python-derived NA — resolve the conditional structure in code.
    signals = enforce_cascade(domain["id"], signals, aim=aim)
    return {
        "id": domain["id"],
        "name": domain["name"],
        "signals": signals,
        "rationales": rationales,
        "judgement": domain["judge"](signals),
        "direction": str(raw.get("direction_of_bias", "NA")).strip() or "NA",
    }


# ── Top-level entry point ──────────────────────────────────────

def assess_cluster_trial(pdf_bytes, study_type, assessed_outcome,
                         extracted_fields, call_llm,
                         outcome_is_override=False, aim="assignment"):
    """Run all 6 domains; return per-domain results + overall judgement."""
    aim = "adhering" if str(aim or "").strip().lower() == "adhering" else "assignment"
    domain_results = {}
    for domain in domains_for_aim(aim):
        domain_results[str(domain["id"])] = assess_domain(
            pdf_bytes, domain, aim, study_type, assessed_outcome,
            extracted_fields, call_llm, outcome_is_override,
        )
    overall_j = overall([d["judgement"] for d in domain_results.values()])
    dirs = [d["direction"] for d in domain_results.values()
            if d["direction"] not in ("", "NA")]
    if not dirs:
        overall_d = "NA"
    else:
        counts = Counter(dirs).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            overall_d = "Unpredictable"
        else:
            overall_d = counts[0][0]
    return {
        "domains": domain_results,
        "aim": aim,
        "overall_judgement": overall_j,
        "overall_direction": overall_d,
    }
```

**From that document's §9. Quick test sketches (no framework — plain `assert`):**

```python
# Domain 1a — concealed + random + no baseline issue → Low
assert domain1a_judge({"1a.1": "Y", "1a.2": "Y", "1a.3": "N"}) == "Low"

# Domain 1a — concealed but NOT random → Some concerns (CRT-specific routing)
assert domain1a_judge({"1a.1": "N", "1a.2": "Y", "1a.3": "N"}) == "Some concerns"

# Domain 1b — all participants recruited before randomization → Low
assert domain1b_judge({"1b.1": "Y", "1b.2": "NA", "1b.3": "NA"}) == "Low"

# Domain 2 (assignment) — not aware + appropriate ITT analysis → Low
assert domain2_assignment_judge({"2.1a": "N", "2.2": "N", "2.6": "Y"}) == "Low"

# Domain 2 (adhering) — not aware, no failures → Low
assert domain2_adhering_judge(
    {"2.1": "N", "2.2": "N", "2.4": "N", "2.5": "N"}) == "Low"

# Cascade — 1b.2 is gated to NA when 1b.1 is Y
assert enforce_cascade_1b({"1b.1": "Y", "1b.2": "Y", "1b.3": "N"})["1b.2"] == "NA"

# Cascade — assignment chain: not-aware gates 2.3 → 2.4 → 2.5
out = enforce_cascade_2_assignment(
    {"2.1a": "N", "2.1b": "Y", "2.2": "N", "2.3": "Y",
     "2.4": "Y", "2.5": "Y", "2.6": "Y", "2.7": "Y"})
assert out["2.1b"] == out["2.3"] == out["2.4"] == out["2.5"] == out["2.7"] == "NA"

# Cascade — D4: an inappropriate measurement method gates the assessor chain
out4 = enforce_cascade_4(
    {"4.1": "Y", "4.2": "N", "4.3a": "N", "4.3b": "N", "4.4": "N", "4.5": "N"})
assert out4["4.3a"] == out4["4.3b"] == out4["4.4"] == out4["4.5"] == "NA"

# Overall — all six Low → Low; two Some concerns → High
assert overall(["Low"] * 6) == "Low"
assert overall(["Low", "Low", "Some concerns", "Low", "Some concerns", "Low"]) == "High"
```

### 12.4 ROBINS-I V2 — from `robins_i_v2_shareable.md`

**From that document's §13. Reference implementation — single self-contained Python module:**

```python
llm_call(pdf_bytes: bytes, prompt: str, max_tokens: int) -> dict
```

```python
"""ROBINS-I V2 — Risk Of Bias In Non-randomised Studies of Interventions,
Version 2. Single-file reference implementation.

Source: ROBINS-I V2 cribsheet (20 November 2025). ROBINS-I V2 development group:
Sterne JA, Brandt Mathur M, Elbers R, Hróbjartsson A, McAleenan A, Reeves B,
Shrier I, Tilling K, Armstrong R, Berkman N, Boutron I, Carpenter J, Chan AW,
Deeks J, Golder S, Henry D, Jüni P, Kirkham J, Konstantinidis M, Lasserson T,
Loke Y, McGuinness L, Page M, Savović J, Shea B, Mawdsley D, Shepperd S,
Tugwell P, Valentine J, Viswanathan M, Waddington HS, Wells G, Hernán M, Higgins J.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Scales
# ─────────────────────────────────────────────
SIGNAL_OPTIONS_ALL = ("Y", "PY", "PN", "N", "NI", "WN", "SN", "WY", "SY")
JUDGEMENTS = ("Low", "Moderate", "Serious", "Critical")
LOW_D1 = "Low (except for concerns about uncontrolled confounding)"
LOW_D1_SA = "Low (except for concerns about uncontrolled benchmarking)"
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})


# ─────────────────────────────────────────────
# Helper predicates
# ─────────────────────────────────────────────
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY", "WY", "SY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN", "WN", "SN")


def _strict_yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _strict_no(ans: str) -> bool:
    return ans in ("N", "PN")


def _weak_no(ans: str) -> bool:
    return ans == "WN"


def _strong_no(ans: str) -> bool:
    return ans == "SN"


def _weak_yes(ans: str) -> bool:
    return ans == "WY"


def _strong_yes(ans: str) -> bool:
    return ans == "SY"


def _no_info(ans: str) -> bool:
    return ans == "NI"


# ─────────────────────────────────────────────
# Decision trees
# ─────────────────────────────────────────────
def domain1_variant_a_judge(signals: dict[str, str]) -> str:
    """D1 Variant A (ITT effect, baseline confounding only). Cribsheet p20."""
    q1 = signals.get("1A.1", "NI")
    q2 = signals.get("1A.2", "NI")
    q3 = signals.get("1A.3", "NI")
    q4 = signals.get("1A.4", "NI")

    if _strong_no(q1) or _no_info(q1):
        return "Critical" if _yes(q4) else "Serious"

    if _strict_yes(q1):
        if _yes(q3):
            if _yes(q4):
                return "Critical"
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else LOW_D1
        return "Serious"

    if _weak_no(q1):
        if _yes(q3):
            if _yes(q4):
                return "Critical"
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else "Moderate"
        return "Serious"

    return "Serious"


def domain1_variant_b_judge(signals: dict[str, str]) -> str:
    """D1 Variant B (per-protocol effect, baseline + time-varying). Cribsheet p24."""
    q1 = signals.get("1B.1", "NI")
    q2 = signals.get("1B.2", "NI")
    q3 = signals.get("1B.3", "NI")
    q4 = signals.get("1B.4", "NI")
    q5 = signals.get("1B.5", "NI")

    if _strict_no(q1) or _no_info(q1):
        if _yes(q4):
            return "Critical"
        return "Critical" if _yes(q5) else "Serious"

    if _strict_yes(q1):
        if _strict_yes(q2):
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else LOW_D1
            return "Serious"
        if _weak_no(q2):
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else "Moderate"
            return "Serious"
        return "Critical" if _yes(q5) else "Serious"

    return "Serious"


def domain1_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D1 Variant single_arm (uncontrolled / single-arm design — no comparator)."""
    q1 = signals.get("1S.1", "NI")
    q2 = signals.get("1S.2", "NI")
    q3 = signals.get("1S.3", "NI")
    q4 = signals.get("1S.4", "NI")
    q5 = signals.get("1S.5", "NI")

    if _yes(q5):
        return "Critical"

    if _strict_no(q1):
        if _strict_no(q4):
            return "Critical"
        return "Serious"

    if _no_info(q1):
        return "Serious"

    if _strict_yes(q1):
        if _strict_yes(q3):
            if _strict_yes(q2):
                return LOW_D1_SA
            return "Moderate"
        if _weak_no(q3):
            return "Moderate"
        if _strong_no(q3) or _no_info(q3):
            if _strict_yes(q4):
                return "Moderate"
            return "Serious"

    return "Serious"


def domain2_judge(signals: dict[str, str]) -> str:
    """D2 Bias in classification of interventions. Cribsheet p28."""
    q1 = signals.get("2.1", "NI")
    q2 = signals.get("2.2", "NI")
    q3 = signals.get("2.3", "NI")
    q4 = signals.get("2.4", "NI")
    q5 = signals.get("2.5", "NI")

    if _yes(q1) or _yes(q2):
        tier = 0
    elif _strong_yes(q3):
        tier = 1
    elif _weak_yes(q3) or _no_info(q3):
        tier = 1
    else:
        tier = 2

    if _strict_no(q4):
        bump4 = 0
    elif _weak_yes(q4) or _no_info(q4):
        bump4 = 1
    elif _strong_yes(q4):
        bump4 = 2
    else:
        bump4 = 1

    if _strict_no(q5):
        bump5 = 0
    else:
        bump5 = 1

    if tier == 2 and (_yes(q4) or _no_info(q4)):
        return "Critical"

    idx = min(tier + bump4 + bump5, 3)
    return JUDGEMENTS[idx]


def domain2_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D2 Variant single_arm — degenerate classification (only one intervention)."""
    q1 = signals.get("2S.1", "NI")
    q2 = signals.get("2S.2", "NI")
    q3 = signals.get("2S.3", "NI")

    if _strong_yes(q3):
        return "Critical"
    if _weak_yes(q3) or _no_info(q3):
        return "Serious"

    if _strict_yes(q1):
        if _strict_yes(q2):
            return "Low"
        if _weak_no(q2):
            return "Moderate"
        if _strong_no(q2):
            return "Serious"
        return "Moderate"

    if _strict_no(q1):
        return "Serious"
    return "Moderate"


def domain3_judge(signals: dict[str, str]) -> str:
    """D3 Bias in selection of participants. Cribsheet p32."""
    q1 = signals.get("3.1", "NI")
    q2 = signals.get("3.2", "NI")
    q3 = signals.get("3.3", "NI")
    q4 = signals.get("3.4", "NI")
    q5 = signals.get("3.5", "NI")
    q6 = signals.get("3.6", "NI")
    q7 = signals.get("3.7", "NI")
    q8 = signals.get("3.8", "NI")

    if _strict_yes(q1):
        a_judgement = "Low" if _strict_no(q2) or _no_info(q2) else "Moderate"
    elif _weak_no(q1) or _no_info(q1):
        a_judgement = "Moderate"
    elif _strong_no(q1):
        a_judgement = "Serious"
    else:
        a_judgement = "Moderate"

    if _strict_no(q3):
        b_judgement = "Low"
    elif _yes(q3):
        if _strict_no(q4) or _no_info(q4):
            b_judgement = "Low"
        elif _yes(q4):
            if _yes(q5):
                b_judgement = "Serious"
            else:
                b_judgement = "Moderate"
        else:
            b_judgement = "Moderate"
    else:
        b_judgement = "Moderate"

    rank = {"Low": 0, "Moderate": 1, "Serious": 2, "Critical": 3}
    worst = max(rank[a_judgement], rank[b_judgement])

    if worst == 0:
        return "Low"
    if worst == 1:
        return "Moderate"

    if _yes(q6):
        return "Moderate"
    if _yes(q7):
        return "Moderate"
    if _yes(q8):
        return "Critical"
    return "Serious"


def domain4_judge(signals: dict[str, str]) -> str:
    """D4 Bias due to missing data. Cribsheet p38."""
    q1 = signals.get("4.1", "NI")
    q2 = signals.get("4.2", "NI")
    q3 = signals.get("4.3", "NI")
    q4 = signals.get("4.4", "NI")
    q5 = signals.get("4.5", "NI")
    q6 = signals.get("4.6", "NI")
    q7 = signals.get("4.7", "NI")
    q8 = signals.get("4.8", "NI")
    q9 = signals.get("4.9", "NI")
    q10 = signals.get("4.10", "NI")
    q11 = signals.get("4.11", "NI")

    if _strict_yes(q1) and _strict_yes(q2) and _strict_yes(q3):
        return "Low"

    if _strict_yes(q4) or _no_info(q4):
        if _strict_no(q5):
            return "Low"
        if _strict_yes(q6):
            if _strict_yes(q11):
                return "Moderate"
            return "Serious"
        if _weak_no(q6) or _no_info(q6):
            if _strict_yes(q11):
                return "Moderate"
            return "Serious"
        return "Critical" if _strict_no(q11) else "Serious"

    if _strict_yes(q7):
        if _strict_yes(q8):
            if _strict_yes(q9):
                return "Low"
            if _weak_no(q9) or _no_info(q9):
                return "Moderate" if _strict_yes(q11) else "Serious"
            return "Critical" if _strict_no(q11) else "Serious"
        return "Critical" if _strict_no(q11) else "Serious"

    if _strict_yes(q10):
        return "Low"
    if _weak_no(q10) or _no_info(q10):
        return "Moderate" if _strict_yes(q11) else "Serious"
    return "Critical" if _strict_no(q11) else "Serious"


def domain5_judge(signals: dict[str, str]) -> str:
    """D5 Bias arising from measurement of the outcome. Cribsheet p41."""
    q1 = signals.get("5.1", "NI")
    q2 = signals.get("5.2", "NI")
    q3 = signals.get("5.3", "NI")

    if _yes(q1):
        return "Serious"

    if _strict_no(q1):
        if _strict_no(q2):
            return "Low"
        if _strong_yes(q3):
            return "Serious"
        if _weak_yes(q3) or _no_info(q3):
            return "Moderate"
        return "Low"

    if _strict_no(q2):
        return "Moderate"
    if _strong_yes(q3):
        return "Serious"
    return "Moderate"


def domain6_judge(signals: dict[str, str]) -> str:
    """D6 Bias in selection of the reported result. Cribsheet p47."""
    q1 = signals.get("6.1", "NI")
    q2 = signals.get("6.2", "NI")
    q3 = signals.get("6.3", "NI")
    q4 = signals.get("6.4", "NI")

    if _strict_yes(q1):
        return "Low"

    yes_count = sum(1 for q in (q2, q3, q4) if _yes(q))
    ni_count = sum(1 for q in (q2, q3, q4) if _no_info(q))

    if yes_count >= 2:
        return "Critical"
    if yes_count == 1:
        return "Serious"
    if ni_count == 3:
        return "Serious"
    if ni_count >= 1:
        return "Moderate"
    return "Low"


DOMAIN_JUDGES_VARIANT_A: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_variant_a_judge,
    2: domain2_judge,
    3: domain3_judge,
    4: domain4_judge,
    5: domain5_judge,
    6: domain6_judge,
}

DOMAIN_JUDGES_VARIANT_B: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_variant_b_judge,
    2: domain2_judge,
    3: domain3_judge,
    4: domain4_judge,
    5: domain5_judge,
    6: domain6_judge,
}

DOMAIN_JUDGES_VARIANT_SINGLE_ARM: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_variant_single_arm_judge,
    2: domain2_variant_single_arm_judge,
    3: domain3_judge,
    4: domain4_judge,
    5: domain5_judge,
    6: domain6_judge,
}


def robins_i_overall(domain_judgements: list[str]) -> str:
    """Overall judgement — worst-domain aggregation per cribsheet p48."""
    rank = {
        LOW_D1: 0,
        LOW_D1_SA: 0,
        "Low": 0,
        "Moderate": 1,
        "Serious": 2,
        "Critical": 3,
    }
    worst = max((rank.get(j, 1) for j in domain_judgements), default=0)
    if worst == 0:
        return "Low"
    return JUDGEMENTS[worst]


# ─────────────────────────────────────────────
# Cascade enforcement — rule-based NA handling per the cribsheet's
# cascading-question structure. Called AFTER the LLM responds, before the
# decision tree runs. Overrides LLM answers for gated-out questions to NA.
# See §17 for the design rationale.
# ─────────────────────────────────────────────
def enforce_cascade_d1_variant_b_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D1 Variant B — 1B.4 only asked when 1B.1 N/PN/NI (cribsheet).

    1B.4 elaboration: 'Asked when an inappropriate analysis method
    (1B.1 N/PN/NI) has been used.'
    """
    out = dict(signals)
    if out.get("1B.1", "NI") in ("Y", "PY"):
        out["1B.4"] = "NA"
    return out


def enforce_cascade_d1_variant_single_arm_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D1 single-arm — 1S.3 NA when no benchmark identified at 1S.1.

    1S.3 elaboration: 'NA only when no benchmark was identified at 1S.1.'
    """
    out = dict(signals)
    if out.get("1S.1", "NI") in ("N", "PN"):
        out["1S.3"] = "NA"
    return out


def enforce_cascade_d2_cohort_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D2 cohort — 2.2 only asked if 2.1 N/PN/NI.

    2.2 elaboration: 'Asked only if 2.1 was N/PN/NI.'
    """
    out = dict(signals)
    if out.get("2.1", "NI") in ("Y", "PY"):
        out["2.2"] = "NA"
    return out


def enforce_cascade_d3_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D3 — 5 cascade rules (cribsheet p32):

    - 3.2 only asked if 3.1 Y/PY (immortal time follow-up)
    - 3.4 only asked if 3.3 Y/PY (post-intervention selection variables)
    - 3.5 only asked if 3.4 Y/PY (collider-style selection bias)
    - 3.6/3.7/3.8 only asked if subsection A or B raised concerns
    - 3.7 only asked if 3.6 N/PN/NI
    """
    out = dict(signals)
    # 3.2 gated on 3.1
    if out.get("3.1", "NI") not in ("Y", "PY"):
        out["3.2"] = "NA"
    # 3.4 gated on 3.3
    if out.get("3.3", "NI") not in ("Y", "PY"):
        out["3.4"] = "NA"
    # 3.5 gated on 3.4
    if out.get("3.4", "NA") not in ("Y", "PY"):
        out["3.5"] = "NA"
    # 3.6/3.7/3.8 only asked if subsection A or B raised concerns
    # A (prevalent-user/immortal time) concerns: 3.1 WN/SN/NI OR (3.1 Y/PY AND 3.2 Y/PY)
    a_concerns = (out.get("3.1", "NI") in ("WN", "SN", "NI")
                  or (out.get("3.1") in ("Y", "PY")
                      and out.get("3.2", "NA") in ("Y", "PY")))
    # B (other selection) concerns: 3.3 Y/PY OR 3.3 NI (NI conservative)
    b_concerns = out.get("3.3", "NI") in ("Y", "PY", "NI")
    if not (a_concerns or b_concerns):
        out["3.6"] = "NA"
        out["3.7"] = "NA"
        out["3.8"] = "NA"
    else:
        # 3.7 only asked if 3.6 N/PN/NI (when adjustment didn't fully fix)
        if out.get("3.6", "NI") in ("Y", "PY"):
            out["3.7"] = "NA"
    return out


def enforce_cascade_d4_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D4 (missing data) — complex path-based cascade (cribsheet p38).

    Best case: 4.1 + 4.2 + 4.3 all Y/PY → 4.4-4.11 all NA (complete data).

    Otherwise, 4.4 is the complete-case-path selector:
    - 4.4 Y/PY/NI (complete case): use 4.5-4.6 path; 4.7-4.10 NA
        - 4.6 only asked if 4.5 Y/PY/NI (concerning exclusion)
    - 4.4 N/PN (not complete case): 4.5/4.6 NA; check 4.7 (imputation)
        - 4.7 Y/PY: imputation path; 4.10 NA
            - 4.9 only asked if 4.8 Y/PY
        - 4.7 N/PN: alternative-method path; 4.8/4.9 NA

    4.11 (sensitivity analysis rescue) is always asked when there's any
    missing-data concern (i.e. not in the all-complete-data short-circuit).
    """
    out = dict(signals)

    # Best case: complete data on all three variables
    if (out.get("4.1", "NI") in ("Y", "PY")
        and out.get("4.2", "NI") in ("Y", "PY")
        and out.get("4.3", "NI") in ("Y", "PY")):
        for sid in ("4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "4.11"):
            out[sid] = "NA"
        return out

    # 4.4 path selector
    q4_4 = out.get("4.4", "NI")
    if q4_4 in ("Y", "PY", "NI"):
        # Complete-case path
        for sid in ("4.7", "4.8", "4.9", "4.10"):
            out[sid] = "NA"
        # 4.6 only asked if 4.5 Y/PY/NI (concerning exclusion)
        if out.get("4.5", "NI") in ("N", "PN"):
            out["4.6"] = "NA"
    elif q4_4 in ("N", "PN"):
        # Not complete case: 4.5/4.6 NA
        out["4.5"] = "NA"
        out["4.6"] = "NA"
        # 4.7 imputation path selector
        q4_7 = out.get("4.7", "NI")
        if q4_7 in ("Y", "PY"):
            # Imputation path; 4.10 NA
            out["4.10"] = "NA"
            # 4.9 only asked if 4.8 Y/PY
            if out.get("4.8", "NI") not in ("Y", "PY"):
                out["4.9"] = "NA"
        elif q4_7 in ("N", "PN"):
            # Alternative-method path; 4.8/4.9 NA
            out["4.8"] = "NA"
            out["4.9"] = "NA"
        # 4.7 NI: leave 4.8-4.10 as-is (tree will handle conservatively)

    return out


def enforce_cascade_d5_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D5 (measurement) — 5.3 only asked if 5.2 Y/PY/NI.

    5.3 elaboration: 'Only asked if 5.2 was Y/PY/NI.' (i.e., NOT N/PN —
    when assessors were demonstrably blinded, 5.3 doesn't apply.)
    """
    out = dict(signals)
    if out.get("5.2", "NI") in ("N", "PN"):
        out["5.3"] = "NA"
    return out


def enforce_cascade_v2(domain_id: int,
                       signals: dict[str, str],
                       variant: str) -> dict[str, str]:
    """Dispatch to per-domain cascade enforcer. Returns signals unchanged
    for domains/variants without cascade rules (D1 Variant A; D2 single-arm;
    D6)."""
    if domain_id == 1:
        if variant == "B":
            return enforce_cascade_d1_variant_b_v2(signals)
        if variant == "single_arm":
            return enforce_cascade_d1_variant_single_arm_v2(signals)
        return signals  # Variant A: no cribsheet cascade rules
    if domain_id == 2:
        if variant == "single_arm":
            return signals  # 2S.1/2S.2/2S.3: no cascade
        return enforce_cascade_d2_cohort_v2(signals)
    if domain_id == 3:
        return enforce_cascade_d3_v2(signals)
    if domain_id == 4:
        return enforce_cascade_d4_v2(signals)
    if domain_id == 5:
        return enforce_cascade_d5_v2(signals)
    return signals  # D6: no cascade


# ─────────────────────────────────────────────
# Per-question response option subsets
# ─────────────────────────────────────────────
_BASIC = ("Y", "PY", "PN", "N", "NI")
_BASIC_NA = ("NA", "Y", "PY", "PN", "N", "NI")
_WITH_WN_SN = ("Y", "PY", "WN", "SN", "NI")
_NA_WITH_WN_SN = ("NA", "Y", "PY", "WN", "SN", "NI")
_WITH_WY_SY = ("Y", "PY", "WY", "SY", "PN", "N", "NI")
_DIFFERENTIAL = ("SY", "WY", "PN", "N", "NI")
_NA_DIFFERENTIAL = ("NA", "SY", "WY", "PN", "N", "NI")


# ─────────────────────────────────────────────
# Signal definitions — verbatim text from the cribsheet
# ─────────────────────────────────────────────
DOMAIN1_VARIANT_A_SIGNALS: list[dict[str, Any]] = [
    {"id": "1A.1", "text": "Did the authors control for all the important confounding factors for which this was necessary?", "options": list(_WITH_WN_SN), "elaboration": "Answer Y/PY if all important confounding factors identified in the preliminary consideration were appropriately controlled for (stratification, regression, matching, standardization, propensity scores, IPTW). Answer WN if most were controlled and uncontrolled confounding was probably not substantial. Answer SN if at least one important confounder should have been controlled but was not, and the failure is likely to have a material impact."},
    {"id": "1A.2", "text": "Were confounding factors that were controlled for (and for which control was necessary) measured validly and reliably by the variables available in this study?", "options": list(_NA_WITH_WN_SN), "elaboration": "Adjustment helps only if confounders were measured well. Answer WN if measurement error was probably not substantial; SN if there was at least one important confounder measured poorly enough that the extent of measurement error in confounders was probably substantial."},
    {"id": "1A.3", "text": "Did the authors control for any post-intervention variables that could have been affected by the intervention?", "options": list(_BASIC_NA), "elaboration": "Controlling for variables on the causal pathway between intervention and outcome (over-adjustment) biases the effect estimate. Classic example: adjusting for a biomarker that the intervention changes."},
    {"id": "1A.4", "text": "Did the use of negative controls, quantitative bias analysis, or other considerations suggest serious uncontrolled confounding?", "options": list(_BASIC[:4]), "elaboration": "If the study did not use negative controls and no other considerations suggest uncontrolled confounding, answer N. Answer Y/PY if negative controls indicate the result being assessed suffers from material bias due to confounding."},
]

DOMAIN1_VARIANT_B_SIGNALS: list[dict[str, Any]] = [
    {"id": "1B.1", "text": "Did the authors use an analysis method that was appropriate to control for time-varying as well as baseline confounding?", "options": list(_BASIC), "elaboration": "Appropriate methods to control for time-varying confounding ('g-methods') include inverse probability weighting based on baseline- and time-varying confounding factors, with adjustment for the censoring weights. Standard regression models including time-varying confounders may be problematic when those confounders are affected by prior intervention (treatment-confounder feedback)."},
    {"id": "1B.2", "text": "Did the authors control for all the important baseline and time-varying confounding factors for which this was necessary?", "options": list(_NA_WITH_WN_SN), "elaboration": "Per-protocol analyses must control for both baseline and time-varying confounding factors that predict changes to intervention received. Same WN / SN semantics as Variant A 1.1."},
    {"id": "1B.3", "text": "Were confounding factors that were controlled for measured validly and reliably by the variables available in this study?", "options": list(_NA_WITH_WN_SN), "elaboration": "Same measurement-validity question as Variant A 1.2 but applied to baseline + time-varying confounders."},
    {"id": "1B.4", "text": "Did the authors control for time-varying factors or other variables measured after the start of intervention?", "options": list(_BASIC_NA), "elaboration": "Asked when an inappropriate analysis method (1B.1 N/PN/NI) has been used. Conditioning on time-varying factors measured after the start of intervention is likely to lead to bias when those factors are also on the causal pathway from intervention to outcome."},
    {"id": "1B.5", "text": "Did the use of negative controls, or other considerations, suggest serious uncontrolled confounding?", "options": list(_BASIC[:4]), "elaboration": "Same as Variant A 1.4."},
]

DOMAIN1_VARIANT_SINGLE_ARM_SIGNALS: list[dict[str, Any]] = [
    {"id": "1S.1", "text": "Was the implied benchmark (historical control rate, pre-specified performance criterion, or null hypothesis with a quantitative decision rule) pre-specified before data collection?", "options": list(_BASIC[:4]), "elaboration": "Single-arm trials have no internal comparator. They are interpreted against an implicit benchmark — usually a historical-control response rate, a regulatory performance criterion (e.g. ORR > 30% to support accelerated approval), or a null hypothesis with a pre-specified statistical decision rule (e.g. Simon's two-stage design). Answer Y/PY if a numeric benchmark + decision rule was clearly stated in the protocol / SAP / methods, BEFORE the data were collected. Answer N/PN if no benchmark is identifiable, or if the benchmark looks chosen post-hoc to match the observed result."},
    {"id": "1S.2", "text": "Is the implied benchmark reasonable given current standard of care and the patient population being studied?", "options": list(_BASIC), "elaboration": "A pre-specified benchmark is only useful if it reflects a clinically meaningful threshold for this population. Answer Y/PY if the benchmark is consistent with contemporary published control-arm rates in comparable patients (similar disease stage, prior therapy, biomarker status). Answer N/PN if the benchmark is implausibly low (inflates apparent benefit) or implausibly high (forces a near-impossible bar). NI if no contemporary comparable estimate exists."},
    {"id": "1S.3", "text": "Is the cohort's measured baseline prognostic profile (stage, prior lines, ECOG / performance status, biomarker status, key comorbidities) comparable to that of the benchmark population?", "options": list(_NA_WITH_WN_SN), "elaboration": "The single-arm proportion is biased upward if the enrolled cohort is more prognostically favourable than the benchmark population (e.g. younger, less heavily pre-treated, biomarker-enriched). Answer Y/PY when measured baseline prognostic factors are comparable. WN when most-but-not-all prognostic factors look comparable. SN when at least one important prognostic factor is materially more favourable in this cohort. NA only when no benchmark was identified at 1S.1."},
    {"id": "1S.4", "text": "Did the authors address residual prognostic-mix differences quantitatively (sensitivity analyses, propensity-score adjustment to external controls, prognostic-score stratification, or similar)?", "options": list(_BASIC_NA), "elaboration": "Even when 1S.3 raises concerns, quantitative external-control adjustment can rescue interpretability. Examples include propensity-score weighting against an external real-world cohort, prognostic-score stratification, MAIC, or pre-specified sensitivity analyses showing the conclusion is robust to plausible prognostic differences. Answer Y/PY when such methods were used and reported. N/PN when not addressed."},
    {"id": "1S.5", "text": "Do negative / falsification controls, external-validity considerations, or other quantitative bias analyses suggest serious uncontrolled selection-prognostic bias?", "options": list(_BASIC[:4]), "elaboration": "Analogous to 1A.4 / 1B.5 in the cohort variants. Answer Y/PY if a falsification analysis (e.g. testing the intervention against an outcome it shouldn't affect) suggested residual bias, or if external-validity checks revealed serious cohort-vs-benchmark mismatch. Answer N if no falsification analysis was performed and no other consideration suggests substantial uncontrolled bias — this is the typical answer."},
]

DOMAIN2_SIGNALS: list[dict[str, Any]] = [
    {"id": "2.1", "text": "Were the intervention strategies distinguishable at the time when follow-up would have started in the target trial?", "options": list(_BASIC), "elaboration": "In most non-randomized studies, participants are classified to intervention strategies based on information about interventions prescribed or received. Some strategies (e.g. 'surgery within 6 months of diagnosis' vs 'delay surgery until clinical progression') cannot be distinguished at follow-up start, creating a period of 'immortal time' during which the outcome cannot occur for some groups."},
    {"id": "2.2", "text": "Did all or nearly all outcome events occur after the intervention and comparator strategies could be distinguished?", "options": list(_BASIC_NA), "elaboration": "Asked only if 2.1 was N/PN/NI. If the indistinguishable period is short relative to total follow-up, the proportion of outcome events during that period may be low and the misclassification bias correspondingly small."},
    {"id": "2.3", "text": "Did the analysis avoid problems arising from intervention strategies that are not distinguishable at the start of follow-up?", "options": list(_NA_DIFFERENTIAL), "elaboration": "Answer SY (strong yes, fully) if predictors of treatment during follow-up were measured and used appropriately to derive inverse-probability weights (e.g. clone-censor-weighting, g-formula), or if the study used a 'landmark' analysis. WY (partially) if appropriate but unlikely to have fully adjusted for prognostic factors predicting treatment after start of follow-up."},
    {"id": "2.4", "text": "Was classification of intervention status influenced by knowledge of the outcome or risk of the outcome?", "options": list(_DIFFERENTIAL), "elaboration": "Differential misclassification arises when the outcome (or its causes, other than the intervention) influences how interventions are classified. SY = yes, and the impact was substantial; WY = yes, but the impact was not substantial."},
    {"id": "2.5", "text": "Were further classification errors (not influenced by knowledge of the outcome or risk of the outcome) likely?", "options": list(_BASIC), "elaboration": "Non-differential misclassification — receipt of intervention not recorded for some participants. Usually biases towards the null. 'Nearly all' should be interpreted as 'enough to be confident of the findings'."},
]

DOMAIN2_VARIANT_SINGLE_ARM_SIGNALS: list[dict[str, Any]] = [
    {"id": "2S.1", "text": "Was the intervention well-defined (dose, schedule, duration, dose-modifications protocol) at the start of follow-up?", "options": list(_BASIC), "elaboration": "In a single-arm trial there is no comparator misclassification, but the single intervention must be specified precisely enough that the reported result corresponds to a reproducible regimen. Answer Y/PY when dose, schedule, duration, and dose-modification rules (reductions, holds, criteria for discontinuation) are fully reported. Answer N/PN when the intervention is described only at high level (e.g. 'standard chemotherapy')."},
    {"id": "2S.2", "text": "Were dose reductions, holds, and discontinuations recorded and reported?", "options": list(_WITH_WN_SN), "elaboration": "Recording of treatment delivery is essential for interpreting the single-arm result. WN if most exposure modifications were recorded; SN if material exposure detail is missing such that the analyzed 'intervention' is effectively undefined."},
    {"id": "2S.3", "text": "Was the analyzed cohort defined by intended treatment (everyone enrolled, ITT-like) or by received treatment (only those completing ≥X cycles / responding to treatment)?", "options": list(_DIFFERENTIAL), "elaboration": "Defining the analyzed cohort by *received* treatment (per-protocol completers, 'evaluable population') selects for patients who tolerated the intervention well enough to keep receiving it — a strong selection toward responders that inflates the single-arm proportion. Answer SY (strong yes) when the primary analysis is explicitly restricted to completers or responders. WY (weak yes) when the analyzed cohort excludes some enrolled patients for treatment-related reasons but not dominantly. Answer N/PN when all enrolled (or all who received any dose of intervention — modified ITT) are analyzed."},
]

DOMAIN3_SIGNALS: list[dict[str, Any]] = [
    {"id": "3.1", "text": "Did follow-up in the analysis begin at the start of the intervention strategies being compared?", "options": list(_WITH_WN_SN), "elaboration": "A. Prevalent-user bias and immortal time. Answer Y/PY if all outcome events and follow-up time after the start of the interventions were included in the analysis. WN if not substantial; SN if leading to a substantial risk of bias."},
    {"id": "3.2", "text": "Were outcome events during a period of follow-up after the start of the interventions excluded from the analysis?", "options": list(_BASIC), "elaboration": "Only asked if 3.1 was Y/PY. Such exclusion creates 'immortal time' during which events cannot occur and biases the effect estimate."},
    {"id": "3.3", "text": "Was selection of participants into the study (or into the analysis) based on participant characteristics observed after the start of intervention, additional to the situations addressed in 3.1 and 3.2?", "options": list(_BASIC), "elaboration": "B. Other selection bias. Answer Y/PY if selection into the study was based on post-intervention characteristics. N/PN if selection was based only on pre-intervention characteristics — baseline confounding is addressed in Domain 1, not here."},
    {"id": "3.4", "text": "Were the post-intervention variables that influenced selection likely to be associated with intervention?", "options": list(_BASIC_NA), "elaboration": "Only asked if 3.3 was Y/PY. Selection bias occurs when selection is related to an effect of either intervention or a cause of intervention AND an effect of either the outcome or a cause of the outcome."},
    {"id": "3.5", "text": "Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?", "options": list(_BASIC_NA), "elaboration": "Only asked if 3.4 was Y/PY. Collider-style selection bias."},
    {"id": "3.6", "text": "Is it likely that the analysis corrected for all of the potential selection biases identified above?", "options": list(_BASIC_NA), "elaboration": "C. Analysis / sensitivity / severity. Only asked if A or B raised concerns. Inverse probability weights can create a pseudo-population without the selection bias if assumptions are justified."},
    {"id": "3.7", "text": "Did sensitivity analyses demonstrate that the likely impact of the potential selection biases identified above was minimal?", "options": list(_BASIC_NA), "elaboration": "Only asked if 3.6 was N/PN/NI."},
    {"id": "3.8", "text": "Were potential selection biases identified above sufficiently severe that the result should not be included in a quantitative synthesis?", "options": list(_BASIC_NA), "elaboration": "Distinguishes 'Serious' from 'Critical' risk of selection bias. Answer N/PN/NI unless there is clear evidence that the selection biases identified were severe."},
]

DOMAIN4_SIGNALS: list[dict[str, Any]] = [
    {"id": "4.1", "text": "Were complete data on intervention status available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "'Nearly all' should be interpreted as the number excluded due to missing intervention data is so small it could not have made an important difference to the estimated effect. NI usually leads to a high risk-of-bias judgement."},
    {"id": "4.2", "text": "Were complete data on the outcome available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "For continuous outcomes, complete data for 95% (or 90%) is often sufficient. For dichotomous outcomes, the proportion required is directly linked to the risk of the outcome event."},
    {"id": "4.3", "text": "Were complete data on important confounding variables available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "Same 'nearly all' interpretation as 4.1 and 4.2."},
    {"id": "4.4", "text": "Is the result based on a complete case analysis?", "options": list(_BASIC_NA), "elaboration": "A complete case analysis is restricted to participants with complete data on all of the intervention, outcome and confounding variables."},
    {"id": "4.5", "text": "Was exclusion from the analysis because of missing data (in intervention, confounders or the outcome) likely to be related to the true value of the outcome?", "options": list(_BASIC_NA), "elaboration": "Y/PY if e.g. (1) differences between intervention groups in proportions excluded; (2) reported reasons indicate missingness depends on the true outcome; (3) the outcome's nature makes missingness likely (severe depression participants missing appointments)."},
    {"id": "4.6", "text": "Is the relationship between the outcome and missingness likely to be explained by the variables in the analysis model?", "options": list(_NA_WITH_WN_SN), "elaboration": "If all variables that plausibly explain the outcome-missingness relationship are included in the complete-case analysis, bias due to missing data will be low. WN if not substantial; SN if bias is likely substantial."},
    {"id": "4.7", "text": "Was the analysis based on imputing missing values?", "options": list(_BASIC_NA), "elaboration": "Y/PY if the analysis used either single or multiple imputation."},
    {"id": "4.8", "text": "Is it reasonable to assume data were 'missing at random' (MAR) or 'missing completely at random' (MCAR)?", "options": list(_BASIC_NA), "elaboration": "Multiple imputation avoids bias provided incomplete variables are MAR or MCAR but not if MNAR (missing not at random). N/PN if there is reason to believe data are MNAR."},
    {"id": "4.9", "text": "Was imputation performed appropriately?", "options": list(_NA_WITH_WN_SN), "elaboration": "WN / SN if simple methods (LOCF, mean imputation) were used; Y/PY if multiple imputation included all predictors of missingness and all variables in the main analysis model."},
    {"id": "4.10", "text": "Was an appropriate alternative method used to correct for bias due to missing data?", "options": list(_NA_WITH_WN_SN), "elaboration": "Asked when the analysis was neither a complete case analysis nor based on imputation. Examples include inverse probability weighting and full information maximum likelihood."},
    {"id": "4.11", "text": "Is there evidence that the result was not biased by missing data?", "options": list(_BASIC_NA), "elaboration": "Evidence may come from (1) analysis methods that would not be biased under plausible assumptions about missingness, or (2) sensitivity analyses showing results change little under plausible assumptions."},
]

DOMAIN5_SIGNALS: list[dict[str, Any]] = [
    {"id": "5.1", "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?", "options": list(_BASIC), "elaboration": "Comparable methods involve the same measurement methods and thresholds, used at comparable time points. Differences can arise through 'diagnostic detection bias' or extra visits for intervention participants."},
    {"id": "5.2", "text": "Were outcome assessors aware of the intervention received by study participants?", "options": list(_BASIC), "elaboration": "N if outcome assessors were blinded, or if participants self-report and were themselves blinded. In observational studies, the answer will usually be Y when participants report their outcomes themselves."},
    {"id": "5.3", "text": "Could assessment of the outcome have been influenced by knowledge of the intervention received?", "options": list(_NA_DIFFERENTIAL), "elaboration": "Only asked if 5.2 was Y/PY/NI. SY (yes, to a large extent) for patient-reported symptoms in homeopathy studies, or assessments of recovery by physiotherapists. WY (yes, to a small extent) when knowledge could have influenced assessment but no strong reason to believe it did."},
]

DOMAIN6_SIGNALS: list[dict[str, Any]] = [
    {"id": "6.1", "text": "Was the result reported in accordance with an available, pre-determined analysis plan?", "options": list(_BASIC), "elaboration": "Analysis plans are rarely publicly available for non-randomized studies, so most papers will not be assessed as Low risk of bias for this domain on the basis of 6.1 alone."},
    {"id": "6.2", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple outcome measurements within the outcome domain?", "options": list(_BASIC), "elaboration": "Pain may be measured via VAS, McGill Pain Questionnaire, etc, at multiple time points. If only the most favourable is reported without justification, answer Y/PY."},
    {"id": "6.3", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple analyses of the data?", "options": list(_BASIC), "elaboration": "Multiple analytic choices (unadjusted vs adjusted, alternative covariate sets, missing-data strategies) generate multiple estimates. Selection on favourable results is concerning."},
    {"id": "6.4", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple subgroups?", "options": list(_BASIC), "elaboration": "Particularly with large cohorts from routine data, multiple subgroup estimates can be generated. Selection of the most interesting subgroup result is selective reporting."},
]


DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Bias due to confounding",
        "variants": ["A", "B", "single_arm"],
        "variant_signals": {
            "A": DOMAIN1_VARIANT_A_SIGNALS,
            "B": DOMAIN1_VARIANT_B_SIGNALS,
            "single_arm": DOMAIN1_VARIANT_SINGLE_ARM_SIGNALS,
        },
        "signals": DOMAIN1_VARIANT_A_SIGNALS + DOMAIN1_VARIANT_B_SIGNALS + DOMAIN1_VARIANT_SINGLE_ARM_SIGNALS,
        "relevant_fields": ["confounders_measured", "adjustment_method", "exposure_definition", "comparator_group", "comparator_historical_reference", "immortal_time_bias", "confounding_control", "primary_endpoint_prespecified", "consecutive_enrolment"],
    },
    {
        "id": 2,
        "name": "Bias in classification of interventions",
        "variants": ["A", "B", "single_arm"],
        "variant_signals": {"A": DOMAIN2_SIGNALS, "B": DOMAIN2_SIGNALS, "single_arm": DOMAIN2_VARIANT_SINGLE_ARM_SIGNALS},
        "signals": DOMAIN2_SIGNALS + DOMAIN2_VARIANT_SINGLE_ARM_SIGNALS,
        "relevant_fields": ["exposure_definition", "exposure_measurement", "exposure_ascertainment", "intervention_classification", "escalation_scheme", "dose_levels", "expansion_cohort"],
    },
    {"id": 3, "name": "Bias in selection of participants into the study (or analysis)", "signals": DOMAIN3_SIGNALS, "relevant_fields": ["case_source", "control_selection", "sampling_method", "loss_to_follow_up", "immortal_time_bias"]},
    {"id": 4, "name": "Bias due to missing data", "signals": DOMAIN4_SIGNALS, "relevant_fields": ["loss_to_follow_up", "missing_data_handling", "attrition_rate"]},
    {"id": 5, "name": "Bias arising from measurement of the outcome", "signals": DOMAIN5_SIGNALS, "relevant_fields": ["outcome_ascertainment", "outcome_definition"]},
    {"id": 6, "name": "Bias in selection of the reported result", "signals": DOMAIN6_SIGNALS, "relevant_fields": ["outcome_definition", "statistical_analysis"]},
]


# ─────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "non-randomized study of an intervention using the Cochrane ROBINS-I V2 "
    "tool (20 November 2025 cribsheet). Read the PDF carefully. Answer each "
    "signaling question with one of the allowed tokens for that question — "
    "Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information), "
    "and where indicated WN (weak no), SN (strong no), WY (weak yes), "
    "SY (strong yes). Provide a 1-2 sentence rationale for each answer, "
    "quoting the paper where possible. Return ONLY a valid JSON object — no "
    "preamble, no markdown fences."
)


def _build_preflight_prompt_cohort(study_type: str, primary_outcome: str,
                                    extracted_fields: dict[str, str]) -> str:
    relevant_keys = ["confounders_measured", "adjustment_method", "outcome_definition",
                     "outcome_ascertainment", "analysis_framework", "primary_outcome_measurement"]
    relevant = {k: extracted_fields[k] for k in relevant_keys if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are performing the **Preliminary Considerations** screen of ROBINS-I V2 on a non-randomized study.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

Answer four preliminary-consideration questions:

**B1. Did the authors make any attempt to control for confounding in the result being assessed?**
Options: Y / PY / PN / N
Elaboration: Confounding is a substantial problem in most non-randomized studies. Answer Y/PY if the analysis includes multivariable adjustment, matching, stratification, propensity-score methods, or inverse probability weighting.

**B2. (Only if N/PN to B1) Is there sufficient potential for confounding that an unadjusted result should not be considered further?**
Options: Y / PY / PN / N
Elaboration: If there is sufficient potential for confounding that an unadjusted result should not be considered, the result is at Critical risk of bias.

**B3. Was the method of measuring the outcome inappropriate?**
Options: Y / PY / PN / N
Elaboration: Identify methods of outcome measurement unsuitable for the outcome they evaluate. Answer Y/PY if (1) important outcome values fall outside levels detectable by the method; (2) the instrument has demonstrated poor reliability/validity; or (3) measurement differed substantially between intervention and comparator groups so that group differences are not interpretable. In most circumstances answer N/PN.

**C4. Did the analysis account for switches during follow-up between the intervention strategies being compared, or for other protocol deviations during follow-up?**
Options: No (the analysis is estimating the intention-to-treat effect — Variant A) / Yes (the analysis is estimating the per-protocol effect — Variant B)

Return JSON with exactly this shape:
{{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences quoting the paper",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences (or 'NA' if B1 was Y/PY)",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences quoting the paper",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences explaining whether the analysis estimates ITT or per-protocol"
}}"""


def _build_preflight_prompt_single_arm(study_type: str, primary_outcome: str,
                                        extracted_fields: dict[str, str]) -> str:
    relevant_keys = ["primary_endpoint_prespecified", "inclusion_exclusion_criteria",
                     "comparator_historical_reference", "consecutive_enrolment",
                     "outcome_definition", "outcome_ascertainment",
                     "primary_outcome_measurement", "analysis_framework"]
    relevant = {k: extracted_fields[k] for k in relevant_keys if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are performing the **Preliminary Considerations** screen of ROBINS-I V2 (adapted for single-arm / uncontrolled designs) on an uncontrolled clinical study.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

This study has **no comparator group** — every participant received the intervention. Risk of bias is therefore not about confounding-by-indication (which requires a comparator) but about whether the implied benchmark for interpretation (historical control rate, performance criterion, or null hypothesis with a decision rule) was pre-specified and reasonable.

Answer four preliminary-consideration questions:

**B1-SA. Did the authors pre-specify a quantitative benchmark (historical control rate, performance criterion, or null hypothesis with a statistical decision rule) against which the single-arm result is being judged?**
Options: Y / PY / PN / N
Elaboration: Examples of pre-specified benchmarks include: a Simon two-stage design with a numeric response-rate threshold; an FDA accelerated-approval ORR threshold cited in the protocol; a published historical control rate that the trial was powered against. Answer Y/PY if such a benchmark is clearly identifiable in the protocol/SAP/methods. Answer N/PN if no benchmark is stated, or if the benchmark looks post-hoc.

**B2-SA. (Only if N/PN to B1-SA) Is the absence of any pre-specified benchmark severe enough that the single-arm proportion is uninterpretable for causal inference?**
Options: Y / PY / PN / N
Elaboration: Y/PY when the result is reported as a bare proportion with no reference point at all, such that any interpretation depends entirely on post-hoc comparisons. This short-circuits to Critical risk of bias.

**B3. Was the method of measuring the outcome inappropriate?**
Options: Y / PY / PN / N
Elaboration: Identify methods of outcome measurement unsuitable for the outcome they evaluate. Answer Y/PY if (1) important outcome values fall outside levels detectable by the method; (2) the instrument has demonstrated poor reliability/validity; or (3) measurement methods are not interpretable for the question. In most circumstances answer N/PN.

**C4. Did the analysis account for protocol deviations during follow-up (e.g. participants who discontinued the intervention or switched to another therapy)?**
Options: No (the analysis is an intention-to-treat / modified-ITT analysis of all enrolled) / Yes (the analysis is a per-protocol analysis restricted to those who completed treatment / responded)
Note: For single-arm studies this answer is recorded as metadata but does NOT swap risk-of-bias variants. It informs interpretation of D2-single-arm question 2S.3.

Return JSON with exactly this shape:
{{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences quoting the paper (answer to B1-SA)",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences (or 'NA' if B1 was Y/PY)",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences quoting the paper",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences explaining whether the analysis is ITT-like or per-protocol-like"
}}"""


def _signals_for_domain(domain: dict[str, Any], variant: str) -> list[dict[str, Any]]:
    if domain.get("variant_signals"):
        return domain["variant_signals"][variant]
    return domain["signals"]


def build_domain_prompt(domain: dict[str, Any], variant: str, study_type: str,
                        primary_outcome: str, extracted_fields: dict[str, str],
                        target_pico: dict[str, str] | None = None) -> str:
    signals = _signals_for_domain(domain, variant)

    relevant = {k: extracted_fields[k] for k in domain.get("relevant_fields", [])
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    pico_block = ""
    if target_pico:
        pico_block = "\nTarget PICO (user-supplied):\n" + json.dumps(target_pico, indent=2) + "\n"

    domain_header = f"Domain {domain['id']} — {domain['name']}"
    if domain.get("variant_signals"):
        domain_header += f" (Variant {variant})"

    q_lines = []
    for sig in signals:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: {'/'.join(sig['options'])}."
        )
    questions_block = "\n".join(q_lines)

    shape = "{\n"
    for sig in signals:
        opt_string = "|".join(sig["options"])
        shape += f'  "{sig["id"]}": "{opt_string}",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours intervention|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"

    return f"""Assess **{domain_header}** for the study described in the attached PDF using the ROBINS-I V2 tool.

Study type: {study_type}
Outcome being assessed: {primary_outcome}
{pico_block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V2:
- The judgement scale is **Low / Moderate / Serious / Critical** (4 levels). Code maps your signal answers to the judgement — answer the signaling questions only.
- Some questions allow **WN / SN** (weak / strong no) or **WY / SY** (weak / strong yes). Use the strong version only when the magnitude is clearly substantial; use the weak version when the direction is right but the magnitude is uncertain.
- For each question, answer based on what the paper says about that specific question — **do NOT try to determine whether a question is gated out (NA) by the cribsheet's cascading structure**. Python applies the cascade rules after you answer and will set `NA` for any question that should be gated out. Just answer each question independently based on its own text.
- Answer N (or PN) when the paper gives enough information to rule out the problem; NI only when the paper is silent.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


# ─────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────
def run_preflight(pdf_bytes: bytes, study_type: str, primary_outcome: str,
                  extracted_fields: dict[str, str],
                  llm_call: Callable[[bytes, str, int], dict[str, Any]]) -> dict[str, Any]:
    """Run the preflight LLM call. Returns answers + variant + screening decision."""
    is_single_arm = study_type in SINGLE_ARM_STUDY_TYPES
    if is_single_arm:
        prompt = _build_preflight_prompt_single_arm(study_type, primary_outcome, extracted_fields)
    else:
        prompt = _build_preflight_prompt_cohort(study_type, primary_outcome, extracted_fields)
    raw = llm_call(pdf_bytes, prompt, 2048)

    def _opt(key: str, default: str = "NI",
             allowed: tuple = ("Y", "PY", "PN", "N")) -> str:
        v = str(raw.get(key, default)).strip().upper()
        if v not in allowed:
            return default
        return v

    b1 = _opt("B1")
    b2 = _opt("B2", default="NA", allowed=("Y", "PY", "PN", "N", "NA"))
    b3 = _opt("B3")
    c4_raw = str(raw.get("C4", "No")).strip().lower()
    c4 = "Yes" if c4_raw.startswith("y") else "No"

    rationales = {
        "B1": str(raw.get("B1_rationale", "")).strip(),
        "B2": str(raw.get("B2_rationale", "")).strip(),
        "B3": str(raw.get("B3_rationale", "")).strip(),
        "C4": str(raw.get("C4_rationale", "")).strip(),
    }

    if is_single_arm:
        variant = "single_arm"
        b2_reason = ("B2-SA: Absence of any pre-specified benchmark is severe enough "
                     "that the single-arm proportion is uninterpretable for causal inference.")
    else:
        variant = "A" if c4 == "No" else "B"
        b2_reason = ("B2: Sufficient potential for confounding that the unadjusted "
                     "result should not be considered further.")

    if b2 in ("Y", "PY"):
        return {"B1": b1, "B2": b2, "B3": b3, "C4": c4, "rationales": rationales,
                "screening_decision": "critical", "screening_reason": b2_reason,
                "variant": variant}
    if b3 in ("Y", "PY"):
        return {"B1": b1, "B2": b2, "B3": b3, "C4": c4, "rationales": rationales,
                "screening_decision": "critical",
                "screening_reason": "B3: The method of measuring the outcome is inappropriate.",
                "variant": variant}
    return {"B1": b1, "B2": b2, "B3": b3, "C4": c4, "rationales": rationales,
            "screening_decision": "proceed", "screening_reason": "", "variant": variant}


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any], variant: str,
                   study_type: str, primary_outcome: str,
                   extracted_fields: dict[str, str],
                   llm_call: Callable[[bytes, str, int], dict[str, Any]],
                   target_pico: dict[str, str] | None = None) -> dict[str, Any]:
    prompt = build_domain_prompt(domain, variant, study_type, primary_outcome,
                                 extracted_fields, target_pico)
    raw = llm_call(pdf_bytes, prompt, 8192)

    signals_for_this = _signals_for_domain(domain, variant)
    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in signals_for_this:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        allowed = set(sig["options"])
        if ans not in allowed:
            logger.warning("ROBINS-I V2 domain %s question %s: invalid answer %r — defaulting to NI",
                           domain["id"], sid, ans)
            ans = "NI" if "NI" in allowed else next(iter(allowed))
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    # Python-side cascade enforcement: override LLM answers to NA for
    # questions gated out by the cribsheet's cascading structure.
    # See §17 for the design rationale.
    pre_cascade = dict(signals)
    signals = enforce_cascade_v2(domain["id"], signals, variant=variant)
    overrides = {sid: (pre_cascade[sid], signals[sid])
                 for sid in signals
                 if sid in pre_cascade and pre_cascade[sid] != signals[sid]}
    if overrides:
        logger.debug("ROBINS-I V2 D%s variant %s cascade enforcement overrode LLM answers: %r",
                     domain["id"], variant, overrides)

    if variant == "A":
        judges = DOMAIN_JUDGES_VARIANT_A
    elif variant == "B":
        judges = DOMAIN_JUDGES_VARIANT_B
    elif variant == "single_arm":
        judges = DOMAIN_JUDGES_VARIANT_SINGLE_ARM
    else:
        judges = DOMAIN_JUDGES_VARIANT_A
    judgement = judges[domain["id"]](signals)
    direction = str(raw.get("direction_of_bias", "NA")).strip() or "NA"

    result: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
        "direction": direction,
    }
    if domain.get("variant_signals"):
        result["variant"] = variant
    return result


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        *,
        llm_call: Callable[[bytes, str, int], dict[str, Any]],
        target_pico: dict[str, str] | None = None,
        progress: Callable[[int], None] | None = None,
        ) -> tuple[dict[str, Any], str, str]:
    """Run ROBINS-I V2 against a non-randomized study.

    Pipeline:
      1. Preflight (B1/B2/B3 + C4) — single LLM call.
      2. If B2=Y/PY or B3=Y/PY → return Critical immediately (skip domains).
      3. Otherwise per-domain assessments (Domain 1 dispatched by Variant).

    Returns ``(domain_results, overall_judgement, overall_direction)``.
    """
    study_type = classification.get("study_type", "Cohort Study")

    if progress:
        try:
            progress(0)
        except Exception:
            pass

    preflight = run_preflight(pdf_bytes, study_type, primary_outcome,
                              extracted_fields, llm_call)
    domain_results: dict[str, Any] = {"preflight": preflight}

    if preflight["screening_decision"] == "critical":
        return domain_results, "Critical", "Unpredictable"

    variant = preflight["variant"]
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(pdf_bytes, domain, variant, study_type,
                                primary_outcome, extracted_fields,
                                llm_call=llm_call, target_pico=target_pico)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    domain_judgements = [domain_results[str(d["id"])]["judgement"] for d in DOMAINS]
    overall = robins_i_overall(domain_judgements)

    dirs = [domain_results[str(d["id"])]["direction"]
            for d in DOMAINS
            if domain_results[str(d["id"])]["direction"] not in ("", "NA")]
    if not dirs:
        overall_direction = "NA"
    else:
        counts = Counter(dirs).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            overall_direction = "Unpredictable"
        else:
            overall_direction = counts[0][0]

    return domain_results, overall, overall_direction
```

**From that document's §14. Quick test sketches:**

```python
# ─────────────────────────────────────────────
# Domain 1 — Variant A (ITT)
# ─────────────────────────────────────────────
# All-yes path → Low (with variant label)
assert domain1_variant_a_judge({"1A.1": "Y", "1A.2": "Y", "1A.3": "N", "1A.4": "N"}) == LOW_D1
# Negative-control hit on 1A.4 with otherwise-clean → Serious
assert domain1_variant_a_judge({"1A.1": "Y", "1A.2": "Y", "1A.3": "N", "1A.4": "Y"}) == "Serious"
# Strong-no on 1A.1 → Serious
assert domain1_variant_a_judge({"1A.1": "SN", "1A.2": "Y", "1A.3": "N", "1A.4": "N"}) == "Serious"
# Strong-no on 1A.1 + negative-control hit → Critical
assert domain1_variant_a_judge({"1A.1": "SN", "1A.2": "Y", "1A.3": "N", "1A.4": "Y"}) == "Critical"
# Weak-no on 1A.1 (floor: Moderate, not Low)
assert domain1_variant_a_judge({"1A.1": "WN", "1A.2": "Y", "1A.3": "N", "1A.4": "N"}) == "Moderate"

# ─────────────────────────────────────────────
# Domain 1 — Variant B (per-protocol)
# ─────────────────────────────────────────────
assert domain1_variant_b_judge({"1B.1": "Y", "1B.2": "Y", "1B.3": "Y", "1B.4": "N", "1B.5": "N"}) == LOW_D1
# Inappropriate analysis method (1B.1 N) → Serious or Critical
assert domain1_variant_b_judge({"1B.1": "N", "1B.2": "Y", "1B.3": "Y", "1B.4": "N", "1B.5": "N"}) == "Serious"
assert domain1_variant_b_judge({"1B.1": "N", "1B.2": "Y", "1B.3": "Y", "1B.4": "Y", "1B.5": "N"}) == "Critical"

# ─────────────────────────────────────────────
# Domain 1 — Single-arm variant
# ─────────────────────────────────────────────
assert domain1_variant_single_arm_judge({"1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "NA", "1S.5": "N"}) == LOW_D1_SA
# 1S.5 dominates — falsification-control hit → Critical
assert domain1_variant_single_arm_judge({"1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "NA", "1S.5": "Y"}) == "Critical"
# No benchmark + no quantitative adjustment → Critical
assert domain1_variant_single_arm_judge({"1S.1": "N", "1S.2": "NI", "1S.3": "NA", "1S.4": "N", "1S.5": "N"}) == "Critical"
# Benchmark + prognostic mismatch + quantitative adjustment → Moderate
assert domain1_variant_single_arm_judge({"1S.1": "Y", "1S.2": "Y", "1S.3": "SN", "1S.4": "Y", "1S.5": "N"}) == "Moderate"

# ─────────────────────────────────────────────
# Domain 2 cohort
# ─────────────────────────────────────────────
# Strategies distinguishable + no diff/non-diff misclassification → Low
assert domain2_judge({"2.1": "Y", "2.2": "NA", "2.3": "NA", "2.4": "N", "2.5": "N"}) == "Low"
# Bottom tier (2.3 N) + 2.4 SY → Critical via direct-route
assert domain2_judge({"2.1": "N", "2.2": "N", "2.3": "N", "2.4": "SY", "2.5": "N"}) == "Critical"

# ─────────────────────────────────────────────
# Domain 2 single-arm
# ─────────────────────────────────────────────
# Per-protocol completers cohort definition → Critical
assert domain2_variant_single_arm_judge({"2S.1": "Y", "2S.2": "Y", "2S.3": "SY"}) == "Critical"
# ITT cohort + well-defined intervention + good recording → Low
assert domain2_variant_single_arm_judge({"2S.1": "Y", "2S.2": "Y", "2S.3": "N"}) == "Low"

# ─────────────────────────────────────────────
# Domain 3 — selection
# ─────────────────────────────────────────────
# Best case: no immortal time, no other selection bias → Low
assert domain3_judge({"3.1": "Y", "3.2": "N", "3.3": "N",
                      "3.4": "NA", "3.5": "NA",
                      "3.6": "NA", "3.7": "NA", "3.8": "NA"}) == "Low"

# ─────────────────────────────────────────────
# Domain 4 — missing data
# ─────────────────────────────────────────────
# All complete data → Low directly
assert domain4_judge({"4.1": "Y", "4.2": "Y", "4.3": "Y", "4.4": "NA",
                      "4.5": "NA", "4.6": "NA", "4.7": "NA", "4.8": "NA",
                      "4.9": "NA", "4.10": "NA", "4.11": "NA"}) == "Low"

# ─────────────────────────────────────────────
# Domain 5 — outcome measurement
# ─────────────────────────────────────────────
assert domain5_judge({"5.1": "N", "5.2": "N", "5.3": "NA"}) == "Low"
# Differential measurement → Serious directly
assert domain5_judge({"5.1": "Y", "5.2": "N", "5.3": "NA"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 6 — selective reporting
# ─────────────────────────────────────────────
# Pre-determined plan → Low
assert domain6_judge({"6.1": "Y", "6.2": "N", "6.3": "N", "6.4": "N"}) == "Low"
# Two selective-reporting flags → Critical
assert domain6_judge({"6.1": "N", "6.2": "Y", "6.3": "Y", "6.4": "N"}) == "Critical"

# ─────────────────────────────────────────────
# Preflight short-circuit logic
# ─────────────────────────────────────────────
def _preflight_decide(b2, b3, c4, study_type):
    """Inline mirror of the dispatcher logic for unit-testing without an LLM."""
    is_sa = study_type in SINGLE_ARM_STUDY_TYPES
    variant = "single_arm" if is_sa else ("B" if c4 == "Yes" else "A")
    if b2 in ("Y", "PY"):
        return {"screening_decision": "critical", "variant": variant}
    if b3 in ("Y", "PY"):
        return {"screening_decision": "critical", "variant": variant}
    return {"screening_decision": "proceed", "variant": variant}

# B2 = Y → Critical
assert _preflight_decide("Y", "N", "No", "Cohort Study")["screening_decision"] == "critical"
assert _preflight_decide("PY", "N", "No", "Cohort Study")["screening_decision"] == "critical"
# B3 = Y → Critical
assert _preflight_decide("NA", "Y", "No", "Cohort Study")["screening_decision"] == "critical"
# All clean → proceed
assert _preflight_decide("NA", "N", "No", "Cohort Study")["screening_decision"] == "proceed"
# C4 = No → Variant A
assert _preflight_decide("NA", "N", "No", "Cohort Study")["variant"] == "A"
# C4 = Yes → Variant B
assert _preflight_decide("NA", "N", "Yes", "Cohort Study")["variant"] == "B"
# Single-arm study type pins variant regardless of C4
assert _preflight_decide("NA", "N", "No", "Single-Arm Trial")["variant"] == "single_arm"
assert _preflight_decide("NA", "N", "Yes", "Single-Arm Trial")["variant"] == "single_arm"
assert _preflight_decide("NA", "N", "Yes", "Dose-Escalation Study")["variant"] == "single_arm"

# ─────────────────────────────────────────────
# robins_i_overall — worst-domain aggregation
# ─────────────────────────────────────────────
assert robins_i_overall(["Low", "Low", "Low", "Low", "Low", "Low"]) == "Low"
# Variant-labeled Low normalizes to Low for ranking
assert robins_i_overall([LOW_D1, "Low", "Low", "Low", "Low", "Low"]) == "Low"
assert robins_i_overall([LOW_D1_SA, "Low", "Low", "Low", "Low", "Low"]) == "Low"
# Worst domain wins
assert robins_i_overall([LOW_D1, "Moderate", "Low", "Serious", "Low", "Low"]) == "Serious"
assert robins_i_overall(["Low", "Low", "Low", "Critical", "Low", "Low"]) == "Critical"
assert robins_i_overall([]) == "Low"

# ─────────────────────────────────────────────
# DOMAINS structural invariants
# ─────────────────────────────────────────────
assert len(DOMAINS) == 6
assert [d["id"] for d in DOMAINS] == [1, 2, 3, 4, 5, 6]

# Domain 1: 3 variants × (4 + 5 + 5) = 14 unique signals via variant_signals
d1 = DOMAINS[0]
assert set(d1["variant_signals"].keys()) == {"A", "B", "single_arm"}
assert len(d1["variant_signals"]["A"]) == 4
assert len(d1["variant_signals"]["B"]) == 5
assert len(d1["variant_signals"]["single_arm"]) == 5

# Domain 2: cohort variants share 5, single-arm has 3
d2 = DOMAINS[1]
assert d2["variant_signals"]["A"] == d2["variant_signals"]["B"]  # cohort shares
assert len(d2["variant_signals"]["A"]) == 5
assert len(d2["variant_signals"]["single_arm"]) == 3

# D3-D6 invariant (no variant_signals key)
for d in DOMAINS[2:]:
    assert "variant_signals" not in d

# Signal counts per invariant domain
assert len(DOMAINS[2]["signals"]) == 8   # D3
assert len(DOMAINS[3]["signals"]) == 11  # D4
assert len(DOMAINS[4]["signals"]) == 3   # D5
assert len(DOMAINS[5]["signals"]) == 4   # D6

# Variant-specific signal ID prefixes appear exactly where expected
all_d1_ids = {s["id"] for v in ("A", "B", "single_arm") for s in d1["variant_signals"][v]}
assert {"1A.1", "1A.2", "1A.3", "1A.4"}.issubset(all_d1_ids)
assert {"1B.1", "1B.2", "1B.3", "1B.4", "1B.5"}.issubset(all_d1_ids)
assert {"1S.1", "1S.2", "1S.3", "1S.4", "1S.5"}.issubset(all_d1_ids)

all_d2_ids = {s["id"] for v in ("A", "B", "single_arm") for s in d2["variant_signals"][v]}
assert {"2.1", "2.2", "2.3", "2.4", "2.5"}.issubset(all_d2_ids)
assert {"2S.1", "2S.2", "2S.3"}.issubset(all_d2_ids)

# ─────────────────────────────────────────────
# Cascade enforcement (Python-side NA gating) — V2
# ─────────────────────────────────────────────
# D1 Variant B: 1B.4 NA when 1B.1 Y/PY (appropriate analysis method)
out = enforce_cascade_d1_variant_b_v2({"1B.1": "Y", "1B.4": "Y"})
assert out["1B.4"] == "NA"
out = enforce_cascade_d1_variant_b_v2({"1B.1": "N", "1B.4": "Y"})
assert out["1B.4"] == "Y"  # kept when 1B.1 inappropriate

# D1 single-arm: 1S.3 NA when 1S.1 N/PN (no benchmark)
out = enforce_cascade_d1_variant_single_arm_v2({"1S.1": "N", "1S.3": "Y"})
assert out["1S.3"] == "NA"
out = enforce_cascade_d1_variant_single_arm_v2({"1S.1": "Y", "1S.3": "Y"})
assert out["1S.3"] == "Y"  # benchmark identified, 1S.3 applies

# D2 cohort: 2.2 NA when 2.1 Y/PY (strategies distinguishable)
out = enforce_cascade_d2_cohort_v2({"2.1": "Y", "2.2": "N"})
assert out["2.2"] == "NA"
out = enforce_cascade_d2_cohort_v2({"2.1": "N", "2.2": "Y"})
assert out["2.2"] == "Y"

# D3: 3.2 NA when 3.1 not Y/PY
out = enforce_cascade_d3_v2({"3.1": "WN", "3.2": "Y", "3.3": "N",
                             "3.4": "Y", "3.5": "Y", "3.6": "N",
                             "3.7": "N", "3.8": "N"})
assert out["3.2"] == "NA"  # 3.1 not Y/PY → 3.2 gated
# 3.4, 3.5 NA when 3.3 not Y/PY
assert out["3.4"] == "NA"
assert out["3.5"] == "NA"
# A raises concerns (3.1 WN) → 3.6 asked
assert out["3.6"] == "N"

# D3: 3.7 NA when 3.6 Y/PY (analysis fully corrected)
out = enforce_cascade_d3_v2({"3.1": "WN", "3.3": "N",
                             "3.6": "Y", "3.7": "Y", "3.8": "N"})
assert out["3.7"] == "NA"

# D3: 3.6/3.7/3.8 NA when no concerns (3.1 Y/PY AND 3.3 N/PN)
out = enforce_cascade_d3_v2({"3.1": "Y", "3.2": "N", "3.3": "N",
                             "3.6": "Y", "3.7": "Y", "3.8": "Y"})
assert out["3.6"] == "NA"
assert out["3.7"] == "NA"
assert out["3.8"] == "NA"

# D4 best case: complete data → all downstream NA
out = enforce_cascade_d4_v2({"4.1": "Y", "4.2": "Y", "4.3": "Y",
                             "4.4": "Y", "4.5": "Y", "4.7": "Y",
                             "4.11": "Y"})
for sid in ("4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "4.11"):
    assert out[sid] == "NA", f"D4 best-case didn't NA {sid}"

# D4 complete-case path (4.4 Y/PY): 4.7-4.10 NA, 4.5/4.6/4.11 kept
out = enforce_cascade_d4_v2({"4.1": "N", "4.2": "Y", "4.3": "Y",
                             "4.4": "Y", "4.5": "Y", "4.6": "Y",
                             "4.7": "Y", "4.8": "Y", "4.9": "Y",
                             "4.10": "Y", "4.11": "Y"})
for sid in ("4.7", "4.8", "4.9", "4.10"):
    assert out[sid] == "NA"
assert out["4.5"] == "Y"
assert out["4.6"] == "Y"
assert out["4.11"] == "Y"

# D4 complete-case path with 4.5 N/PN (no concerning exclusion): 4.6 NA
out = enforce_cascade_d4_v2({"4.1": "N", "4.2": "Y", "4.3": "Y",
                             "4.4": "Y", "4.5": "N", "4.6": "Y"})
assert out["4.6"] == "NA"

# D4 imputation path (4.4 N/PN, 4.7 Y/PY): 4.5/4.6/4.10 NA, 4.8/4.9 kept
out = enforce_cascade_d4_v2({"4.1": "N", "4.2": "Y", "4.3": "Y",
                             "4.4": "N", "4.5": "Y", "4.6": "Y",
                             "4.7": "Y", "4.8": "Y", "4.9": "Y",
                             "4.10": "Y", "4.11": "Y"})
assert out["4.5"] == "NA"
assert out["4.6"] == "NA"
assert out["4.10"] == "NA"
assert out["4.8"] == "Y"
assert out["4.9"] == "Y"

# D4 imputation path with 4.8 N/PN (MAR/MCAR unreasonable): 4.9 NA
out = enforce_cascade_d4_v2({"4.1": "N", "4.4": "N", "4.7": "Y",
                             "4.8": "N", "4.9": "Y", "4.10": "Y"})
assert out["4.9"] == "NA"

# D4 alternative-method path (4.4 N/PN, 4.7 N/PN): 4.8/4.9 NA, 4.10 kept
out = enforce_cascade_d4_v2({"4.1": "N", "4.4": "N", "4.7": "N",
                             "4.8": "Y", "4.9": "Y", "4.10": "Y"})
assert out["4.8"] == "NA"
assert out["4.9"] == "NA"
assert out["4.10"] == "Y"

# D5: 5.3 NA when 5.2 N/PN (assessor blinded — no influence to assess)
out = enforce_cascade_d5_v2({"5.1": "N", "5.2": "N", "5.3": "SY"})
assert out["5.3"] == "NA"
out = enforce_cascade_d5_v2({"5.1": "N", "5.2": "Y", "5.3": "SY"})
assert out["5.3"] == "SY"  # 5.2 Y/PY → 5.3 applies

# Dispatch helper: D1 Variant A + D2 single-arm + D6 unchanged
assert enforce_cascade_v2(1, {"1A.1": "Y"}, variant="A") == {"1A.1": "Y"}
assert enforce_cascade_v2(2, {"2S.1": "Y"}, variant="single_arm") == {"2S.1": "Y"}
assert enforce_cascade_v2(6, {"6.1": "N"}, variant="A") == {"6.1": "N"}

# Integration: LLM inconsistency is caught
# LLM answered 3.2 = "Y" even though 3.1 = "SN" (which gates 3.2 out)
llm_response = {"3.1": "SN", "3.2": "Y", "3.3": "N",
                "3.6": "Y", "3.8": "N"}
enforced = enforce_cascade_d3_v2(llm_response)
assert enforced["3.2"] == "NA"  # cascade override caught LLM error
assert enforced["3.4"] == "NA"  # 3.3 N → 3.4 NA
assert enforced["3.5"] == "NA"
# 3.1 SN → A concerns → 3.6 asked; 3.6 Y → 3.7 NA
assert enforced["3.7"] == "NA"
# Tree should still produce a sensible judgement
result = domain3_judge(enforced)
assert result in ("Low", "Moderate", "Serious", "Critical")

print("All ROBINS-I V2 sanity checks passed.")
```

### 12.5 ROBINS-I V1 (incl. single-arm adaptation) — from `robins_i_v1_shareable.md`

**From that document's §15. Reference implementation — single self-contained Python module:**

```python
llm_call(pdf_bytes: bytes, prompt: str, max_tokens: int) -> dict
```

```python
"""ROBINS-I V1 (1 August 2016) — Single-file reference implementation.

Source: Sterne JAC, Hernán MA, Reeves BC, Savović J, Berkman ND, Viswanathan M,
Henry D, Altman DG, Ansari MT, Boutron I, Carpenter JR, Chan A-W, Churchill R,
Hróbjartsson A, Kirkham J, Jüni P, Loke YK, Pigott TD, Ramsay CR, Regidor D,
Rothstein HR, Sandhu L, Santaguida PL, Schünemann HJ, Shea B, Shrier I, Tugwell P,
Turner L, Valentine JC, Waddington H, Waters E, Whiting P, Higgins JPT.
*The Risk Of Bias In Non-randomized Studies — of Interventions (ROBINS-I)
assessment tool (version for cohort-type studies).* Version 1 August 2016.
Underlying paper: Sterne JAC et al., BMJ 2016;355:i4919.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Scales
# ─────────────────────────────────────────────
SIGNAL_OPTIONS_V1 = ("Y", "PY", "PN", "N", "NI")
JUDGEMENTS_V1 = ("Low", "Moderate", "Serious", "Critical", "No information")
AIMS = ("assignment_to", "starting_and_adhering")


# ─────────────────────────────────────────────
# Per-question response option subsets
# ─────────────────────────────────────────────
_BASIC = ("Y", "PY", "PN", "N", "NI")          # standard 5-token
_BASIC_NA = ("NA", "Y", "PY", "PN", "N", "NI") # gated questions add NA
_BASIC_NO_NI = ("Y", "PY", "PN", "N")          # 1.1 has no NI option


# ─────────────────────────────────────────────
# Helper predicates
# ─────────────────────────────────────────────
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN")


def _no_info(ans: str) -> bool:
    return ans == "NI"


# ─────────────────────────────────────────────
# Decision trees — conservative interpretations of Tables 1 + 2
# ─────────────────────────────────────────────
def domain1_judge_v1(signals: dict[str, str]) -> str:
    """V1 D1 — Bias due to confounding. Cribsheet Table 1 row."""
    q1_1 = signals.get("1.1", "NI")
    if q1_1 in ("N", "PN"):
        return "Low"  # cribsheet early exit
    if q1_1 == "NI":
        return "No information"

    q1_2 = signals.get("1.2", "NI")
    q1_3 = signals.get("1.3", "NI")

    if q1_2 in ("Y", "PY") and q1_3 in ("Y", "PY"):
        q1_7 = signals.get("1.7", "NI")
        q1_8 = signals.get("1.8", "NI")
        if q1_7 in ("Y", "PY") and q1_8 in ("Y", "PY"):
            return "Moderate"
        if q1_7 in ("N", "PN") or q1_8 in ("N", "PN"):
            return "Serious"
        return "No information"

    q1_4 = signals.get("1.4", "NI")
    q1_5 = signals.get("1.5", "NI")
    q1_6 = signals.get("1.6", "NI")

    if q1_6 in ("Y", "PY"):
        return "Serious"
    if q1_4 in ("N", "PN") or q1_5 in ("N", "PN"):
        return "Serious"
    if q1_4 in ("Y", "PY") and q1_5 in ("Y", "PY") and q1_6 in ("N", "PN"):
        return "Moderate"
    return "No information"


def domain2_judge_v1(signals: dict[str, str]) -> str:
    """V1 D2 — Bias in selection of participants. Cribsheet Table 1 row."""
    q2_1 = signals.get("2.1", "NI")
    q2_4 = signals.get("2.4", "NI")
    q2_2 = signals.get("2.2", "NI")
    q2_3 = signals.get("2.3", "NI")
    q2_5 = signals.get("2.5", "NI")

    if q2_1 in ("N", "PN") and q2_4 in ("Y", "PY"):
        return "Low"
    if q2_5 in ("Y", "PY"):
        return "Moderate"
    if q2_1 in ("Y", "PY") and q2_2 in ("Y", "PY") and q2_3 in ("Y", "PY"):
        return "Serious"
    if q2_4 in ("N", "PN"):
        return "Serious"
    if q2_1 == "NI" and q2_4 == "NI":
        return "No information"
    return "Moderate"


def domain3_judge_v1(signals: dict[str, str]) -> str:
    """V1 D3 — Bias in classification of interventions. Cribsheet Table 1 row."""
    q3_1 = signals.get("3.1", "NI")
    q3_2 = signals.get("3.2", "NI")
    q3_3 = signals.get("3.3", "NI")

    if q3_1 in ("Y", "PY") and q3_2 in ("Y", "PY") and q3_3 in ("N", "PN"):
        return "Low"
    if q3_1 in ("N", "PN") or q3_3 in ("Y", "PY"):
        return "Serious"
    if q3_1 == "NI":
        return "No information"
    return "Moderate"


def domain4_judge_v1(signals: dict[str, str], aim: str = "assignment_to") -> str:
    """V1 D4 — Bias due to deviations from intended interventions. Table 2 row.

    aim must be "assignment_to" (uses 4.1, 4.2) or
                "starting_and_adhering" (uses 4.3-4.6).
    """
    if aim == "assignment_to":
        q4_1 = signals.get("4.1", "NI")
        q4_2 = signals.get("4.2", "NI")
        if q4_1 in ("N", "PN"):
            return "Low"
        if q4_2 in ("N", "PN"):
            return "Low"
        if q4_2 in ("Y", "PY"):
            return "Serious"
        if q4_1 == "NI" or q4_2 == "NI":
            return "No information"
        return "Moderate"

    if aim == "starting_and_adhering":
        q4_3 = signals.get("4.3", "NI")
        q4_4 = signals.get("4.4", "NI")
        q4_5 = signals.get("4.5", "NI")
        q4_6 = signals.get("4.6", "NI")
        if (q4_3 in ("Y", "PY") and q4_4 in ("Y", "PY") and q4_5 in ("Y", "PY")):
            return "Low"
        if q4_6 in ("Y", "PY"):
            return "Moderate"
        bad = (q4_3 in ("N", "PN") or q4_4 in ("N", "PN") or q4_5 in ("N", "PN"))
        if bad and q4_6 in ("N", "PN", "NI"):
            return "Serious"
        if q4_3 == "NI" and q4_4 == "NI" and q4_5 == "NI":
            return "No information"
        return "Moderate"

    raise ValueError(f"Unknown aim: {aim}")


def domain5_judge_v1(signals: dict[str, str]) -> str:
    """V1 D5 — Bias due to missing data. Cribsheet Table 2 row."""
    q5_1 = signals.get("5.1", "NI")
    q5_2 = signals.get("5.2", "NI")
    q5_3 = signals.get("5.3", "NI")
    q5_4 = signals.get("5.4", "NI")
    q5_5 = signals.get("5.5", "NI")

    if (q5_1 in ("Y", "PY") and q5_2 in ("N", "PN") and q5_3 in ("N", "PN")):
        return "Low"

    has_missing = (q5_1 in ("N", "PN") or q5_2 in ("Y", "PY") or q5_3 in ("Y", "PY"))
    if has_missing:
        if q5_4 in ("Y", "PY") or q5_5 in ("Y", "PY"):
            return "Moderate"
        if q5_4 in ("N", "PN") or q5_5 in ("N", "PN"):
            return "Serious"
        if q5_4 == "NI" and q5_5 == "NI":
            return "No information"
        return "Moderate"

    if q5_1 == "NI" and q5_2 == "NI" and q5_3 == "NI":
        return "No information"
    return "Moderate"


def domain6_judge_v1(signals: dict[str, str]) -> str:
    """V1 D6 — Bias in measurement of outcomes. Cribsheet Table 2 row."""
    q6_1 = signals.get("6.1", "NI")
    q6_2 = signals.get("6.2", "NI")
    q6_3 = signals.get("6.3", "NI")
    q6_4 = signals.get("6.4", "NI")

    if (q6_3 in ("Y", "PY")
        and (q6_1 in ("N", "PN") or q6_2 in ("N", "PN"))
        and q6_4 in ("N", "PN")):
        return "Low"
    if q6_3 in ("N", "PN"):
        return "Serious"
    if q6_1 in ("Y", "PY") and q6_2 in ("Y", "PY"):
        return "Serious"
    if q6_4 in ("Y", "PY"):
        return "Serious"
    if q6_3 == "NI" and q6_1 == "NI" and q6_2 == "NI":
        return "No information"
    return "Moderate"


def domain7_judge_v1(signals: dict[str, str]) -> str:
    """V1 D7 — Bias in selection of the reported result. Cribsheet Table 2 row."""
    q7_1 = signals.get("7.1", "NI")
    q7_2 = signals.get("7.2", "NI")
    q7_3 = signals.get("7.3", "NI")

    yes_count = sum(1 for q in (q7_1, q7_2, q7_3) if q in ("Y", "PY"))
    ni_count = sum(1 for q in (q7_1, q7_2, q7_3) if q == "NI")

    if yes_count >= 2:
        return "Critical"
    if yes_count == 1:
        return "Serious"
    if ni_count == 3:
        return "No information"
    if ni_count >= 1:
        return "Moderate"
    return "Low"


def robins_i_v1_overall(domain_judgements: list[str]) -> str:
    """Overall risk-of-bias per V1 Table 3."""
    if not domain_judgements:
        return "No information"
    if any(j == "Critical" for j in domain_judgements):
        return "Critical"
    if any(j == "Serious" for j in domain_judgements):
        return "Serious"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    if all(j == "No information" for j in domain_judgements):
        return "No information"
    if all(j in ("Low", "Moderate") for j in domain_judgements):
        return "Moderate"
    return "No information"


DOMAIN_JUDGES_V1 = {
    1: domain1_judge_v1,
    2: domain2_judge_v1,
    3: domain3_judge_v1,
    # 4: dispatched separately because it takes aim= kwarg
    5: domain5_judge_v1,
    6: domain6_judge_v1,
    7: domain7_judge_v1,
}


# ─────────────────────────────────────────────
# Cascade enforcement — rule-based NA handling per the cribsheet's
# cascading-question structure. Called AFTER the LLM responds, before the
# decision tree runs. Overrides LLM answers for gated-out questions to NA.
#
# Why Python-side enforcement instead of asking the LLM to determine NA?
#   1. The cascade rules are deterministic from the cribsheet, not a
#      judgement call — Python is the right place.
#   2. Prevents LLM inconsistency (e.g. answering 1.3 = Y when 1.2 = N,
#      which would incorrectly route into the time-varying confounding path).
#   3. Cleanly separates concerns: LLM answers "what does the paper say?",
#      Python answers "what does the cribsheet say to ask next?".
#   4. Same paper always produces the same cascade structure regardless of
#      LLM stochasticity.
# ─────────────────────────────────────────────
def enforce_cascade_d1_v1(signals: dict[str, str]) -> dict[str, str]:
    """V1 D1 — confounding. Cascading structure (cribsheet pp 5-6).

    Gating rules:
      1.1 = N/PN/NI → 1.2-1.8 are all NA (early-exit per cribsheet, or
                       insufficient info to proceed)
      1.1 = Y/PY →
        1.2 = Y/PY AND 1.3 = Y/PY → time-varying path
          1.4, 1.5, 1.6 are NA
          1.7 always asked; 1.8 NA unless 1.7 = Y/PY
        else (1.2 N/PN/NI OR 1.3 N/PN/NI) → baseline-only path
          1.7, 1.8 are NA
          1.4, 1.6 always asked; 1.5 NA unless 1.4 = Y/PY
          1.3 NA if 1.2 not Y/PY (1.3 only asked when 1.2 Y/PY)
    """
    out = dict(signals)
    q1_1 = out.get("1.1", "NI")

    if q1_1 in ("N", "PN", "NI"):
        for sid in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"):
            out[sid] = "NA"
        return out

    # 1.1 Y/PY: continue cascade
    q1_2 = out.get("1.2", "NI")
    q1_3 = out.get("1.3", "NI")

    if q1_2 in ("Y", "PY") and q1_3 in ("Y", "PY"):
        # Time-varying path
        for sid in ("1.4", "1.5", "1.6"):
            out[sid] = "NA"
        if out.get("1.7", "NI") not in ("Y", "PY"):
            out["1.8"] = "NA"
    else:
        # Baseline-only path
        for sid in ("1.7", "1.8"):
            out[sid] = "NA"
        if out.get("1.4", "NI") not in ("Y", "PY"):
            out["1.5"] = "NA"
        # 1.3 only asked if 1.2 Y/PY
        if q1_2 not in ("Y", "PY"):
            out["1.3"] = "NA"

    return out


def enforce_cascade_d2_v1(signals: dict[str, str]) -> dict[str, str]:
    """V1 D2 — selection. Cascading structure (cribsheet p 7).

    Gating rules:
      2.1 = N/PN/NI → 2.2, 2.3 are NA (go to 2.4)
      2.1 = Y/PY →
        2.2 = Y/PY → 2.3 asked
        2.2 = N/PN/NI → 2.3 is NA
      2.5 only asked if (2.2 Y/PY AND 2.3 Y/PY) OR (2.4 N/PN); else NA
    """
    out = dict(signals)
    q2_1 = out.get("2.1", "NI")

    if q2_1 not in ("Y", "PY"):
        out["2.2"] = "NA"
        out["2.3"] = "NA"
    else:
        if out.get("2.2", "NI") not in ("Y", "PY"):
            out["2.3"] = "NA"

    cond_22_23 = (out.get("2.2", "NA") in ("Y", "PY")
                  and out.get("2.3", "NA") in ("Y", "PY"))
    cond_24 = out.get("2.4", "NI") in ("N", "PN")
    if not (cond_22_23 or cond_24):
        out["2.5"] = "NA"

    return out


def enforce_cascade_d4_v1(signals: dict[str, str], aim: str) -> dict[str, str]:
    """V1 D4 — deviations. Within-path cascading (cribsheet pp 9-10).

    The aim-gating itself (assignment_to → 4.1+4.2 only;
    starting_and_adhering → 4.3-4.6 only) is handled upstream by
    _signals_for_domain_v1. This function handles WITHIN-PATH gating:
      assignment_to:
        4.2 only asked if 4.1 = Y/PY; else NA
      starting_and_adhering:
        4.6 only asked if any of (4.3, 4.4, 4.5) = N/PN; else NA
    """
    out = dict(signals)
    if aim == "assignment_to":
        if out.get("4.1", "NI") not in ("Y", "PY"):
            out["4.2"] = "NA"
    elif aim == "starting_and_adhering":
        any_bad = any(out.get(sid, "NI") in ("N", "PN")
                      for sid in ("4.3", "4.4", "4.5"))
        if not any_bad:
            out["4.6"] = "NA"
    return out


def enforce_cascade_d5_v1(signals: dict[str, str]) -> dict[str, str]:
    """V1 D5 — missing data. Cascading structure (cribsheet p 11).

    Gating rules:
      5.4, 5.5 only asked if (5.1 PN/N) OR (5.2 Y/PY) OR (5.3 Y/PY); else NA
    """
    out = dict(signals)
    trigger = (out.get("5.1", "NI") in ("PN", "N")
               or out.get("5.2", "NI") in ("Y", "PY")
               or out.get("5.3", "NI") in ("Y", "PY"))
    if not trigger:
        out["5.4"] = "NA"
        out["5.5"] = "NA"
    return out


# Dispatch helper — used by _assess_domain_v1 after the LLM response is parsed
def enforce_cascade_v1(domain_id: int,
                       signals: dict[str, str],
                       aim: str = "assignment_to") -> dict[str, str]:
    """Apply the appropriate per-domain cascade enforcer. D3, D6, D7 have
    no cascading and return signals unchanged."""
    if domain_id == 1:
        return enforce_cascade_d1_v1(signals)
    if domain_id == 2:
        return enforce_cascade_d2_v1(signals)
    if domain_id == 4:
        return enforce_cascade_d4_v1(signals, aim=aim)
    if domain_id == 5:
        return enforce_cascade_d5_v1(signals)
    return signals  # D3, D6, D7 have no cascade


# ─────────────────────────────────────────────
# Signal definitions — verbatim from the V1 cribsheet
# ─────────────────────────────────────────────
DOMAIN1_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "1.1", "text": "Is there potential for confounding of the effect of intervention in this study?", "options": list(_BASIC_NO_NI), "elaboration": "In rare situations, such as when studying harms that are very unlikely to be related to factors that influence treatment decisions, no confounding is expected and the study can be considered to be at low risk of bias due to confounding, equivalent to a fully randomized trial. There is no NI (No information) option for this signalling question."},
    {"id": "1.2", "text": "Was the analysis based on splitting participants' follow up time according to intervention received?", "options": list(_BASIC_NA), "elaboration": "If participants could switch between intervention groups then associations between intervention and outcome may be biased by time-varying confounding. This occurs when prognostic factors influence switches between intended interventions."},
    {"id": "1.3", "text": "Were intervention discontinuations or switches likely to be related to factors that are prognostic for the outcome?", "options": list(_BASIC_NA), "elaboration": "If intervention switches are unrelated to the outcome, for example when the outcome is an unexpected harm, then time-varying confounding will not be present and only control for baseline confounding is required."},
    {"id": "1.4", "text": "Did the authors use an appropriate analysis method that controlled for all the important confounding domains?", "options": list(_BASIC_NA), "elaboration": "Appropriate methods to control for measured confounders include stratification, regression, matching, standardization, and inverse probability weighting. They may control for individual variables or for the estimated propensity score. Each method depends on the assumption that there is no unmeasured or residual confounding."},
    {"id": "1.5", "text": "If Y/PY to 1.4: Were confounding domains that were controlled for measured validly and reliably by the variables available in this study?", "options": list(_BASIC_NA), "elaboration": "Appropriate control of confounding requires that the variables adjusted for are valid and reliable measures of the confounding domains. Subjective measures (e.g. based on self-report) may have lower validity and reliability than objective measures such as lab findings."},
    {"id": "1.6", "text": "Did the authors control for any post-intervention variables that could have been affected by the intervention?", "options": list(_BASIC_NA), "elaboration": "Controlling for post-intervention variables that are affected by intervention is not appropriate. Controlling for mediating variables estimates the direct effect of intervention and may introduce bias."},
    {"id": "1.7", "text": "Did the authors use an appropriate analysis method that adjusted for all the important confounding domains and for time-varying confounding?", "options": list(_BASIC_NA), "elaboration": "Adjustment for time-varying confounding is necessary to estimate the effect of starting and adhering to intervention. Appropriate methods include those based on inverse probability weighting. Standard regression models that include time-updated confounders may be problematic if time-varying confounding is present."},
    {"id": "1.8", "text": "If Y/PY to 1.7: Were confounding domains that were adjusted for measured validly and reliably by the variables available in this study?", "options": list(_BASIC_NA), "elaboration": "Same measurement-validity question as 1.5 but applied to baseline + time-varying confounders."},
]

DOMAIN2_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "2.1", "text": "Was selection of participants into the study (or into the analysis) based on participant characteristics observed after the start of intervention?", "options": list(_BASIC), "elaboration": "This domain is concerned only with selection into the study based on participant characteristics observed after the start of intervention. Baseline confounding is addressed in Domain 1, not here."},
    {"id": "2.2", "text": "If Y/PY to 2.1: Were the post-intervention variables that influenced selection likely to be associated with intervention?", "options": list(_BASIC_NA), "elaboration": "Selection bias occurs when selection is related to an effect of either intervention or a cause of intervention AND an effect of either the outcome or a cause of the outcome."},
    {"id": "2.3", "text": "If Y/PY to 2.2: Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?", "options": list(_BASIC_NA), "elaboration": "Collider-style selection bias."},
    {"id": "2.4", "text": "Do start of follow-up and start of intervention coincide for most participants?", "options": list(_BASIC), "elaboration": "If participants are not followed from the start of the intervention then a period of follow up has been excluded, and individuals who experienced the outcome soon after intervention will be missing from analyses."},
    {"id": "2.5", "text": "If Y/PY to 2.2 and 2.3, or N/PN to 2.4: Were adjustment techniques used that are likely to correct for the presence of selection biases?", "options": list(_BASIC_NA), "elaboration": "It is in principle possible to correct for selection biases using inverse probability weights or missing-data methods, but such methods are rarely used in practice."},
]

DOMAIN3_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "3.1", "text": "Were intervention groups clearly defined?", "options": list(_BASIC), "elaboration": "A pre-requisite for an appropriate comparison of interventions is that the interventions are well defined. For individual-level interventions, criteria for considering individuals to have received each intervention should be clear and explicit, covering issues such as type, setting, dose, frequency, intensity and/or timing of intervention."},
    {"id": "3.2", "text": "Was the information used to define intervention groups recorded at the start of the intervention?", "options": list(_BASIC), "elaboration": "If information about interventions received is available from sources that could not have been affected by subsequent outcomes, then differential misclassification of intervention status is unlikely. Collection at the time of intervention makes it easier to avoid such misclassification."},
    {"id": "3.3", "text": "Could classification of intervention status have been affected by knowledge of the outcome or risk of the outcome?", "options": list(_BASIC), "elaboration": "Collection of the information at the time of the intervention may not be sufficient to avoid bias. The way in which the data are collected for the purposes of the NRSI should also avoid misclassification."},
]

DOMAIN4_SIGNALS_V1: list[dict[str, Any]] = [
    # Aim = "assignment_to" path
    {"id": "4.1", "text": "Were there deviations from the intended intervention beyond what would be expected in usual practice?", "options": list(_BASIC), "elaboration": "Deviations that happen in usual practice following the intervention (for example, cessation of a drug intervention because of acute toxicity) are part of the intended intervention and therefore do not lead to bias in the effect of assignment to intervention. Such deviations are not expected in observational studies of individuals in routine care."},
    {"id": "4.2", "text": "If Y/PY to 4.1: Were these deviations from intended intervention unbalanced between groups and likely to have affected the outcome?", "options": list(_BASIC_NA), "elaboration": "Deviations from intended interventions that do not reflect usual practice will be important if they affect the outcome, but not otherwise. Bias will arise only if there is imbalance in the deviations across the two groups."},
    # Aim = "starting_and_adhering" path
    {"id": "4.3", "text": "Were important co-interventions balanced across intervention groups?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if unplanned co-interventions were implemented in a way that would bias the estimated effect of intervention. Bias will arise only if there is imbalance in such co-interventions between the intervention groups."},
    {"id": "4.4", "text": "Was the intervention implemented successfully for most participants?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if the intervention was not implemented as intended by, for example, the health care professionals delivering care during the trial."},
    {"id": "4.5", "text": "Did study participants adhere to the assigned intervention regimen?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if participants did not adhere to the intervention as intended. Lack of adherence includes imperfect compliance, cessation of intervention, crossovers, and switches to another active intervention."},
    {"id": "4.6", "text": "If N/PN to 4.3, 4.4 or 4.5: Was an appropriate analysis used to estimate the effect of starting and adhering to the intervention?", "options": list(_BASIC_NA), "elaboration": "Examples of appropriate analysis strategies include inverse probability weighting or instrumental variable estimation. Specialist advice may be needed to assess studies that used these approaches."},
]

DOMAIN5_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "5.1", "text": "Were outcome data available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "'Nearly all' should be interpreted as 'enough to be confident of the findings'. Availability of data from 95% (or 90%) of participants may be sufficient when events are reasonably common in both intervention groups."},
    {"id": "5.2", "text": "Were participants excluded due to missing data on intervention status?", "options": list(_BASIC), "elaboration": "Missing intervention status may be a problem. This requires that the intended study sample is clear, which it may not be in practice."},
    {"id": "5.3", "text": "Were participants excluded due to missing data on other variables needed for the analysis?", "options": list(_BASIC), "elaboration": "This question relates particularly to participants excluded from the analysis because of missing information on confounders that were controlled for in the analysis."},
    {"id": "5.4", "text": "If PN/N to 5.1, or Y/PY to 5.2 or 5.3: Are the proportion of participants and reasons for missing data similar across interventions?", "options": list(_BASIC_NA), "elaboration": "This aims to elicit whether either differential proportion of missing observations or differences in reasons for missing observations could substantially impact on our ability to answer the question being addressed."},
    {"id": "5.5", "text": "If PN/N to 5.1, or Y/PY to 5.2 or 5.3: Is there evidence that results were robust to the presence of missing data?", "options": list(_BASIC_NA), "elaboration": "Evidence for robustness may come from how missing data were handled and whether sensitivity analyses were performed. Both content knowledge and statistical expertise will often be required for this judgement."},
]

DOMAIN6_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "6.1", "text": "Could the outcome measure have been influenced by knowledge of the intervention received?", "options": list(_BASIC), "elaboration": "Some outcome measures involve negligible assessor judgment, e.g. all-cause mortality or non-repeatable automated laboratory assessments. Risk of bias due to measurement of these outcomes would be expected to be low."},
    {"id": "6.2", "text": "Were outcome assessors aware of the intervention received by study participants?", "options": list(_BASIC), "elaboration": "N if outcome assessors were blinded. In studies where participants report their outcomes themselves, the outcome assessor is the study participant — in observational studies the answer will usually be 'Yes' when participants report their outcomes themselves."},
    {"id": "6.3", "text": "Were the methods of outcome assessment comparable across intervention groups?", "options": list(_BASIC), "elaboration": "Comparable assessment methods would involve the same outcome detection methods and thresholds, same time point, same definition, and same measurements."},
    {"id": "6.4", "text": "Were any systematic errors in measurement of the outcome related to intervention received?", "options": list(_BASIC), "elaboration": "This refers to differential misclassification of outcomes. Systematic errors in measuring the outcome, if present, could cause bias if they are related to intervention or to a confounder of the intervention-outcome relationship."},
]

DOMAIN7_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "7.1", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from multiple outcome measurements within the outcome domain?", "options": list(_BASIC), "elaboration": "For a specified outcome domain, it is possible to generate multiple effect estimates for different measurements. If multiple measurements were made but only one or a subset is reported, there is a risk of selective reporting on the basis of results."},
    {"id": "7.2", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from multiple analyses of the intervention-outcome relationship?", "options": list(_BASIC), "elaboration": "Examples include unadjusted vs adjusted models; final value vs change from baseline vs ANCOVA; different transformations; different covariate sets; different missing-data strategies. If the analyst does not pre-specify methods and multiple estimates are generated but only one or a subset is reported, there is a risk of selective reporting."},
    {"id": "7.3", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from different subgroups?", "options": list(_BASIC), "elaboration": "Particularly with large cohorts often available from routine data sources, it is possible to generate multiple effect estimates for different subgroups or simply to omit varying proportions of the original cohort."},
]


DOMAINS_V1: list[dict[str, Any]] = [
    {"id": 1, "name": "Bias due to confounding", "signals": DOMAIN1_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Unpredictable"), "relevant_fields": ["confounders_measured", "adjustment_method", "exposure_definition", "comparator_group", "immortal_time_bias", "confounding_control", "consecutive_enrolment"]},
    {"id": 2, "name": "Bias in selection of participants into the study", "signals": DOMAIN2_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["case_source", "control_selection", "sampling_method", "loss_to_follow_up", "immortal_time_bias"]},
    {"id": 3, "name": "Bias in classification of interventions", "signals": DOMAIN3_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["exposure_definition", "exposure_measurement", "exposure_ascertainment", "intervention_classification"]},
    {"id": 4, "name": "Bias due to deviations from intended interventions", "signals": DOMAIN4_SIGNALS_V1, "aim_gated": True, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["intervention_classification", "loss_to_follow_up", "co_interventions", "adherence"]},
    {"id": 5, "name": "Bias due to missing data", "signals": DOMAIN5_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["loss_to_follow_up", "missing_data_handling", "attrition_rate"]},
    {"id": 6, "name": "Bias in measurement of outcomes", "signals": DOMAIN6_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["outcome_ascertainment", "outcome_definition", "assessor_blinding"]},
    {"id": 7, "name": "Bias in selection of the reported result", "signals": DOMAIN7_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["outcome_definition", "statistical_analysis", "pre_registered_protocol"]},
]


# ─────────────────────────────────────────────
# Prompts + orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT_V1 = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "non-randomized study of an intervention using the Cochrane ROBINS-I tool "
    "(Version 1, 1 August 2016 cribsheet — Sterne JAC et al., BMJ 2016;355:i4919). "
    "Read the PDF carefully. Answer each signaling question with one of: "
    "Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). "
    "Some questions are gated on prior answers and additionally allow NA (not "
    "applicable). Provide a 1-2 sentence rationale for each answer, quoting "
    "the paper where possible. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)


def _signals_for_domain_v1(domain: dict[str, Any], aim: str) -> list[dict[str, Any]]:
    """For D4, return only the aim-relevant signals. For other domains,
    return all signals."""
    if domain.get("aim_gated"):
        if aim == "assignment_to":
            return [s for s in domain["signals"] if s["id"] in ("4.1", "4.2")]
        if aim == "starting_and_adhering":
            return [s for s in domain["signals"] if s["id"] in ("4.3", "4.4", "4.5", "4.6")]
    return domain["signals"]


# ─────────────────────────────────────────────
# Aim preflight (§1.1) — one LLM call, auto-determines the Stage-II aim
# ─────────────────────────────────────────────
_AIM_PREFLIGHT_RELEVANT_KEYS = (
    "analysis_framework",
    "primary_outcome_measurement",
    "outcome_definition",
    "outcome_ascertainment",
)


def _build_aim_preflight_prompt_v1(primary_outcome: str,
                                   extracted_fields: dict[str, str]) -> str:
    """Build the §1.1 aim-preflight prompt.

    Mirrors V2's `_build_preflight_prompt_cohort` structure (context block
    derived from prefilled methods/analysis fields + a single question);
    the question is V2's C4 reworded to map onto V1's AIMS output values.
    """
    relevant = {k: extracted_fields[k] for k in _AIM_PREFLIGHT_RELEVANT_KEYS
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are determining the **aim of study** for a ROBINS-I V1 risk-of-bias assessment of a non-randomized study.

Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

ROBINS-I V1 assesses risk of bias against a target estimand. The Stage-II aim of study commits to which estimand the appraisal targets:

- **"assignment_to"** — the analysis estimates the effect of *assignment to* intervention (intention-to-treat). Participants are analysed in the group they were originally assigned to; switches, crossovers, and non-adherence are ignored. This aim is set when the paper reports an as-randomized / as-assigned / mITT analysis as its primary estimate.
- **"starting_and_adhering"** — the analysis estimates the effect of *starting and adhering to* intervention (per-protocol). The analysis is restricted to (or weights toward) participants who actually started and adhered to the assigned intervention, and protocol deviations are accounted for via censoring, IPCW, g-methods, marginal structural models, or instrumental-variable estimation. This aim is set when the paper reports a per-protocol / as-treated / completer analysis as its primary estimate.

**Question.** Which aim does the primary analysis of the paper target for the outcome being assessed?

Elaboration:
- If the paper reports both an ITT and a per-protocol analysis, pick the aim that matches the **headline / primary estimate** for this outcome, not the sensitivity analysis.
- Observational cohort studies typically map to **"starting_and_adhering"** because exposure is defined by who actually started the treatment — unless the analysis explicitly uses an ITT-like exposure definition (e.g. first prescription regardless of refill).
- If the analysis section is genuinely silent on whether protocol deviations are accounted for, default to **"assignment_to"** and note the ambiguity in the rationale.

Return JSON with exactly this shape:
{{
  "aim": "assignment_to|starting_and_adhering",
  "rationale": "1-2 sentences quoting or paraphrasing the analysis-section text that supports the choice"
}}"""


def determine_aim_v1(pdf_bytes: bytes,
                     primary_outcome: str,
                     extracted_fields: dict[str, str],
                     llm_call: Callable[[bytes, str, int], dict[str, Any]],
                     ) -> tuple[str, str]:
    """V1 aim preflight — single LLM call returns (aim, rationale).

    aim ∈ AIMS = ("assignment_to", "starting_and_adhering").
    Mechanically equivalent to V2's C4 question; only the output mapping differs.
    Falls back to "assignment_to" when the LLM returns an unrecognized value
    (matches the cribsheet's ambiguous-methods guidance).
    """
    prompt = _build_aim_preflight_prompt_v1(primary_outcome, extracted_fields)
    raw = llm_call(pdf_bytes, prompt, 512)
    aim_raw = str(raw.get("aim", "")).strip().lower()
    if aim_raw not in AIMS:
        logger.warning("ROBINS-I V1 aim preflight: invalid LLM answer %r — defaulting to 'assignment_to'", aim_raw)
        aim_raw = "assignment_to"
    rationale = str(raw.get("rationale", "")).strip()
    return aim_raw, rationale


def build_domain_prompt_v1(domain: dict[str, Any],
                           study_type: str,
                           primary_outcome: str,
                           extracted_fields: dict[str, str],
                           aim: str = "assignment_to",
                           target_pico: dict[str, str] | None = None) -> str:
    """Per-domain prompt for ROBINS-I V1."""
    signals = _signals_for_domain_v1(domain, aim)

    relevant = {k: extracted_fields[k] for k in domain.get("relevant_fields", [])
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    aim_block = ""
    if domain.get("aim_gated"):
        aim_block = (
            f"\nAim of study: {aim}\n"
            '- "assignment_to" → answer signaling questions 4.1 and 4.2 only.\n'
            '- "starting_and_adhering" → answer signaling questions 4.3 through 4.6 only.\n'
        )

    pico_block = ""
    if target_pico:
        pico_block = "\nTarget PICO (user-supplied):\n" + json.dumps(target_pico, indent=2) + "\n"

    q_lines = []
    for sig in signals:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: {'/'.join(sig['options'])}."
        )
    questions_block = "\n".join(q_lines)

    shape = "{\n"
    for sig in signals:
        opt_string = "|".join(sig["options"])
        shape += f'  "{sig["id"]}": "{opt_string}",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    direction_options = domain.get("direction_options", ("NA",))
    shape += f'  "direction_of_bias": "{"|".join(direction_options)}"\n'
    shape += "}"

    return f"""Assess **Domain {domain['id']} — {domain['name']}** for the study described in the attached PDF using the ROBINS-I V1 tool (1 August 2016 cribsheet).

Study type: {study_type}
Outcome being assessed: {primary_outcome}
{aim_block}{pico_block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V1:
- The signal vocabulary is **Y / PY / PN / N / NI** (5 tokens). For each question, answer based on what the paper says about that specific question — **do NOT try to determine whether a question is gated out by the cribsheet's cascading structure**. Python applies the cascade rules after you answer and will set `NA` for any question that should be gated out. Just answer each question independently based on its own text.
- The judgement scale is **Low / Moderate / Serious / Critical / No information** (5 levels). The code maps your signal answers to a judgement — answer the signaling questions only.
- Answer N (or PN) when the paper gives enough information to rule out the problem; NI only when the paper is silent.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain_v1(pdf_bytes: bytes, domain: dict[str, Any],
                      study_type: str, primary_outcome: str,
                      extracted_fields: dict[str, str],
                      llm_call: Callable[[bytes, str, int], dict[str, Any]],
                      aim: str = "assignment_to",
                      target_pico: dict[str, str] | None = None) -> dict[str, Any]:
    prompt = build_domain_prompt_v1(
        domain, study_type, primary_outcome, extracted_fields, aim, target_pico)
    raw = llm_call(pdf_bytes, prompt, 8192)

    signals_for_this = _signals_for_domain_v1(domain, aim)
    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in signals_for_this:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        allowed = set(sig["options"])
        if ans not in allowed:
            logger.warning("ROBINS-I V1 domain %s question %s: invalid answer %r — defaulting to NI",
                           domain["id"], sid, ans)
            ans = "NI" if "NI" in allowed else next(iter(allowed))
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    # Python-side cascade enforcement: override LLM answers to NA for
    # questions gated out by the cribsheet's cascading structure.
    # See §17.6 and the enforce_cascade_*_v1 docstrings for the rules.
    pre_cascade = dict(signals)
    signals = enforce_cascade_v1(domain["id"], signals, aim=aim)
    overrides = {sid: (pre_cascade[sid], signals[sid])
                 for sid in signals
                 if sid in pre_cascade and pre_cascade[sid] != signals[sid]}
    if overrides:
        logger.debug("ROBINS-I V1 D%s cascade enforcement overrode LLM answers: %r",
                     domain["id"], overrides)

    if domain["id"] == 4:
        judgement = domain4_judge_v1(signals, aim=aim)
    else:
        judgement = DOMAIN_JUDGES_V1[domain["id"]](signals)

    direction = str(raw.get("direction_of_bias", "NA")).strip() or "NA"

    out: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
        "direction": direction,
    }
    if domain.get("aim_gated"):
        out["aim"] = aim
    return out


def run_v1(pdf_bytes: bytes,
           extracted_fields: dict[str, str],
           classification: dict[str, str],
           primary_outcome: str,
           *,
           llm_call: Callable[[bytes, str, int], dict[str, Any]],
           aim: str | None = None,
           target_pico: dict[str, str] | None = None,
           progress: Callable[[int], None] | None = None,
           ) -> tuple[dict[str, Any], str, str, str | None]:
    """Run ROBINS-I V1 against a non-randomized study.

    Returns (domain_results, overall_judgement, overall_direction, aim_rationale).

    `aim`:
        - `None` (default) → auto-determine via the §1.1 aim preflight (one
          LLM call). The chosen aim is recorded on the D4 result; the
          rationale is returned as the 4th tuple element.
        - `"assignment_to"` → uses D4 questions 4.1+4.2 (ITT estimand).
          Manual Stage-II value; preflight is skipped; aim_rationale is None.
        - `"starting_and_adhering"` → uses D4 questions 4.3-4.6 (per-protocol
          estimand). Manual Stage-II value; preflight is skipped;
          aim_rationale is None.

    V1's original cribsheet has no preflight stage; this implementation adds
    the optional aim-preflight LLM call (§1.1) so the Stage-II aim can be
    auto-determined from the paper. All 7 domains are still assessed
    unconditionally — the preflight only chooses which D4 question subset
    to ask.
    """
    if aim is None:
        aim, aim_rationale = determine_aim_v1(
            pdf_bytes, primary_outcome, extracted_fields, llm_call)
    else:
        if aim not in AIMS:
            raise ValueError(f"aim must be None or one of {AIMS}; got {aim!r}")
        aim_rationale = None
    study_type = classification.get("study_type", "Cohort Study")

    domain_results: dict[str, Any] = {}
    for domain in DOMAINS_V1:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain_v1(pdf_bytes, domain, study_type,
                                   primary_outcome, extracted_fields,
                                   llm_call=llm_call, aim=aim,
                                   target_pico=target_pico)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    domain_judgements = [domain_results[str(d["id"])]["judgement"] for d in DOMAINS_V1]
    overall = robins_i_v1_overall(domain_judgements)

    dirs = [domain_results[str(d["id"])]["direction"]
            for d in DOMAINS_V1
            if domain_results[str(d["id"])]["direction"] not in ("", "NA")]
    if not dirs:
        overall_direction = "NA"
    else:
        counts = Counter(dirs).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            overall_direction = "Unpredictable"
        else:
            overall_direction = counts[0][0]

    return domain_results, overall, overall_direction, aim_rationale
```

**From that document's §16. Quick test sketches:**

```python
# ─────────────────────────────────────────────
# Domain 1 — confounding (cascading)
# ─────────────────────────────────────────────
assert domain1_judge_v1({"1.1": "N"}) == "Low"
assert domain1_judge_v1({"1.1": "PN"}) == "Low"
assert domain1_judge_v1({"1.1": "NI"}) == "No information"
# Baseline-only, all clean → Moderate
assert domain1_judge_v1({"1.1": "Y", "1.2": "N", "1.3": "N",
                         "1.4": "Y", "1.5": "Y", "1.6": "N"}) == "Moderate"
# 1.4 N → Serious
assert domain1_judge_v1({"1.1": "Y", "1.2": "N", "1.3": "N",
                         "1.4": "N", "1.5": "Y", "1.6": "N"}) == "Serious"
# 1.6 Y (over-adjustment) → Serious
assert domain1_judge_v1({"1.1": "Y", "1.2": "N", "1.3": "N",
                         "1.4": "Y", "1.5": "Y", "1.6": "Y"}) == "Serious"
# Time-varying path, all clean → Moderate
assert domain1_judge_v1({"1.1": "Y", "1.2": "Y", "1.3": "Y",
                         "1.7": "Y", "1.8": "Y"}) == "Moderate"
# Time-varying, 1.7 N → Serious
assert domain1_judge_v1({"1.1": "Y", "1.2": "Y", "1.3": "Y",
                         "1.7": "N", "1.8": "Y"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 2 — selection
# ─────────────────────────────────────────────
assert domain2_judge_v1({"2.1": "N", "2.4": "Y"}) == "Low"
assert domain2_judge_v1({"2.1": "Y", "2.2": "Y", "2.3": "Y",
                         "2.4": "Y", "2.5": "Y"}) == "Moderate"
assert domain2_judge_v1({"2.1": "Y", "2.2": "Y", "2.3": "Y",
                         "2.4": "Y", "2.5": "N"}) == "Serious"
assert domain2_judge_v1({"2.1": "N", "2.4": "N", "2.5": "N"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 3 — classification
# ─────────────────────────────────────────────
assert domain3_judge_v1({"3.1": "Y", "3.2": "Y", "3.3": "N"}) == "Low"
assert domain3_judge_v1({"3.1": "N", "3.2": "Y", "3.3": "N"}) == "Serious"
assert domain3_judge_v1({"3.1": "Y", "3.2": "Y", "3.3": "Y"}) == "Serious"
assert domain3_judge_v1({"3.1": "Y", "3.2": "N", "3.3": "N"}) == "Moderate"
assert domain3_judge_v1({"3.1": "NI"}) == "No information"

# ─────────────────────────────────────────────
# Domain 4 — deviations (aim-gated)
# ─────────────────────────────────────────────
assert domain4_judge_v1({"4.1": "N"}, aim="assignment_to") == "Low"
assert domain4_judge_v1({"4.1": "Y", "4.2": "N"}, aim="assignment_to") == "Low"
assert domain4_judge_v1({"4.1": "Y", "4.2": "Y"}, aim="assignment_to") == "Serious"
assert domain4_judge_v1({"4.3": "Y", "4.4": "Y", "4.5": "Y"},
                        aim="starting_and_adhering") == "Low"
assert domain4_judge_v1({"4.3": "N", "4.4": "Y", "4.5": "Y", "4.6": "Y"},
                        aim="starting_and_adhering") == "Moderate"
assert domain4_judge_v1({"4.3": "N", "4.4": "Y", "4.5": "Y", "4.6": "N"},
                        aim="starting_and_adhering") == "Serious"
try:
    domain4_judge_v1({}, aim="bogus")
    assert False, "should have raised"
except ValueError:
    pass

# ─────────────────────────────────────────────
# §1.1 Aim preflight — auto-determination of the Stage-II aim
# ─────────────────────────────────────────────
def _mock_llm_itt(pdf_bytes, prompt, max_tokens):
    return {"aim": "assignment_to",
            "rationale": "Methods: intention-to-treat analysis; all participants analysed in originally assigned group."}

def _mock_llm_pp(pdf_bytes, prompt, max_tokens):
    return {"aim": "starting_and_adhering",
            "rationale": "Methods: per-protocol with inverse probability of censoring weights for treatment discontinuation."}

def _mock_llm_garbage(pdf_bytes, prompt, max_tokens):
    return {"aim": "Maybe?", "rationale": ""}

# ITT-like analysis → assignment_to
aim, rat = determine_aim_v1(b"", "all-cause mortality at 12 months",
                            {"analysis_framework": "ITT; participants analysed as originally assigned"},
                            llm_call=_mock_llm_itt)
assert aim == "assignment_to"
assert "intention-to-treat" in rat.lower()

# Per-protocol-like analysis (with IPCW) → starting_and_adhering
aim, rat = determine_aim_v1(b"", "all-cause mortality at 12 months",
                            {"analysis_framework": "Per-protocol with IPCW for treatment discontinuation"},
                            llm_call=_mock_llm_pp)
assert aim == "starting_and_adhering"
assert "ipcw" in rat.lower() or "per-protocol" in rat.lower()

# Garbage LLM answer → safe default per cribsheet ambiguity guidance
aim, rat = determine_aim_v1(b"", "any outcome", {}, llm_call=_mock_llm_garbage)
assert aim == "assignment_to"

# Empty extracted_fields → prompt builder still produces a valid prompt
prompt = _build_aim_preflight_prompt_v1("all-cause mortality at 12 months", {})
assert "Outcome being assessed: all-cause mortality at 12 months" in prompt
assert "(no pre-extracted fields)" in prompt
assert '"aim": "assignment_to|starting_and_adhering"' in prompt

# ─────────────────────────────────────────────
# Domain 5 — missing data
# ─────────────────────────────────────────────
assert domain5_judge_v1({"5.1": "Y", "5.2": "N", "5.3": "N"}) == "Low"
assert domain5_judge_v1({"5.1": "N", "5.2": "N", "5.3": "N",
                         "5.4": "Y", "5.5": "NI"}) == "Moderate"
assert domain5_judge_v1({"5.1": "N", "5.2": "N", "5.3": "N",
                         "5.4": "N", "5.5": "N"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 6 — measurement
# ─────────────────────────────────────────────
assert domain6_judge_v1({"6.1": "N", "6.2": "Y", "6.3": "Y", "6.4": "N"}) == "Low"
assert domain6_judge_v1({"6.1": "N", "6.2": "Y", "6.3": "N", "6.4": "N"}) == "Serious"
assert domain6_judge_v1({"6.1": "Y", "6.2": "Y", "6.3": "Y", "6.4": "N"}) == "Serious"
assert domain6_judge_v1({"6.1": "N", "6.2": "N", "6.3": "Y", "6.4": "Y"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 7 — selective reporting
# ─────────────────────────────────────────────
assert domain7_judge_v1({"7.1": "N", "7.2": "N", "7.3": "N"}) == "Low"
assert domain7_judge_v1({"7.1": "Y", "7.2": "N", "7.3": "N"}) == "Serious"
assert domain7_judge_v1({"7.1": "Y", "7.2": "Y", "7.3": "N"}) == "Critical"
assert domain7_judge_v1({"7.1": "NI", "7.2": "NI", "7.3": "NI"}) == "No information"

# ─────────────────────────────────────────────
# Overall aggregation (Table 3)
# ─────────────────────────────────────────────
assert robins_i_v1_overall(["Low"] * 7) == "Low"
assert robins_i_v1_overall(["Low", "Moderate", "Low", "Low", "Moderate", "Low", "Low"]) == "Moderate"
assert robins_i_v1_overall(["Low", "Serious", "Low", "Low", "Low", "Low", "Low"]) == "Serious"
assert robins_i_v1_overall(["Low", "Serious", "Critical", "Low", "Low", "Low", "Low"]) == "Critical"
assert robins_i_v1_overall(["No information"] * 7) == "No information"
assert robins_i_v1_overall(["Low", "Moderate", "No information", "Low", "Low", "Low", "Low"]) == "No information"
assert robins_i_v1_overall([]) == "No information"

# ─────────────────────────────────────────────
# Cascade enforcement (Python-side NA gating)
# ─────────────────────────────────────────────
# D1 — 1.1 N/PN → all downstream NA (early exit)
out = enforce_cascade_d1_v1({"1.1": "N", "1.2": "Y", "1.3": "Y",
                             "1.4": "Y", "1.5": "Y", "1.6": "N",
                             "1.7": "Y", "1.8": "Y"})
assert out["1.1"] == "N"
for sid in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"):
    assert out[sid] == "NA", f"D1 early-exit failed for {sid}"

# D1 — 1.1 NI → all downstream NA
out = enforce_cascade_d1_v1({"1.1": "NI"})
for sid in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"):
    assert out[sid] == "NA"

# D1 — time-varying path: 1.1 Y, 1.2 Y, 1.3 Y → 1.4-1.6 NA, 1.7/1.8 kept
out = enforce_cascade_d1_v1({"1.1": "Y", "1.2": "Y", "1.3": "Y",
                             "1.4": "Y", "1.5": "Y", "1.6": "N",
                             "1.7": "Y", "1.8": "PY"})
for sid in ("1.4", "1.5", "1.6"):
    assert out[sid] == "NA", f"D1 time-varying path didn't NA {sid}"
assert out["1.7"] == "Y"
assert out["1.8"] == "PY"

# D1 — baseline-only path: 1.1 Y, 1.2 N → 1.3 NA (1.2 not Y/PY), 1.7/1.8 NA
out = enforce_cascade_d1_v1({"1.1": "Y", "1.2": "N", "1.3": "Y",
                             "1.4": "Y", "1.5": "Y", "1.6": "N",
                             "1.7": "Y", "1.8": "Y"})
assert out["1.3"] == "NA", "1.3 should be NA when 1.2 not Y/PY"
assert out["1.7"] == "NA"
assert out["1.8"] == "NA"
assert out["1.4"] == "Y"  # baseline-only path keeps 1.4-1.6
assert out["1.6"] == "N"

# D1 — baseline-only, 1.4 N → 1.5 NA (1.5 only asked if 1.4 Y/PY)
out = enforce_cascade_d1_v1({"1.1": "Y", "1.2": "N", "1.3": "N",
                             "1.4": "N", "1.5": "Y", "1.6": "N"})
assert out["1.5"] == "NA"
assert out["1.4"] == "N"

# D1 — time-varying, 1.7 N → 1.8 NA
out = enforce_cascade_d1_v1({"1.1": "Y", "1.2": "Y", "1.3": "Y",
                             "1.7": "N", "1.8": "Y"})
assert out["1.8"] == "NA"

# D2 — 2.1 N → 2.2/2.3 NA
out = enforce_cascade_d2_v1({"2.1": "N", "2.2": "Y", "2.3": "Y",
                             "2.4": "Y", "2.5": "Y"})
assert out["2.2"] == "NA"
assert out["2.3"] == "NA"
assert out["2.5"] == "NA"  # neither (2.2+2.3 Y/PY) nor (2.4 N/PN)

# D2 — 2.1 Y, 2.2 N → 2.3 NA
out = enforce_cascade_d2_v1({"2.1": "Y", "2.2": "N", "2.3": "Y",
                             "2.4": "Y", "2.5": "Y"})
assert out["2.3"] == "NA"

# D2 — 2.5 kept when 2.4 N/PN
out = enforce_cascade_d2_v1({"2.1": "N", "2.2": "Y", "2.3": "Y",
                             "2.4": "N", "2.5": "Y"})
assert out["2.5"] == "Y"  # 2.4 N triggers 2.5

# D4 — assignment_to: 4.2 NA when 4.1 N
out = enforce_cascade_d4_v1({"4.1": "N", "4.2": "Y"}, aim="assignment_to")
assert out["4.2"] == "NA"

# D4 — assignment_to: 4.2 kept when 4.1 Y
out = enforce_cascade_d4_v1({"4.1": "Y", "4.2": "Y"}, aim="assignment_to")
assert out["4.2"] == "Y"

# D4 — starting_and_adhering: 4.6 NA when 4.3/4.4/4.5 all Y/PY (no rescue needed)
out = enforce_cascade_d4_v1({"4.3": "Y", "4.4": "Y", "4.5": "Y", "4.6": "Y"},
                            aim="starting_and_adhering")
assert out["4.6"] == "NA"

# D4 — starting_and_adhering: 4.6 kept when any of 4.3/4.4/4.5 N/PN
out = enforce_cascade_d4_v1({"4.3": "N", "4.4": "Y", "4.5": "Y", "4.6": "Y"},
                            aim="starting_and_adhering")
assert out["4.6"] == "Y"

# D5 — 5.4/5.5 NA when no missingness trigger
out = enforce_cascade_d5_v1({"5.1": "Y", "5.2": "N", "5.3": "N",
                             "5.4": "Y", "5.5": "Y"})
assert out["5.4"] == "NA"
assert out["5.5"] == "NA"

# D5 — 5.4/5.5 kept when 5.1 N (missingness triggered)
out = enforce_cascade_d5_v1({"5.1": "N", "5.2": "N", "5.3": "N",
                             "5.4": "Y", "5.5": "Y"})
assert out["5.4"] == "Y"
assert out["5.5"] == "Y"

# Dispatch helper — D3/D6/D7 unchanged
assert enforce_cascade_v1(3, {"3.1": "Y"}) == {"3.1": "Y"}
assert enforce_cascade_v1(6, {"6.1": "N"}) == {"6.1": "N"}
assert enforce_cascade_v1(7, {"7.1": "N"}) == {"7.1": "N"}

# Integration: an LLM that wrongly answered a gated question is caught.
# Imagine the LLM said 1.3 = "Y" even though 1.2 = "N" (which gates 1.3 out).
# Without cascade enforcement, the tree would route into time-varying path
# and look for 1.7/1.8 → No information. WITH enforcement, 1.3 → NA, and
# the tree correctly routes baseline-only.
llm_response = {"1.1": "Y", "1.2": "N", "1.3": "Y",  # 1.3 inconsistent!
                "1.4": "Y", "1.5": "Y", "1.6": "N"}
enforced = enforce_cascade_d1_v1(llm_response)
assert enforced["1.3"] == "NA"
assert domain1_judge_v1(enforced) == "Moderate"  # baseline-only path

# ─────────────────────────────────────────────
# DOMAINS_V1 structural invariants
# ─────────────────────────────────────────────
assert len(DOMAINS_V1) == 7
assert [d["id"] for d in DOMAINS_V1] == [1, 2, 3, 4, 5, 6, 7]
assert len(DOMAIN1_SIGNALS_V1) == 8
assert len(DOMAIN2_SIGNALS_V1) == 5
assert len(DOMAIN3_SIGNALS_V1) == 3
assert len(DOMAIN4_SIGNALS_V1) == 6
assert len(DOMAIN5_SIGNALS_V1) == 5
assert len(DOMAIN6_SIGNALS_V1) == 4
assert len(DOMAIN7_SIGNALS_V1) == 3
assert sum(len(d["signals"]) for d in DOMAINS_V1) == 34
assert "NI" not in DOMAIN1_SIGNALS_V1[0]["options"]
assert DOMAINS_V1[3].get("aim_gated") is True
assert sum(1 for d in DOMAINS_V1 if d.get("aim_gated")) == 1
assert len(DOMAINS_V1[0]["direction_options"]) == 4  # D1 special: 3 + NA
for d in DOMAINS_V1[1:]:
    assert len(d["direction_options"]) == 6

print("All ROBINS-I V1 sanity checks passed.")
```

**From that document's §19. Single-arm adaptation (project-specific extension):**

```python
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})

def run(pdf_bytes, extracted_fields, classification, primary_outcome, ...):
    study_type = classification.get("study_type", "Cohort Study")
    is_single_arm = study_type in SINGLE_ARM_STUDY_TYPES
    if is_single_arm:
        # benchmark preflight (§19.2) + SA domain assessment (§19.3-19.5)
        ...
    else:
        # standard V1 cohort path: aim preflight (§1.1) + 7-domain assessment
        ...
```

```python
def domain1_single_arm_judge(signals: dict[str, str]) -> str:
    q1 = signals.get("1S.1", "NI")
    q2 = signals.get("1S.2", "NI")
    q3 = signals.get("1S.3", "NI")
    q4 = signals.get("1S.4", "NI")
    q5 = signals.get("1S.5", "NI")

    # 1S.5 dominates: falsification-control hit → Critical regardless
    if _yes(q5):
        return "Critical"

    # 1S.1 N/PN: no pre-specified benchmark
    if _no(q1):
        # 1S.4 N/PN: no quantitative adjustment either → Critical
        if _no(q4):
            return "Critical"
        return "Serious"

    if _no_info(q1):
        return "No information"

    # 1S.1 Y/PY: benchmark pre-specified
    if _yes(q1):
        # 1S.3 prognostic comparability
        if _yes(q3):
            # 1S.2 (benchmark reasonable) decides Low-SA vs Moderate
            if _yes(q2):
                return LOW_D1_SA  # "Low (except for concerns about uncontrolled benchmarking)"
            return "Moderate"
        # V1 collapse: PN → V2-WN-equivalent (Moderate floor)
        if q3 == "PN":
            return "Moderate"
        # V1 collapse: N → V2-SN-equivalent (substantial mismatch).
        # NI on 1S.3 — silent on prognostic comparability — treated the same.
        if q3 == "N" or _no_info(q3):
            # 1S.4 (quantitative adjustment) can rescue
            if _yes(q4):
                return "Moderate"
            return "Serious"

    return "Serious"
```

```python
def domain2_single_arm_judge(signals: dict[str, str]) -> str:
    q1 = signals.get("2S.1", "NI")
    q2 = signals.get("2S.2", "NI")
    q3 = signals.get("2S.3", "NI")

    # 2S.3 dominates: cohort-definition selection bias
    if q3 == "Y":
        # V2-SY-equivalent — primary analysis explicitly restricted to
        # completers/responders → Critical
        return "Critical"
    if q3 == "PY" or _no_info(q3):
        # V2-WY-equivalent or unclear → Serious
        return "Serious"

    # q3 in (PN, N): cohort defined by intended treatment → low concern here
    if _yes(q1):
        # Well-defined intervention. 2S.2 (recording fidelity) decides.
        if _yes(q2):
            return "Low"
        if q2 == "PN":
            return "Moderate"
        if q2 == "N":
            return "Serious"
        # NI on 2S.2 — measurement-fidelity uncertain
        return "Moderate"

    # 2S.1 N/PN: intervention definition unclear
    if _no(q1):
        return "Serious"
    # NI on 2S.1
    return "No information"
```

```python
domain_results["4"] = {
    "id": 4,
    "name": "Bias due to deviations from intended interventions",
    "signals": {},
    "rationales": {},
    "judgement": "NA",
    "direction": "NA",
    "reason": (
        "Not applicable to single-arm trials — V2 retired this domain "
        "entirely; intent-vs-received cohort definition is assessed in "
        "Domain 2-SA (question 2S.3)."
    ),
}
```

```python
# ─────────────────────────────────────────────
# Single-arm constants
# ─────────────────────────────────────────────
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})

LOW_D1_SA = "Low (except for concerns about uncontrolled benchmarking)"


# ─────────────────────────────────────────────
# Single-arm signal sets (port of V2-SA, collapsed to V1's 5-token vocab)
# ─────────────────────────────────────────────
DOMAIN1_SIGNALS_SA = [
    # 1S.1, 1S.2, 1S.3, 1S.4, 1S.5 — full text in §19.3 above
]

DOMAIN2_SIGNALS_SA = [
    # 2S.1, 2S.2, 2S.3 — full text in §19.4 above
]


# ─────────────────────────────────────────────
# Single-arm decision trees — code in §19.3 and §19.4 above
# ─────────────────────────────────────────────
# def domain1_single_arm_judge(signals): ...
# def domain2_single_arm_judge(signals): ...


# ─────────────────────────────────────────────
# Single-arm judge dispatch
# ─────────────────────────────────────────────
DOMAIN_JUDGES_SA = {
    1: domain1_single_arm_judge,
    2: domain2_single_arm_judge,
    3: domain3_judge,  # reused
    5: domain5_judge,  # reused
    6: domain6_judge,  # reused
    7: domain7_judge,  # reused
    # 4: not present — set to NA in run() with no LLM call
}


# ─────────────────────────────────────────────
# Benchmark preflight prompt (§19.2)
# ─────────────────────────────────────────────
def build_benchmark_preflight_prompt(study_type, primary_outcome, extracted_fields):
    relevant_keys = (
        "primary_endpoint_prespecified", "inclusion_exclusion_criteria",
        "comparator_historical_reference", "consecutive_enrolment",
        "outcome_definition", "outcome_ascertainment",
        "primary_outcome_measurement", "analysis_framework",
    )
    relevant = {k: extracted_fields[k] for k in relevant_keys if extracted_fields.get(k)}
    import json
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"
    return f\"\"\"You are performing the **Preliminary Considerations** screen of ROBINS-I V1 (adapted for single-arm / uncontrolled designs) on an uncontrolled clinical study.

Study type: {{study_type}}
Outcome being assessed: {{primary_outcome}}

Context (fields already extracted from the paper):
{{ctx_json}}

[... full prompt body — see §19.2 of robins_i_v1_shareable.md (single-arm benchmark preflight prompt) ...]

Return JSON with exactly this shape:
{{{{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences (B1-SA)",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences"
}}}}\"\"\"


# ─────────────────────────────────────────────
# Single-arm run dispatch (extends the cohort run() in §15)
# ─────────────────────────────────────────────
def run_single_arm(pdf_bytes, extracted_fields, classification, primary_outcome,
                   llm_call_with_pdf):
    \"\"\"Single-arm path. Called by run() when study_type ∈ SINGLE_ARM_STUDY_TYPES.\"\"\"
    study_type = classification.get("study_type", "Single-Arm Trial")

    # Stage 1 — benchmark preflight
    preflight = run_benchmark_preflight(
        pdf_bytes, study_type, primary_outcome, extracted_fields,
        llm_call_with_pdf=llm_call_with_pdf,
    )
    domain_results = {"preflight": preflight, "aim_preflight": None}

    # Stage 2 — screening short-circuit
    if preflight["screening_decision"] == "critical":
        return domain_results, "Critical", "Unpredictable"

    # Stage 3 — 6 active domains; D4 set to NA in code
    active_domains = (
        # D1-SA with DOMAIN1_SIGNALS_SA
        # D2-SA with DOMAIN2_SIGNALS_SA
        # D3, D5, D6, D7 reused unchanged
    )
    for domain in active_domains:
        result = _assess_domain_sa(pdf_bytes, domain, ..., llm_call_with_pdf)
        domain_results[str(domain["id"])] = result

    # D4 = NA — no LLM call
    domain_results["4"] = {
        "id": 4, "name": "Bias due to deviations from intended interventions",
        "signals": {}, "rationales": {}, "judgement": "NA", "direction": "NA",
        "reason": "Not applicable to single-arm trials — V2 retired this "
                  "domain entirely; intent-vs-received is in D2-SA's 2S.3.",
    }

    judgements = [domain_results[str(d["id"])]["judgement"] for d in active_domains]
    overall = robins_i_v1_overall(judgements)  # excludes NA from aggregation
    return domain_results, overall, "NA"


def run(pdf_bytes, extracted_fields, classification, primary_outcome,
        llm_call_with_pdf, aim=None):
    study_type = classification.get("study_type", "Cohort Study")
    if study_type in SINGLE_ARM_STUDY_TYPES:
        return run_single_arm(pdf_bytes, extracted_fields, classification,
                              primary_outcome, llm_call_with_pdf)
    # ... existing cohort path (§15) ...
```

```python
def test_v1_sa_d1_pre_specified_low():
    # Best case: benchmark pre-specified + reasonable + prognostic match
    assert domain1_single_arm_judge({
        "1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "Y", "1S.5": "N",
    }) == LOW_D1_SA

def test_v1_sa_d1_no_benchmark_no_adjustment_critical():
    # No pre-specified benchmark AND no quantitative adjustment → Critical
    assert domain1_single_arm_judge({
        "1S.1": "N", "1S.2": "NI", "1S.3": "NI", "1S.4": "N", "1S.5": "N",
    }) == "Critical"

def test_v1_sa_d1_falsification_hit_dominates():
    # 1S.5 falsification hit → Critical regardless of other signals
    assert domain1_single_arm_judge({
        "1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "Y", "1S.5": "Y",
    }) == "Critical"

def test_v1_sa_d2_completers_only_critical():
    # 2S.3 = Y (V2-SY-equivalent: restricted to completers) → Critical
    assert domain2_single_arm_judge({
        "2S.1": "Y", "2S.2": "Y", "2S.3": "Y",
    }) == "Critical"

def test_v1_sa_d2_partial_filter_serious():
    # 2S.3 = PY (V2-WY-equivalent: some treatment-related exclusions) → Serious
    assert domain2_single_arm_judge({
        "2S.1": "Y", "2S.2": "Y", "2S.3": "PY",
    }) == "Serious"

def test_v1_sa_d2_itt_well_defined_low():
    # 2S.3 = N (ITT-like), well-defined intervention, recording fidelity → Low
    assert domain2_single_arm_judge({
        "2S.1": "Y", "2S.2": "Y", "2S.3": "N",
    }) == "Low"

def test_v1_sa_overall_excludes_d4_na():
    # D4 = NA must not block "Low" overall judgement
    assert robins_i_v1_overall(
        [LOW_D1_SA, "Low", "Low", "NA", "Low", "Low", "Low"]
    ) == "Low"
```

### 12.6 QUADAS-2 — from `quadas2_shareable.md`

**From that document's §7. Reference implementation — single self-contained Python module:**

```python
llm_call(pdf_bytes: bytes, prompt: str, max_tokens: int) -> dict
```

```python
"""QUADAS-2 (2011) — Risk of bias + applicability for diagnostic test
accuracy studies. Single-file reference implementation.

Source: Whiting PF, Rutjes AWS, Westwood ME, Mallett S, Deeks JJ, Reitsma JB,
Leeflang MMG, Sterne JAC, Bossuyt PMM, and the QUADAS-2 Group.
"QUADAS-2: A Revised Tool for the Quality Assessment of Diagnostic Accuracy
Studies." Ann Intern Med. 2011;155:529-536.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Scales
# ─────────────────────────────────────────────
SIGNAL_OPTIONS = ("Y", "N", "U")
JUDGEMENTS = ("Low", "High", "Unclear")
APPLICABILITY_OPTIONS = ("Low", "High", "Unclear")


# ─────────────────────────────────────────────
# Decision trees (pure Python — no LLM)
# ─────────────────────────────────────────────
def _yes(ans: str) -> bool:
    return ans == "Y"


def _no(ans: str) -> bool:
    return ans == "N"


def quadas2_domain_judge(signals: dict[str, str]) -> str:
    """Map signaling-question answers (Y/N/U) to a domain-level RoB
    judgement (Low / High / Unclear) per the QUADAS-2 Phase 4 narrative.

    Rule (conservative):
      - All signals Y → "Low"
      - Any N → "High"
      - Any U without N → "Unclear"
      - Empty / all-U → "Unclear"
    """
    answered = [v for v in signals.values()]
    if not answered:
        return "Unclear"
    if any(_no(v) for v in answered):
        return "High"
    if all(_yes(v) for v in answered):
        return "Low"
    return "Unclear"


def quadas2_overall(domain_judgements: list[str]) -> str:
    """Aggregate per the QUADAS-2 paper ("Incorporating Assessments" section).

    - Any domain High → "High"
    - All domains Low → "Low"
    - Otherwise (any Unclear, none High) → "Unclear"
    """
    if not domain_judgements:
        return "Unclear"
    if any(j == "High" for j in domain_judgements):
        return "High"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    return "Unclear"


def quadas2_applicability_overall(judgements: list[str]) -> str:
    """Aggregate applicability judgements (same rule as RoB).

    Only 3 domains carry applicability (Patient Selection, Index Test,
    Reference Standard); Flow and Timing is excluded from the input list.
    """
    return quadas2_overall(judgements)


DOMAIN_JUDGES: dict[int, Callable[[dict[str, str]], str]] = {
    1: quadas2_domain_judge,
    2: quadas2_domain_judge,
    3: quadas2_domain_judge,
    4: quadas2_domain_judge,
}


# ─────────────────────────────────────────────
# Domain definitions — signaling questions transcribed verbatim from
# QUADAS-2 (Whiting 2011, Table 1 + section-by-section narrative)
# ─────────────────────────────────────────────
DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Patient Selection",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the included patients and setting do "
            "not match the review question?"
        ),
        "applicability_elaboration": (
            "Concerns about applicability may exist if patients included in "
            "the study differ from those targeted by the review question in "
            "terms of severity of the target condition, demographic features, "
            "presence of differential diagnosis or comorbid conditions, "
            "setting of the study, and previous testing protocols."
        ),
        "relevant_fields": [
            "spectrum_of_patients", "verification_bias", "flow_and_timing",
            "population_inclusion", "population_exclusion",
        ],
        "signals": [
            {
                "id": "1.1",
                "text": "Was a consecutive or random sample of patients enrolled?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "A study should ideally enrol a consecutive or random "
                    "sample of eligible patients with suspected disease to "
                    "prevent the potential for bias. Convenience samples or "
                    "selection on test-related criteria → 'No'."
                ),
            },
            {
                "id": "1.2",
                "text": "Was a case-control design avoided?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Studies enrolling participants with known disease and a "
                    "separate control group without the condition may "
                    "exaggerate diagnostic accuracy (spectrum bias). Answer "
                    "'Yes' for single-gate (cohort) designs; 'No' for "
                    "case-control / multi-gate designs."
                ),
            },
            {
                "id": "1.3",
                "text": "Did the study avoid inappropriate exclusions?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Studies that make inappropriate exclusions (e.g. not "
                    "including 'difficult-to-diagnose' patients, or excluding "
                    "patients with 'red flags' for the target condition who "
                    "may be easier to diagnose) may over- or underestimate "
                    "diagnostic accuracy. 'No' if exclusions are likely to "
                    "have distorted the spectrum."
                ),
            },
        ],
    },
    {
        "id": 2,
        "name": "Index Test",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the index test, its conduct, or its "
            "interpretation differ from the review question?"
        ),
        "applicability_elaboration": (
            "Variations in test technology, execution, or interpretation may "
            "affect estimates of the diagnostic accuracy of a test. If index "
            "test methods vary from those specified in the review question, "
            "concerns about applicability may exist."
        ),
        "relevant_fields": [
            "index_test", "blinding_index_to_reference", "threshold_effects",
        ],
        "signals": [
            {
                "id": "2.1",
                "text": "Were the index test results interpreted without knowledge of the results of the reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Knowledge of the reference standard may influence "
                    "interpretation of index test results (review bias). If "
                    "the index test is always conducted and interpreted "
                    "before the reference standard, this item can be rated "
                    "'Yes'."
                ),
            },
            {
                "id": "2.2",
                "text": "If a threshold was used, was it pre-specified?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Selecting the test threshold to optimize sensitivity "
                    "and/or specificity post-hoc may lead to overestimation "
                    "of test performance. Test performance is likely to be "
                    "poorer in an independent sample of patients in whom the "
                    "same threshold is used. Mark 'Unclear' if no threshold "
                    "was used (e.g. continuous test reported as AUC only)."
                ),
            },
        ],
    },
    {
        "id": 3,
        "name": "Reference Standard",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the target condition as defined by the "
            "reference standard does not match the review question?"
        ),
        "applicability_elaboration": (
            "The reference standard may be free of bias, but the target "
            "condition that it defines may differ from the target condition "
            "specified in the review question. For example, when defining "
            "urinary tract infection, the reference standard is generally "
            "based on specimen culture; however, the threshold above which a "
            "result is considered positive may vary."
        ),
        "relevant_fields": [
            "reference_standard", "blinding_reference_to_index",
            "flow_and_timing",
        ],
        "signals": [
            {
                "id": "3.1",
                "text": "Is the reference standard likely to correctly classify the target condition?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Estimates of test accuracy are based on the assumptions "
                    "that the reference standard is 100% sensitive and that "
                    "any specific disagreements between the reference "
                    "standard and index test result from incorrect "
                    "classification by the index test. 'No' if the reference "
                    "standard is known to be inaccurate or substantially "
                    "different from the accepted diagnostic criterion."
                ),
            },
            {
                "id": "3.2",
                "text": "Were the reference standard results interpreted without knowledge of the results of the index test?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Potential for bias is related to the potential influence "
                    "of previous knowledge of the index test result on the "
                    "interpretation of the reference standard."
                ),
            },
        ],
    },
    {
        "id": 4,
        "name": "Flow and Timing",
        "has_applicability": False,
        "applicability_question": None,
        "applicability_elaboration": None,
        "relevant_fields": [
            "flow_and_timing", "verification_bias", "two_by_two_table",
        ],
        "signals": [
            {
                "id": "4.1",
                "text": "Was there an appropriate interval between the index test and reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Results of the index test and reference standard are "
                    "ideally collected on the same patients at the same time. "
                    "If a delay occurs or if treatment begins between the "
                    "index test and the reference standard, recovery or "
                    "deterioration of the condition may cause "
                    "misclassification. The appropriate interval is "
                    "condition-specific (hours for stroke, weeks for a "
                    "slow-growing tumour)."
                ),
            },
            {
                "id": "4.2",
                "text": "Did all patients receive a reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Partial verification — applying the reference standard "
                    "only to a subset (e.g. index-positive participants) — "
                    "biases sensitivity and specificity estimates. 'No' if "
                    "verification was selective."
                ),
            },
            {
                "id": "4.3",
                "text": "Did all patients receive the same reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Differential verification — different reference "
                    "standards for index-positive vs index-negative patients "
                    "— introduces bias. 'No' if multiple reference standards "
                    "were used non-randomly."
                ),
            },
            {
                "id": "4.4",
                "text": "Were all patients included in the analysis?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "All patients recruited into the study should be included "
                    "in the analysis. A potential for bias exists if the "
                    "number of patients enrolled differs from the number of "
                    "patients included in the 2×2 table of results, because "
                    "patients lost to follow-up differ systematically from "
                    "those who remain."
                ),
            },
        ],
    },
]


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing a diagnostic test "
    "accuracy study using the QUADAS-2 tool (Whiting et al., 2011, Ann Intern "
    "Med). For each domain, read the PDF carefully and answer the signaling "
    "questions with one of: Y (yes), N (no), U (unclear). When the domain has "
    "an applicability assessment, also rate concern that the as-conducted "
    "study matches the review question (PIRT: Patient population / Index test "
    "/ Reference standard / Target condition) as: Low / High / Unclear. "
    "Provide a short rationale (1-2 sentences, quoting the paper where "
    "possible) for every answer. Return ONLY a valid JSON object — no "
    "preamble, no markdown fences."
)


def _format_estimate_block(estimate: dict[str, Any] | None) -> str:
    """Render the estimate context block for the prompt header. Empty when
    no estimate was supplied (single-estimate fallback)."""
    if not estimate:
        return "(assessment is for the paper's primary / headline accuracy estimate)"
    parts = []
    for key in ("description", "subgroup", "index_test", "threshold",
                "reference_standard", "unit_of_analysis", "sensitivity",
                "specificity", "n"):
        val = estimate.get(key)
        if val:
            parts.append(f"- {key.replace('_', ' ').title()}: {val}")
    return "\n".join(parts) if parts else "(assessment is for an estimate but no descriptor fields were supplied)"


def _format_review_context(review_context: str | None) -> str:
    """Render the review-level context (PIRT review question) for the
    prompt header. Empty when not supplied — the LLM falls back to a
    generic intended-use baseline."""
    if not review_context or not review_context.strip():
        return (
            "(no review question supplied — judge applicability against the "
            "generic 'intended-use population' implied by the paper)"
        )
    return review_context.strip()


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        primary_outcome: str,
                        extracted_fields: dict[str, str],
                        estimate: dict[str, Any] | None = None,
                        review_context: str | None = None) -> str:
    """Per-domain prompt for QUADAS-2 signaling-question + applicability assessment."""
    relevant = {k: extracted_fields[k]
                for k in domain["relevant_fields"] if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    q_lines = []
    for sig in domain["signals"]:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: {'/'.join(sig['options'])}."
        )
    questions_block = "\n".join(q_lines)

    shape_lines = ["{"]
    for sig in domain["signals"]:
        shape_lines.append(f'  "{sig["id"]}": "Y|N|U",')
        shape_lines.append(f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",')
    if domain["has_applicability"]:
        shape_lines.append('  "applicability_judgement": "Low|High|Unclear",')
        shape_lines.append('  "applicability_rationale": "1-2 sentences explaining the concern relative to the review question"')
    else:
        if shape_lines[-1].endswith(","):
            shape_lines[-1] = shape_lines[-1][:-1]
    shape_lines.append("}")
    shape = "\n".join(shape_lines)

    applicability_block = ""
    if domain["has_applicability"]:
        applicability_block = (
            "\n\n**Applicability assessment** (rate as Low / High / Unclear):\n"
            f"{domain['applicability_question']}\n"
            f"Elaboration: {domain['applicability_elaboration']}\n"
            "\n**Review question** (PIRT — use this to judge applicability):\n"
            f"{_format_review_context(review_context)}"
        )

    return f"""Assess **Domain {domain['id']} — {domain['name']}** of QUADAS-2 (Whiting 2011) for the diagnostic test accuracy study described in the attached PDF.

Study type: {study_type}
Primary outcome (target condition): {primary_outcome}

Estimate being assessed:
{_format_estimate_block(estimate)}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}{applicability_block}

Return a JSON object with exactly this shape:
{shape}

Answer N only when the paper gives enough information to rule out adherence; answer U only when the paper is silent or the information is ambiguous. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _normalise_answer(raw_value: Any) -> str:
    """Normalise an LLM answer string to one of Y / N / U.

    Accepts Y / Yes / yes / YES → Y;
            N / No / no / NO → N;
            anything else (U / Unclear / NI / ?) → U.
    """
    s = str(raw_value or "").strip().lower()
    if s in ("y", "yes"):
        return "Y"
    if s in ("n", "no"):
        return "N"
    return "U"


def _assess_domain(pdf_bytes: bytes,
                   domain: dict[str, Any],
                   study_type: str,
                   primary_outcome: str,
                   extracted_fields: dict[str, str],
                   llm_call: Callable[[bytes, str, int], dict[str, Any]],
                   estimate: dict[str, Any] | None = None,
                   review_context: str | None = None) -> dict[str, Any]:
    """LLM-assess one domain. Returns
    ``{signals, rationales, judgement, applicability_judgement, applicability_rationale}``
    (the last two are absent for the Flow and Timing domain)."""
    prompt = build_domain_prompt(domain, study_type, primary_outcome,
                                 extracted_fields, estimate=estimate,
                                 review_context=review_context)
    raw = llm_call(pdf_bytes, prompt, 8192)

    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = _normalise_answer(raw.get(sid))
        if ans not in SIGNAL_OPTIONS:
            logger.warning("QUADAS-2 domain %s question %s: invalid answer %r — defaulting to U",
                           domain["id"], sid, raw.get(sid))
            ans = "U"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    judgement = DOMAIN_JUDGES[domain["id"]](signals)

    out: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
    }

    if domain["has_applicability"]:
        app = str(raw.get("applicability_judgement", "Unclear")).strip()
        norm = app.lower()
        if norm in ("u", "unclear", "?", "insufficient", "insufficient information",
                    "no information", "ni"):
            app = "Unclear"
        elif norm in ("low", "low concern", "low concerns"):
            app = "Low"
        elif norm in ("high", "high concern", "high concerns"):
            app = "High"
        else:
            app = "Unclear"
        out["applicability_judgement"] = app
        out["applicability_rationale"] = str(
            raw.get("applicability_rationale", "")).strip()

    return out


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        *,
        llm_call: Callable[[bytes, str, int], dict[str, Any]],
        estimate: dict[str, Any] | None = None,
        review_context: str | None = None,
        progress: Callable[[int], None] | None = None,
        ) -> tuple[dict[str, Any], str, str, str]:
    """Run QUADAS-2 against a diagnostic test accuracy study.

    Returns ``(domain_results, overall_rob, overall_direction, overall_applicability)``.

    - ``domain_results`` is keyed by domain id (``"1"`` … ``"4"``), each with
      ``{name, signals, rationales, judgement, applicability_judgement,
      applicability_rationale}`` (the last two only for domains 1-3).
    - ``overall_rob`` is "Low" / "High" / "Unclear".
    - ``overall_direction`` is always ``"NA"`` for diagnostic accuracy.
    - ``overall_applicability`` is "Low" / "High" / "Unclear", aggregated
      over the 3 applicability-bearing domains only.
    """
    study_type = classification.get("study_type", "Diagnostic Accuracy")

    domain_results: dict[str, Any] = {}
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(pdf_bytes, domain, study_type,
                                primary_outcome, extracted_fields,
                                llm_call=llm_call,
                                estimate=estimate,
                                review_context=review_context)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        result["has_applicability"] = domain["has_applicability"]
        domain_results[str(domain["id"])] = result

    rob_overall = quadas2_overall(
        [domain_results[str(d["id"])]["judgement"] for d in DOMAINS])
    app_overall = quadas2_applicability_overall(
        [domain_results[str(d["id"])]["applicability_judgement"]
         for d in DOMAINS if d["has_applicability"]])

    return domain_results, rob_overall, "NA", app_overall
```

**From that document's §8. Quick test sketches:**

```python
# Domain decision tree
assert quadas2_domain_judge({"1.1": "Y", "1.2": "Y", "1.3": "Y"}) == "Low"
assert quadas2_domain_judge({"1.1": "Y", "1.2": "N", "1.3": "Y"}) == "High"
assert quadas2_domain_judge({"1.1": "Y", "1.2": "U", "1.3": "Y"}) == "Unclear"
assert quadas2_domain_judge({"1.1": "U", "1.2": "U", "1.3": "U"}) == "Unclear"
assert quadas2_domain_judge({}) == "Unclear"
# An N anywhere outranks any number of U or Y
assert quadas2_domain_judge({"1.1": "U", "1.2": "N", "1.3": "Y"}) == "High"

# Overall RoB across 4 domains
assert quadas2_overall(["Low", "Low", "Low", "Low"]) == "Low"
assert quadas2_overall(["Low", "High", "Low", "Low"]) == "High"
assert quadas2_overall(["Low", "Unclear", "Low", "Low"]) == "Unclear"
assert quadas2_overall(["Unclear", "High", "Unclear", "Low"]) == "High"
assert quadas2_overall([]) == "Unclear"

# Overall applicability across the 3 applicability-bearing domains
# (D4 Flow and Timing is excluded by the caller)
assert quadas2_applicability_overall(["Low", "Low", "Low"]) == "Low"
assert quadas2_applicability_overall(["Low", "High", "Low"]) == "High"
assert quadas2_applicability_overall(["Low", "Unclear", "Low"]) == "Unclear"

# DOMAINS structural invariants
assert len(DOMAINS) == 4
assert [d["id"] for d in DOMAINS] == [1, 2, 3, 4]
assert [d["has_applicability"] for d in DOMAINS] == [True, True, True, False]
assert [len(d["signals"]) for d in DOMAINS] == [3, 2, 2, 4]
# 11 total signals across the tool
assert sum(len(d["signals"]) for d in DOMAINS) == 11

print("All QUADAS-2 sanity checks passed.")
```

### 12.7 Per-paper GRADE (indirectness + imprecision + combiner) — from `quality_appraisal_grade_shareable.md`

**From that document's §9. Reference implementation as a single Python file:**

```python
"""grade_certainty.py — single-study GRADE downgrade pipeline (RoB + indirectness + imprecision).

Self-contained: no framework, database, or HTTP dependencies. Supply your own
``llm_call(pdf_bytes, prompt, system) -> dict`` that returns parsed JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger("grade")

LlmCall = Callable[[bytes, str, str], dict]


# ─────────────────────────────────────────────
# 1. Certainty ladder + initial certainty by design
# ─────────────────────────────────────────────
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]


def grade_index(level: str) -> int:
    try:
        return GRADE_LEVELS.index(level)
    except ValueError:
        return 0


# initial_grade of None means "this design gets no GRADE certainty rating"
# (systematic reviews); skip_extras means "run the combiner, but with
# indirectness and imprecision forced to zero" (diagnostic accuracy).
INITIAL_GRADE_BY_DESIGN: dict[str, dict[str, Any]] = {
    "Randomized Controlled Trial":  {"initial_grade": "High"},
    "Crossover Trial":              {"initial_grade": "High"},
    "Cluster Randomized Trial":     {"initial_grade": "High"},
    "Cohort Study":                 {"initial_grade": "Low"},
    "Case-Control":                 {"initial_grade": "Low"},
    "Non-Randomized Trial":         {"initial_grade": "Low"},
    "Cross-Sectional (Analytical)": {"initial_grade": "Low"},
    "Case-Crossover":               {"initial_grade": "Low"},
    "Single-Arm Trial":             {"initial_grade": "Very low"},
    "Dose-Escalation Study":        {"initial_grade": "Very low"},
    "Diagnostic Accuracy":          {"initial_grade": "High", "skip_extras": True},
    "SR with Meta-Analysis":        {"initial_grade": None, "skip_grade": True},
    "SR without Meta-Analysis":     {"initial_grade": None, "skip_grade": True},
}


# ─────────────────────────────────────────────
# 2. Risk of bias → downgrade levels
# ─────────────────────────────────────────────
def rob_downgrade(rob_overall: str,
                  rob_domain_judgements: list[str] | None = None
                  ) -> tuple[int, str]:
    """Map an RoB instrument's overall judgement to 0/1/2 downgrade levels.

    Branch ORDER is load-bearing: the instrument vocabularies overlap on
    "Low" and "High", which are handled once at the top.

    NOTE: ROBINS-I Domain 1's "Low (except for concerns about uncontrolled
    confounding)" / "... benchmarking)" labels must be normalised to plain
    "Low" by the RoB tool's overall aggregator BEFORE reaching this function,
    or they fall through to the catch-all and cost a level.
    """
    judgements = rob_domain_judgements or []
    if rob_overall == "Low":
        return 0, "Low risk of bias"

    # RoB 2 (and its cross-over / cluster extensions)
    if rob_overall == "Some concerns":
        return 1, "Some concerns in risk of bias"
    if rob_overall == "High":
        high_count = sum(1 for j in judgements if j == "High")
        if high_count >= 2:
            return 2, f"High risk of bias in {high_count} domains"
        return 1, "High risk of bias"

    # ROBINS-I V2 (and V1, which shares this vocabulary)
    if rob_overall == "Moderate":
        return 1, "Moderate risk of bias (ROBINS-I V2)"
    if rob_overall == "Serious":
        serious_count = sum(1 for j in judgements if j == "Serious")
        if serious_count >= 2:
            return 2, f"Serious risk of bias in {serious_count} ROBINS-I V2 domains"
        return 1, "Serious risk of bias (ROBINS-I V2)"
    if rob_overall == "Critical":
        return 2, "Critical risk of bias (ROBINS-I V2)"

    # ROBINS-I V1 only — V2 retired the "No information" overall judgement
    if rob_overall == "No information":
        return 1, "No information in one or more ROBINS-I domains (conservative; legacy V1 result)"

    # QUADAS-3
    if rob_overall == "Insufficient information":
        return 1, "Insufficient information in one or more QUADAS-3 domains (conservative)"

    # QUADAS-2
    if rob_overall == "Unclear":
        return 1, "Unclear risk of bias in one or more QUADAS-2 domains (conservative)"

    return 1, f"risk of bias ({rob_overall})"


def collect_domain_judgements(rob_domains: dict[str, Any]) -> list[str]:
    """Filter an RoB instrument's domain map down to entries carrying a judgement.

    Instruments that store preflight metadata alongside the domains would
    otherwise poison the High/Serious domain counts.
    """
    return [
        d.get("judgement", "Low")
        for k, d in rob_domains.items()
        if k != "preflight" and isinstance(d, dict) and "judgement" in d
    ]


# ─────────────────────────────────────────────
# 3. Indirectness — scale, subdomains, tree
# ─────────────────────────────────────────────
IND_JUDGEMENT_OPTIONS = ("direct", "probably_direct", "probably_not_direct", "not_direct")
SEVERITY_LEVELS = ("none", "serious", "very_serious", "extremely_serious")

IND_SUBDOMAINS: list[dict[str, Any]] = [
    {
        "id": "population",
        "label": "Population",
        "guidance": (
            "Assess how closely the study population matches the population of "
            "interest in the target question (age, sex, comorbidities, severity, "
            "setting, geographic context). Highly selected, narrow, or atypical "
            "populations limit generalisability and warrant 'probably not "
            "sufficiently direct' or stronger."
        ),
    },
    {
        "id": "intervention",
        "label": "Intervention",
        "guidance": (
            "Assess how closely the studied intervention matches the intervention "
            "of interest (dose, formulation, mode of delivery, intensity, "
            "duration, provider type). Substantial differences in delivery "
            "context (\"too ideal\" trial conditions, specialised provider, "
            "non-translatable infrastructure) warrant downgrading."
        ),
    },
    {
        "id": "comparator",
        "label": "Comparator",
        "guidance": (
            "Assess how closely the studied comparator matches the comparator of "
            "interest. Active comparators that include potentially effective "
            "co-interventions, or 'usual care' that varies markedly across "
            "settings, warrant downgrading. Placebo controls when the question "
            "is about head-to-head comparison are not direct."
        ),
    },
    {
        "id": "outcome",
        "label": "Outcomes",
        "guidance": (
            "Assess whether outcome measures capture what matters to patients. "
            "Per the GRADE handbook: 'surrogate outcomes should be rated down "
            "for indirectness unless there is a strong and well-established "
            "correlation with meaningful, patient-important outcomes — a "
            "criterion that is rarely fulfilled.' Examples of surrogates: "
            "HbA1c (diabetes complications), LDL cholesterol (cardiovascular "
            "events), bone mineral density (fractures), progression-free "
            "survival (overall survival), tumour response rate (survival)."
        ),
    },
]

IND_SUBDOMAIN_IDS = [s["id"] for s in IND_SUBDOMAINS]
IND_LABELS = {s["id"]: s["label"] for s in IND_SUBDOMAINS}


def indirectness_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """reds=not_direct, oranges=probably_not_direct.

      reds>=3            → extremely_serious (3)
      reds==2            → very_serious (2)
      reds==1 or o>=2    → serious (1)
      otherwise          → none (0)
    """
    reds = sum(1 for v in judgements.values() if v == "not_direct")
    oranges = sum(1 for v in judgements.values() if v == "probably_not_direct")
    counts = {"reds": reds, "oranges": oranges}

    if reds >= 3:
        return "extremely_serious", 3, counts
    if reds == 2:
        return "very_serious", 2, counts
    if reds == 1 or oranges >= 2:
        return "serious", 1, counts
    return "none", 0, counts


def indirectness_explanation(severity: str, counts: dict[str, int],
                             per_subdomain: dict[str, str]) -> str:
    if severity == "none":
        return ("No serious indirectness: PICO components are sufficiently "
                "direct for the target question.")
    drivers = [IND_LABELS.get(sid, sid)
               for sid, j in per_subdomain.items()
               if j in ("not_direct", "probably_not_direct")]
    drivers_text = ", ".join(drivers) if drivers else "PICO mismatch"
    if severity == "serious":
        return (f"Serious indirectness: concerns in {drivers_text} "
                f"({counts['reds']} not-direct, {counts['oranges']} probably-not-direct).")
    if severity == "very_serious":
        return (f"Very serious indirectness: 2 PICO components not sufficiently "
                f"direct ({drivers_text}).")
    return (f"Extremely serious indirectness: {counts['reds']} PICO components "
            f"not sufficiently direct ({drivers_text}).")


# ─────────────────────────────────────────────
# 4. Indirectness — prompts
# ─────────────────────────────────────────────
IND_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing the GRADE "
    "indirectness domain for a single study. Read the PDF carefully. For each "
    "of the four PICO subdomains (Population, Intervention, Comparison, "
    "Outcome), judge how directly the study's evidence applies to the "
    "specified target question on a 4-level scale: 'direct' (sufficiently "
    "direct), 'probably_direct' (probably sufficiently direct), "
    "'probably_not_direct' (probably not sufficiently direct), or 'not_direct' "
    "(not sufficiently direct). Provide a 1-2 sentence rationale per "
    "subdomain, quoting the paper where possible. Per GRADE guidance, do NOT "
    "rate down unless there are compelling reasons to believe the mismatch "
    "would lead to meaningful, systematic differences in the effect estimate. "
    "Surrogate outcomes (HbA1c, LDL, bone density, progression-free survival, "
    "etc.) should be rated 'probably_not_direct' or worse unless a strong, "
    "well-established correlation with patient-important outcomes is "
    "documented in the paper. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)

IND_RELEVANT_KEYS = [
    "population_description", "population_age", "population_sex",
    "population_comorbidities", "population_setting", "geography",
    "inclusion_criteria", "exclusion_criteria",
    "intervention_description", "intervention_dose",
    "intervention_duration", "intervention_provider",
    "comparator_description", "comparator_type",
    "primary_outcome_definition", "primary_outcome_measurement",
    "follow_up_duration",
]


def format_target_pico(target_pico: dict[str, str] | None) -> str:
    """Render the target PICO block, or the no-target fallback.

    The fallback is methodological, not cosmetic: with no review question the
    assessment collapses to outcome surrogacy and P/I/C are pinned near-direct.
    """
    if not target_pico or not any(
            (target_pico.get(k) or "").strip()
            for k in ("population", "intervention", "comparator", "outcome")):
        return (
            "(No target PICO supplied — assess against the as-conducted PICO "
            "of the study itself. Focus the OUTCOME judgement on whether the "
            "primary outcome is a surrogate vs. a patient-important outcome. "
            "For Population, Intervention, and Comparator, default to "
            "'probably_direct' unless the study's selection is unusually "
            "narrow or atypical for routine clinical use.)"
        )
    lines = []
    for key, label in (("population", "Population"),
                       ("intervention", "Intervention"),
                       ("comparator", "Comparator"),
                       ("outcome", "Outcome")):
        val = (target_pico.get(key) or "").strip()
        lines.append(f"  {label}: {val if val else '(unspecified — judge based on as-conducted PICO)'}")
    return "Target question (PICO):\n" + "\n".join(lines)


def _context_json(extracted_fields: dict[str, str], keys: list[str]) -> str:
    relevant = {k: extracted_fields[k] for k in keys if extracted_fields.get(k)}
    return json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"


def _subdomains_block(subdomains: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"\n**{sub['label']} ({sub['id']})**\nGuidance: {sub['guidance']}"
        for sub in subdomains
    )


def build_indirectness_prompt(target_pico: dict[str, str] | None,
                              study_type: str,
                              primary_outcome: str,
                              extracted_fields: dict[str, str]) -> str:
    ctx_json = _context_json(extracted_fields, IND_RELEVANT_KEYS)
    target_block = format_target_pico(target_pico)
    subdomains_block = _subdomains_block(IND_SUBDOMAINS)

    shape = "{\n"
    for sub in IND_SUBDOMAINS:
        sid = sub["id"]
        shape += f'  "{sid}": "direct|probably_direct|probably_not_direct|not_direct",\n'
        shape += f'  "{sid}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "primary_outcome_is_surrogate": true|false,\n'
    shape += ('  "surrogate_rationale": "If outcome is a surrogate, briefly explain '
              '(e.g., \\"HbA1c is a surrogate for diabetes complications\\")."\n')
    shape += "}"

    return f"""Assess **GRADE indirectness** for the study described in the attached PDF.

Study type: {study_type}
Outcome being rated: {assessed_outcome}

{target_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether the mismatch is likely to produce systematic differences in effect estimates. Default to 'probably_direct' rather than 'direct' when there is any meaningful uncertainty. Reserve 'not_direct' for clear, substantial mismatches. Rationales must quote the paper verbatim where possible."""


def normalize_indirectness(raw: str) -> str:
    """Coerce to one of the four options; unknown → probably_direct (non-downgrading)."""
    val = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if val in IND_JUDGEMENT_OPTIONS:
        return val
    aliases = {
        "sufficiently_direct": "direct",
        "probably_sufficiently_direct": "probably_direct",
        "probably_not_sufficiently_direct": "probably_not_direct",
        "not_sufficiently_direct": "not_direct",
        "yes": "direct",
        "no": "not_direct",
    }
    if val in aliases:
        return aliases[val]
    logger.warning("Indirectness: unknown judgement %r — defaulting to probably_direct", raw)
    return "probably_direct"


def assess_indirectness(llm_call: LlmCall,
                        pdf_bytes: bytes,
                        extracted_fields: dict[str, str],
                        study_type: str,
                        primary_outcome: str,
                        target_pico: dict[str, str] | None = None,
                        ) -> tuple[dict[str, Any], str, int, str]:
    """Returns (per_subdomain_results, severity_label, downgrade_levels, explanation)."""
    prompt = build_indirectness_prompt(target_pico, study_type, primary_outcome,
                                       extracted_fields)
    raw = llm_call(pdf_bytes, prompt, IND_SYSTEM_PROMPT)

    per_sub: dict[str, Any] = {}
    judgements: dict[str, str] = {}
    for sub in IND_SUBDOMAINS:
        sid = sub["id"]
        judgement = normalize_indirectness(str(raw.get(sid, "")))
        per_sub[sid] = {"judgement": judgement,
                        "rationale": str(raw.get(f"{sid}_rationale", "")).strip(),
                        "label": sub["label"]}
        judgements[sid] = judgement

    per_sub["primary_outcome_is_surrogate"] = bool(raw.get("primary_outcome_is_surrogate", False))
    per_sub["surrogate_rationale"] = str(raw.get("surrogate_rationale", "")).strip()

    severity, levels, counts = indirectness_severity(judgements)
    per_sub["counts"] = counts
    return per_sub, severity, levels, indirectness_explanation(severity, counts, judgements)


# ─────────────────────────────────────────────
# 5. Imprecision — scale, subdomains, tree
# ─────────────────────────────────────────────
IMP_JUDGEMENT_OPTIONS = ("precise", "probably_precise", "probably_not_precise", "not_precise")

IMP_SUBDOMAINS: list[dict[str, Any]] = [
    {
        "id": "ci_width",
        "label": "Confidence-interval width",
        "guidance": (
            "Per the GRADE handbook, imprecision is judged primarily by "
            "whether the 95% confidence interval around the absolute effect "
            "estimate crosses clinical-decision thresholds. Default thresholds "
            "are the line of no effect plus the minimal important difference "
            "(MID) for benefit and harm if supplied. A CI that does not cross "
            "any threshold → 'precise'. A CI crossing one threshold → "
            "'probably_not_precise' (1-level concern). A CI crossing two or "
            "more thresholds → 'not_precise'. If no effect estimate / CI is "
            "reported, return 'probably_not_precise' and explain in rationale."
        ),
    },
    {
        "id": "sample_size",
        "label": "Sample-size adequacy",
        "guidance": (
            "Is the enrolled N large enough that the result is unlikely to "
            "flip with a few more participants? This is a single-trial "
            "surrogate for the GRADE Optimal Information Size (we do not "
            "compute formal RIS). Rule-of-thumb thresholds for a clinically "
            "important effect: <100 total participants → 'not_precise'; "
            "100–300 → 'probably_not_precise'; 300–1000 → 'probably_precise'; "
            ">1000 → 'precise'. Adjust for outcome type and observed effect "
            "size; underpowered trials with extreme effects warrant concern."
        ),
    },
    {
        "id": "event_count",
        "label": "Event count (binary outcomes)",
        "guidance": (
            "For binary primary outcomes: are there enough events to support "
            "the observed effect? Rule-of-thumb: <100 total events across "
            "arms → 'not_precise'; 100–300 → 'probably_not_precise'; "
            "300–1000 → 'probably_precise'; >1000 → 'precise'. Pay extra "
            "attention to the smaller arm — significance driven by ≤10 "
            "events in one arm is fragile. **Mark this subdomain N/A for "
            "continuous outcomes** (return ``\"n_a\"`` or ``\"not_applicable\"``); "
            "the normalizer treats N/A as 'precise' so it never contributes "
            "to severity counting."
        ),
    },
    {
        "id": "fragility",
        "label": "Fragility / robustness",
        "guidance": (
            "Could a small number of additional events change the conclusion? "
            "Per the GRADE handbook: small studies that produce large "
            "relative effects on dichotomous outcomes can appear precise via "
            "narrow CIs but be fragile because CIs for odds ratios / relative "
            "risks tend to narrow as effects grow. Flag: extreme effect "
            "sizes from few events, p-values barely under 0.05 with small N, "
            "single-event-driven significance, or large relative effects "
            "(RRR > 50%) with sparse data. Continuous outcomes: judge "
            "robustness from observed variance + sample size."
        ),
    },
]

IMP_SUBDOMAIN_IDS = [s["id"] for s in IMP_SUBDOMAINS]
IMP_LABELS = {s["id"]: s["label"] for s in IMP_SUBDOMAINS}


def imprecision_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """reds=not_precise, oranges=probably_not_precise. Same tree as indirectness.

    N/A subdomains (event_count on continuous outcomes) are normalised to
    'precise' upstream so they never contribute to reds/oranges.
    """
    reds = sum(1 for v in judgements.values() if v == "not_precise")
    oranges = sum(1 for v in judgements.values() if v == "probably_not_precise")
    counts = {"reds": reds, "oranges": oranges}

    if reds >= 3:
        return "extremely_serious", 3, counts
    if reds == 2:
        return "very_serious", 2, counts
    if reds == 1 or oranges >= 2:
        return "serious", 1, counts
    return "none", 0, counts


def imprecision_explanation(severity: str, counts: dict[str, int],
                            per_subdomain: dict[str, str]) -> str:
    if severity == "none":
        return ("No serious imprecision: confidence intervals, sample size, "
                "and event counts are sufficient for the target question.")
    drivers = [IMP_LABELS.get(sid, sid)
               for sid, j in per_subdomain.items()
               if j in ("not_precise", "probably_not_precise")]
    drivers_text = ", ".join(drivers) if drivers else "imprecision concerns"
    if severity == "serious":
        return (f"Serious imprecision: concerns in {drivers_text} "
                f"({counts['reds']} not-precise, {counts['oranges']} probably-not-precise).")
    if severity == "very_serious":
        return (f"Very serious imprecision: 2 subdomains not sufficiently "
                f"precise ({drivers_text}).")
    return (f"Extremely serious imprecision: {counts['reds']} subdomains "
            f"not sufficiently precise ({drivers_text}).")


# ─────────────────────────────────────────────
# 6. Imprecision — outcome-type heuristic + prompts
# ─────────────────────────────────────────────
_BINARY_HINTS = (
    "binary", "dichotom", "event", "incidence", "mortalit", "death",
    "rate", "proportion", "frequency", "occurrence",
)
_CONTINUOUS_HINTS = (
    "continuous", "mean", "score", "scale", "concentration",
    "level", "change from baseline",
)


def infer_outcome_is_binary(extracted_fields: dict[str, str],
                            assessed_outcome: str,
                            outcome_is_primary: bool = True,
                            outcome_type: str = "") -> bool | None:
    """True=binary, False=continuous, None=indeterminate.

    A caller-supplied outcome_type wins; then, for the PRIMARY outcome only,
    primary_outcome_type; otherwise binary hints are checked BEFORE continuous
    hints. Paper-level primary_outcome_* fields are gated on outcome_is_primary.
    """
    explicit = (outcome_type or "").strip().lower()
    if not explicit and outcome_is_primary:
        explicit = (extracted_fields.get("primary_outcome_type") or "").lower()
    if any(h in explicit for h in ("binary", "dichotom")):
        return True
    if "continuous" in explicit:
        return False

    if outcome_is_primary:
        measurement = (extracted_fields.get("primary_outcome_measurement") or "")
        definition = (extracted_fields.get("primary_outcome_definition") or "")
    else:
        measurement = definition = ""
    haystack = " ".join([assessed_outcome or "", measurement, definition]).lower()
    if any(h in haystack for h in _BINARY_HINTS):
        return True
    if any(h in haystack for h in _CONTINUOUS_HINTS):
        return False
    return None


IMP_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing the GRADE "
    "imprecision domain for a single trial. Read the PDF carefully. For each "
    "of the four subdomains (CI width, sample size, event count, fragility), "
    "judge how precise the primary-outcome evidence is on a 4-level scale: "
    "'precise' (sufficiently precise), 'probably_precise' (probably "
    "sufficiently precise), 'probably_not_precise' (probably not "
    "sufficiently precise), or 'not_precise' (not sufficiently precise). "
    "Provide a 1-2 sentence rationale per subdomain, quoting the paper "
    "where possible. Per the GRADE handbook, the primary tool is whether "
    "the 95% CI for the absolute effect crosses decision thresholds — the "
    "line of no effect, plus minimal important difference (MID) thresholds "
    "for benefit and harm if supplied. Mark event_count as 'n_a' for "
    "continuous outcomes (it will be excluded from severity counting). Be "
    "alert to single-trial fragility: large relative effects on few events "
    "may appear precise but be unreliable. If baseline risk is very low "
    "(<5%) and the absolute-risk CI is narrow despite a wide relative-risk "
    "CI, briefly note this in rationale rather than rating down. Return "
    "ONLY a valid JSON object — no preamble, no markdown fences."
)

IMP_RELEVANT_KEYS = [
    "primary_outcome_definition", "primary_outcome_measurement",
    "primary_outcome_type",
    "effect_size", "effect_estimate", "confidence_interval",
    "p_value", "statistical_test",
    "sample_size", "sample_size_intervention", "sample_size_comparator",
    "events_intervention", "events_comparator",
    "follow_up_duration", "baseline_risk",
    "population_outcomes",
]


def format_thresholds(thresholds: dict[str, str] | None) -> str:
    if not thresholds or not any(
            (thresholds.get(k) or "").strip() for k in ("mid_benefit", "mid_harm")):
        return (
            "(No MID thresholds supplied — assess CI width against the line "
            "of no effect plus your judgement of clinically important "
            "effect sizes for this outcome. Default to 'probably_precise' "
            "rather than 'precise' when CI width is uncertain.)"
        )
    lines = []
    for key, label in (("mid_benefit", "MID for benefit"),
                       ("mid_harm", "MID for harm")):
        val = (thresholds.get(key) or "").strip()
        lines.append(f"  {label}: {val if val else '(unspecified — use line-of-no-effect only)'}")
    return "Decision thresholds (a priori):\n" + "\n".join(lines)


def format_outcome_type(outcome_is_binary: bool | None) -> str:
    if outcome_is_binary is True:
        return ("Outcome type (inferred): BINARY. Judge event_count using "
                "the rule-of-thumb thresholds in the guidance.")
    if outcome_is_binary is False:
        return ("Outcome type (inferred): CONTINUOUS. Mark event_count as "
                "'n_a' (it will be excluded from severity counting). Judge "
                "fragility from sample size and observed variance instead.")
    return ("Outcome type (inferred): UNCERTAIN. Determine binary vs "
            "continuous from the paper; if continuous, mark event_count as "
            "'n_a'.")


def build_imprecision_prompt(thresholds: dict[str, str] | None,
                             study_type: str,
                             primary_outcome: str,
                             extracted_fields: dict[str, str],
                             outcome_is_binary: bool | None = None) -> str:
    ctx_json = _context_json(extracted_fields, IMP_RELEVANT_KEYS)
    threshold_block = format_thresholds(thresholds)
    outcome_block = format_outcome_type(outcome_is_binary)
    subdomains_block = _subdomains_block(IMP_SUBDOMAINS)

    shape = "{\n"
    for sub in IMP_SUBDOMAINS:
        sid = sub["id"]
        if sid == "event_count":
            shape += f'  "{sid}": "precise|probably_precise|probably_not_precise|not_precise|n_a",\n'
        else:
            shape += f'  "{sid}": "precise|probably_precise|probably_not_precise|not_precise",\n'
        shape += f'  "{sid}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "outcome_is_binary": true|false,\n'
    shape += '  "sample_size_total": <integer or null>,\n'
    shape += '  "events_total": <integer or null>,\n'
    shape += ('  "ci_summary": "Brief description of the reported 95% CI for the primary '
              'outcome (e.g., \\"RR 0.78, 95% CI 0.62 to 0.98\\"), or null if not reported."\n')
    shape += "}"

    return f"""Assess **GRADE imprecision** for the trial described in the attached PDF.

Study type: {study_type}
Outcome being rated: {assessed_outcome}

{outcome_block}

{threshold_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether imprecision is likely to leave the truth uncertain for clinical decision-making. Default to 'probably_precise' rather than 'precise' when there is meaningful uncertainty. Reserve 'not_precise' for clear, substantial imprecision concerns. Rationales must quote the paper verbatim where possible (effect estimates, CIs, sample sizes, event counts)."""


def normalize_imprecision(raw: str) -> str:
    """Coerce to one of the four options. N/A aliases → 'precise' so they never
    contribute to severity counting. Unknown → probably_precise."""
    val = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if val in IMP_JUDGEMENT_OPTIONS:
        return val
    aliases = {
        "sufficiently_precise": "precise",
        "probably_sufficiently_precise": "probably_precise",
        "probably_not_sufficiently_precise": "probably_not_precise",
        "not_sufficiently_precise": "not_precise",
        "yes": "precise",
        "no": "not_precise",
        "n_a": "precise",
        "na": "precise",
        "not_applicable": "precise",
        "n/a": "precise",
    }
    if val in aliases:
        return aliases[val]
    logger.warning("Imprecision: unknown judgement %r — defaulting to probably_precise", raw)
    return "probably_precise"


def _coerce_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def assess_imprecision(llm_call: LlmCall,
                       pdf_bytes: bytes,
                       extracted_fields: dict[str, str],
                       study_type: str,
                       primary_outcome: str,
                       thresholds: dict[str, str] | None = None,
                       ) -> tuple[dict[str, Any], str, int, str]:
    """Returns (per_subdomain_results, severity_label, downgrade_levels, explanation)."""
    inferred_binary = infer_outcome_is_binary(extracted_fields, primary_outcome)
    prompt = build_imprecision_prompt(thresholds, study_type, primary_outcome,
                                      extracted_fields, outcome_is_binary=inferred_binary)
    raw = llm_call(pdf_bytes, prompt, IMP_SYSTEM_PROMPT)

    per_sub: dict[str, Any] = {}
    judgements: dict[str, str] = {}
    for sub in IMP_SUBDOMAINS:
        sid = sub["id"]
        judgement = normalize_imprecision(str(raw.get(sid, "")))
        per_sub[sid] = {"judgement": judgement,
                        "rationale": str(raw.get(f"{sid}_rationale", "")).strip(),
                        "label": sub["label"]}
        judgements[sid] = judgement

    raw_binary = raw.get("outcome_is_binary")
    per_sub["outcome_is_binary"] = raw_binary if isinstance(raw_binary, bool) else inferred_binary
    per_sub["sample_size_total"] = _coerce_int(raw.get("sample_size_total"))
    per_sub["events_total"] = _coerce_int(raw.get("events_total"))
    per_sub["ci_summary"] = str(raw.get("ci_summary", "") or "").strip() or None

    severity, levels, counts = imprecision_severity(judgements)
    per_sub["counts"] = counts
    return per_sub, severity, levels, imprecision_explanation(severity, counts, judgements)


# ─────────────────────────────────────────────
# 8. Combining
# ─────────────────────────────────────────────
def compute_grade(initial: str,
                  rob_overall: str,
                  rob_domain_judgements: list[str] | None = None,
                  indirectness_levels: int = 0,
                  indirectness_explanation: str = "",
                  imprecision_levels: int = 0,
                  imprecision_explanation: str = "",
                  *,
                  inconsistency_levels: int = 0,
                  inconsistency_explanation: str = "",
                  publication_bias_levels: int = 0,
                  publication_bias_explanation: str = "",
                  not_assessed: frozenset[str] = frozenset(),
                  ) -> tuple[str, str]:
    """Sum the downgrade domains, clamp at 'Very low', build the explanation.

    There is no rating-up path — every domain input is clamped at 0.

    The two body-of-evidence domains default to inert: with no inconsistency
    or publication-bias arguments this returns a byte-identical level AND
    explanation to the three-domain single-study version. Explanation parts
    are emitted in canonical GRADE order: risk of bias, inconsistency,
    indirectness, imprecision, publication bias.
    """
    idx = grade_index(initial)
    rob_levels, rob_reason = rob_downgrade(rob_overall, rob_domain_judgements)
    indir_levels = max(0, int(indirectness_levels or 0))
    imprec_levels = max(0, int(imprecision_levels or 0))
    inconsis_levels = max(0, int(inconsistency_levels or 0))
    pubbias_levels = max(0, int(publication_bias_levels or 0))
    total = rob_levels + inconsis_levels + indir_levels + imprec_levels + pubbias_levels
    new_idx = min(idx + total, len(GRADE_LEVELS) - 1)
    new_level = GRADE_LEVELS[new_idx]

    if total == 0:
        clean_parts: list[str] = []
        # A body-of-evidence domain only appears in the string when it was
        # actually in play — that is what keeps single-study output unchanged.
        if "inconsistency" in not_assessed:
            clean_parts.append("inconsistency not applicable")
        elif inconsistency_explanation:
            clean_parts.append("no serious inconsistency")
        if indir_levels == 0:
            clean_parts.append("no serious indirectness")
        if imprec_levels == 0:
            clean_parts.append("no serious imprecision")
        if "publication_bias" in not_assessed:
            clean_parts.append("publication bias not applicable")
        elif publication_bias_explanation:
            clean_parts.append("publication bias undetected")
        suffix = " and " + ", ".join(clean_parts) + " detected" if clean_parts else ""
        return new_level, f"No downgrade: overall risk of bias is Low{suffix}."

    parts: list[str] = []
    if rob_levels > 0:
        unit = "level" if rob_levels == 1 else "levels"
        parts.append(f"{rob_levels} {unit} for {rob_reason}")
    if inconsis_levels > 0:
        sev_label = {1: "serious", 2: "very serious"}.get(
            inconsis_levels, f"{inconsis_levels}-level")
        unit = "level" if inconsis_levels == 1 else "levels"
        suffix = f" — {inconsistency_explanation}" if inconsistency_explanation else ""
        parts.append(f"{inconsis_levels} {unit} for {sev_label} inconsistency{suffix}")
    if indir_levels > 0:
        sev_label = {1: "serious", 2: "very serious",
                     3: "extremely serious"}.get(indir_levels, f"{indir_levels}-level")
        unit = "level" if indir_levels == 1 else "levels"
        suffix = f" — {indirectness_explanation}" if indirectness_explanation else ""
        parts.append(f"{indir_levels} {unit} for {sev_label} indirectness{suffix}")
    if imprec_levels > 0:
        sev_label = {1: "serious", 2: "very serious",
                     3: "extremely serious"}.get(imprec_levels, f"{imprec_levels}-level")
        unit = "level" if imprec_levels == 1 else "levels"
        suffix = f" — {imprecision_explanation}" if imprecision_explanation else ""
        parts.append(f"{imprec_levels} {unit} for {sev_label} imprecision{suffix}")
    if pubbias_levels > 0:
        # GRADE phrases this domain differently and only ever costs 1 level.
        unit = "level" if pubbias_levels == 1 else "levels"
        suffix = f" — {publication_bias_explanation}" if publication_bias_explanation else ""
        parts.append(f"{pubbias_levels} {unit} for publication bias strongly suspected{suffix}")

    total_unit = "level" if total == 1 else "levels"
    return new_level, f"Downgraded {total} {total_unit}: " + " + ".join(parts) + "."


# ─────────────────────────────────────────────
# 9. Top-level entry point
# ─────────────────────────────────────────────
def assess_certainty(llm_call: LlmCall,
                     pdf_bytes: bytes,
                     *,
                     study_type: str,
                     extracted_fields: dict[str, str],
                     primary_outcome: str,
                     rob_overall: str,
                     rob_domains: dict[str, Any] | None = None,
                     target_pico: dict[str, str] | None = None,
                     imprecision_thresholds: dict[str, str] | None = None,
                     ) -> dict[str, Any]:
    """Run the GRADE downgrade pipeline for one study.

    Failure in either extra-domain module degrades to zero levels with an
    ``error`` key rather than aborting the assessment. Check for that key
    before presenting "no serious indirectness/imprecision" as a finding.
    """
    cfg = INITIAL_GRADE_BY_DESIGN.get(study_type)
    if cfg is None:
        return {"status": "unsupported_study_type", "study_type": study_type}

    if cfg.get("skip_grade"):
        # Systematic reviews: the appraisal tool's own rating is the headline
        # output; a review's methodological quality is not a GRADE certainty.
        return {"status": "grade_skipped", "study_type": study_type,
                "rob_overall": rob_overall}

    domain_judgements = collect_domain_judgements(rob_domains or {})
    skip_extras = bool(cfg.get("skip_extras"))

    indirectness: dict[str, Any] = {}
    indir_severity, indir_levels, indir_expl = "none", 0, ""
    imprecision: dict[str, Any] = {}
    imprec_severity, imprec_levels, imprec_expl = "none", 0, ""

    if not skip_extras:
        try:
            indirectness, indir_severity, indir_levels, indir_expl = assess_indirectness(
                llm_call, pdf_bytes, extracted_fields, study_type,
                primary_outcome, target_pico=target_pico)
        except Exception:
            logger.exception("Indirectness assessment failed")
            indirectness = {"error": "Indirectness assessment failed."}

        try:
            imprecision, imprec_severity, imprec_levels, imprec_expl = assess_imprecision(
                llm_call, pdf_bytes, extracted_fields, study_type,
                primary_outcome, thresholds=imprecision_thresholds)
        except Exception:
            logger.exception("Imprecision assessment failed")
            imprecision = {"error": "Imprecision assessment failed."}

    initial_grade = cfg["initial_grade"]
    updated_grade, explanation = compute_grade(
        initial_grade, rob_overall, domain_judgements,
        indirectness_levels=indir_levels, indirectness_explanation=indir_expl,
        imprecision_levels=imprec_levels, imprecision_explanation=imprec_expl)

    return {
        "status": "ok",
        "study_type": study_type,
        "initial_grade": initial_grade,
        "updated_grade": updated_grade,
        "grade_explanation": explanation,
        "rob_overall": rob_overall,
        "indirectness": indirectness,
        "indirectness_overall": indir_severity,
        "indirectness_levels": indir_levels,
        "indirectness_explanation": indir_expl,
        "imprecision": imprecision,
        "imprecision_overall": imprec_severity,
        "imprecision_levels": imprec_levels,
        "imprecision_explanation": imprec_expl,
        "extras_skipped": skip_extras,
    }
```

**From that document's §10. Quick test sketches (no framework — plain assert):**

```python
# ── Ladder ──
assert grade_index("High") == 0
assert grade_index("Very low") == 3
assert grade_index("nonsense") == 0          # fails OPEN to High — documented, not desirable

# ── RoB → downgrade: every branch ──
assert rob_downgrade("Low") == (0, "Low risk of bias")
assert rob_downgrade("Some concerns")[0] == 1
assert rob_downgrade("High", ["High", "Low", "Low"])[0] == 1
assert rob_downgrade("High", ["High", "High", "Low"]) == (2, "High risk of bias in 2 domains")
assert rob_downgrade("Moderate")[0] == 1
assert rob_downgrade("Serious", ["Serious", "Low"])[0] == 1
assert rob_downgrade("Serious", ["Serious", "Serious", "Moderate"])[0] == 2
assert rob_downgrade("Critical")[0] == 2
assert rob_downgrade("Critical", ["Critical", "Critical"])[0] == 2   # never escalates past 2
assert rob_downgrade("No information")[0] == 1                        # ROBINS-I V1 legacy
assert rob_downgrade("Insufficient information")[0] == 1              # QUADAS-3
assert rob_downgrade("Unclear")[0] == 1                               # QUADAS-2
# Un-normalised ROBINS-I Domain 1 label hits the catch-all and silently costs a level
assert rob_downgrade("Low (except for concerns about uncontrolled confounding)")[0] == 1
assert rob_downgrade("Banana")[0] == 1

# Preflight metadata must not leak into the domain count
assert collect_domain_judgements({
    "preflight": {"variant": "single_arm"},
    "1": {"judgement": "High"}, "2": {"judgement": "High"},
    "notes": "free text",
}) == ["High", "High"]

# ── Indirectness severity tree ──
D, PD, PND, ND = "direct", "probably_direct", "probably_not_direct", "not_direct"
assert indirectness_severity({"a": D, "b": D, "c": D, "d": D})[:2] == ("none", 0)
assert indirectness_severity({"a": D, "b": PD, "c": D, "d": PND})[:2] == ("none", 0)   # 1 orange tolerated
assert indirectness_severity({"a": D, "b": PND, "c": D, "d": PND})[:2] == ("serious", 1)
assert indirectness_severity({"a": D, "b": D, "c": D, "d": ND})[:2] == ("serious", 1)
assert indirectness_severity({"a": ND, "b": PND, "c": PND, "d": D})[:2] == ("serious", 1)  # reds==1 wins
assert indirectness_severity({"a": ND, "b": ND, "c": D, "d": D})[:2] == ("very_serious", 2)
assert indirectness_severity({"a": ND, "b": ND, "c": ND, "d": D})[:2] == ("extremely_serious", 3)
assert indirectness_severity({"a": ND, "b": ND, "c": ND, "d": ND})[:2] == ("extremely_serious", 3)

# ── Imprecision severity tree (same shape, different tokens) ──
P, PP, PNP, NP = "precise", "probably_precise", "probably_not_precise", "not_precise"
assert imprecision_severity({"a": P, "b": PP, "c": P, "d": PNP})[:2] == ("none", 0)
assert imprecision_severity({"a": PNP, "b": PNP, "c": P, "d": P})[:2] == ("serious", 1)
assert imprecision_severity({"a": NP, "b": NP, "c": P, "d": P})[:2] == ("very_serious", 2)
assert imprecision_severity({"a": NP, "b": NP, "c": NP, "d": P})[:2] == ("extremely_serious", 3)

# ── N/A never contributes: a continuous-outcome paper that is otherwise clean ──
assert normalize_imprecision("n_a") == "precise"
assert normalize_imprecision("N/A") == "precise"
assert normalize_imprecision("not applicable") == "precise"
assert imprecision_severity({
    "ci_width": P, "sample_size": PP,
    "event_count": normalize_imprecision("n_a"),   # continuous outcome
    "fragility": PP,
})[:2] == ("none", 0)

# ── Normalisation defaults are non-downgrading ──
assert normalize_indirectness("Probably Direct") == "probably_direct"
assert normalize_indirectness("not-sufficiently-direct") == "not_direct"
assert normalize_indirectness("???") == "probably_direct"
assert normalize_imprecision("???") == "probably_precise"

# ── Outcome-type heuristic ──
assert infer_outcome_is_binary({"primary_outcome_type": "Binary"}, "") is True
assert infer_outcome_is_binary({"primary_outcome_type": "continuous"}, "") is False
assert infer_outcome_is_binary({}, "All-cause mortality at 12 months") is True
assert infer_outcome_is_binary({}, "Mean change in HbA1c from baseline") is False
assert infer_outcome_is_binary({}, "Investigator-assessed global impression") is None
# Binary hints are checked BEFORE continuous hints
assert infer_outcome_is_binary({}, "mean event rate") is True

# ── Combining ──
level, expl = compute_grade("High", "Low")
assert level == "High"
assert expl == ("No downgrade: overall risk of bias is Low and no serious "
                "indirectness, no serious imprecision detected.")

# Note the doubled full stop: the module explanation already ends in "." and the
# combiner appends its own. Cosmetic, and faithful to the reference platform.
level, expl = compute_grade("High", "Some concerns", indirectness_levels=1,
                            indirectness_explanation="Surrogate primary outcome.")
assert level == "Low"
assert expl == ("Downgraded 2 levels: 1 level for Some concerns in risk of bias "
                "+ 1 level for serious indirectness — Surrogate primary outcome..")

level, expl = compute_grade("High", "High", ["High", "High"], imprecision_levels=1,
                            imprecision_explanation="Wide CI crossing the MID.")
assert level == "Very low"
assert expl.startswith("Downgraded 3 levels: 2 levels for High risk of bias in 2 domains")

# Singular/plural agreement
assert compute_grade("High", "Unclear")[1] == (
    "Downgraded 1 level: 1 level for Unclear risk of bias in one or more "
    "QUADAS-2 domains (conservative).")

# Very-low floor: the explanation reports the COMPUTED downgrade, not the applied one
level, expl = compute_grade("Very low", "Critical")
assert level == "Very low"
assert expl == "Downgraded 2 levels: 2 levels for Critical risk of bias (ROBINS-I V2)."

# Maximum possible total is 8; still clamps to Very low
assert compute_grade("High", "Critical", indirectness_levels=3,
                     imprecision_levels=3)[0] == "Very low"

# Negative level counts are clamped, not subtracted — there is no rating-up path
assert compute_grade("Moderate", "Low", indirectness_levels=-2)[0] == "Moderate"

print("All GRADE downgrade-pipeline sanity checks passed.")
```

### 12.8 Pooling / meta-analysis (incl. the extraction-to-pool assembly bridge) — from `pooling_meta_analysis_shareable.md`

**From that document's §9. From extraction to a pooled outcome (the assembly bridge):**

```python
def study_is_poolable(study):
    outs = study.get("outcomes")
    return isinstance(outs, list) and any(
        isinstance(oc, dict) and outcome_to_study_input(study, oc, None) is not None
        for oc in outs)

def prepare_study(item, extract_outcome_data, force_extract=False):
    """item: flat study dict, may carry outcomes[] and/or pdf_bytes.
    extract_outcome_data(pdf_bytes, injected=...) is the §9.4 model pass, injected."""
    study = {k: v for k, v in item.items() if k != "pdf_bytes"}
    if not force_extract and study_is_poolable(study):
        return study                                    # from the extraction agent — no model call
    if item.get("pdf_bytes"):
        priming = {k: v for k, v in study.items() if k != "outcomes"} or None
        return extract_outcome_data(item["pdf_bytes"], injected=priming)   # self-extract fallback
    return study

def pool_studies(items, extract_outcome_data, *, force_extract=False, **kw):
    studies = [prepare_study(it, extract_outcome_data, force_extract) for it in items]
    return pool_extractions(studies, **kw)
```

```python
import re

_RANDOMIZED = {"randomized controlled trial", "rct", "crossover trial",
    "cross-over trial", "cluster randomized trial", "cluster randomised trial",
    "randomized trial", "randomised controlled trial"}
_NON_RANDOMIZED = {"cohort study", "cohort", "case-control", "case-control study",
    "non-randomized trial", "cross-sectional", "case-crossover", "single-arm trial",
    "dose-escalation study", "observational", "nrsi"}
_BIN = ("events_int", "n_int", "events_ctrl", "n_ctrl")
_CON = ("mean_int", "sd_int", "n_int", "mean_ctrl", "sd_ctrl", "n_ctrl")


def _norm(x):
    if x is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip())


def _design_class(design):
    d = _norm(design)
    if not d:
        return "unknown"
    if d in {_norm(x) for x in _RANDOMIZED} or "randomiz" in d or "randomis" in d:
        return "rct"
    if d in {_norm(x) for x in _NON_RANDOMIZED} or "cohort" in d or "observational" in d:
        return "nrs"
    return "unknown"


def _has(d, keys):
    return all(_num(d.get(k)) is not None for k in keys)


def _num(v):  # tolerant float
    try:
        return float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _canon(measure):  # reuse the pooling reference's _canon / study_effect / pool_outcome
    return (measure or "").upper()


# CANONICAL_KEY is defined in §9.7; harmonization stamps it onto outcome objects and
# this module prefers it wherever an outcome name is used as a key.
CANONICAL_KEY = "canonical_outcome"


def resolve_rob(study_level, oc, outcome_key=None):
    """Resolve the risk-of-bias label for one (study x outcome) pair.

    Returns ``(label, source)``. ``outcome_key`` is the body's outcome name -- the
    canonical one when harmonization (§9.7) has run. Precedence: the outcome object's
    own label, then the study's per-outcome map, then a study-level label, then
    nothing. An explicit ``rob_source`` on either dict always wins over the inferred
    value (that is how an instrument stamps "tool"). Nothing here judges the label.
    """
    oc = oc or {}
    explicit = oc.get("rob_source") or study_level.get("rob_source")

    def _out(label, inferred):
        label = str(label).strip() if label is not None else ""
        if not label:
            return None, "missing"
        return label, (explicit or inferred)

    if oc.get("rob"):                                          # 1. outcome object
        return _out(oc["rob"], "user_outcome")

    table = study_level.get("rob_by_outcome") or {}            # 2. per-outcome map
    if table:
        key = oc.get(CANONICAL_KEY) or outcome_key or oc.get("name") or oc.get("outcome_name")
        hit = {_norm(k): k for k in table}.get(_norm(key))
        if hit is not None and table.get(hit):
            return _out(table[hit], "user_outcome")

    if study_level.get("rob"):                                 # 3. study-level
        return _out(study_level["rob"], "user_study")
    return None, "missing"                                     # 4. nothing


def attach_rob(studies, records, id_key="study_id"):
    """Merge risk-of-bias records onto study dicts, BEFORE harmonization (§9.7).

    ``records``: [{"study_id": ..., "outcome": <str or None>, "rob": <label>,
                   "rob_source": <"tool"|"user_outcome"|"user_study">}, ...].
    A record with an ``outcome`` populates that study's ``rob_by_outcome`` map; one
    without becomes the study-level ``rob``. Pure -- no I/O, no judgement. This is the
    seam an appraisal-database adapter targets.

    Order matters: attach_rob -> harmonize_by_targets -> group_into_bodies. Attaching
    after harmonization leaves the rob_by_outcome keys un-canonicalized, so every
    lookup misses and an appraised body reads as unappraised.
    """
    by_id = {}
    for r in records or []:
        sid = r.get(id_key) or r.get("study_id")
        if not sid or not r.get("rob"):
            continue
        slot = by_id.setdefault(str(sid), {"rob_by_outcome": {}})
        if r.get("outcome"):
            slot["rob_by_outcome"][r["outcome"]] = r["rob"]
        else:
            slot["rob"] = r["rob"]
        if r.get("rob_source"):
            slot["rob_source"] = r["rob_source"]
    out = []
    for s in studies:
        add = by_id.get(str(s.get("study_id") or s.get("citation_authors") or ""))
        if not add:
            out.append(s); continue
        s2 = dict(s)
        s2["rob_by_outcome"] = {**(s.get("rob_by_outcome") or {}), **add["rob_by_outcome"]}
        for k in ("rob", "rob_source"):
            if add.get(k) and not s2.get(k):
                s2[k] = add[k]
        out.append(s2)
    return out


def outcome_to_study_input(study_level, oc, target_measure, outcome_key=None):
    base = {"study_id": study_level.get("study_id") or study_level.get("citation_authors"),
            "design": study_level.get("study_type") or study_level.get("design")}
    base["rob"], base["rob_source"] = resolve_rob(study_level, oc, outcome_key)
    for src in (oc, study_level):
        if _has(src, _BIN):
            base.update({k: _num(src.get(k)) for k in _BIN}); return base
        if _has(src, _CON):
            base.update({k: _num(src.get(k)) for k in _CON}); return base
    metric = _canon(oc.get("effect_metric")); est = _num(oc.get("effect_estimate"))
    if est is None or not metric:
        return None
    if target_measure and metric != _canon(target_measure):
        return None
    base.update({"estimate": est, "ci_lower": _num(oc.get("ci_lower")),
                 "ci_upper": _num(oc.get("ci_upper")), "se": _num(oc.get("se"))})
    return base


def group_into_bodies(studies, include_timepoint=True):
    bodies = {}
    for s in studies:
        outs = s.get("outcomes")
        if not isinstance(outs, list):
            continue
        dcls = _design_class(s.get("study_type") or s.get("design"))
        for oc in outs:
            if not isinstance(oc, dict):
                continue
            name = oc.get(CANONICAL_KEY) or oc.get("name") or oc.get("outcome_name")
            comp = oc.get("comparison") or s.get("population_comparator")
            tim = oc.get("timing") or oc.get("outcome_timing")
            key = (_norm(name), _norm(comp), _norm(tim) if include_timepoint else "", dcls)
            b = bodies.setdefault(key, {"outcome_name": name, "comparison": comp,
                "timepoint": tim, "design_class": dcls, "members": []})
            b["members"].append((s, oc))
    return list(bodies.values())


def _choose_measure(members, override):
    if override:
        return _canon(override)
    counts = {}
    for _s, oc in members:
        m = _canon(oc.get("effect_metric"))
        if m:
            counts[m] = counts.get(m, 0) + 1
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    for _s, oc in members:  # default from raw data
        for src in (oc, _s):
            if _has(src, _BIN):
                return "RR"
            if _has(src, _CON):
                return "MD"
    return None


def pool_body(body, measure=None, model="random", tau2_method="REML"):
    target = _choose_measure(body["members"], measure)
    inputs, excluded, warnings = [], [], []
    if body["design_class"] == "unknown":
        raw = {(s.get("study_type") or s.get("design")) for s, _oc in body["members"]}
        warnings.append("unrecognized study design(s), kept in their own body: "
                        + ", ".join(sorted(str(r) for r in raw if r)))
    direction = None
    for s, oc in body["members"]:
        direction = direction or oc.get("favorable_direction")
        si = outcome_to_study_input(s, oc, target, body["outcome_name"])
        label = s.get("study_id") or s.get("citation_authors")
        if si is None:
            m = _canon(oc.get("effect_metric"))
            excluded.append(f"{label}: reported {m}, body pools {target}" if m and target
                            else f"{label}: no poolable effect or arm data")
        else:
            inputs.append(si)
    out = {"outcome_name": body["outcome_name"], "comparison": body["comparison"],
           "timepoint": body["timepoint"], "design_class": body["design_class"],
           "measure": target, "favorable_direction": direction or "lower",
           "k": len(inputs), "excluded": excluded, "warnings": warnings, "pooled": None}
    if target and inputs:
        out["pooled"] = pool_outcome(inputs, target, model=model, tau2_method=tau2_method,
                                     outcome_name=body["outcome_name"],
                                     favorable_direction=out["favorable_direction"],
                                     design_class=body["design_class"])
    return out


def pool_extractions(studies, measures=None, default_measure=None,
                     include_timepoint=True, min_studies=1, model="random", tau2_method="REML"):
    measures = measures or {}
    out = []
    for body in group_into_bodies(studies, include_timepoint):
        forced = measures.get(_norm(body["outcome_name"])) or default_measure
        res = pool_body(body, forced, model, tau2_method)
        if res["k"] >= min_studies:
            out.append(res)
    return out
```

```python
CANONICAL_KEY = "canonical_outcome"


def build_alias_index(targets):
    index = {}
    for t in targets or []:
        if isinstance(t, str):
            canon, aliases = t, []
        elif isinstance(t, dict):
            canon = t.get("canonical") or t.get("name") or t.get("outcome")
            aliases = t.get("aliases") or t.get("synonyms") or []
        else:
            continue
        if not canon:
            continue
        index[_norm(canon)] = canon
        for a in aliases:
            if a:
                index[_norm(a)] = canon
    return index


def match_outcome_name(name, index, fuzzy=True, min_jaccard=0.6):
    n = _norm(name)
    if not n:
        return None
    if n in index:
        return index[n]
    if not fuzzy:
        return None
    nt = set(n.split())
    best, best_score = None, 0.0
    for alias_norm, canon in index.items():
        at = set(alias_norm.split())
        inter = len(nt & at)
        if not at or inter == 0:
            continue
        subset = at <= nt or nt <= at
        jac = inter / len(nt | at)
        if subset or jac >= min_jaccard:
            score = 1.0 if subset else jac
            if score > best_score:
                best, best_score = canon, score
    return best


def apply_canonical_map(studies, name_to_canonical):
    out = []
    for s in studies:
        s2 = dict(s)
        if isinstance(s.get("outcomes"), list):
            new = []
            for oc in s["outcomes"]:
                if isinstance(oc, dict) and not oc.get(CANONICAL_KEY):
                    nm = oc.get("name") or oc.get("outcome_name")
                    canon = name_to_canonical.get(_norm(nm)) if nm else None
                    if canon:
                        oc = {**oc, CANONICAL_KEY: canon}
                new.append(oc)
            s2["outcomes"] = new
        # Risk-of-bias keys are outcome names too (§9.3). Canonicalizing the outcomes
        # but not these breaks every per-outcome lookup: the body becomes the canonical
        # name while the key stays an alias, so an appraised body reads as unappraised.
        if isinstance(s.get("rob_by_outcome"), dict):
            s2["rob_by_outcome"] = {(name_to_canonical.get(_norm(k)) or k): v
                                    for k, v in s["rob_by_outcome"].items()}
        out.append(s2)
    return out


def clusters_to_map(clusters):          # LLM output [{canonical, members}] -> name->canonical
    m = {}
    for cl in clusters or []:
        canon = cl.get("canonical")
        if not canon:
            continue
        for mem in cl.get("members") or []:
            if mem:
                m[_norm(mem)] = canon
        m.setdefault(_norm(canon), canon)
    return m


def harmonize_by_targets(studies, targets, fuzzy=True):
    index = build_alias_index(targets)
    names, mapping, report = {}, {}, []
    for s in studies:                    # distinct names (skip already-canonical)
        for oc in s.get("outcomes") or []:
            if isinstance(oc, dict) and not oc.get(CANONICAL_KEY):
                nm = oc.get("name") or oc.get("outcome_name")
                if nm:
                    names[nm] = names.get(nm, 0) + 1
        for k in (s.get("rob_by_outcome") or {}):   # RoB keys are outcome names too
            if k:
                names[k] = names.get(k, 0) + 1
    for nm, cnt in names.items():
        canon = match_outcome_name(nm, index, fuzzy)
        if canon:
            mapping[_norm(nm)] = canon
        report.append({"name": nm, "canonical": canon, "count": cnt})
    return apply_canonical_map(studies, mapping), report
```

**From that document's §10. Turnkey reference implementation:**

```python
"""Pooling (meta-analysis) — dependency-free turnkey reference. Standard library only."""
from __future__ import annotations
import math
from typing import Any, Iterable, Optional

_Z = 1.959964  # two-sided 95% normal quantile
_LOG = frozenset({"OR", "RR", "IRR", "HR"})
_BINARY = frozenset({"OR", "RR", "RD"})   # IRR is NOT a 2x2 measure — see _irr
_CONT = frozenset({"MD", "SMD"})
_SYN = {"hazard ratio": "HR", "odds ratio": "OR", "risk ratio": "RR",
        "relative risk": "RR", "rate ratio": "IRR", "incidence rate ratio": "IRR",
        "mean difference": "MD", "standardized mean difference": "SMD",
        "standardised mean difference": "SMD", "risk difference": "RD"}


def _canon(measure: str) -> str:
    s = str(measure or "").strip()
    return _SYN.get(s.lower(), s.upper())


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# --- special functions (no scipy) -----------------------------------------
def _inv_phi(p: float) -> Optional[float]:
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plo, phi = 0.02425, 1.0 - 0.02425
    if p < plo:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p <= phi:
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)


def _gser(a, x):
    ap, s, dlt = a, 1.0/a, 1.0/a
    for _ in range(1000):
        ap += 1.0; dlt *= x/ap; s += dlt
        if abs(dlt) < abs(s)*1e-15:
            break
    return s*math.exp(-x + a*math.log(x) - math.lgamma(a))


def _gcf(a, x):
    tiny = 1e-30; b = x+1.0-a; c = 1.0/tiny; d = 1.0/b; h = d
    for i in range(1, 1000):
        an = -i*(i-a); b += 2.0; d = an*d+b
        if abs(d) < tiny:
            d = tiny
        c = b+an/c
        if abs(c) < tiny:
            c = tiny
        d = 1.0/d; dl = d*c; h *= dl
        if abs(dl-1.0) < 1e-15:
            break
    return math.exp(-x + a*math.log(x) - math.lgamma(a))*h


def _gammq(a, x):
    if x <= 0.0 or a <= 0.0:
        return 1.0
    return 1.0-_gser(a, x) if x < a+1.0 else _gcf(a, x)


def _chi2_sf(x, df):
    return 1.0 if (df <= 0 or x <= 0) else _gammq(df/2.0, x/2.0)


def _betacf(a, b, x):
    tiny = 1e-30; qab, qap, qam = a+b, a+1.0, a-1.0
    c = 1.0; d = 1.0-qab*x/qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0/d; h = d
    for m in range(1, 300):
        m2 = 2*m
        aa = m*(b-m)*x/((qam+m2)*(a+m2))
        d = 1.0+aa*d; d = tiny if abs(d) < tiny else d
        c = 1.0+aa/c; c = tiny if abs(c) < tiny else c
        d = 1.0/d; h *= d*c
        aa = -(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d = 1.0+aa*d; d = tiny if abs(d) < tiny else d
        c = 1.0+aa/c; c = tiny if abs(c) < tiny else c
        d = 1.0/d; dl = d*c; h *= dl
        if abs(dl-1.0) < 1e-14:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lb = math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)
    front = math.exp(lb + a*math.log(x) + b*math.log(1.0-x))
    return front*_betacf(a, b, x)/a if x < (a+1.0)/(a+b+2.0) \
        else 1.0-front*_betacf(b, a, 1.0-x)/b


def _t_sf2(t, df):
    t = abs(t)
    return _betai(df/2.0, 0.5, df/(df+t*t))


def _t_quantile(p, df):
    z = _inv_phi(p)
    if z is None:
        return float("nan")
    if not math.isfinite(df) or df > 1e6:
        return z
    g1 = (z**3+z)/4.0
    g2 = (5*z**5+16*z**3+3*z)/96.0
    g3 = (3*z**7+19*z**5+17*z**3-15*z)/384.0
    g4 = (79*z**9+776*z**7+1482*z**5-1920*z**3-945*z)/92160.0
    return z + g1/df + g2/df**2 + g3/df**3 + g4/df**4


def _ncdf(x):
    return 0.5*(1.0+math.erf(x/math.sqrt(2.0)))


def _order(lo, hi):
    return (hi, lo) if (lo is not None and hi is not None and lo > hi) else (lo, hi)


# --- effect size -----------------------------------------------------------
def study_effect(study: dict, measure: str) -> Optional[dict]:
    m = _canon(measure); is_log = m in _LOG
    yi, vi = _num(study.get("yi")), _num(study.get("vi"))
    if yi is not None and vi is not None and vi > 0:
        return _counts(study, yi, vi, study.get("note"))
    est = _num(study.get("estimate"))
    if est is not None:
        se = _num(study.get("se"))
        lo, hi = _order(_num(study.get("ci_lower")), _num(study.get("ci_upper")))
        if is_log:
            if est <= 0:
                return None
            t = math.log(est)
            if se is None and lo and hi and lo > 0 and hi > 0:
                se = (math.log(hi)-math.log(lo))/(2*_Z)
        else:
            t = est
            if se is None and lo is not None and hi is not None:
                se = (hi-lo)/(2*_Z)
        return _counts(study, t, se*se, study.get("note")) if (se and se > 0) else None
    if m in _BINARY:
        return _binary(study, m)
    if m == "IRR":
        return _irr(study)
    if m in _CONT:
        return _cont(study, m)
    return None


def _binary(s, m):
    a, n1, c, n2 = (_num(s.get(k)) for k in ("events_int", "n_int", "events_ctrl", "n_ctrl"))
    if None in (a, n1, c, n2) or n1 <= 0 or n2 <= 0 or a < 0 or c < 0 or a > n1 or c > n2:
        return None
    b, d = n1-a, n2-c; note = None
    if m == "RD":
        p1, p2 = a/n1, c/n2
        vi = p1*(1-p1)/n1 + p2*(1-p2)/n2 or 1.0/(n1+n2)
        return _counts(s, p1-p2, vi, None)
    if m == "RR" and a == 0 and c == 0:
        return None
    if m == "OR" and ((a == 0 and c == 0) or (b == 0 and d == 0)):
        return None
    if 0 in (a, b, c, d):
        a, b, c, d = a+0.5, b+0.5, c+0.5, d+0.5
        n1c, n2c = a+b, c+d; note = "continuity_correction_0.5"
    else:
        n1c, n2c = n1, n2
    if m == "RR":
        yi = math.log((a/n1c)/(c/n2c)); vi = 1/a-1/n1c+1/c-1/n2c
    else:
        yi = math.log((a*d)/(b*c)); vi = 1/a+1/b+1/c+1/d
    return _counts(s, yi, vi, note) if vi > 0 else None


def _irr(s):  # incidence rate ratio needs person-time, NOT a 2x2 count table
    a, c = _num(s.get("events_int")), _num(s.get("events_ctrl"))
    t1 = _num(s.get("time_int")) if s.get("time_int") is not None else _num(s.get("pyears_int"))
    t2 = _num(s.get("time_ctrl")) if s.get("time_ctrl") is not None else _num(s.get("pyears_ctrl"))
    if None in (a, c, t1, t2) or t1 <= 0 or t2 <= 0 or a < 0 or c < 0:
        return None
    if a == 0 and c == 0:
        return None
    note = None
    if a == 0 or c == 0:
        a, c = a+0.5, c+0.5; note = "continuity_correction_0.5"
    return _counts(s, math.log((a/t1)/(c/t2)), 1/a + 1/c, note)


def _cont(s, m):
    m1, s1, n1, m2, s2, n2 = (_num(s.get(k)) for k in
        ("mean_int", "sd_int", "n_int", "mean_ctrl", "sd_ctrl", "n_ctrl"))
    if None in (m1, s1, n1, m2, s2, n2) or n1 <= 1 or n2 <= 1 or s1 < 0 or s2 < 0:
        return None
    if m == "MD":
        vi = s1*s1/n1 + s2*s2/n2
        return _counts(s, m1-m2, vi, None) if vi > 0 else None
    df = n1+n2-2
    sp2 = ((n1-1)*s1*s1 + (n2-1)*s2*s2)/df
    if sp2 <= 0:
        return None
    d = (m1-m2)/math.sqrt(sp2)
    j = 1.0 - 3.0/(4.0*(n1+n2)-9.0); g = j*d
    vi = j*j*((n1+n2)/(n1*n2) + d*d/(2.0*df))
    return _counts(s, g, vi, "hedges_g") if vi > 0 else None


def _counts(s, yi, vi, note):
    # Every effect-size path funnels through here, so this is the single place that
    # decides which input fields survive into the pooled record.
    rob = str(s.get("rob")).strip() if s.get("rob") is not None else ""
    return {"study_id": s.get("study_id") or s.get("id") or s.get("name"),
            "design": s.get("design") or s.get("study_type"),
            "n_int": _num(s.get("n_int")), "n_ctrl": _num(s.get("n_ctrl")),
            "events_int": _num(s.get("events_int")), "events_ctrl": _num(s.get("events_ctrl")),
            "yi": yi, "vi": vi, "note": note,
            # Carried, never interpreted. The only normalization is that a blank label
            # means "missing"; a supplied rob_source is passed through untouched.
            "rob": rob or None,
            "rob_source": s.get("rob_source") or ("missing" if not rob else None)}


# --- pooling + tau2 --------------------------------------------------------
def _fe(y, v):
    w = [1.0/vi for vi in v]; sw = sum(w)
    est = sum(wi*yi for wi, yi in zip(w, y))/sw
    return {"estimate": est, "var": 1.0/sw, "se": math.sqrt(1.0/sw), "weights": w, "sum_w": sw}


def _re(y, v, tau2):
    w = [1.0/(vi+tau2) for vi in v]; sw = sum(w)
    est = sum(wi*yi for wi, yi in zip(w, y))/sw
    return {"estimate": est, "var": 1.0/sw, "se": math.sqrt(1.0/sw), "weights": w, "sum_w": sw}


def _Q(y, v, fe):
    return sum((1.0/vi)*(yi-fe)**2 for yi, vi in zip(y, v))


def tau2_dl(y, v):
    k = len(y)
    if k < 2:
        return 0.0
    fe = _fe(y, v); q = _Q(y, v, fe["estimate"])
    sw, sw2 = fe["sum_w"], sum(wi*wi for wi in fe["weights"])
    denom = sw - sw2/sw
    return max(0.0, (q-(k-1))/denom) if denom > 0 else 0.0


def tau2_reml(y, v, max_iter=200, tol=1e-7):
    k = len(y)
    if k < 2:
        return 0.0
    tau2 = tau2_dl(y, v)
    for _ in range(max_iter):
        w = [1.0/(vi+tau2) for vi in v]; sw = sum(w)
        mu = sum(wi*yi for wi, yi in zip(w, y))/sw
        sw2 = sum(wi*wi for wi in w)
        num = sum(wi*wi*((yi-mu)**2-vi) for wi, yi, vi in zip(w, y, v))
        nt = max(0.0, num/sw2 + 1.0/sw)
        if abs(nt-tau2) < tol:
            return nt
        tau2 = nt
    return tau2_dl(y, v)


def tau2_pm(y, v, max_iter=200, tol=1e-7):
    k = len(y)
    if k < 2:
        return 0.0

    def g(tau2):
        w = [1.0/(vi+tau2) for vi in v]; sw = sum(w)
        mu = sum(wi*yi for wi, yi in zip(w, y))/sw
        return sum(wi*(yi-mu)**2 for wi, yi in zip(w, y)) - (k-1)
    if g(0.0) <= 0:
        return 0.0
    lo, hi = 0.0, max(v)+1.0
    while g(hi) > 0 and hi < 1e12:
        hi *= 2.0
    for _ in range(max_iter):
        mid = 0.5*(lo+hi); gm = g(mid)
        if abs(gm) < tol:
            return mid
        lo, hi = (mid, hi) if gm > 0 else (lo, mid)
    return 0.5*(lo+hi)


_TAU2 = {"DL": tau2_dl, "REML": tau2_reml, "PM": tau2_pm}


# --- publication bias ------------------------------------------------------
def eggers_test(y, v):
    k = len(y)
    if k < 3:
        return None
    s = [math.sqrt(vi) for vi in v]
    x = [1.0/si for si in s]; yy = [yi/si for yi, si in zip(y, s)]
    n = float(k); mx = sum(x)/n; my = sum(yy)/n
    sxx = sum((xi-mx)**2 for xi in x)
    sxy = sum((xi-mx)*(yi-my) for xi, yi in zip(x, yy))
    if sxx <= 0:
        return None
    slope = sxy/sxx; icpt = my-slope*mx
    resid = [yi-(icpt+slope*xi) for xi, yi in zip(x, yy)]
    sig2 = sum(r*r for r in resid)/(k-2)
    se = math.sqrt(sig2*(1.0/n + mx*mx/sxx))
    if se <= 0:
        return None
    t = icpt/se
    return {"intercept": icpt, "se": se, "t": t, "df": k-2,
            "p": _t_sf2(t, k-2), "k": k, "adequate_power": k >= 10, "slope": slope}


def _signed_ranks(av):
    order = sorted(range(len(av)), key=lambda i: av[i]); r = [0.0]*len(av); i = 0
    while i < len(order):
        j = i
        while j+1 < len(order) and av[order[j+1]] == av[order[i]]:
            j += 1
        avg = (i+j)/2.0 + 1.0
        for t in range(i, j+1):
            r[order[t]] = avg
        i = j+1
    return r


def trim_and_fill(y, v, tau2_method="DL", max_iter=100):
    k = len(y)
    if k < 3:
        return None
    pairs = sorted(zip(y, v), key=lambda p: p[0])
    ys = [p[0] for p in pairs]; vs = [p[1] for p in pairs]
    est = _TAU2.get(tau2_method, tau2_dl)

    def pm(yy, vv):
        return _re(yy, vv, est(yy, vv))["estimate"]
    l0, n = 0, k; cy, cv = list(ys), list(vs); side = "left"
    for _ in range(max_iter):
        mu = pm(cy, cv); cen = [yi-mu for yi in cy]
        rk = _signed_ranks([abs(c) for c in cen])
        tr = sum(r for c, r in zip(cen, rk) if c > 0)
        tl = sum(r for c, r in zip(cen, rk) if c < 0)
        side, tn = ("left", tr) if tr >= tl else ("right", tl)
        nn = len(cy)
        ln = int(round((4.0*tn - nn*(nn+1))/(2.0*nn-1.0)))
        ln = max(0, min(ln, nn-1))
        if ln == l0:
            break
        l0 = ln
        idx = sorted(range(n), key=lambda i: ys[i])
        keep = idx[:n-l0] if side == "left" else idx[l0:]
        cy = [ys[i] for i in keep]; cv = [vs[i] for i in keep]
        if len(cy) < 2:
            break
    if l0 <= 0:
        re = _re(ys, vs, est(ys, vs))
        return {"side": None, "n_imputed": 0, "estimate": re["estimate"], "se": re["se"],
                "ci_lower": re["estimate"]-_Z*re["se"], "ci_upper": re["estimate"]+_Z*re["se"]}
    mu = pm(cy, cv); idx = sorted(range(n), key=lambda i: ys[i])
    ex = idx[n-l0:] if side == "left" else idx[:l0]
    ay = ys + [2.0*mu-ys[i] for i in ex]; av = vs + [vs[i] for i in ex]
    re = _re(ay, av, est(ay, av))
    return {"side": side, "n_imputed": l0, "estimate": re["estimate"], "se": re["se"],
            "ci_lower": re["estimate"]-_Z*re["se"], "ci_upper": re["estimate"]+_Z*re["se"]}


# --- top-level composer ----------------------------------------------------
def _block(t, se, is_log):
    lo, hi = t-_Z*se, t+_Z*se
    z = t/se if se > 0 else float("nan")
    p = 2.0*(1.0-_ncdf(abs(z))) if math.isfinite(z) else None
    return {"yi": t, "se": se, "z": z, "p": (min(max(p, 1e-300), 1.0) if p is not None else None),
            "estimate": math.exp(t) if is_log else t,
            "ci_lower": math.exp(lo) if is_log else lo,
            "ci_upper": math.exp(hi) if is_log else hi}


def pool_outcome(studies: Iterable[dict], measure: str, *, model="random",
                 tau2_method="REML", outcome_name=None, favorable_direction="lower",
                 design_class=None):
    m = _canon(measure); is_log = m in _LOG
    prep, warn = [], []
    for i, s in enumerate(studies):
        e = study_effect(s, m)
        lab = s.get("study_id") or s.get("id") or s.get("name") or f"study[{i}]"
        if e is None or not math.isfinite(e["yi"]) or not (e["vi"] > 0):
            warn.append(f"dropped (no usable {m} data): {lab}"); continue
        e["study_id"] = e.get("study_id") or lab
        if e.get("note") == "continuity_correction_0.5":
            warn.append(f"continuity correction (+0.5) applied: {e['study_id']}")
        prep.append(e)
    res = {"measure": m, "scale": "log" if is_log else "raw", "model": model,
           "outcome_name": outcome_name, "favorable_direction": favorable_direction,
           "design_class": design_class,
           "k": len(prep), "warnings": warn, "studies": [], "fixed": None, "random": None,
           "pooled": None, "heterogeneity": None, "publication_bias": None,
           "totals": _totals(prep)}
    if not prep:
        res["warnings"].append("no poolable studies"); return res
    y = [e["yi"] for e in prep]; v = [e["vi"] for e in prep]
    fe = _fe(y, v)
    tau2 = _TAU2.get((tau2_method or "REML").upper(), tau2_reml)(y, v) if len(y) >= 2 else 0.0
    re = _re(y, v, tau2)
    res["fixed"] = _block(fe["estimate"], fe["se"], is_log)
    res["random"] = _block(re["estimate"], re["se"], is_log); res["random"]["tau2"] = tau2
    chosen = re if model == "random" else fe
    res["pooled"] = _block(chosen["estimate"], chosen["se"], is_log)
    sw = chosen["sum_w"]
    for e, w in zip(prep, chosen["weights"]):
        se = math.sqrt(e["vi"])
        res["studies"].append({"study_id": e["study_id"], "design": e.get("design"),
            "yi": e["yi"], "vi": e["vi"], "se": se,
            "estimate": math.exp(e["yi"]) if is_log else e["yi"],
            "ci_lower": math.exp(e["yi"]-_Z*se) if is_log else e["yi"]-_Z*se,
            "ci_upper": math.exp(e["yi"]+_Z*se) if is_log else e["yi"]+_Z*se,
            "weight_pct": 100.0*w/sw, "note": e.get("note"),
            "rob": e.get("rob"), "rob_source": e.get("rob_source")})
    df = len(y)-1; q = _Q(y, v, fe["estimate"])
    het = {"k": len(y), "q": q, "df": df, "q_p": _chi2_sf(q, df) if df > 0 else None,
           "i2": max(0.0, (q-df)/q)*100.0 if (df > 0 and q > 0) else 0.0,
           "h2": (q/df) if df > 0 else None, "tau2": tau2, "tau": math.sqrt(tau2),
           "tau2_method": (tau2_method or "REML").upper(), "tau2_dl": tau2_dl(y, v),
           "prediction_interval": None}
    if len(y) >= 3:
        t = _t_quantile(0.975, len(y)-2); sp = math.sqrt(tau2+re["var"])
        lo, hi = re["estimate"]-t*sp, re["estimate"]+t*sp
        het["prediction_interval"] = {
            "lower": math.exp(lo) if is_log else lo,
            "upper": math.exp(hi) if is_log else hi, "t_df": len(y)-2}
    res["heterogeneity"] = het
    tf = trim_and_fill(y, v, tau2_method=(tau2_method or "REML").upper())
    if tf is not None:
        tf = dict(tf)
        tf["adjusted_estimate"] = math.exp(tf["estimate"]) if is_log else tf["estimate"]
        tf["adjusted_ci_lower"] = math.exp(tf["ci_lower"]) if is_log else tf["ci_lower"]
        tf["adjusted_ci_upper"] = math.exp(tf["ci_upper"]) if is_log else tf["ci_upper"]
    res["publication_bias"] = {"egger": eggers_test(y, v), "trim_fill": tf}
    return res


def _totals(prep):
    def s(k):
        vals = [e[k] for e in prep if e.get(k) is not None]
        return sum(vals) if vals else None
    return {"n_int": s("n_int"), "n_ctrl": s("n_ctrl"),
            "events_int": s("events_int"), "events_ctrl": s("events_ctrl")}


def grade_pooling_inputs(result: dict) -> dict:
    """Flattened convenience view for forest plots / summary tables / exports.

    NOT the GRADE hand-off object -- that is the whole pool_outcome() result (§8).
    This view has no studies[], so it cannot carry risk of bias or per-study weights.
    """
    p = result.get("pooled") or {}; h = result.get("heterogeneity") or {}
    pb = result.get("publication_bias") or {}; eg = pb.get("egger") or {}; tf = pb.get("trim_fill") or {}
    t = result.get("totals") or {}
    return {"k": result.get("k"), "measure": result.get("measure"),
            "design_class": result.get("design_class"),
            "pooled_estimate": p.get("estimate"), "ci_lower": p.get("ci_lower"),
            "ci_upper": p.get("ci_upper"), "i2": h.get("i2"), "tau2": h.get("tau2"),
            "q_p": h.get("q_p"), "prediction_interval": h.get("prediction_interval"),
            # BOTH arms: the intervention arm alone halves the Optimal Information Size.
            "total_n": ((t.get("n_int") or 0.0) + (t.get("n_ctrl") or 0.0)) or None,
            "events_int": t.get("events_int"),
            "events_ctrl": t.get("events_ctrl"), "egger_p": eg.get("p"),
            "egger_adequate_power": eg.get("adequate_power"),
            "trim_fill_n_imputed": tf.get("n_imputed"),
            "trim_fill_adjusted_estimate": tf.get("adjusted_estimate")}
```

**From that document's §11. Quick test sketches (plain `assert`, no framework):**

```python
import math

# --- special functions vs reference tables ---
assert abs(_chi2_sf(3.841, 1) - 0.05) < 1e-3
assert abs(_t_sf2(2.776, 4) - 0.05) < 1e-3
assert abs(_t_quantile(0.975, 10) - 2.228) < 1e-3

# --- effect sizes ---
e = study_effect({"events_int": 15, "n_int": 100, "events_ctrl": 25, "n_ctrl": 100}, "OR")
assert abs(math.exp(e["yi"]) - 0.5294) < 1e-3 and abs(e["vi"] - 0.13176) < 1e-4
e = study_effect({"events_int": 15, "n_int": 100, "events_ctrl": 25, "n_ctrl": 100}, "RR")
assert abs(math.exp(e["yi"]) - 0.6) < 1e-6
e = study_effect({"mean_int": 10, "sd_int": 2, "n_int": 30,
                  "mean_ctrl": 8, "sd_ctrl": 2.5, "n_ctrl": 30}, "SMD")
assert abs(e["yi"] - 0.872) < 2e-3 and e["note"] == "hedges_g"

# --- IRR: from person-time only, never from a 2x2 count table ---
assert study_effect({"events_int": 10, "n_int": 100,                        # counts, no person-time
                     "events_ctrl": 20, "n_ctrl": 100}, "IRR") is None
e = study_effect({"events_int": 10, "time_int": 500,
                  "events_ctrl": 20, "time_ctrl": 480}, "IRR")               # IRR=0.48, vi=0.15
assert abs(math.exp(e["yi"]) - 0.48) < 1e-3 and abs(e["vi"] - 0.15) < 1e-6
assert study_effect({"events_int": 0, "n_int": 50, "events_ctrl": 0, "n_ctrl": 50}, "RR") is None  # double-zero
assert study_effect({"events_int": 0, "n_int": 50, "events_ctrl": 5, "n_ctrl": 50}, "OR")["note"] \
       == "continuity_correction_0.5"

# --- pooling: FE matches manual, weights sum to 100, tau2=0 when homogeneous ---
S = [{"yi": math.log(0.5), "vi": 0.1}, {"yi": math.log(0.8), "vi": 0.05}, {"yi": math.log(0.6), "vi": 0.08}]
r = pool_outcome(S, "OR", tau2_method="DL")
w = [1/0.1, 1/0.05, 1/0.08]; ylg = [math.log(0.5), math.log(0.8), math.log(0.6)]
assert abs(r["fixed"]["estimate"] - math.exp(sum(a*b for a, b in zip(w, ylg))/sum(w))) < 1e-6
assert abs(sum(s["weight_pct"] for s in r["studies"]) - 100.0) < 1e-6
assert r["heterogeneity"]["tau2"] == 0.0  # Q < df

# --- tau2 estimators agree in order of magnitude on a heterogeneous set ---
yh = [0.1, 0.9, 0.3, -0.2, 0.6]; vh = [0.05, 0.04, 0.06, 0.05, 0.03]
assert tau2_dl(yh, vh) > 0 and tau2_reml(yh, vh) > 0 and tau2_pm(yh, vh) > 0

# --- heterogeneous binary RR: Q / I2 ---
studies = [
    {"events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100},
    {"events_int": 8,  "n_int": 80,  "events_ctrl": 25, "n_ctrl": 90},
    {"events_int": 30, "n_int": 120, "events_ctrl": 28, "n_ctrl": 110},
    {"events_int": 5,  "n_int": 60,  "events_ctrl": 18, "n_ctrl": 65}]
h = pool_outcome(studies, "RR", tau2_method="DL")["heterogeneity"]
assert abs(h["q"] - 8.477) < 0.05 and abs(h["i2"] - 64.6) < 1.0

# --- publication bias ---
ys = [-0.4, -0.2, 0.0, 0.2, 0.4]; vs = [0.02, 0.06, 0.1, 0.06, 0.02]
assert abs(eggers_test(ys, vs)["intercept"]) < 1e-6           # symmetric
assert trim_and_fill(ys, vs)["n_imputed"] == 0
ay = [0.1, 0.2, 0.25, 0.5, 0.7, 0.9]; av = [0.02, 0.03, 0.05, 0.1, 0.15, 0.2]
tf = trim_and_fill(ay, av)
assert tf["n_imputed"] >= 1 and tf["side"] == "left"

# --- extraction -> pool bridge (§9): design + measure separation ---
def _rct(a, oc):
    return {"citation_authors": a, "study_type": "RCT",
            "population_comparator": "placebo", "outcomes": [oc]}

studies = [
    _rct("Smith", {"name": "All-cause mortality", "comparison": "d vs p", "timing": "12m",
                   "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}),
    _rct("Jones", {"name": "all cause mortality", "comparison": "d vs p", "timing": "12m",
                   "effect_metric": "RR", "effect_estimate": 0.6, "ci_lower": 0.4, "ci_upper": 0.9}),
    _rct("Kim",   {"name": "all-cause mortality", "comparison": "d vs p", "timing": "12m",
                   "effect_metric": "OR", "effect_estimate": 0.55}),  # excluded from RR body
    {"citation_authors": "Park", "study_type": "Cohort Study", "population_comparator": "placebo",
     "outcomes": [{"name": "All cause mortality", "comparison": "d vs p", "timing": "12m",
                   "effect_metric": "RR", "effect_estimate": 0.7}]},   # separate (nrs) body
]
bodies = pool_extractions(studies)
rct = [b for b in bodies if b["design_class"] == "rct"][0]
assert rct["measure"] == "RR" and rct["k"] == 2          # raw + reported RR; OR excluded
assert any("Kim" in e for e in rct["excluded"])
assert {b["design_class"] for b in bodies} == {"rct", "nrs"}  # designs never share a body
assert rct["pooled"]["design_class"] == "rct"        # reaches the result, not just the wrapper
assert rct["pooled"]["outcome_name"] == rct["outcome_name"]

# --- risk of bias rides on the study record, so a drop cannot misalign it (§7) ---
S_rob = [{"yi": math.log(0.5), "vi": 0.10, "study_id": "A", "rob": "Low", "rob_source": "tool"},
         {"yi": math.log(0.8), "vi": 0.05, "study_id": "B", "rob": "Some concerns"},
         {"yi": 0.0, "vi": 0.0, "study_id": "C", "rob": "High"},    # dropped: vi <= 0
         {"yi": math.log(0.6), "vi": 0.08, "study_id": "D"}]        # no label
r_rob = pool_outcome(S_rob, "RR")
assert [s["study_id"] for s in r_rob["studies"]] == ["A", "B", "D"]   # C dropped, order shifts
assert [s["rob"] for s in r_rob["studies"]] == ["Low", "Some concerns", None]
assert r_rob["studies"][2]["rob_source"] == "missing"     # absent label -> "missing"
assert r_rob["studies"][0]["rob_source"] == "tool"        # supplied source preserved
# The whole point: after the drop, B's label is still paired with B's weight.
# A positional list would now be reading "High" against B.
assert r_rob["studies"][1]["rob"] == "Some concerns" and r_rob["studies"][1]["weight_pct"] > 0
assert abs(sum(s["weight_pct"] for s in r_rob["studies"]) - 100.0) < 1e-6
# every measure path carries it -- _counts is the single funnel
assert study_effect({"events_int": 15, "n_int": 100, "events_ctrl": 25,
                     "n_ctrl": 100, "rob": "Low"}, "OR")["rob"] == "Low"
assert study_effect({"mean_int": 10, "sd_int": 2, "n_int": 30, "mean_ctrl": 8,
                     "sd_ctrl": 2.5, "n_ctrl": 30, "rob": "High"}, "SMD")["rob"] == "High"
assert study_effect({"events_int": 10, "time_int": 500, "events_ctrl": 20,
                     "time_ctrl": 480, "rob": "Serious"}, "IRR")["rob"] == "Serious"

# --- per-(study x outcome) resolution, precedence ladder (§9.3) ---
_s = {"study_id": "Smith", "study_type": "RCT",
      "rob": "High",                                        # tier 3, study-level
      "rob_by_outcome": {"All-cause mortality": "Low"}}      # tier 2, outcome-specific
assert resolve_rob(_s, {"name": "All-cause mortality"},
                   "All-cause mortality") == ("Low", "user_outcome")     # tier 2 beats tier 3
assert resolve_rob(_s, {"name": "Serious adverse events"},
                   "Serious adverse events") == ("High", "user_study")   # falls back
assert resolve_rob(_s, {"name": "All-cause mortality", "rob": "Some concerns"},
                   "All-cause mortality") == ("Some concerns", "user_outcome")  # tier 1 wins
assert resolve_rob({"study_id": "X"}, {"name": "y"}, "y") == (None, "missing")
assert resolve_rob({"rob": "Low", "rob_source": "tool"}, {}, None) == ("Low", "tool")
assert resolve_rob({"rob_by_outcome": {"all cause  MORTALITY": "Low"}},
                   {"name": "All-cause Mortality"}, "All-cause Mortality")[0] == "Low"
assert resolve_rob({"rob": "   "}, {}, None) == (None, "missing")   # blank is not a label

# --- attach_rob is the injection seam ---
_att = attach_rob([{"study_id": "S1", "outcomes": []}],
                  [{"study_id": "S1", "outcome": "Death from any cause",
                    "rob": "Low", "rob_source": "tool"}])
assert _att[0]["rob_by_outcome"] == {"Death from any cause": "Low"}
assert resolve_rob(_att[0], {"name": "Death from any cause"},
                   "Death from any cause") == ("Low", "tool")

# --- harmonization must canonicalize the RoB keys too, or an appraised body
#     reads as unappraised (§9.7); grouping must actually read canonical_outcome ---
_h_in = [
  {"study_id": "S1", "study_type": "RCT", "rob_by_outcome": {"Death from any cause": "Low"},
   "outcomes": [{"name": "Death from any cause", "comparison": "d vs p", "timing": "12m",
                 "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}]},
  {"study_id": "S2", "study_type": "RCT", "rob_by_outcome": {"Overall mortality": "High"},
   "outcomes": [{"name": "Overall mortality", "comparison": "d vs p", "timing": "12m",
                 "events_int": 8, "n_int": 80, "events_ctrl": 25, "n_ctrl": 90}]}]
_h, _rep = harmonize_by_targets(_h_in, [{"canonical": "All-cause mortality",
    "aliases": ["Death from any cause", "Overall mortality"]}])
_bodies = pool_extractions(_h)
assert len(_bodies) == 1                                   # synonyms pooled into one body
assert _bodies[0]["outcome_name"] == "All-cause mortality" # grouping reads canonical_outcome
_ps = _bodies[0]["pooled"]["studies"]
assert {s["study_id"]: s["rob"] for s in _ps} == {"S1": "Low", "S2": "High"}
assert all(s["rob_source"] == "user_outcome" for s in _ps)  # matched via the canonical key

# --- the flat convenience view totals BOTH arms (OIS would otherwise halve) ---
_g = grade_pooling_inputs(pool_outcome(
    [{"events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100},
     {"events_int": 8,  "n_int": 80,  "events_ctrl": 25, "n_ctrl": 90}], "RR"))
assert _g["total_n"] == 370          # 180 intervention + 190 comparator, not 180

print("all pooling self-checks passed")
```

### 12.9 Per-study evidence table (Table 2) — from `table2_evidence_table_shareable.md`

**From that document's §10. Reference implementation — single self-contained Python module:**

```python
"""
table2_reference.py — Reference implementation for the "Table 2" (per-study
evidence table) build.

Table 2 is the PER-STUDY EVIDENCE TABLE: one row = (study x outcome x comparison x
timepoint), transcribing each study's REPORTED results (intervention vs comparator,
per outcome, with instrument, timepoint, effect + CI/p). It is almost entirely DIRECT
EXTRACTION. Pooling / GRADE / meta-analysis belong to a different table and are
explicitly OUT OF SCOPE here.

This module implements every part of Table 2 that is NOT pure extraction:
  * building the study_id label,
  * canonicalizing effect-measure names + their families / null values,
  * inferring the direction of effect from an estimate + CI,
  * parsing a free-text "effect cell" into structured stats (with a faithful p-operator),
  * filling ONLY the missing stat from the reported ones (never overwriting/fabricating),
  * mapping a risk-of-bias overall label onto a 3-level quality band (tool-routed),
  * seeding a 1-element outcomes[] from single-outcome universal fields,
  * exploding + de-duplicating rows,
  * the dual-mode merge of injected vs self-extracted tags,
  * the pure-Python top-level assembler (NO model call), and
  * a tiny orchestrator showing isolation-mode vs injected-mode.

Design rules honored throughout:
  - NEVER fabricate: absent parts stay None; we only DERIVE a stat when there is
    something valid to derive it from, and we record every derived value in a
    `derived` set so a renderer can show it differently.
  - NEVER distort a reported value: "p<0.001" is carried as (p_value=0.001,
    p_operator="lt") — the inequality is preserved, not collapsed to "=".
  - Direction semantics depend on whether the outcome is desirable (survival,
    response) or adverse (mortality, symptom burden). We take an
    `outcome_favorable_direction` hint and document the default explicitly.
  - Standard library only. Any LLM use is an injected `llm_call` callable — but the
    vast majority of this module is pure Python with no model calls at all.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# 1. Study id
# ---------------------------------------------------------------------------

def build_study_id(authors: Any, year: Any) -> str:
    """Build a "First-author et al., YYYY" study label.

    Handles author input as a list or a delimited string. Rules:
      * 1 author           -> "Smith, YYYY"
      * 2 authors          -> "Smith & Jones, YYYY"
      * 3+ authors         -> "Smith et al., YYYY"
      * missing/blank year -> the year (and its comma) is omitted, e.g. "Smith et al."
      * no usable authors   -> "Unknown study" (+ year if present)

    We only ever use each author's SURNAME. This is a display convenience, not a
    citation parser — it must never fabricate an author who is not present.
    """
    names = _normalize_authors(authors)
    yr = _clean_year(year)
    yr_suffix = f", {yr}" if yr else ""

    if not names:
        return f"Unknown study{yr_suffix}"
    if len(names) == 1:
        core = _surname(names[0])
    elif len(names) == 2:
        core = f"{_surname(names[0])} & {_surname(names[1])}"
    else:
        core = f"{_surname(names[0])} et al."
    return f"{core}{yr_suffix}"


def _normalize_authors(authors: Any) -> list[str]:
    """Coerce list/str author input into a clean list of non-empty name strings.

    Delimiter precedence avoids the Vancouver double-count trap: a "Family, Given;
    Family, Given" list must split on ';' (or ' and '/'&'), NOT on the commas that
    separate each surname from its initials. We only fall back to comma-splitting
    when no stronger delimiter is present; a lone "Smith, JQ" then yields one author
    because the initials fragment is filtered out.
    """
    if authors is None:
        return []
    if isinstance(authors, (list, tuple)):
        raw_list = [str(a) for a in authors]
    else:
        s = str(authors)
        if ";" in s:
            raw_list = re.split(r"\s*;\s*", s)
        elif re.search(r"\band\b|&", s):
            raw_list = re.split(r"\s*(?:\band\b|&)\s*", s)
        else:
            raw_list = re.split(r"\s*,\s*", s)
    out: list[str] = []
    for a in raw_list:
        a = a.strip()
        # Drop initials-only fragments left over from splitting a "Family, Initials"
        # string (e.g. the "JQ" in "Smith, JQ").
        if a and not re.fullmatch(r"[A-Z]\.?(?:\s*[A-Z]\.?)*", a):
            out.append(a)
    return out


def _surname(name: str) -> str:
    """Best-effort surname extraction from a single author string."""
    name = name.strip().strip(".")
    if not name:
        return ""
    if "," in name:                       # "Family, Given/Initials" -> family before comma
        return name.split(",")[0].strip()
    parts = name.split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]\.?(?:[A-Z]\.?)*", parts[-1]):
        return parts[0]                   # "Smith JQ" -> "Smith"
    return parts[-1]                       # "Jane Q. Smith" -> "Smith"


def _clean_year(year: Any) -> str:
    """Return a 4-digit year string if one can be recovered, else ""."""
    if year is None:
        return ""
    m = re.search(r"(1[89]\d{2}|20\d{2})", str(year))
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 2. Effect-measure families + canonicalization
# ---------------------------------------------------------------------------

# family in {"ratio", "difference", "time_to_event", "narrative", "unknown"}.
# time_to_event (HR) is mathematically a ratio; we keep it distinct only so callers
# can label it, and null_value_for treats it as a ratio (null = 1, log scale).
METRIC_FAMILIES: dict[str, str] = {
    "HR": "time_to_event",
    "OR": "ratio",
    "RR": "ratio",
    "IRR": "ratio",
    "MD": "difference",
    "SMD": "difference",
    "RD": "difference",
}

# Synonym -> canonical token. Matched case-insensitively after whitespace collapse
# and apostrophe normalization. "ARD"/"absolute risk difference" collapse to RD.
_METRIC_SYNONYMS: dict[str, str] = {
    "hr": "HR", "hazard ratio": "HR",
    "or": "OR", "odds ratio": "OR",
    "rr": "RR", "risk ratio": "RR", "relative risk": "RR",
    "irr": "IRR", "rate ratio": "IRR", "incidence rate ratio": "IRR",
    "md": "MD", "mean difference": "MD",
    "wmd": "MD", "weighted mean difference": "MD",
    "smd": "SMD",
    "standardized mean difference": "SMD", "standardised mean difference": "SMD",
    "cohen's d": "SMD", "cohens d": "SMD",
    "hedges g": "SMD", "hedges' g": "SMD", "hedges's g": "SMD",
    "rd": "RD", "risk difference": "RD",
    "absolute risk difference": "RD", "absolute difference": "RD", "ard": "RD",
}


def canonicalize_metric(raw: Any) -> tuple[Optional[str], str]:
    """Map a raw effect-measure name onto (canonical_metric, family).

      * Recognized synonym -> ("HR"/"OR"/"RR"/"IRR"/"MD"/"SMD"/"RD", family).
      * A narrative marker ("narrative", "NR", "not reported", "qualitative") ->
        (None, "narrative") so the renderer knows there is no numeric effect.
      * Empty / None        -> (None, "unknown").
      * Anything else       -> (raw_stripped, "unknown").
    """
    if raw is None:
        return None, "unknown"
    s = re.sub(r"\s+", " ", str(raw)).strip()
    if not s:
        return None, "unknown"

    low = s.lower().strip(".").replace("’", "'")   # normalize curly apostrophes

    narrative_markers = {
        "narrative", "qualitative", "descriptive", "nr", "not reported",
        "not estimable", "ne", "n/a", "na",
    }
    if low in narrative_markers:
        return None, "narrative"
    if low in _METRIC_SYNONYMS:
        canon = _METRIC_SYNONYMS[low]
        return canon, METRIC_FAMILIES[canon]

    upper = s.upper()
    if upper in METRIC_FAMILIES:            # a bare canonical token, any case
        return upper, METRIC_FAMILIES[upper]
    return s, "unknown"


def null_value_for(family: str) -> Optional[float]:
    """No-effect null value: ratio/time_to_event -> 1.0; difference -> 0.0; else None."""
    if family in ("ratio", "time_to_event"):
        return 1.0
    if family == "difference":
        return 0.0
    return None


# ---------------------------------------------------------------------------
# 3. Direction of effect
# ---------------------------------------------------------------------------

FAVOURS_INTERVENTION = "favours_intervention"
FAVOURS_COMPARATOR = "favours_comparator"
NO_DIFFERENCE = "no_difference"
NOT_ESTIMABLE = "not_estimable"

DIRECTIONS = (FAVOURS_INTERVENTION, FAVOURS_COMPARATOR, NO_DIFFERENCE, NOT_ESTIMABLE)


def infer_direction(
    family: str,
    estimate: Optional[float],
    ci_lower: Optional[float],
    ci_upper: Optional[float],
    reported_direction: Optional[str] = None,
    outcome_favorable_direction: str = "lower",
) -> str:
    """Infer which arm an effect favours. Returns one of DIRECTIONS.

    DIRECTION SEMANTICS DEPEND ON THE OUTCOME.
    ------------------------------------------
    A value below the null does not universally mean "intervention is better"; it
    depends on whether the outcome is DESIRABLE or ADVERSE:

      * outcome_favorable_direction="lower"  (DEFAULT): a SMALLER value is good for
        the intervention — the right default for the ADVERSE / symptom-burden
        outcomes commonly tabulated (mortality, relapse, fatigue score, pain,
        progression), where HR/OR/RR < 1 or MD < 0 => favours_intervention.
      * outcome_favorable_direction="higher": a LARGER value is good for the
        intervention (survival probability, response rate, QoL score). An estimate
        ABOVE the null then favours the intervention.
      * "neutral"/None: desirability unknown. We can still detect whether the CI
        excludes the null, but we do NOT guess which arm wins — we return
        not_estimable and defer to reported_direction / the model's source_quote.

    Boundary rule (documented convention): a CI bound sitting exactly ON the null
    (ratio CI 1.0-1.5, difference CI 0.0-0.5) is treated as no_difference — touching
    the null is the standard non-significant reading.

    Reconciliation: a confidently parsed reported_direction WINS (authors know their
    own sign conventions); otherwise the CI/estimate computation is used; otherwise
    not_estimable.
    """
    reported = _parse_reported_direction(reported_direction)

    null = null_value_for(family)
    if family in ("narrative", "unknown") or null is None:
        return reported or NOT_ESTIMABLE

    lo, hi = _order_ci(ci_lower, ci_upper)
    computed: Optional[str] = None
    if lo is not None and hi is not None:
        if lo > null and hi > null:
            computed = _side_to_favour("above", outcome_favorable_direction)
        elif lo < null and hi < null:
            computed = _side_to_favour("below", outcome_favorable_direction)
        else:
            computed = NO_DIFFERENCE          # CI includes/touches null => not significant
    elif estimate is not None:
        if estimate > null:
            computed = _side_to_favour("above", outcome_favorable_direction)
        elif estimate < null:
            computed = _side_to_favour("below", outcome_favorable_direction)
        else:
            computed = NO_DIFFERENCE

    if reported is not None:
        return reported
    return computed if computed is not None else NOT_ESTIMABLE


def _side_to_favour(side: str, favorable_direction: Optional[str]) -> str:
    """Translate 'above'/'below' the null into which arm it favours."""
    fd = (favorable_direction or "").strip().lower()
    if fd == "lower":
        return FAVOURS_INTERVENTION if side == "below" else FAVOURS_COMPARATOR
    if fd == "higher":
        return FAVOURS_INTERVENTION if side == "above" else FAVOURS_COMPARATOR
    return NOT_ESTIMABLE                       # desirability unknown -> cannot assign an arm


def _parse_reported_direction(reported: Optional[str]) -> Optional[str]:
    """Normalize a free-text reported direction into a canonical constant, or None.

    Short tokens ("ns", "ne", "nr", "na", "null") are matched only as WHOLE WORDS —
    substring matching would mislabel benign words ("consistent" contains "ns",
    "generated" contains "ne"), and because reported-direction wins, that would
    silently flip a significant result to no_difference.
    """
    if not reported:
        return None
    r = str(reported).strip().lower()
    if r in DIRECTIONS:
        return r
    # Unambiguous multi-word phrases: substring is safe.
    if any(k in r for k in ("favours intervention", "favors intervention",
                            "favour treatment", "favor treatment",
                            "intervention better", "in favour of intervention",
                            "in favor of intervention")):
        return FAVOURS_INTERVENTION
    if any(k in r for k in ("favours comparator", "favors comparator",
                            "favours control", "favors control",
                            "control better", "comparator better", "placebo better")):
        return FAVOURS_COMPARATOR
    if any(k in r for k in ("no difference", "no significant",
                            "non-significant", "not significant")):
        return NO_DIFFERENCE
    if any(k in r for k in ("not estimable", "not reported", "cannot be estimated")):
        return NOT_ESTIMABLE
    # Short tokens only as whole words.
    tokens = set(re.findall(r"[a-z']+", r))
    if tokens & {"ns", "null"}:
        return NO_DIFFERENCE
    if tokens & {"ne", "nr", "na"}:
        return NOT_ESTIMABLE
    return None


def _order_ci(lo: Optional[float], hi: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Return CI bounds in (lower, upper) order, tolerating swapped inputs."""
    if lo is not None and hi is not None and lo > hi:
        return hi, lo
    return lo, hi


# ---------------------------------------------------------------------------
# 4. Parsing a free-text effect cell
# ---------------------------------------------------------------------------

_NUM = r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?|[-+]?\d*\.\d+|[-+]?\d+"
_CI_SEP = r"\s*(?:–|—|-|to|,)\s*"    # en/em dash, hyphen, "to", comma
_P_OP = {"<": "lt", "<=": "le", "≤": "le", ">": "gt", ">=": "ge", "≥": "ge", "=": "eq"}


def parse_effect_cell(text: Any) -> Optional[dict[str, Any]]:
    """Parse a reported effect string into
    {estimate, ci_lower, ci_upper, p_value, p_operator}.

    Handles, e.g.:
      "0.72 (95% CI 0.55-0.94), p=0.01"        -> est .72, CI .55-.94, p=0.01 (eq)
      "HR 0.72 [0.55, 0.94]"                    -> est .72, CI .55-.94
      "MD -3.4 (-5.1 to -1.7); p < 0.001"       -> est -3.4, CI, p=0.001 (lt)
      "HR 0.68, 95% CI 0.55 to 0.94"            -> unbracketed CI fallback
      "p<0.001"                                 -> only p populated (operator lt)

    Rules:
      * Absent parts stay None. We NEVER guess a missing bound or p.
      * The p-value requires an explicit comparator ('p=' / 'p<' / 'p<='); a bare
        'p 0.7' or an embedded 'grp2'/'group1' is NOT read as a p-value, and any
        captured value outside (0, 1] is rejected.
      * Thousands separators are stripped; en/em-dash / hyphen / "to" / comma are
        all accepted CI separators.
      * A leading metric token (HR/OR/RR/...) is ignored here — canonicalize_metric
        owns the metric name; this function only extracts numbers.
      * Returns None if the text contains no parseable value at all.
    """
    if text is None:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    if not s:
        return None

    result: dict[str, Any] = {
        "estimate": None, "ci_lower": None, "ci_upper": None,
        "p_value": None, "p_operator": None,
    }
    work = s

    # --- p-value: boundary before 'p', MANDATORY comparator, value in (0, 1].
    p_match = re.search(
        r"(?:^|[^A-Za-z])[pP]\s*(<=|>=|≤|≥|<|>|=)\s*(" + _NUM + r")", s
    )
    if p_match:
        pv = _to_float(p_match.group(2))
        if pv is not None and 0.0 < pv <= 1.0:
            result["p_value"] = pv
            result["p_operator"] = _P_OP.get(p_match.group(1), "eq")
            work = (work[: p_match.start()] + " " + work[p_match.end():]).strip()

    # --- CI: a bracketed pair "(lo <sep> hi)" or "[lo <sep> hi]".
    ci_match = re.search(
        r"[\(\[]\s*(?:\d{1,3}%?\s*(?:CI|confidence interval)[:\s]*)?"
        r"(" + _NUM + r")" + _CI_SEP + r"(" + _NUM + r")\s*[\)\]]",
        work, flags=re.IGNORECASE,
    )
    if not ci_match:
        # Fallback: unbracketed "95% CI a to b" — requires the CI keyword as an anchor.
        ci_match = re.search(
            r"(?:\d{1,3}\s*%?\s*)?(?:CI|confidence interval)[:\s]*"
            r"(" + _NUM + r")" + _CI_SEP + r"(" + _NUM + r")",
            work, flags=re.IGNORECASE,
        )
    if ci_match:
        lo = _to_float(ci_match.group(1))
        hi = _to_float(ci_match.group(2))
        result["ci_lower"], result["ci_upper"] = _order_ci(lo, hi)
        work = (work[: ci_match.start()] + " " + work[ci_match.end():]).strip()

    # --- estimate: the first standalone number left in the (metric-stripped) text.
    est_match = re.search(_NUM, work)
    if est_match:
        result["estimate"] = _to_float(est_match.group(0))

    if all(v is None for v in result.values()):
        return None
    return result


def _to_float(token: Any) -> Optional[float]:
    """Parse a numeric token (optional thousands separators) into float, or None."""
    if token is None:
        return None
    t = str(token).replace(",", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _split_p(value: Any) -> tuple[Optional[float], Optional[str]]:
    """Split a reported p value (which may carry an inequality) into (float, operator).

    "<0.001" -> (0.001, "lt");  "0.03" -> (0.03, "eq");  "p<=0.05" -> (0.05, "le");
    "NS"/"" -> (None, None). Values outside (0, 1] are rejected.
    """
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        v = float(value)
        return (v, "eq") if 0.0 < v <= 1.0 else (None, None)
    s = str(value).strip()
    m = re.search(r"(<=|>=|≤|≥|<|>|=)?\s*(" + _NUM + r")", s)
    if not m:
        return None, None
    v = _to_float(m.group(2))
    if v is None or not (0.0 < v <= 1.0):
        return None, None
    return v, _P_OP.get(m.group(1) or "=", "eq")


# ---------------------------------------------------------------------------
# 5. Statistical reconciliation (fill ONLY missing values)
# ---------------------------------------------------------------------------

_Z_95 = 1.959964    # two-sided 95% normal quantile


def _phi(x: float) -> float:
    """Standard normal CDF Phi(x) via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inv_phi(p: float) -> Optional[float]:
    """Inverse standard normal CDF via Acklam's rational approximation (no scipy)."""
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)


def _z_from_p(p: float) -> Optional[float]:
    """Two-sided p -> |Z|. Solves p = 2*(1 - Phi(z)); None for p outside (0, 1)."""
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    return _inv_phi(1.0 - p / 2.0)


def reconcile_stats(
    estimate: Optional[float],
    ci_lower: Optional[float],
    ci_upper: Optional[float],
    p_value: Optional[float],
    family: str,
    p_operator: Optional[str] = "eq",
) -> dict[str, Any]:
    """Fill ONLY missing stats from the reported ones. Never overwrite a reported value.

    Returns {estimate, ci_lower, ci_upper, p_value, p_operator, se, derived} where
    `derived` names which of {ci_lower, ci_upper, p_value} we computed.

    Math (z = 1.959964 for two-sided 95%):
      * ratio / time_to_event -> LOG scale:
          - CI present:            SE = (ln(hi) - ln(lo)) / (2z)
          - CI missing, est + EXACT p:  SE = |ln(est)| / z_from_p(p);
                                        CI = exp(ln(est) +/- z*SE)
          - p missing, CI/SE present:   Z = ln(est) / SE; p = 2*(1 - Phi(|Z|))
      * difference -> identical arithmetic with the identity function instead of ln.

    Guards (skip that derivation, inputs unchanged):
      * ratio/time_to_event with non-positive estimate or CI bound -> cannot take a log.
      * p missing/<=0/>=1, or p is a BOUND (operator lt/gt/le/ge) -> cannot invert to
        an exact Z, so p is not used to derive an SE.
      * a null-valued estimate (HR/RR/OR = 1, MD = 0) with only a p -> genuinely
        under-determined (ln(1)=0, so SE = 0/z); we intentionally leave the CI blank
        rather than fabricate. This is a documented limitation, not an oversight.
      * nothing to derive from -> no fabrication.
    """
    is_log = family in ("ratio", "time_to_event")
    is_diff = family == "difference"
    derived: set[str] = set()
    out: dict[str, Any] = {
        "estimate": estimate, "ci_lower": ci_lower, "ci_upper": ci_upper,
        "p_value": p_value, "p_operator": p_operator if p_value is not None else None,
        "se": None, "derived": derived,
    }
    if not (is_log or is_diff):
        return out                              # narrative/unknown: no arithmetic

    ci_lower, ci_upper = _order_ci(ci_lower, ci_upper)
    out["ci_lower"], out["ci_upper"] = ci_lower, ci_upper

    if is_log:
        def fwd(v: Optional[float]) -> Optional[float]:
            return math.log(v) if (v is not None and v > 0) else None
        inv = math.exp
    else:
        def fwd(v: Optional[float]) -> Optional[float]:
            return v
        def inv(v: float) -> float:
            return v

    t_est = fwd(estimate) if estimate is not None else None
    t_lo = fwd(ci_lower) if ci_lower is not None else None
    t_hi = fwd(ci_upper) if ci_upper is not None else None

    if is_log:
        if estimate is not None and t_est is None:
            return out                          # non-positive estimate on log scale
        if (ci_lower is not None and t_lo is None) or (ci_upper is not None and t_hi is None):
            t_lo = t_hi = None                  # non-positive CI bound -> drop CI reasoning

    se: Optional[float] = None
    if t_lo is not None and t_hi is not None:                       # (a) SE from CI
        se = (t_hi - t_lo) / (2.0 * _Z_95)
    if se is None and t_est is not None and p_value is not None and p_operator == "eq":
        z_p = _z_from_p(p_value)                                    # (b) SE from est + EXACT p
        if z_p and z_p != 0 and abs(t_est) > 0:
            se = abs(t_est) / z_p
    out["se"] = se

    if se is not None and t_est is not None and ci_lower is None and ci_upper is None:
        lo, hi = _order_ci(inv(t_est - _Z_95 * se), inv(t_est + _Z_95 * se))
        out["ci_lower"], out["ci_upper"] = lo, hi
        derived.update({"ci_lower", "ci_upper"})

    if p_value is None and se is not None and se > 0 and t_est is not None:
        z = t_est / se
        p = 2.0 * (1.0 - _phi(abs(z)))
        out["p_value"] = min(max(p, 1e-300), 1.0 - 1e-16)          # never exactly 0/1
        out["p_operator"] = "eq"
        derived.add("p_value")

    return out


# ---------------------------------------------------------------------------
# 6. Quality rating (risk-of-bias overall -> 3-level quality band)
# ---------------------------------------------------------------------------

_QUALITY_HIGH = "High"
_QUALITY_INTERMEDIATE = "Intermediate"
_QUALITY_LOW = "Low"


def map_quality_rating(rob_overall: Any, rob_tool: Optional[str] = None) -> Optional[str]:
    """Map a risk-of-bias overall label onto a 3-level study-quality band.

    Returns "High" | "Intermediate" | "Low" | None.

    THE MAPPING IS AN INVERSION for risk-of-bias tools: LOW risk of bias => HIGH
    quality. AMSTAR-2 is NOT inverted — it reports a *confidence* rating that already
    runs in the quality direction (High confidence => High quality). Because "High"
    and "Low" mean opposite things across the two scales, `rob_tool` is required to
    resolve a bare "High"/"Low"; without it, only unambiguous labels resolve and a
    bare "High"/"Low" returns None rather than risk a silent inversion.

      RoB 2:        Low->High; "Some concerns"->Intermediate; High->Low
      ROBINS-I:     Low->High; Moderate->Intermediate; Serious/Critical->Low
      QUADAS(-2/3): Low->High; Unclear/"Insufficient information"->Intermediate; High->Low
      AMSTAR-2:     High->High; Moderate->Intermediate; Low/"Critically low"->Low
    """
    if rob_overall is None:
        return None
    label = re.sub(r"\s+", " ", str(rob_overall)).strip().lower()
    tool = (rob_tool or "").strip().lower()

    if label == "moderate":                     # Intermediate on every scale — safe
        return _QUALITY_INTERMEDIATE

    if tool:
        if "amstar" in tool:                    # confidence scale (not inverted)
            if label == "high":
                return _QUALITY_HIGH
            if label in ("low", "critically low"):
                return _QUALITY_LOW
            return None
        # RoB 2 / ROBINS-I / QUADAS — inverted bias scale
        if label == "low":
            return _QUALITY_HIGH
        if label in ("some concerns", "some concern", "unclear", "insufficient information"):
            return _QUALITY_INTERMEDIATE
        if label in ("high", "serious", "critical"):
            return _QUALITY_LOW
        return None

    # No tool: only unambiguous labels resolve.
    if label == "critically low":               # exists only on the AMSTAR-2 scale
        return _QUALITY_LOW
    if label in ("some concerns", "some concern", "unclear", "insufficient information"):
        return _QUALITY_INTERMEDIATE
    if label in ("serious", "critical"):        # exist only on the ROBINS-I scale
        return _QUALITY_LOW
    return None                                  # bare high/low without a tool -> ambiguous


# ---------------------------------------------------------------------------
# 7. Seeding outcomes[] from single-outcome universal fields
# ---------------------------------------------------------------------------

def seed_outcomes_from_universal(tags: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a 1-element outcomes[] from single-outcome universal fields (no model call).

      primary_outcome_definition   -> name
      primary_outcome_measurement  -> instrument
      primary_outcome_timing       -> timing   (falls back to follow_up_duration)
      key_findings_effect_estimate -> effect_estimate
      key_findings_metric          -> effect_metric
      key_findings_ci_lower/upper  -> ci_lower/ci_upper
      key_findings_pvalue          -> p_value (+ p_operator, parsed from any inequality)
      key_findings_direction       -> direction (reported; else inferred at explode time)

    Returns [] when there is no primary-outcome signal at all (no name AND no effect).
    `comparison` is left None here — it is filled from population_comparator at explode.
    Seeded rows carry no verbatim source_quote and no per-outcome confidence (both
    None): those only exist once the extraction pass has run.
    """
    name = _get(tags, "primary_outcome_definition")
    metric = _get(tags, "key_findings_metric")
    estimate = _coerce_num(_get(tags, "key_findings_effect_estimate"))
    if not name and estimate is None and not metric:
        return []

    p_val, p_op = _split_p(_get(tags, "key_findings_pvalue"))
    return [{
        "name": name,
        "instrument": _get(tags, "primary_outcome_measurement"),
        "timing": _get(tags, "primary_outcome_timing") or _get(tags, "follow_up_duration"),
        "comparison": None,
        "effect_metric": metric,
        "effect_estimate": estimate,
        "direction": _get(tags, "key_findings_direction"),
        "ci_lower": _coerce_num(_get(tags, "key_findings_ci_lower")),
        "ci_upper": _coerce_num(_get(tags, "key_findings_ci_upper")),
        "p_value": p_val,
        "p_operator": p_op,
        "source_quote": None,
        "confidence": None,
        "is_subgroup": False,
        "subgroup_label": None,
    }]


# ---------------------------------------------------------------------------
# 8. N cell + display composers
# ---------------------------------------------------------------------------

def format_n_cell(study_type: Any, sample_size_total: Any, included_studies_n: Any) -> str:
    """Compose the "N" sub-cell, whose meaning depends on the design.

    For a systematic review / meta-analysis "study" row, N is a count of pooled
    STUDIES (k), optionally with pooled participants: "k=12 (N=3450)". For a primary
    study it is the participant total. This keeps the reader from confusing k with N.
    """
    st = (str(study_type) or "").lower()
    k = _coerce_num(included_studies_n)
    n = _coerce_num(sample_size_total)
    is_review = "systematic review" in st or "meta-analysis" in st or "meta analysis" in st
    if is_review and k is not None:
        return f"k={_fmt_num(k)} (N={_fmt_num(n)})" if n is not None else f"k={_fmt_num(k)}"
    if n is not None:
        return _fmt_num(n)
    return f"k={_fmt_num(k)}" if k is not None else ""


# ---------------------------------------------------------------------------
# 9. Exploding rows + dedupe
# ---------------------------------------------------------------------------

def explode_rows(
    study_level: dict[str, Any],
    outcomes: Iterable[dict[str, Any]],
    provenance: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Explode one study into flat Table 2 rows — one per outcome object.

    Study-level fields are DENORMALIZED (repeated) across every outcome row so each
    row is display-complete. For each outcome we canonicalize the metric + family,
    reconcile stats (filling only the missing CI or p), infer direction, compose the
    `result_effect` / `result_ci_p` display strings, and carry the subgroup flag +
    provenance. Effects are reported AS REPORTED — never pooled. Narrative-only
    outcomes (no numeric estimate) fall back to their text; CI/p stay blank.
    """
    study_id = study_level.get("study_id") or build_study_id(
        study_level.get("citation_authors"), study_level.get("citation_year"))
    default_comparison = study_level.get("population_comparator")
    n_cell = format_n_cell(
        study_level.get("study_type") or study_level.get("design"),
        study_level.get("sample_size_total"),
        study_level.get("included_studies_n"),
    )

    rows: list[dict[str, Any]] = []
    for oc in outcomes:
        canon_metric, family = canonicalize_metric(oc.get("effect_metric"))
        comparison = oc.get("comparison") or default_comparison
        p_val, p_op = _read_p(oc)

        rec = reconcile_stats(
            _coerce_num(oc.get("effect_estimate")),
            _coerce_num(oc.get("ci_lower")),
            _coerce_num(oc.get("ci_upper")),
            p_val, family, p_op,
        )
        direction = infer_direction(
            family, rec["estimate"], rec["ci_lower"], rec["ci_upper"],
            reported_direction=oc.get("direction"),
            outcome_favorable_direction=oc.get("favorable_direction", "lower"),
        )
        rows.append({
            # study-level (denormalized)
            "study_id": study_id,
            "design": study_level.get("study_type") or study_level.get("design"),
            "population": study_level.get("population_participants"),
            "n": n_cell,
            "eligibility_threshold": study_level.get("eligibility_threshold"),
            "intervention": study_level.get("population_intervention_exposure"),
            "comparator": comparison,
            "statistical_method": study_level.get("statistical_method"),
            "quality_rating": study_level.get("quality_rating"),
            # outcome-level
            "outcome_name": oc.get("name"),
            "outcome_instrument": oc.get("instrument"),
            "outcome_timing": oc.get("timing"),
            "comparison": comparison,
            "effect_metric": canon_metric,
            "effect_family": family,
            "effect_estimate": rec["estimate"] if rec["estimate"] is not None
                               else oc.get("effect_estimate"),
            "ci_lower": rec["ci_lower"],
            "ci_upper": rec["ci_upper"],
            "p_value": rec["p_value"],
            "p_operator": rec["p_operator"],
            "direction": direction,
            "derived_stats": sorted(rec["derived"]),
            "is_subgroup": bool(oc.get("is_subgroup") or oc.get("subgroup")),
            "subgroup_label": oc.get("subgroup_label") or oc.get("subgroup"),
            "source_quote": oc.get("source_quote"),
            "confidence": oc.get("confidence"),
            "provenance": provenance,
            # composed display strings
            "result_effect": _compose_effect(canon_metric, rec["estimate"], direction, oc),
            "result_ci_p": _compose_ci_p(rec["ci_lower"], rec["ci_upper"],
                                         rec["p_value"], rec["p_operator"]),
        })
    return rows


def _read_p(oc: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """Read (p_value, p_operator) from an outcome object, tolerating a string p."""
    if oc.get("p_operator") and oc.get("p_value") is not None:
        return _coerce_num(oc.get("p_value")), oc.get("p_operator")
    return _split_p(oc.get("p_value"))


def _compose_effect(metric: Optional[str], estimate: Optional[float],
                    direction: str, oc: dict[str, Any]) -> str:
    """Compose `result_effect`, e.g. 'HR 0.72 (favours intervention)'.

    Narrative-only outcomes (no numeric estimate) fall back to their source text.
    """
    if estimate is None:
        return (oc.get("source_quote") or oc.get("narrative")
                or (str(oc.get("effect_estimate")) if oc.get("effect_estimate") else "")
                or oc.get("name") or "").strip() or "Not estimable"
    metric_str = f"{metric} " if metric else ""
    dir_str = {
        FAVOURS_INTERVENTION: "favours intervention",
        FAVOURS_COMPARATOR: "favours comparator",
        NO_DIFFERENCE: "no difference",
        NOT_ESTIMABLE: "",
    }.get(direction, "")
    tail = f" ({dir_str})" if dir_str else ""
    return f"{metric_str}{_fmt_num(estimate)}{tail}".strip()


def _compose_ci_p(lo: Optional[float], hi: Optional[float],
                  p: Optional[float], p_op: Optional[str]) -> str:
    """Compose `result_ci_p`, e.g. '95% CI 0.55-0.94; p<0.001'. Blank parts omitted."""
    pieces: list[str] = []
    if lo is not None and hi is not None:
        pieces.append(f"95% CI {_fmt_num(lo)}–{_fmt_num(hi)}")
    if p is not None:
        pieces.append(_fmt_p(p, p_op or "eq"))
    return "; ".join(pieces)


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop only TRULY identical rows. Keeps the first occurrence (stable).

    The key includes the subgroup flag/label and the effect metric so legitimately
    distinct analyses of one outcome survive: a subgroup row vs the main analysis,
    an adjusted vs unadjusted estimate (distinct comparison), or two metrics for one
    outcome are all kept.
    """
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = (r.get("study_id"), r.get("outcome_name"), r.get("comparison"),
               r.get("outcome_timing"), bool(r.get("is_subgroup")),
               r.get("subgroup_label"), r.get("effect_metric"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 10. Dual-mode merge
# ---------------------------------------------------------------------------

def merge_injected_and_extracted(
    injected: Optional[dict[str, Any]],
    extracted: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Merge injected (upstream) tags with self-extracted tags. Dual-mode entry point.

    PRECEDENCE:
      * Study-level scalar tags: INJECTED WINS field-by-field; a self-extracted value
        fills a gap only where the injected tag is missing/blank. (Upstream extraction
        is authoritative when present; the isolation-mode pull only fills gaps.)
      * outcomes[]: chosen as a whole, in priority order:
          1. injected["outcomes"] if a non-empty list,
          2. else extracted["outcomes"] if non-empty,
          3. else seed_outcomes_from_universal(merged).
        We never element-wise merge two arrays — two independently produced arrays
        have no shared row identity, so the higher-priority array is taken intact.
    """
    injected = injected or {}
    extracted = extracted or {}
    merged: dict[str, Any] = dict(extracted)
    for k, v in injected.items():
        if k == "outcomes":
            continue
        if _present(v):
            merged[k] = v

    inj, ext = injected.get("outcomes"), extracted.get("outcomes")
    if isinstance(inj, list) and inj:
        merged["outcomes"] = inj
    elif isinstance(ext, list) and ext:
        merged["outcomes"] = ext
    else:
        merged["outcomes"] = seed_outcomes_from_universal(merged)
    return merged


# ---------------------------------------------------------------------------
# 11. Top-level assembler (pure Python — NO LLM call)
# ---------------------------------------------------------------------------

def assemble_table2(
    study_level_tags: dict[str, Any],
    outcomes: Optional[list[dict[str, Any]]] = None,
    rob: Optional[dict[str, Any]] = None,
    provenance: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Compose the Table 2 rows for one study. Pure Python; NEVER calls the model.

    Single top-level entry point used in BOTH modes:
      1. Resolve outcomes[]: passed `outcomes`, else tags["outcomes"], else seed a
         1-element array from the universal single-outcome fields.
      2. Derive study_id (if absent) and quality_rating (from `rob`, tool-routed).
      3. explode_rows() -> one flat row per outcome, denormalizing study-level fields.
      4. dedupe_rows() -> drop only truly identical rows.
    """
    tags = dict(study_level_tags or {})
    if outcomes is None:
        outcomes = tags.get("outcomes")
    if not (isinstance(outcomes, list) and outcomes):
        outcomes = seed_outcomes_from_universal(tags)

    if not tags.get("study_id"):
        tags["study_id"] = build_study_id(tags.get("citation_authors"), tags.get("citation_year"))
    if rob:
        tags["quality_rating"] = map_quality_rating(rob.get("rob_overall"), rob.get("rob_tool"))

    return dedupe_rows(explode_rows(tags, outcomes, provenance=provenance))


# ---------------------------------------------------------------------------
# 12. Tiny orchestrator: isolation-mode vs injected-mode
# ---------------------------------------------------------------------------

def build_table2(
    paper: dict[str, Any],
    injected: Optional[dict[str, Any]] = None,
    llm_call: Optional[Callable[..., Any]] = None,
    *,
    enrich_injected: bool = False,
) -> list[dict[str, Any]]:
    """Produce Table 2 rows for one paper in either mode. Thin dispatch layer.

    INJECTED MODE (injected is not None):
        Upstream extraction already produced tags (and possibly an outcomes[] array).
        Default: ZERO model calls — assemble directly (seeding a 1-element outcomes[]
        from single-outcome universal fields if no array was injected). Opt-in
        `enrich_injected=True` runs ONLY the outcomes[] pass to discover secondary
        outcomes; injected study-level tags still win.

    ISOLATION MODE (injected is None):
        No upstream tags. Runs its OWN extraction via `llm_call`: a study-level
        characteristics pull + the outcomes[] pass. Both are pure EXTRACTION prompts
        (out of scope for this non-extraction module); their JSON funnels into the
        same assemble_table2.
    """
    if injected is not None:
        extracted: dict[str, Any] = {}
        provenance = "injected"
        has_injected_outcomes = isinstance(injected.get("outcomes"), list) and injected["outcomes"]
        if enrich_injected and not has_injected_outcomes:
            if llm_call is None:
                raise ValueError("enrich_injected=True requires an `llm_call`.")
            extracted = {"outcomes": _extract_outcomes(paper, llm_call)}
            provenance = "enriched"
        elif not has_injected_outcomes:
            provenance = "seeded"
        merged = merge_injected_and_extracted(injected, extracted)
        rob = {"rob_overall": merged.get("rob_overall"), "rob_tool": merged.get("rob_tool")}
        return assemble_table2(merged, outcomes=merged.get("outcomes"), rob=rob, provenance=provenance)

    if llm_call is None:
        raise ValueError("Isolation mode requires an `llm_call` callable to run extraction.")
    study_level = _extract_study_level(paper, llm_call)
    outcomes = _extract_outcomes(paper, llm_call)
    rob = {"rob_overall": study_level.get("rob_overall"), "rob_tool": study_level.get("rob_tool")}
    return assemble_table2(study_level, outcomes=outcomes, rob=rob, provenance="extracted")


# --- Isolation-mode extraction shims (EXTRACTION — the ONLY places a model is touched).
# Their bodies are pure extraction prompts (see the prompt section of the companion doc);
# these stubs show the contract and where llm_call plugs in. No derivation happens here.

def _extract_study_level(paper: dict[str, Any], llm_call: Callable[..., Any]) -> dict[str, Any]:
    """Study-level characteristics pull (study_id/design/population/arms/method)."""
    return llm_call(task="table2_study_level", paper=paper) or {}


def _extract_outcomes(paper: dict[str, Any], llm_call: Callable[..., Any]) -> list[dict[str, Any]]:
    """The outcomes[] pass — one object per (outcome x comparison x timepoint)."""
    result = llm_call(task="table2_outcomes", paper=paper)
    return result if isinstance(result, list) else (result or {}).get("outcomes", []) or []


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _get(d: dict[str, Any], key: str) -> Any:
    """dict.get that also treats empty/whitespace strings as absent (None)."""
    v = d.get(key)
    return None if isinstance(v, str) and not v.strip() else v


def _present(v: Any) -> bool:
    """True if a value counts as supplied (not None, not a blank string)."""
    return not (v is None or (isinstance(v, str) and not v.strip()))


def _coerce_num(v: Any) -> Optional[float]:
    """Coerce a value to float when possible (parsing a numeric string), else None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return _to_float(v)


def _fmt_num(v: Optional[float]) -> str:
    """Format a numeric value compactly. inf/NaN -> blank (never crashes)."""
    if v is None:
        return ""
    if not math.isfinite(v):
        return ""
    if v == int(v):
        return str(int(v))
    return f"{v:.3g}"


def _fmt_p(p: Optional[float], op: str = "eq") -> str:
    """Format a p-value faithfully, preserving any inequality operator."""
    if p is None:
        return ""
    sym = {"lt": "<", "le": "≤", "gt": ">", "ge": "≥", "eq": "="}.get(op, "=")
    if op == "eq" and p < 0.001:
        return "p<0.001"
    if p < 0.001:
        return f"p{sym}{p:.1g}"
    return f"p{sym}{p:.3f}"


# ---------------------------------------------------------------------------
# Self-check (illustrative; not a test framework). Run: python table2_reference.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # study_id — list, 2 authors, Vancouver comma list, and the single-author comma case.
    assert build_study_id(["Smith JQ", "Jones A", "Lee K"], 2021) == "Smith et al., 2021"
    assert build_study_id(["Smith JQ", "Jones A"], "2020") == "Smith & Jones, 2020"
    assert build_study_id("Jane Q. Smith", None) == "Smith"
    assert build_study_id("Smith JQ, Jones A, Lee K", 2019) == "Smith et al., 2019"   # not "et al." of 6
    assert build_study_id("Smith, JQ", 2018) == "Smith, 2018"                          # one author
    assert build_study_id("Smith, John and Jones, Amy", 2017) == "Smith & Jones, 2017"

    # metric canonicalization + null value + apostrophe normalization
    assert canonicalize_metric("hazard ratio") == ("HR", "time_to_event")
    assert canonicalize_metric("relative risk") == ("RR", "ratio")
    assert canonicalize_metric("standardised mean difference") == ("SMD", "difference")
    assert canonicalize_metric("Hedges’ g") == ("SMD", "difference")             # curly apostrophe
    assert canonicalize_metric("absolute risk difference") == ("RD", "difference")
    assert null_value_for("ratio") == 1.0 and null_value_for("difference") == 0.0

    # parse_effect_cell — bracketed, unbracketed, p-only, and the 'grp2'/'group1' trap
    assert parse_effect_cell("HR 0.72 (95% CI 0.55-0.94), p=0.01") == {
        "estimate": 0.72, "ci_lower": 0.55, "ci_upper": 0.94, "p_value": 0.01, "p_operator": "eq"}
    assert parse_effect_cell("HR 0.68, 95% CI 0.55 to 0.94")["ci_lower"] == 0.55       # unbracketed
    pp = parse_effect_cell("p<0.001")
    assert pp["p_value"] == 0.001 and pp["p_operator"] == "lt"                          # faithful bound
    assert parse_effect_cell("grp2 HR 0.7 (0.5-0.9)")["p_value"] is None               # not 'p=2'
    assert parse_effect_cell("group1 mean 5.0")["p_value"] is None

    # reported-direction whole-word safety — 'consistent'/'generated' must NOT match ns/ne
    assert _parse_reported_direction("results were consistent") is None
    assert _parse_reported_direction("effect was generated by the model") is None
    assert _parse_reported_direction("NS") == NO_DIFFERENCE

    # direction — adverse-outcome default (lower is good); CI straddle; desirable outcome
    assert infer_direction("time_to_event", 0.72, 0.55, 0.94) == FAVOURS_INTERVENTION
    assert infer_direction("ratio", 1.1, 0.8, 1.5) == NO_DIFFERENCE                     # straddles null
    assert infer_direction("ratio", 1.0, 1.0, 1.5) == NO_DIFFERENCE                     # touches null
    assert infer_direction("ratio", 1.4, 1.1, 1.8, outcome_favorable_direction="higher") == FAVOURS_INTERVENTION
    assert infer_direction("ratio", 0.5, 0.3, 0.8, outcome_favorable_direction="neutral") == NOT_ESTIMABLE

    # reconcile — derive p from HR + CI (round-trip), and preserve a reported '<' p as a bound
    rec = reconcile_stats(0.72, 0.55, 0.94, None, "time_to_event")
    assert "p_value" in rec["derived"] and 0.0 < rec["p_value"] < 0.05
    rec2 = reconcile_stats(0.72, None, None, 0.001, "time_to_event", p_operator="lt")
    assert rec2["ci_lower"] is None and "ci_lower" not in rec2["derived"]               # bound -> no SE
    rec3 = reconcile_stats(0.72, None, None, 0.01, "time_to_event", p_operator="eq")
    assert "ci_lower" in rec3["derived"] and rec3["ci_lower"] is not None               # exact p -> CI

    # quality mapping — tool-routed inversion; AMSTAR not inverted; bare High needs a tool
    assert map_quality_rating("Low", "rob2") == "High"
    assert map_quality_rating("Some concerns", "rob2") == "Intermediate"
    assert map_quality_rating("High", "robins_i") == "Low"
    assert map_quality_rating("High", "amstar2") == "High"                              # not inverted
    assert map_quality_rating("Critically low", "amstar2") == "Low"
    assert map_quality_rating("Critically low") == "Low"                               # unambiguous w/o tool
    assert map_quality_rating("High") is None                                          # ambiguous w/o tool

    # dedupe keeps a subgroup row alongside the main analysis
    base = {"study_id": "X", "outcome_name": "OS", "comparison": "A vs B",
            "outcome_timing": "12m", "effect_metric": "HR"}
    kept = dedupe_rows([
        {**base, "is_subgroup": False, "subgroup_label": None},
        {**base, "is_subgroup": True, "subgroup_label": "PD-L1>=50%"},
        {**base, "is_subgroup": False, "subgroup_label": None},   # true duplicate -> dropped
    ])
    assert len(kept) == 2

    # SR/MA N cell shows k (studies) with pooled N
    assert format_n_cell("SR with Meta-Analysis", 3450, 12) == "k=12 (N=3450)"
    assert format_n_cell("Randomized Controlled Trial", 240, None) == "240"

    # _fmt_num tolerates inf/NaN
    assert _fmt_num(float("inf")) == "" and _fmt_num(float("nan")) == ""

    # end-to-end assemble (injected mode, single universal outcome, p as '<' bound preserved)
    tags = {
        "citation_authors": ["Doe J", "Roe R"], "citation_year": 2019,
        "study_type": "Randomized Controlled Trial",
        "population_participants": "Adults with cancer-related fatigue",
        "sample_size_total": 240,
        "population_intervention_exposure": "Exercise programme",
        "population_comparator": "Usual care",
        "statistical_method": "Mixed-effects model",
        "primary_outcome_definition": "Fatigue", "primary_outcome_measurement": "BFI",
        "primary_outcome_timing": "12 weeks",
        "key_findings_metric": "MD", "key_findings_effect_estimate": -3.4,
        "key_findings_ci_lower": -5.1, "key_findings_ci_upper": -1.7,
        "key_findings_pvalue": "<0.001",
    }
    table = assemble_table2(tags, rob={"rob_overall": "Low", "rob_tool": "rob2"}, provenance="seeded")
    assert len(table) == 1
    row = table[0]
    assert row["study_id"] == "Doe & Roe, 2019"
    assert row["direction"] == FAVOURS_INTERVENTION       # MD < 0, adverse-outcome default
    assert row["quality_rating"] == "High"                # Low risk of bias -> High quality
    assert row["p_operator"] == "lt" and "p<0.001" in row["result_ci_p"]   # faithful bound
    assert row["provenance"] == "seeded"

    print("All self-checks passed.")
```

### 12.10 Body-of-evidence GRADE agent — from `grade_certainty_shareable.md`

**From that document's §9. Reference implementation (turnkey, dependency-free):**

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
    large_effect_1: float = 2.0; large_effect_2: float = 5.0
    require_ci_for_large_effect: bool = True; upgrade_requires_no_downgrade: bool = True

_CFG = GradeConfig()

def _num(v):
    if v is None or isinstance(v, bool): return None
    try: f = float(v)
    except (TypeError, ValueError): return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None

def _grade_index(level):
    try: return GRADE_LEVELS.index(level)
    except ValueError: return 0

def _randomized_design(design_class, studies):
    dc = (design_class or "").lower()
    if dc == "rct": return True
    if dc == "nrs": return False
    designs = " ".join(str((s or {}).get("design") or "") for s in (studies or [])).lower()
    non_random = "non-random" in designs or "nonrandom" in designs
    randomized = "randomized" in designs or "randomised" in designs or "rct" in designs
    return randomized and not non_random

def _initial_from_design(design_class, measure, studies):
    designs = " ".join(str((s or {}).get("design") or "") for s in (studies or [])).lower()
    if "single-arm" in designs or "single arm" in designs or "dose-escalation" in designs:
        return "Very low"
    return "High" if _randomized_design(design_class, studies) else "Low"

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

def _large_effect(measure, est, lo, hi, cfg):
    if measure not in _RATIO_MEASURES: return 0, "large-effect criterion applies to ratio measures"
    if est is None or est <= 0: return 0, "no positive pooled ratio"
    if est >= 1.0: r, near = est, lo
    else: r, near = 1.0 / est, (1.0 / hi if (hi and hi > 0) else None)
    def clears(t):
        return r >= t if not cfg.require_ci_for_large_effect else (r >= t and near is not None and near >= 1.0)
    if clears(cfg.large_effect_2): return 2, f"very large effect ({measure}≈{r:.1f}); CI excludes no-effect"
    if clears(cfg.large_effect_1): return 1, f"large effect ({measure}≈{r:.1f})"
    return 0, f"effect magnitude below the large-effect threshold ({measure}≈{r:.1f})"

def _dose_response(dose_response, metareg):
    if dose_response is not None:
        return (1, "dose-response gradient (assessor judgement)") if dose_response else (0, "no dose-response gradient")
    return 0, "no dose moderator modelled"

def _opposing(opposing):
    return (1, "plausible residual confounding would only attenuate the observed effect") if opposing else (0, "not applicable")

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
               baseline_risk_per_1000=None, dose_response=None, opposing_confounding=False,
               subgroup=None, metaregression=None, overrides=None, cfg=None):
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
    # Upgrade eligibility keys on the DESIGN, not the starting certainty (§5.4).
    is_randomized = _randomized_design(pool_result.get("design_class"), studies)
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
    total_up = 0
    if (not is_randomized and initial == "Low" and (total_down == 0 or not cfg.upgrade_requires_no_downgrade)):
        le = pin("large_effect", *_large_effect(measure, est, lo, hi, cfg))
        dr = pin("dose_response", *_dose_response(dose_response, metaregression))
        oc = pin("opposing_confounding", *_opposing(opposing_confounding))
        for name, (lv, r) in (("Large effect", le), ("Dose-response gradient", dr), ("Opposing plausible confounding", oc)):
            domains.append({"domain": name, "kind": "upgrade", "downgrade": 0, "upgrade": lv, "reason": r})
            total_up += lv
    final = GRADE_LEVELS[max(0, min(len(GRADE_LEVELS) - 1, _grade_index(initial) + total_down - total_up))]
    fired = [f"{d['domain'].lower()} (−{d['downgrade']}: {d['reason']})"
             for d in domains if d["downgrade"] > 0]
    raised = [f"{d['domain'].lower()} (+{d['upgrade']}: {d['reason']})"
              for d in domains if d["upgrade"] > 0]
    parts = [f"Initial certainty {initial}"]
    if fired:
        parts.append(f"downgraded {total_down} level(s) for " + "; ".join(fired))
    if raised:
        parts.append(f"upgraded {total_up} level(s) for " + "; ".join(raised))
    if not fired and not raised:
        parts.append("no serious concerns across GRADE domains")
    explanation = ". ".join(parts) + f". Final certainty: {final}."
    return {"initial": initial, "final": final, "total_downgrade": total_down, "total_upgrade": total_up,
            "domains": domains,
            "explanation": explanation,
            "absolute_effects": absolute_effects(measure, est, lo, hi, baseline_risk_per_1000)}
```

**From that document's §10. Quick test sketches (plain assert, no framework):**

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

# NRS large effect RR 3 -> Low upgrades to Moderate.
g = grade_body(_rr_body(3.0, 2.0, 4.5), initial="Low", per_study_rob=["Low"]*3)
assert g["total_upgrade"] == 1 and g["final"] == "Moderate"

# Upgrade blocked when a domain was downgraded (imprecision crosses null + below OIS).
g = grade_body(_rr_body(3.0, 0.9, 10.0, n=40, ev=40, k=2), initial="Low", per_study_rob=["Low", "Low"])
assert g["total_upgrade"] == 0

# RCT never upgraded even with a huge effect.
g = grade_body(_rr_body(6.0, 3.0, 12.0), initial="High", per_study_rob=["Low", "Low"])
assert g["total_upgrade"] == 0 and g["final"] == "High"

# ...and pinning an RCT body to initial="Low" does not open the gate — eligibility
# keys on the design, not the starting certainty.
g = grade_body(_rr_body(6.0, 3.0, 12.0, dc="rct"), initial="Low", per_study_rob=["Low", "Low"])
assert g["total_upgrade"] == 0 and g["final"] == "Low"

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

### 12.11 Systematic-review synthesis pipeline — from `synthesis_meta_analysis_shareable.md`

**From that document's §10. Reference implementation (single self-contained file):**

```python
"""meta_analysis.py — self-contained pair-wise meta-analysis engine.
Dependencies: numpy, scipy. LLM calls are injected; the maths is offline.
"""
import math
import numpy as np
from scipy import optimize, stats

Z95 = float(stats.norm.ppf(0.975))
LOG_MEASURES = {"OR", "RR", "IRR", "HR"}

# ── 3. effect sizes ────────────────────────────────────────────────────────
def smd_hedges_g(m1, sd1, n1, m2, sd2, n2):
    df = n1 + n2 - 2
    if df <= 0: return None
    sp2 = ((n1 - 1) * sd1**2 + (n2 - 1) * sd2**2) / df
    if sp2 <= 0: return None
    d = (m1 - m2) / math.sqrt(sp2)
    J = 1 - 3 / (4 * df - 1)
    vd = (n1 + n2) / (n1 * n2) + d**2 / (2 * (n1 + n2))
    return J * d, J**2 * vd

def md(m1, sd1, n1, m2, sd2, n2):
    vi = sd1**2 / n1 + sd2**2 / n2
    return (m1 - m2, vi) if vi > 0 else None

def _cc(a, b, c, d, k=0.5):
    return (a+k, b+k, c+k, d+k, True) if min(a, b, c, d) == 0 else (a, b, c, d, False)

def log_or(e1, t1, e2, t2, k=0.5):
    a, c = e1, e2; b, d = t1 - e1, t2 - e2
    if a == 0 and c == 0: return None
    a, b, c, d, _ = _cc(a, b, c, d, k)
    return math.log((a*d)/(b*c)), 1/a + 1/b + 1/c + 1/d

def log_rr(e1, t1, e2, t2, k=0.5):
    a, c = e1, e2; b, d = t1 - e1, t2 - e2
    if a == 0 and c == 0: return None
    a, b, c, d, _ = _cc(a, b, c, d, k)
    n1c, n2c = a + b, c + d
    return math.log((a/n1c)/(c/n2c)), 1/a - 1/n1c + 1/c - 1/n2c

def risk_difference(e1, t1, e2, t2):
    p1, p2 = e1/t1, e2/t2
    return p1 - p2, p1*(1-p1)/t1 + p2*(1-p2)/t2

def fisher_z(r, n):
    if n <= 3: return None
    r = max(-0.999999, min(0.999999, r))
    return math.atanh(r), 1.0/(n-3)

def proportion_logit(e, n, k=0.5):
    if e == 0 or e == n: e += k; n += 2*k
    p = e/n
    return math.log(p/(1-p)), 1/(n*p) + 1/(n*(1-p))

def proportion_ft(e, n):
    return 0.5*(math.asin(math.sqrt(e/(n+1))) + math.asin(math.sqrt((e+1)/(n+1)))), 1/(4*n+2)

def incidence_rate_log(e, pt):
    return math.log(e/pt), 1.0/e

def hazard_ratio(hr=None, ci_lower=None, ci_upper=None, loghr=None, se=None, o_e=None, v=None):
    if o_e is not None and v is not None and v > 0:      # log-rank Peto
        return o_e / v, 1.0 / v
    if loghr is not None and se is not None and se > 0:  # reported log-HR + SE
        return loghr, se**2
    if hr and ci_lower and ci_upper and hr > 0 and ci_lower > 0 and ci_upper > 0:
        lo, hi = sorted((ci_lower, ci_upper))
        s = (math.log(hi) - math.log(lo)) / (2*Z95)
        return (math.log(hr), s**2) if s > 0 else None
    return None

def back_transform(yi, measure):
    if measure in LOG_MEASURES: return math.exp(yi)
    if measure == "PLOGIT": return 1/(1+math.exp(-yi))
    if measure == "ZCOR": return math.tanh(yi)
    if measure == "PFT": return math.sin(yi)**2
    return yi

# ── 5. heterogeneity ───────────────────────────────────────────────────────
def _q(yi, vi):
    w = 1/vi; mu = (w*yi).sum()/w.sum()
    return float((w*(yi-mu)**2).sum()), float(mu)

def tau2_dl(yi, vi):
    w = 1/vi; q, _ = _q(yi, vi); k = yi.size
    c = w.sum() - (w**2).sum()/w.sum()
    return max(0.0, (q-(k-1))/c) if c > 0 else 0.0

def tau2_reml(yi, vi):
    if yi.size < 2: return 0.0
    def neg_ll(t2):
        w = 1/(vi+t2); mu = (w*yi).sum()/w.sum()
        return 0.5*(np.log(vi+t2).sum() + math.log(w.sum()) + (w*(yi-mu)**2).sum())
    up = max(10*float(vi.max()), 10*tau2_dl(yi, vi)+1, 10)
    return max(0.0, float(optimize.minimize_scalar(neg_ll, bounds=(0, up), method="bounded").x))

def tau2_pm(yi, vi, tol=1e-7, max_iter=200):     # Paule-Mandel (empirical Bayes)
    k = yi.size
    if k < 2: return 0.0
    g = lambda t2: float(((1/(vi+t2)) * (yi - ((1/(vi+t2))*yi).sum()/(1/(vi+t2)).sum())**2).sum()) - (k-1)
    if g(0.0) <= 0: return 0.0
    lo, hi = 0.0, 10*float(vi.max()) + 10
    while g(hi) > 0 and hi < 1e12: hi *= 2
    for _ in range(max_iter):
        mid = 0.5*(lo+hi); gm = g(mid)
        if abs(gm) < tol: return mid
        lo, hi = (mid, hi) if gm > 0 else (lo, mid)
    return 0.5*(lo+hi)

def heterogeneity(yi, vi):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); k = yi.size
    if k < 2: return {"k": k, "Q": None, "I2": None, "tau2_REML": 0.0, "tau2_PM": 0.0, "df": max(0, k-1)}
    q, _ = _q(yi, vi); df = k-1
    return {"k": k, "Q": q, "df": df, "p": float(stats.chi2.sf(q, df)),
            "I2": max(0.0, (q-df)/q)*100 if q > 0 else 0.0, "H": math.sqrt(q/df),
            "tau2_DL": tau2_dl(yi, vi), "tau2_REML": tau2_reml(yi, vi), "tau2_PM": tau2_pm(yi, vi)}

# ── 4. pooling ─────────────────────────────────────────────────────────────
def iv_pool(yi, vi, tau2=0.0, knapp=False):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); k = yi.size
    w = 1/(vi+tau2); sw = w.sum(); est = float((w*yi).sum()/sw)
    if knapp and k >= 2:
        qd = float((w*(yi-est)**2).sum()/(k-1)); se = math.sqrt(qd/sw)
        crit = float(stats.t.ppf(0.975, k-1)); p = float(2*stats.t.sf(abs(est/se), k-1))
    else:
        se = math.sqrt(1/sw); crit = Z95; p = float(2*stats.norm.sf(abs(est/se)))
    return {"estimate": est, "se": se, "ci_low": est-crit*se, "ci_high": est+crit*se,
            "p": p, "weights_pct": (w/sw*100).tolist(), "tau2": tau2}

def mantel_haenszel_or(tables):
    A = np.array([t["a"] for t in tables], float); B = np.array([t["b"] for t in tables], float)
    C = np.array([t["c"] for t in tables], float); D = np.array([t["d"] for t in tables], float)
    N = A+B+C+D; R = (A*D/N).sum(); S = (B*C/N).sum()
    P = (A+D)/N; Q = (B+C)/N; Ri = A*D/N; Si = B*C/N
    var = (P*Ri).sum()/(2*R**2) + (P*Si+Q*Ri).sum()/(2*R*S) + (Q*Si).sum()/(2*S**2)
    est = math.log(R/S); se = math.sqrt(var)
    return {"estimate": est, "se": se, "ci_low": est-Z95*se, "ci_high": est+Z95*se,
            "p": float(2*stats.norm.sf(abs(est/se)))}

def pool(yi, vi, model="random", tau2_method="REML", knapp=False):
    het = heterogeneity(yi, vi)
    _key = {"REML": "tau2_REML", "DL": "tau2_DL", "PM": "tau2_PM"}.get(tau2_method.upper(), "tau2_REML")
    tau2 = het[_key] if het["k"] >= 2 else 0.0
    return {"fixed": iv_pool(yi, vi, 0.0), "random": iv_pool(yi, vi, tau2, knapp),
            "heterogeneity": het}

# ── 6. publication bias ────────────────────────────────────────────────────
def eggers_test(yi, vi):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); k = yi.size
    if k < 3: return None
    se = np.sqrt(vi); snd = yi/se; X = np.column_stack([np.ones(k), 1/se])
    beta, *_ = np.linalg.lstsq(X, snd, rcond=None)
    resid = snd - X@beta; s2 = (resid@resid)/(k-2)
    se_int = math.sqrt(s2*np.linalg.inv(X.T@X)[0, 0]); t = beta[0]/se_int
    return {"intercept": float(beta[0]), "p": float(2*stats.t.sf(abs(t), k-2)), "underpowered": k < 10}

def _signed_rank_t(dev):
    order = np.argsort(np.abs(dev), kind="mergesort")
    ranks = np.empty_like(order, float); ranks[order] = np.arange(1, dev.size+1)
    return float(ranks[dev > 0].sum())

def trim_and_fill(yi, vi, tau2_method="REML", side="auto", max_iter=100):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); k0 = yi.size
    if k0 < 3: return {"n_imputed": 0}
    est = lambda y, v: (float((1/(v+ (tau2_reml(y, v) if tau2_method=="REML" else tau2_dl(y, v)))*y).sum()
                              / (1/(v+(tau2_reml(y, v) if tau2_method=="REML" else tau2_dl(y, v)))).sum())
                        if y.size >= 2 else float(y[0]))
    mu0 = est(yi, vi)
    if side == "auto":
        side = "left" if _signed_rank_t(yi-mu0) > k0*(k0+1)/4 else "right"
    flip = 1.0 if side == "left" else -1.0
    y = (yi-mu0)*flip; mu = 0.0; L = 0
    for _ in range(max_iter):
        keep = np.ones(y.size, bool)
        if L > 0: keep[np.argsort(y)[-L:]] = False
        yk, vk = y[keep], vi[keep]
        mu = (est(yk+mu0, vk)-mu0) if yk.size else 0.0
        kk = yk.size
        Ln = max(0, round((4*_signed_rank_t(yk-mu) - kk*(kk+1))/(2*kk-1))) if kk > 1 else 0
        if Ln == L: break
        L = Ln
    return {"n_imputed": int(L), "side": side}

# ── 8. sensitivity ─────────────────────────────────────────────────────────
def leave_one_out(yi, vi, tau2_method="REML"):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); out = []
    for i in range(yi.size):
        k = np.arange(yi.size) != i
        r = pool(yi[k], vi[k], tau2_method=tau2_method)
        out.append({"omitted": i, "estimate": r["random"]["estimate"], "I2": r["heterogeneity"]["I2"]})
    return out

# ── 9. GRADE ───────────────────────────────────────────────────────────────
GRADE = ["High", "Moderate", "Low", "Very low"]
ROB_SEV = {"low": 0, "some concerns": 1, "moderate": 1, "no information": 1,
           "insufficient information": 1, "unclear": 1, "high": 2, "serious": 2, "critical": 2}
def _sev(label): return ROB_SEV.get((label or "").strip().lower().split(" (")[0], 1)

# Instruments that do not produce a risk-of-bias label. AMSTAR-2 rates a review's
# *confidence* ("High" is good), so mapping it through ROB_SEV inverts the domain.
NON_ROB_TOOLS = {"amstar2"}

def resolve_study_rob(rob_for_this_outcome, study_level_rob=None, tool=None,
                      legacy_study_scope=False):
    """One (study x outcome) label, or None when there is nothing assessable.

    None means "excluded from the risk-of-bias domain" -- never "clean".
    """
    if (tool or "").strip().lower() in NON_ROB_TOOLS:
        return None
    if (rob_for_this_outcome or "").strip():
        return rob_for_this_outcome.strip()
    # Only bodies appraised before per-outcome appraisal existed may fall back.
    if legacy_study_scope and (study_level_rob or "").strip():
        return study_level_rob.strip()
    return None

def rob_across_studies(per_study_rob, weights):
    """Returns (downgrade, assessed). assessed=False -> the domain is not rateable.

    Studies with no label are dropped and the weights renormalized over the rest:
    scoring them "some concerns" invents a finding about a study nobody appraised
    (and on its own pushes frac_some past 0.50), while scoring them "low" inflates
    certainty.
    """
    labels = list(per_study_rob or [])
    w_all = (np.asarray(weights, float)
             if weights is not None and len(weights) == len(labels)
             else np.ones(len(labels), float))
    keep = [i for i, r in enumerate(labels) if (r or "").strip()]
    if not keep:
        return 0, False
    sev = np.array([_sev(labels[i]) for i in keep], float)
    w = w_all[keep]; w = w / w.sum()
    fs = float(w[sev >= 2].sum())
    fm = float(w[sev >= 1].sum())
    return (2 if fs >= 0.5 else (1 if (fs >= 0.25 or fm >= 0.5) else 0)), True

def grade_body(initial, per_study_rob, weights, het, pooled, measure, total_n,
               subgroup_p=None, egger=None, n_imputed=0, indirectness=0,
               mid_benefit=None, mid_harm=None, is_binary=False):
    d_rob, rob_assessed = rob_across_studies(per_study_rob, weights)
    if not rob_assessed:
        # Risk of bias is a required GRADE domain. A 0 downgrade here would read
        # as "assessed and clean". Withhold the rating instead; the pooled
        # estimate, heterogeneity and publication-bias results stay valid.
        return {"final": None, "status": "not_rated", "total_downgrade": None,
                "warning": "no risk-of-bias judgement for any pooled study"}
    i2, pq = (het.get("I2") or 0.0), het.get("p", 1.0)
    if subgroup_p is not None and subgroup_p < 0.05: d_inc = 0
    elif i2 > 75 and pq < 0.10: d_inc = 1
    elif i2 > 50 and pq < 0.10: d_inc = 1
    else: d_inc = 0
    lo, hi = pooled.get("ci_low"), pooled.get("ci_high")
    crosses = lo is not None and hi is not None and lo <= 0 <= hi
    ois_fail = total_n < (300 if is_binary else 400)
    if mid_benefit is not None and mid_harm is not None and lo is not None:
        dl, dh = back_transform(lo, measure), back_transform(hi, measure)
        d_imp = 2 if (dl <= mid_harm and dh >= mid_benefit) else (1 if crosses or ois_fail else 0)
    else:
        d_imp = 2 if (crosses and ois_fail) else (1 if (crosses or ois_fail) else 0)
    d_pub = 1 if (egger and egger.get("p", 1) < 0.10) or n_imputed >= 2 else 0
    total = d_rob + d_inc + max(0, indirectness) + d_imp + d_pub
    final = GRADE[min(3, GRADE.index(initial) + total)]
    return {"final": final, "status": "rated", "total_downgrade": total,
            "domains": {"risk_of_bias": d_rob, "inconsistency": d_inc,
                        "indirectness": indirectness, "imprecision": d_imp, "publication_bias": d_pub}}

# ── 1-2. LLM steps (model injected) ────────────────────────────────────────
def derive_eligibility(pico, llm_call):
    import json
    sys = ("You are a systematic-review methodologist. Given a review's PICO, produce "
           "concise, machine-checkable inclusion and exclusion criteria a screener can "
           "apply to a full-text article. Respond with JSON only.")
    return llm_call("Review PICO:\n" + json.dumps(pico) +
                    '\n\nReturn JSON {"inclusion":[{"axis":"","criterion":""}],'
                    '"exclusion":[...],"design_filter":[...]}', system=sys)
```

**From that document's §11. Test sketches (framework-free):**

```python
import numpy as np, math
import meta_analysis as ma

# JSLHR golden fixture (Zhang, Cheng & Zhang 2022, Fig 2): 22 studies, SMD.
TE = [0.08,0.12,-1.36,3.63,0.08,-0.18,0.29,1.01,0.26,0.59,1.28,0.38,4.51,1.19,
      0.65,-0.20,0.24,-0.03,-0.12,0.14,0.00,0.46]
SE = [0.1513,0.4407,0.4047,0.9258,0.3537,0.2466,0.2852,0.5346,0.2930,0.3127,0.4663,
      0.4253,0.7014,1.0960,0.7687,1.0563,0.2083,0.2569,0.2571,0.8794,0.2709,0.3487]
yi = np.array(TE); vi = np.array(SE)**2

het = ma.heterogeneity(yi, vi)
assert het["df"] == 21
assert abs(het["Q"] - 83.85) < 0.6            # metafor reference
assert abs(het["I2"] - 75.0) < 1.0
assert abs(het["tau2_REML"] - 0.8240) < 0.05

re = ma.pool(yi, vi, tau2_method="REML")["random"]
assert abs(re["estimate"] - 0.468) < 0.01     # metafor: 0.468 [0.038, 0.898]
assert abs(re["ci_low"] - 0.038) < 0.02
assert abs(re["ci_high"] - 0.898) < 0.02

# Hedges' g from raw means
g, vg = ma.smd_hedges_g(10, 2, 50, 8, 2, 50)
assert abs(g - (1 - 3/(4*98 - 1)) * 1.0) < 1e-9   # d = 1.0

# Mantel-Haenszel OR
mh = ma.mantel_haenszel_or([{"a":10,"b":90,"c":5,"d":95},{"a":20,"b":80,"c":10,"d":90}])
assert abs(math.exp(mh["estimate"]) - 2.2) < 1e-6   # 13.75/6.25

# zero-cell continuity + double-zero drop
assert ma.log_or(0, 100, 5, 100) is not None        # single zero -> corrected
assert ma.log_or(0, 100, 0, 100) is None            # double zero -> dropped

# trim-and-fill: symmetric => ~0 imputed; asymmetric => >=1
sym = ma.trim_and_fill(np.array([-0.4,-0.2,0.0,0.2,0.4]), np.array([0.01]*5))
assert sym["n_imputed"] <= 1
asym = ma.trim_and_fill(np.array([0.1,0.2,0.5,0.6,0.9,1.1]),
                        np.array([0.0025,0.0025,0.09,0.1225,0.25,0.3025]))
assert asym["n_imputed"] >= 1

# GRADE: RCT body with high RoB + high I2 + imprecision -> downgraded
g = ma.grade_body("High", ["High","High","Some concerns"], [1,1,1],
                  {"I2":80.0,"p":0.001}, {"ci_low":-0.1,"ci_high":0.6}, "SMD", 120,
                  egger={"p":0.02})
assert g["final"] == "Very low"

# Risk of bias is weighted by the pooled weights, not counted
assert ma.rob_across_studies(["Low","Low","High"], [5,5,90]) == (2, True)

# Unassessed studies are dropped and the weights renormalized: the two High
# studies hold 20% of total weight but 100% of the *assessed* weight.
assert ma.rob_across_studies(["High","High",None], [10,10,80]) == (2, True)

# A body where nothing was appraised is not rateable. This must NOT come back
# as a 0 downgrade: [None, None, None] is what a review run without risk of
# bias looks like, and scoring each None "some concerns" would instead
# manufacture a 1-level downgrade citing studies nobody appraised.
assert ma.rob_across_studies([None, None, None], [33,33,34]) == (0, False)
g2 = ma.grade_body("High", [None,None], [1,1], {"I2":0.0,"p":0.9},
                   {"ci_low":0.2,"ci_high":0.6}, "SMD", 2000)
assert g2["final"] is None and g2["status"] == "not_rated"

# An unmappable label is still a judgement someone made -> "some concerns";
# that is different from no judgement at all.
assert ma.rob_across_studies(["Low","banana"], [50,50]) == (1, True)

# Per (study x outcome) resolution: the outcome's own label wins; a study-level
# label is only a fallback for bodies appraised before per-outcome appraisal.
assert ma.resolve_study_rob("High", "Low") == "High"
assert ma.resolve_study_rob(None, "Low") is None
assert ma.resolve_study_rob(None, "Low", legacy_study_scope=True) == "Low"

# AMSTAR-2 rates confidence (High = good) and must never be scored as a risk label
assert ma.resolve_study_rob("High", tool="amstar2") is None
assert "critically low" not in ma.ROB_SEV

# Hazard ratio: reported HR + 95% CI -> log scale; not from a 2x2
hr = ma.hazard_ratio(hr=0.75, ci_lower=0.60, ci_upper=0.94)
assert abs(hr[0] - math.log(0.75)) < 1e-9
assert abs(ma.hazard_ratio(o_e=-5.0, v=20.0)[0] + 0.25) < 1e-9   # Peto (O-E)/V
assert ma.back_transform(hr[0], "HR") == 0.75 or abs(math.exp(hr[0]) - 0.75) < 1e-9

# Paule-Mandel tau^2: > 0 on heterogeneous data, selectable in pool()
yh = np.array([0.1,0.9,0.3,-0.2,0.6]); vh = np.array([0.05,0.04,0.06,0.05,0.03])
assert ma.tau2_pm(yh, vh) > 0
assert abs(ma.pool(yh, vh, tau2_method="PM")["random"]["tau2"] - ma.tau2_pm(yh, vh)) < 1e-9
```
