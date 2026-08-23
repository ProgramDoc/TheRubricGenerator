# Study Taxonomy & Agent Pipeline — Sharable Methodology Reference

A self-contained master reference for the full agent pipeline: the **study-design taxonomy**, the **classification agent**, the **extraction agent**, every **quality-appraisal agent** (risk-of-bias tools, reporting guidelines, GRADE domains), and the **evidence-synthesis agents**. It unifies two previously separate bodies of work — the OGAI study-design taxonomy and classification rubric, and the appraisal platform's deployed agent suite — into one atlas. Contains:

- The unified study-design taxonomy (union of OGAI taxonomy v1.9 and platform taxonomy v2.1) with a per-type routing table: risk-of-bias tool → reporting guideline → initial GRADE certainty → deployment status
- The classification agent in full: methods-over-labels, the three layers of study identity, the primacy hierarchy (Rules 1, 2, 2b, 3, 4), the master decision flowchart, the exogeneity test, the before-after spectrum, the 11 design features with the feature-to-type consistency matrix, the confusion-pair disambiguation catalog, the cluster-subtype decision tree — plus the deployed classification prompt verbatim
- The extraction agent in full: the three-layer field catalog (universal / type-specific / design modifiers), the deployed extraction prompt verbatim, the selective-assembly contract, and the three-stage large-document pipeline
- A methodology digest of **every** quality-appraisal agent — RoB 2 (parallel, cross-over, cluster), ROBINS-I V1 and V2, QUADAS-2 and QUADAS-3, AMSTAR-2, the six reporting-guideline checkers, GRADE indirectness, GRADE imprecision, the per-paper GRADE combiner, and outcome extraction — each with a pointer to its full standalone companion document where one exists
- Digests of the evidence-synthesis agents: pooling / meta-analysis, the per-study evidence table (Table 2), the body-of-evidence GRADE agent, and the systematic-review synthesis pipeline
- The cross-agent engineering conventions every implementation should preserve
- Implementation notes for other platforms

**Sources.** This document consolidates two lineages. (1) The **OGAI study-design taxonomy and classification rubric** — the OGAI Pipeline v3 reference site (Taxonomy v1.9, 32 types, March 2026; Classification Rubric v1.8; Extraction Fields Reference v1.6) and the *OGAI AI-CEA Pipeline v3.1 — AI Classification, Extraction & Appraisal Rubric* (February 2026), published at <https://programdoc.github.io/StudyTaxonomy/>. (2) The **appraisal platform's deployed agents** (platform taxonomy v2.1, 33 types), whose per-instrument methodologies are transcribed in the companion documents listed in §1.3 — each companion carries the full academic citation for its instrument (RoB 2: Sterne 2019; ROBINS-I: Sterne 2016 / 20 Nov 2025 cribsheet; QUADAS-2: Whiting 2011; AMSTAR-2: Shea 2017; GRADE handbook chapters; etc.).

**Scope.** This is the *atlas*: the taxonomy, the routing, the classification and extraction methodologies in full, and a working digest of every downstream agent. For agents that have a standalone companion document, the companion is the document of record — the digest here states what the agent is, its unit of assessment, its judgement scales, and how it plugs into the pipeline, and defers signaling-question text, decision trees, prompts, and reference implementations to the companion. For agents that do **not** yet have a companion (RoB 2 parallel-group, QUADAS-3, AMSTAR-2, and the six reporting-guideline checkers), the digest here is richer and is marked *standalone document pending*.

Explicitly **out of scope**: verbatim signaling-question and checklist-item text for instruments whose companion carries it; the numerical statistics engines (effect sizes, pooling models, heterogeneity — see `pooling_meta_analysis_shareable.md` and `synthesis_meta_analysis_shareable.md`); rubric generation and answer judging for LLM benchmarking (a separate subsystem, not part of this pipeline).

> **Deployment-status convention — read this first.**
> This document deliberately mixes two kinds of content, and every section is tagged so a reader always knows which they are looking at:
>
> - **⚙ Deployed** — behavior a working implementation exhibits today. Prompts marked ⚙ are transcribed verbatim from the production agents.
> - **📐 Reference** — methodology specified by the OGAI rubric that a *complete* implementation should exhibit, but which the current deployed agents implement only partially or not at all. 📐 material is not speculative — it is the design target — but a reader replicating "what the platform does" should implement the ⚙ parts first and treat 📐 parts as the roadmap.
>
> The flagship example: the deployed classification agent (§3.11, ⚙) is a single taxonomy-constrained prompt returning three keys, while the full classification rubric (§3.1–3.10, 📐) adds primacy rules, design-feature cross-validation, confusion-pair disambiguation, and mismatch documentation.

---

## 1. The pipeline at a glance

### 1.1 Flow

