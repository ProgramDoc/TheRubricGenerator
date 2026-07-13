"""Evidence-synthesis package — building ASCO-style guideline evidence tables.

Ships two agents so far:

* **Table 2** (per-study evidence table) — the pure-Python calculation + assembly
  core (``table2``) and the outcomes extraction wiring (``table2_extract``).
  Methodology: ``docs/shareable/table2_evidence_table_shareable.md``.
* **Pooling / meta-analysis** (body-of-evidence effect-size engine, "T5" input):
  ``pooling`` (pure — inverse-variance fixed/random-effects pooling with DL/REML/PM
  tau2, heterogeneity, Egger + trim-and-fill; uses numpy + scipy.stats),
  ``pooling_prep`` (pure — the bridge that absorbs per-study extraction outputs,
  groups outcomes into bodies of evidence, and routes each to the pooler), and
  ``pooling_extract`` (model-wired outcome-data extraction + PDF->pool orchestrator).
  Methodology: ``docs/shareable/pooling_meta_analysis_shareable.md``.
"""

from .pooling import (
    eggers_test,
    grade_pooling_inputs,
    pool_outcome,
    study_effect,
    trim_and_fill,
)
from .pooling_harmonize import (
    apply_canonical_map,
    build_alias_index,
    harmonize_by_targets,
    match_outcome_name,
)
from .pooling_prep import (
    group_into_bodies,
    outcome_to_study_input,
    pool_body,
    pool_extractions,
    study_is_poolable,
)
from .table2 import (
    assemble_table2,
    build_study_id,
    canonicalize_metric,
    dedupe_rows,
    explode_rows,
    infer_direction,
    map_quality_rating,
    merge_injected_and_extracted,
    parse_effect_cell,
    reconcile_stats,
    seed_outcomes_from_universal,
)

__all__ = [
    "assemble_table2",
    "build_study_id",
    "canonicalize_metric",
    "dedupe_rows",
    "explode_rows",
    "infer_direction",
    "map_quality_rating",
    "merge_injected_and_extracted",
    "parse_effect_cell",
    "reconcile_stats",
    "seed_outcomes_from_universal",
    # Pooling / meta-analysis agent
    "pool_outcome",
    "study_effect",
    "eggers_test",
    "trim_and_fill",
    "grade_pooling_inputs",
    # Extraction -> pooling bridge
    "pool_extractions",
    "pool_body",
    "group_into_bodies",
    "outcome_to_study_input",
    "study_is_poolable",
    # Outcome harmonization (pure)
    "harmonize_by_targets",
    "build_alias_index",
    "match_outcome_name",
    "apply_canonical_map",
]