```
PAPER (PDF)
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│ CLASSIFICATION AGENT (§3)                                     │
│ study type within the unified taxonomy (§2)                   │
│ ⚙ taxonomy-constrained prompt → {major_category, subcategory, │
│    study_type}                                                │
│ 📐 + primacy rules, 11 design features, confusion pairs,      │
│    author-label concordance, confidence + alternative          │
└──────────┬───────────────────────────────────────────────────┘
           │
     ┌─────┴──────┐
     │  ROUTING    │ ← unified routing table (§2.3):
     └─────┬──────┘   type → RoB tool + reporting guideline + initial GRADE
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│ EXTRACTION AGENT (§4)                                         │
│ Layer 1 universal fields (32, 8 groups)                       │
│ Layer 2 type-specific fields (per study type, RoB-aligned)    │
│ Layer 3 design modifiers (cross-cutting overlays)             │
│ 📐 + classification-validation block, red-flag re-routing     │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│ QUALITY-APPRAISAL AGENTS — per (study × outcome) unit (§5)    │
│ • Outcome extraction → the outcome axis (§8.4)                │
│ • Risk-of-bias tool matched to the study type (§6)            │
│ • Reporting-guideline adherence check (§7)                    │
│ • GRADE indirectness + imprecision (§8.1–8.2)                 │
│ • Per-paper GRADE combiner (§8.3)                             │
└──────────┬───────────────────────────────────────────────────┘
           │  many appraised studies
           ▼
┌──────────────────────────────────────────────────────────────┐
│ EVIDENCE-SYNTHESIS AGENTS — per body of evidence (§9)         │
│ • Per-study evidence table (Table 2)                          │
│ • Pooling / meta-analysis                                     │
│ • Body-of-evidence GRADE agent (all 5 + 3 domains)            │
│ • Systematic-review synthesis pipeline                        │
└──────────────────────────────────────────────────────────────┘
```

Two design principles run through the whole pipeline:

1. **Classification exists to drive correct appraisal.** The study type is not an end in itself — it selects the extraction template, the risk-of-bias instrument, the reporting guideline, and the starting GRADE certainty. When classification is ambiguous, the tiebreaker is always: *which risk-of-bias tool must be used to properly evaluate this study?*
2. **Misclassification is caught and corrected rather than silently propagated** (📐). Downstream stages carry validation hooks — design-feature consistency checks at classification time, red-flag blocks at extraction time, domain-applicability checks at appraisal time — that can send a study back for re-routing or human review.

### 1.2 The agent roster

| # | Agent | Unit of work | Status | Methodology of record |
|---|-------|--------------|--------|----------------------|
| 1 | Study classification | one paper → study type | ⚙ + 📐 rubric | **this document** §3 |
| 2 | Field extraction (three-layer) | one paper → structured fields | ⚙ + 📐 validation | **this document** §4 |
| 3 | Outcome extraction | one paper → appraisable outcome list | ⚙ | `outcome_extraction_shareable.md` |
| 4 | RoB 2 (parallel-group RCT) | (study × outcome) | ⚙ | **this document** §6.1 *(standalone pending)* |
| 5 | RoB 2 cross-over extension | (study × outcome) | ⚙ | `rob2_crossover_shareable.md` |
| 6 | RoB 2 cluster extension (CRT) | (study × outcome) | ⚙ | `rob2_cluster_shareable.md` |
| 7 | ROBINS-I V2 (incl. single-arm variant) | (study × outcome) | ⚙ | `robins_i_v2_shareable.md` |
| 8 | ROBINS-I V1 (opt-in; incl. single-arm variant) | (study × outcome) | ⚙ | `robins_i_v1_shareable.md` |
| 9 | QUADAS-2 | (study × estimate) | ⚙ | `quadas2_shareable.md` |
| 10 | QUADAS-3 v1.2 | (study × estimate) | ⚙ | **this document** §6.6 *(standalone pending)* |
| 11 | AMSTAR-2 | one systematic review | ⚙ | **this document** §6.8 *(standalone pending)* |
| 12 | Reporting-guideline checkers (CONSORT 2025, CONSORT cross-over, CONSORT cluster, STROBE, STARD 2015, PRISMA 2020) | one paper | ⚙ | **this document** §7 *(standalones pending; cross-over/cluster companions exist — see §7.3)* |
| 13 | GRADE indirectness (per-paper) | (study × outcome) | ⚙ | `quality_appraisal_grade_shareable.md` §4 |
| 14 | GRADE imprecision (per-paper) | (study × outcome) | ⚙ | `quality_appraisal_grade_shareable.md` §5 |
| 15 | Per-paper GRADE combiner | (study × outcome) | ⚙ | `quality_appraisal_grade_shareable.md` §§1–3, 6 |
| 16 | Pooling / meta-analysis agent | body of evidence (outcome × comparison × design class) | ⚙ | `pooling_meta_analysis_shareable.md` |
| 17 | Per-study evidence table (Table 2) | study × outcome × comparison × timepoint rows | ⚙ | `table2_evidence_table_shareable.md` |
| 18 | Body-of-evidence GRADE agent | one pooled outcome | ⚙ | `grade_certainty_shareable.md` |
| 19 | Systematic-review synthesis pipeline | one review (screen → extract → RoB → pool → GRADE) | ⚙ | `synthesis_meta_analysis_shareable.md` |
| — | EPOC, NOS/ROBINS-E, QUIPS, PROBAST, JBI, AXIS, CASP, MMAT, CHEC, STROBE-MR, SCCS-checklist tools | (study × outcome) | 📐 routed, not built | routing rows in §2.3 |

### 1.3 Companion documents

All companions are self-contained sibling documents distributed alongside this one: `outcome_extraction_shareable.md`, `rob2_crossover_shareable.md`, `rob2_cluster_shareable.md`, `robins_i_v1_shareable.md`, `robins_i_v2_shareable.md`, `quadas2_shareable.md`, `quality_appraisal_grade_shareable.md`, `grade_certainty_shareable.md` (and its downgrades-only draft variant `grade_certainty_downgrades_shareable.md`), `pooling_meta_analysis_shareable.md`, `table2_evidence_table_shareable.md`, `synthesis_meta_analysis_shareable.md`. Where this document and a companion disagree, **the companion wins** for that agent's internals; this document wins for taxonomy, routing, and cross-agent contracts.

---

## 2. The unified study-design taxonomy

### 2.1 Two lineages, one union

Two taxonomy versions were maintained in parallel and have now converged to near-identity:

- **OGAI taxonomy v1.9** (32 types, March 2026) — deliberately *consolidated by RoB-tool and reporting-guideline utility*: types that route to the same instrument were merged (prospective/retrospective/ambidirectional cohort → Cohort Study; case-control/nested/case-cohort → Case-Control; case report + case series → Case Report / Series; SR ± meta-analysis kept split because AMSTAR-2 items 11/12/15 differ; guideline + consensus statement → Guideline / Consensus). Includes **Controlled Before-After**.
- **Platform taxonomy v2.1** (33 types) — the deployed classification agent's taxonomy. Identical structure, but adds two uncontrolled experimental designs the appraisal platform supports (**Single-Arm Trial**, **Dose-Escalation Study**) and omits Controlled Before-After.

This document canonicalizes the **union: 34 study types** across 5 major categories. Each type below carries its deployment status:

- **A — appraisable ⚙**: classification + extraction + full quality-appraisal pipeline deployed (the 13 registry types).
- **C — classify/extract ⚙**: the deployed classifier can assign the type and (for most) type-specific extraction fields exist, but appraisal routing is 📐 (the study is marked *skipped* by the appraisal orchestrator, with the charge refunded).
- **T — taxonomy-only 📐**: in the unified tree, but not yet in the deployed classifier's taxonomy prompt.

### 2.2 The unified tree (34 types)

```
Study Designs
├── Primary Studies
│   ├── Randomized Controlled
│   │   ├── Randomized Controlled Trial        [A]
│   │   ├── Cluster Randomized Trial           [A]  (parallel-cluster; subtypes §2.4)
│   │   ├── Stepped-Wedge Cluster RCT          [C]  (appraisal 📐 — see §2.4)
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

One row per unified type: risk-of-bias tool → reporting guideline → initial GRADE certainty → status. ⚙ rows are deployed exactly as stated; 📐 rows carry the OGAI routing map's assignment for implementers who extend coverage.

| Study type | RoB tool | Reporting guideline | Initial GRADE | Status |
|---|---|---|---|---|
| Randomized Controlled Trial | RoB 2 (2019) | CONSORT 2025 | High | **A ⚙** |
| Cluster Randomized Trial | RoB 2 cluster ext. (2021) | CONSORT + cluster ext. (Campbell 2012) | High | **A ⚙** |
| Crossover Trial | RoB 2 cross-over ext. | CONSORT + cross-over ext. (Dwan 2019) | High | **A ⚙** |
| Stepped-Wedge Cluster RCT | RoB 2 cluster + SW considerations 📐 | CONSORT stepped-wedge ext. 📐 | High | C (appraisal 📐 — §2.4) |
| Non-Randomized Trial | ROBINS-I V2 (V1 opt-in) | STROBE | Low | **A ⚙** |
| Single-Arm Trial | ROBINS-I V2/V1 single-arm variant | STROBE (pragmatic reuse) | Very low | **A ⚙** |
| Dose-Escalation Study | ROBINS-I V2/V1 single-arm variant | STROBE (pragmatic reuse) | Very low | **A ⚙** |
| Interrupted Time Series | EPOC criteria 📐 | EPOC / STROBE 📐 | Low | C |
| Controlled Before-After | EPOC criteria 📐 | EPOC / STROBE 📐 | Low | T 📐 |
| Uncontrolled Before-After | EPOC criteria 📐 | EPOC / STROBE 📐 | Very Low | C |
| Difference-in-Differences | ROBINS-I 📐 | STROBE 📐 | Low | C |
| Regression Discontinuity | ROBINS-I 📐 | STROBE 📐 | Low | C |
| Cohort Study | ROBINS-I V2 (V1 opt-in) | STROBE | Low | **A ⚙** |
| Case-Control | ROBINS-I V2 (V1 opt-in) | STROBE | Low | **A ⚙** |
| Cross-Sectional (Analytical) | ROBINS-I V2 (approximation) | STROBE | Low | **A ⚙** |
| Case-Crossover | ROBINS-I V2 (approximation) | STROBE | Low | **A ⚙** |
| Self-Controlled Case Series | adapted ROBINS-I / SCCS checklist 📐 | SCCS guidelines 📐 | Low | C |
| Mendelian Randomization | STROBE-MR checklist 📐 | STROBE-MR 📐 | Low | C |
| Case Report / Series | JBI / CARE checklist 📐 | CARE / PROCESS 📐 | Very Low / N-A | C |
| Cross-Sectional (Descriptive) | AXIS / JBI 📐 | STROBE 📐 | N/A | C |
| Ecological Study | adapted NOS 📐 | STROBE 📐 | Very Low | C |
| Diagnostic Accuracy | QUADAS-3 v1.2 (default) or QUADAS-2 (per-run toggle) | STARD 2015 | High (accuracy framework) | **A ⚙** |
| Prognostic Factor Study | QUIPS 📐 | REMARK 📐 | Low (modified GRADE) | C |
| Prediction Model Study | PROBAST 📐 | TRIPOD 📐 | separate framework | C |
| SR with Meta-Analysis | AMSTAR-2 | PRISMA 2020 | none (confidence rating instead) | **A ⚙** |
| SR without Meta-Analysis | AMSTAR-2 | PRISMA 2020 | none (confidence rating instead) | **A ⚙** |
| Umbrella Review | AMSTAR-2 📐 | PRISMA 📐 | depends on included reviews | C |
| Network Meta-Analysis | AMSTAR-2 + CINeMA 📐 | PRISMA-NMA 📐 | NMA framework | C |
| Scoping Review | none (typically) | PRISMA-ScR 📐 | N/A | C |
| Narrative Review | none | none standardized | N/A | C |
| Guideline / Consensus | AGREE II 📐 | — | N/A | C |
| Qualitative Research | CASP Qualitative 📐 | COREQ / SRQR 📐 | GRADE-CERQual 📐 | C |
| Mixed Methods | MMAT 📐 | GRAMMS 📐 | per component | C |
| Economic Evaluation | CHEC / Drummond 📐 | CHEERS 📐 | separate framework | C |

Notes on the ⚙ rows:

- **Uncontrolled designs start at Very low**, one step below confounded-comparison designs: the absence of *any* comparator is a more severe limitation than a confounded comparison, and the GRADE combiner clamps further downgrades at Very low.
- **Diagnostic Accuracy starts at High** per the GRADE handbook's treatment of cross-sectional accuracy designs; case-control accuracy designs are downgraded through the participant-selection domain of the QUADAS tools rather than through a lower starting level. PICO-style indirectness and imprecision are *skipped* for accuracy studies — those modules assume treatment trials, not PIRT (Patient / Index test / Reference standard / Target condition) questions.
- **AMSTAR-2 emits a confidence rating, not a GRADE certainty** (High / Moderate / Low / Critically low). The initial-GRADE column is empty by design and the GRADE-domain agents are skipped for review papers.
- **ROBINS-I V2 for Case-Control, Cross-Sectional (Analytical), and Case-Crossover is a best-available approximation** — V2 is published for follow-up (cohort) studies; these designs use it pending design-specific tooling.

### 2.4 Cluster-randomized subtypes

The cluster family has three subtypes; the two lineages handle them differently, and the union keeps both views coherent:

- **Parallel cluster RCT** — clusters randomized once to an arm and stay there. This is the deployed **Cluster Randomized Trial** type: RoB 2 cluster extension (Domain 1a randomization + the cluster-specific Domain 1b identification/recruitment-timing) + CONSORT cluster extension. ⚙
- **Stepped-Wedge Cluster RCT** — all clusters begin in control and cross over to intervention at *randomized* time points. Kept as its own classify-able type (the deployed classifier can assign it; extraction reuses the cluster field set), but **appraisal is deliberately not routed**: the published RoB 2 CRT cribsheet covers only parallel cluster trials, and stepped-wedge needs an additional time-trend / time-period-confounding treatment. Reference guidance (📐): assess whether crossover *timing* was truly randomized (Domain 1); recruitment practices may differ between control and intervention periods within a cluster (1b); awareness of upcoming crossover may change behavior in late control periods (Domain 2); multiple plausible correlation-structure / time-trend model specifications inflate selective-reporting risk (Domain 5); consider GRADE downgrades for time-period confounding and learning-curve indirectness.
- **Cluster crossover RCT** — clusters receive both/all interventions in randomized sequence with washout; switching is *bidirectional* (unlike stepped-wedge). Not a separate type in either lineage's classifier; 📐 reference material for a future subtype.

**Subtype decision tree** (📐, for classifiers that go finer than the deployed one):

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

The classification agent assigns each paper one study type from the unified taxonomy. §§3.1–3.10 give the full OGAI classification rubric — the reference methodology (📐, with the elements the deployed agent already implements noted inline). §3.11 gives the deployed classification profile verbatim (⚙).

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

### 3.8 The 11 design features (cross-validation) 📐

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

### 3.9 Confusion pairs — disambiguation catalog 📐

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

### 3.10 Reference output schema 📐

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

### 3.11 The deployed classification profile ⚙

The production classifier is deliberately minimal: one LLM call, the taxonomy inline, three output keys, deterministic post-filtering. Everything in §§3.3–3.10 beyond this is 📐.

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

### 4.2 Layer 1 — universal fields (32, in 8 groups) ⚙

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

### 4.3 Layer 2 — type-specific fields ⚙

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

### 4.4 Layer 3 — design modifiers ⚙ (+ extended catalog 📐)

Eleven cross-cutting modifier fields are deployed; they apply to any base type without changing classification or routing:

`clinical_trial_phase` · `regulatory_context` · `registration_number` · `industry_sponsored` · `data_source_type` · `database_name` · `adaptive_design` · `pragmatic_vs_explanatory` · `trial_framework` (superiority / non-inferiority / equivalence) · `target_trial_emulation` · `pilot_or_feasibility`

The OGAI extraction reference specifies a richer modifier catalog (📐) that a full implementation should adopt as structured objects rather than flat strings: adaptive-design detail (`adaptation_type`: sample-size re-estimation / arm dropping / dose finding / biomarker-adaptive / seamless phase; interim-analysis count), PRECIS-2 spectrum position for pragmatic-vs-explanatory, master-protocol type (umbrella / basket / platform), factorial-design factor matrix, Bayesian framework with prior specification, registry-based-trial flag, `natural_experiment_flag` with the exogenous-event description and the author's exogeneity argument, and data-source detail (linkage method, code-validation PPV).

### 4.5 The deployed extraction prompt ⚙

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

### 4.6 Custom-schema extraction ⚙

Besides the fixed catalog, the pipeline supports reviewer-defined schemas: a schema can be **parsed from an uploaded document** (a codebook PDF, DOCX, CSV, or pasted text — one LLM call proposes `{field_id, label, description}` entries), **refined** conversationally (one call rewrites the schema per instruction), and then **run** over a batch of papers with the same extraction prompt shape as §4.5, substituting the custom field list. Custom runs reuse the entire large-document pipeline below and the same omission/no-invention rules. Optional extended thinking can be enabled per run, in which case the model's reasoning is captured per paper alongside the extraction.

### 4.7 The three-stage large-document pipeline ⚙

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

### 4.8 Analytics metadata ⚙

Two field-type sets drive downstream analytics without affecting extraction: a **numeric set** (e.g. `sample_size_total`, `attrition_rate`, `included_studies_n`, `f_statistic`, CI bounds) coerced to float for min/median/max/mean summaries, and a **categorical set** (e.g. `study_type`, `funding_source`, `clinical_trial_phase`, `trial_framework`) drawn from small discrete vocabularies for distribution charts. Ports that add fields should classify them into one of numeric / categorical / free-text — the reference heuristic for unknown fields: ≥ 80% float-parsable → numeric; ≤ 8 unique values and ≤ 60-char max → categorical; else text.

### 4.9 Validation and re-routing 📐

The OGAI Stage-2 design adds a feedback loop the deployed extractor does not yet implement:

- **Classification-validation block.** Every extraction output carries `{author_stated_design, classified_design_confirmed, confidence_in_classification, red_flags[], suggested_reclassification, reclassification_reason}`. The extractor — which reads the paper more deeply than the classifier — is positioned to catch misclassification.
- **Red-flag re-routing.** If `classified_design_confirmed` is false with a non-null suggestion, the orchestrator re-routes to the suggested type's template and re-extracts. Red flags with a confirmed classification are logged, not acted on. A re-routed extraction that also fails validation → human review.
- **Low-confidence dual extraction.** When classification confidence is *low* with an alternative: run extraction under both templates, compare completeness (count of non-null fields), keep the more complete, log both with rationale.
- **Human-review flags.** Both templates fail validation; unresolvable feature-consistency warnings; low confidence with no alternative; dual extraction ties.

---

## 5. Appraisal orchestration ⚙

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

### 6.1 RoB 2 — parallel-group randomized trials ⚙ *(standalone document pending — this digest is the current sharable reference)*

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

### 6.2 RoB 2 cross-over extension ⚙ → `rob2_crossover_shareable.md`

For individually randomized trials where each participant receives all interventions sequentially (AB/BA). **6 domains / 23 signals**: the parallel-group five plus **Domain S — bias arising from period and carryover effects** (washout adequacy, carryover assessment, period effects), and a fourth Domain-5 question (5.4) for selective first-period-only reporting on the basis of a carryover test. Domains 1–4 reuse the parallel-group signal trees. Scales and overall rule as §6.1. The companion also carries the CONSORT cross-over reporting checklist (§7.3).

### 6.3 RoB 2 cluster extension (RoB 2 CRT) ⚙ → `rob2_cluster_shareable.md`

For **parallel** cluster-randomized trials (18 March 2021 cribsheet; stepped-wedge is explicitly out of scope — §2.4). **6 domains**: 1a randomization; **1b — bias arising from the timing of identification or recruitment of participants** (the cluster-specific domain: were individuals identified/recruited after cluster allocation was known?); 2–5 as in RoB 2. **Domain 2 has two variants** selected per run: `assignment` (ITT, 8 signals) and `adhering` (per-protocol, 6 signals). Decision trees are transcribed independently from the CRT cribsheet and *diverge* from parallel RoB 2 in places (e.g. concealed-but-non-random allocation is *Some concerns* in D1a); only D5 and the overall rule are shared. Signal 3.2 has no NI option; conditional NA is derived in code. Companion carries the CONSORT cluster checklist (§7.3).

### 6.4 ROBINS-I V2 ⚙ → `robins_i_v2_shareable.md`

The default tool for every non-randomized intervention design (20 Nov 2025 cribsheet). **6 domains** (V2 retired V1's separate deviations domain): confounding; classification of interventions; selection of participants; missing data; outcome measurement; selection of the reported result. **Preflight call** answers screening questions B1/B2/B3 + C4: B2 or B3 = Y/PY short-circuits the whole assessment to **Critical**; C4 (does the analysis account for post-baseline deviations?) dispatches **Domain 1 Variant A** (ITT-like, baseline confounding) vs **Variant B** (per-protocol, adds time-varying confounding). **Single-arm variant** (project extension for Single-Arm Trial / Dose-Escalation Study): pinned by study type before preflight; replaces B1/B2 with benchmark-pre-specification questions, reframes D1 as benchmark adequacy + prognostic-mix comparability (1S.*) and D2 as intervention fidelity + intent-vs-received cohort definition; D3–D6 unchanged. Signal scale adds strength tokens (`SY/WY/WN/SN`) on designated questions; judgements are 4-level **Low / Moderate / Serious / Critical** (the V1 "No information" judgement is retired), with Domain 1's Low labelled "Low (except for concerns about uncontrolled confounding/benchmarking)" — normalized to plain Low before GRADE mapping.

### 6.5 ROBINS-I V1 ⚙ (opt-in per run) → `robins_i_v1_shareable.md`

The 1 Aug 2016 original, kept co-resident for teams standardized on V1 vocabularies. **7 domains** (confounding; selection into the study; classification of interventions; **deviations from intended interventions** — aim-gated; missing data; outcome measurement; selective reporting), 5-token signal scale, 5-level judgement scale (adds "No information"). An **aim preflight** determines whether the study estimates the effect of *assignment* (ITT) or of *starting and adhering* (per-protocol), which gates Domain 4's signal path. Its own single-arm adaptation mirrors V2's (D1-SA benchmark signals 1S.1–1S.5, D2-SA signals 2S.1–2S.3, D4 = NA in code with no LLM call). The companion's migration notes map V1 ↔ V2 vocabulary conservatively.

### 6.6 QUADAS-3 v1.2 — diagnostic test accuracy ⚙ *(standalone document pending — this digest is the current sharable reference)*

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

### 6.7 QUADAS-2 ⚙ (per-run alternative) → `quadas2_shareable.md`

The classic 2011 tool most published reviews still use (Whiting et al., Ann Intern Med 2011;155:529-536). **4 domains / 11 signals** (Patient Selection 3; Index Test 2; Reference Standard 2; Flow & Timing 4), signal scale `Y / N / U`, judgements **Low / High / Unclear**, dual RoB + applicability on domains 1–3. Applicability is framed against the **review question in PIRT terms** (vs. QUADAS-3's ideal-trial framing) — same free-text context input, different meaning. Decision tree: all Y → Low; any N → High; else Unclear. Shares QUADAS-3's estimate extractor and per-estimate path. GRADE: Unclear → −1 conservative; Low/High as above.

### 6.8 AMSTAR-2 — systematic reviews ⚙ *(standalone document pending — this digest is the current sharable reference)*

- **Source:** Shea BJ et al., "AMSTAR 2: a critical appraisal tool for systematic reviews," BMJ 2017;358:j4008. Registered for SR with and without meta-analysis (items 11/12/15 carry a "No meta-analysis conducted" path).
- **Structurally unlike the primary-study tools.** It scores **16 checklist items** (not bias domains), each rated **Yes / Partial Yes / No** (some Yes/No only), and its headline output is an **overall confidence rating** — High / Moderate / Low / Critically low — *not* a GRADE certainty. GRADE, indirectness, and imprecision are all skipped for review papers.
- **The 16 items** (★ = critical, the published default set {2, 4, 7, 9, 11, 13, 15}): 1 PICO components in the research question · **2★** protocol established before the review · 3 explanation of study-design selection · **4★** comprehensive literature search · 5 study selection in duplicate · 6 data extraction in duplicate · **7★** list of excluded studies with justification · 8 adequate description of included studies · **9★** satisfactory risk-of-bias technique · 10 funding sources of included studies · **11★** appropriate meta-analysis methods · 12 impact of RoB on the meta-analysis · **13★** accounting for RoB when interpreting results · 14 explanation and discussion of heterogeneity · **15★** investigation of publication bias · 16 conflicts of interest of the review.
- **Per-item scoring:** the LLM answers each item's Y/N *sub-criteria* (transcribed from the checklist + guidance document); a pure decision function derives the item rating. Logic types: `all_required` (Yes iff every sub-criterion Y), `one_of`, `tiered` (Partial Yes = the partial-tier sub-criteria; Yes = those plus the yes-tier), `rob_design` (item 9 — evaluated per included design; a both-designs review takes the lower rating), `meta_design` (item 11 — design-aware Yes/No).
- **Preflight:** one call determines `review_includes` (rct / nrsi / both — items 9 and 11 are design-aware) and `meta_analysis` (was quantitative synthesis performed). When no synthesis was performed, items 11/12/15 are set to "No meta-analysis conducted" **in code, with no LLM call** (the NA-cascade pattern, §10). Calls per paper: 1 preflight + ≤ 16 item calls.
- **Overall confidence** (the published algorithm): a *critical flaw* = a critical item rated No; a *non-critical weakness* = a non-critical item rated No (Partial Yes and "No meta-analysis conducted" are not flaws). **High** = 0 critical flaws, ≤ 1 weakness · **Moderate** = 0 critical, > 1 weakness · **Low** = exactly 1 critical flaw · **Critically low** = ≥ 2 critical flaws.
- **Display caution:** AMSTAR-2's labels collide with the RoB vocabulary with opposite polarity — "High" is *good* here and *bad* for RoB tools. Any UI or export must key badge/colour semantics on the tool, not the label string.
- Out of scope in the current version: per-run custom critical-item sets; umbrella reviews / NMA (routed 📐, §2.3); reviewer override of item ratings.

---

## 7. Reporting-guideline agents

### 7.1 The shared contract ⚙ *(standalone documents pending — this digest is the current sharable reference)*

Reporting-guideline adherence is a *reporting* signal, deliberately separate from the risk-of-bias judgement: poor adherence does not prove poor methods, and perfect adherence does not prove rigor — but unreported methods cannot be appraised, and missing items correlate empirically with methodological weakness.

Every guideline checker follows one contract:

- A module-level **item catalog**: `{id, section, text, …}` per checklist entry, transcribed from the published statement. Sub-items (10a/10b…) are separate entries.
- **One LLM call per paper** (not per item): the model receives the PDF + the item catalog and returns, per item, `{adhered: true|false|"n/a", evidence: "verbatim or near-verbatim snippet"}`.
- **Score** = adhered ÷ applicable. Items judged not applicable (e.g. adverse-event items for non-invasive imaging; registration items for retrospective records reviews) are excluded from **both** numerator and denominator — an N/A never penalizes.
- Run **once per paper** (outcome units share it), and lazily — only after at least one RoB domain call has succeeded.

### 7.2 The deployed guidelines ⚙

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

### 7.4 Critical-item tiering and RoB integration 📐

The deployed checkers score items flat; the OGAI Stage-3 reference goes further, and implementers extending the reporting layer should adopt it:

- **Three item tiers.** *Critical* items — absence raises internal-validity concerns (for CONSORT: sequence generation 8a, allocation concealment 9, implementation 10, blinding 11a, primary-outcome statistical methods 12a, participant flow 13a/13b/16, primary results with effect size and precision 17a). *Important* items — absence reduces interpretability/reproducibility. *Minor* items — administrative detail.
- **Adherence quality categories:** Complete (all critical, ≤ 1 important missing) / Adequate (all critical, 2–3 important missing) / Partial (1–2 critical missing) / Inadequate (≥ 3 critical missing — the study cannot be fully appraised, and GRADE should consider a risk-of-bias downgrade on the basis that unreported methods are more likely deficient).
- **Reporting-gap → RoB coupling.** A RoB domain that depends on unreported information cannot be judged Low: unreported allocation concealment caps RoB 2 Domain 1 at *Some concerns*; unreported STARD blinding items map onto the QUADAS index-test domain; PRISMA vs. AMSTAR-2 overlap is resolved by letting AMSTAR-2 own the *quality* judgement while the PRISMA check records what was *reported* (no double-counting).

---

## 8. GRADE-domain agents (per-paper)

These three agents produce the appraisal platform's per-(paper × outcome) certainty rating. **Do not confuse this with the body-of-evidence GRADE agent** (§9.3) — the disambiguation table in `quality_appraisal_grade_shareable.md` is the canonical statement of the difference. In one line: this path rates *one appraised study* on the three domains a single paper can support (risk of bias, indirectness, imprecision); the GRADE agent rates *one pooled outcome* on all five downgrade + three upgrade domains.

### 8.1 Indirectness ⚙ → `quality_appraisal_grade_shareable.md` §4

One LLM call judges the four PICO subdomains — population, intervention, comparator, outcome — each on a 4-level scale (`direct / probably_direct / probably_not_direct / not_direct`), plus a surrogate-outcome flag. Judged **against the reviewer's target PICO** when supplied; otherwise falls back to outcome-surrogacy assessment (the other three subdomains default toward `probably_direct` unless the as-conducted PICO is unusually narrow). The GRADE handbook's surrogate rule is baked into the prompt: surrogates rate down unless a strong, well-established correlation with patient-important outcomes exists — a criterion rarely fulfilled. A pure severity tree aggregates: none (0) / serious (−1: one `not_direct` or ≥ 2 `probably_not_direct`) / very serious (−2: two `not_direct`) / extremely serious (−3: ≥ 3 `not_direct`).

### 8.2 Imprecision ⚙ → `quality_appraisal_grade_shareable.md` §5

One LLM call judges four subdomains on the mirror-image scale (`precise / probably_precise / probably_not_precise / not_precise`): **CI width** vs. decision thresholds (the reviewer's optional MID-benefit/MID-harm pair when supplied, else line-of-no-effect + clinical importance), **sample size** adequacy, **event count** (binary outcomes only — N/A for continuous, normalized so it never contributes to severity), and **fragility** (large relative effects from few events; p just under 0.05 with small N). The same severity tree yields 0/−1/−2/−3. The call also reports the inferred outcome type and the extracted N / events / CI summary for display.

### 8.3 The per-paper GRADE combiner ⚙ → `quality_appraisal_grade_shareable.md` §§1–3, 6

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

### 8.4 Outcome extraction ⚙ → `outcome_extraction_shareable.md`

The agent that produces the outcome axis of §5: one call per paper returns a list of *separately appraisable* outcomes (`{name, description, measure, timing, outcome_type, is_primary}`), conservatively split — one outcome with several statistics (HR + KM curve + median survival for overall survival) is **one** outcome, because over-splitting costs a full appraisal pass per spurious entry. Advisory and fully optional: every consumer must fall back to appraising the primary outcome alone.

---

## 9. Evidence-synthesis agents

Downstream of per-study appraisal: many appraised studies → bodies of evidence. Digests only; each companion is the document of record.

### 9.1 Pooling / meta-analysis agent ⚙ → `pooling_meta_analysis_shareable.md`

The model-free pooling engine plus its extraction bridge. Per-study effect sizes (OR / RR / RD from 2×2; MD / SMD from means; **IRR from events + person-time, never a count table**; HR from reported estimates; any measure from a pre-computed estimate + CI), inverse-variance fixed/random-effects pooling with DL / REML / Paule-Mandel τ², heterogeneity (Q, I², τ², prediction interval), Egger + trim-and-fill. The bridge groups many studies' extracted outcomes into bodies of evidence — **one body per outcome × comparison × timepoint × design class; randomized and non-randomized studies never pool together** — picks the measure, maps raw arm data or reported effects, and quarantines what cannot be reconciled with named warnings. Outcome-name harmonization (dictionary-first, LLM-for-the-gaps) makes differently-worded outcomes group. Hand-off to GRADE is raw numbers only — no certainty decisions.

### 9.2 Per-study evidence table (Table 2) ⚙ → `table2_evidence_table_shareable.md`

The guideline-panel evidence table: **one row = study × outcome × comparison × timepoint**, transcribing each study's *reported* results — explicitly no pooling. Dual-mode: assemble from already-extracted tags (zero model calls) or extract in isolation. Covers study-id building, metric canonicalization, direction-of-benefit inference, statistical reconciliation, and quality-rating mapping.

### 9.3 Body-of-evidence GRADE agent ⚙ → `grade_certainty_shareable.md`

The GRADE agent proper: consumes one pooled outcome and rates certainty across **all five downgrade domains** (risk of bias aggregated across studies by pooled weight; inconsistency from I²/Q; indirectness — reviewer-supplied with an optional LLM assist; imprecision from the pooled CI vs. null/MIDs + optimal information size; publication bias from Egger/trim-fill, gated at k ≥ 10) **plus the three upgrade domains** (large effect, dose-response, opposing plausible confounding — gated to non-randomized bodies with no downgrades), then anticipated absolute effects and Summary-of-Findings rows. Rate randomized and non-randomized evidence as separate bodies. A downgrades-only draft variant exists (`grade_certainty_downgrades_shareable.md`); it under-rates non-randomized bodies that qualify for rating up — share the full document unless the draft is specifically wanted.

### 9.4 Systematic-review synthesis pipeline ⚙ → `synthesis_meta_analysis_shareable.md`

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
- **Start with the ⚙ subset.** The deployed pipeline is a complete, working system with the *simple* classifier and *flat* reporting scores. The 📐 layers (primacy rules as structured output, design-feature cross-validation, red-flag re-routing, critical-item tiering, stepped-wedge appraisal) each bolt onto a defined seam — none require re-architecting.
- **Guard the type strings.** The study-type string is the join key across classifier → extraction catalog → routing registry → UI. A single mismatch ("Cross-sectional (analytical)" vs. "Cross-Sectional (Analytical)") silently drops a design to *skipped*. Pin the vocabulary in one place and test membership in all three tables.
- **Respect the mutually-exclusive unit axes.** Reject a paper carrying both outcome and estimate selections at request time — charging happens before classification, so the conflict must be caught early.
- **Per-domain calls are parallelizable per unit;** paper-level calls are not repeated per unit. A 3-outcome cohort paper is: 1 classify + 1 extract + 1 guideline + 3 × (preflight-dependent domain calls + indirectness + imprecision) + 3 combiner runs.
- **Attach the PDF when you can; degrade deliberately when you cannot** (§4.7). Never chunk a holistic judgement; never merge chunked thinking traces; treat an empty text extraction as "scanned PDF, needs OCR", not as an empty paper.
- **Expose the machinery.** A read-only developer view returning every prompt template, item catalog, and decision-tree source is cheap and is what makes an AI appraisal defensible to methodologists. Transparency is part of the methodology, not an accessory.
- **Mirroring.** Like the companions, this document is designed to be distributed verbatim outside its home repository. Cross-references are to sibling shareable filenames only; keep the set together.





