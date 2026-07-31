"""Source per-(study × outcome) risk-of-bias labels from the quality-appraisal tables.

This is the one impure module in the risk-of-bias path: it reads
``quality_appraisal_results`` and emits plain records for
:func:`pooling_prep.attach_rob`, which merges them onto the study dicts *before*
outcome harmonization. Everything downstream — resolution, carrying, weighting — is
pure and lives in ``pooling_prep`` / ``pooling`` / ``grade``.

It is deliberately separate from :mod:`grade_prep`, which documents itself as pure
(no model, no I/O) so it stays fast to unit-test.

Flow::

    rob_records_for_run(conn, qa_run_id)      # here: SQL -> records
      -> pooling_prep.attach_rob(studies, records)
      -> pooling_harmonize.harmonize_*        # canonicalizes rob_by_outcome keys too
      -> pooling_prep.pool_extractions        # resolve_rob per (study x outcome)
      -> grade_prep.grade_bodies              # reads studies[].rob, weights it
"""

from __future__ import annotations

import json
from typing import Any, Optional

# AMSTAR-2 rates a systematic review's *confidence* as High / Moderate / Low /
# Critically low, where "High" is GOOD -- the opposite polarity to every risk-of-bias
# instrument. Feeding one of its ratings into the risk-of-bias domain inverts the
# judgement, so a well-conducted review would trigger a two-level downgrade for being
# good. ("Critically low" is also absent from the severity map and would quietly land
# at severity 1 -- a second, opposite error in the same row.)
#
# The fix is exclusion at the source, not a severity entry: mapping these labels would
# legitimize routing systematic reviews into a body of primary-study evidence, which is
# a different methodological error. Systematic reviews are not primary studies.
EXCLUDED_ROB_TOOLS = ("amstar2",)

_SQL = """
SELECT r.paper_id       AS paper_id,
       r.outcome_json     AS outcome_json,
       r.assessed_outcome AS assessed_outcome,
       r.primary_outcome  AS primary_outcome,
       r.rob_overall      AS rob,
       r.rob_tool         AS rob_tool,
       r.study_type       AS study_type
  FROM quality_appraisal_results r
 WHERE r.run_id = ?
   AND r.status = 'ok'
   AND r.rob_overall IS NOT NULL
   AND r.rob_overall <> ''
"""


def _outcome_key(row: dict[str, Any]) -> Optional[str]:
    """The outcome name to key ``rob_by_outcome`` on.

    ``outcome_json.name`` first, because ``assessed_outcome`` is *composed* for
    prompt quality — "Quality of life — measured as KCCQ total symptom score — at
    8 months" — and a body of evidence is keyed on the short name ("Quality of
    life"). Keying on the composed form makes every per-outcome lookup miss, so a
    fully-appraised body reads as unappraised and is then refused by
    ``require_rob``. Rows predating the per-outcome columns carry no
    ``outcome_json`` and fall back to the older strings.
    """
    raw = row.get("outcome_json")
    if raw:
        try:
            oc = json.loads(raw) if isinstance(raw, str) else raw
            name = (oc or {}).get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (ValueError, TypeError):
            pass
    return ((row.get("assessed_outcome") or "").strip()
            or (row.get("primary_outcome") or "").strip()
            or None)


def rob_records_for_run(
    conn,
    qa_run_id: int,
    study_id_by_paper: Optional[dict[Any, str]] = None,
) -> list[dict[str, Any]]:
    """Read one quality-appraisal run's overall judgements as ``attach_rob`` records.

    Returns ``[{"study_id", "outcome", "rob", "rob_source": "tool", "rob_tool",
    "paper_id"}, ...]``. ``outcome`` is the outcome the instrument actually scored;
    ``None`` when the row records no outcome, which makes it a study-level label
    (tier 3 of the resolution ladder) rather than a per-outcome one.

    ``study_id_by_paper`` maps ``papers.id`` → the ``study_id`` used in the pooling
    inputs. Nothing in the pooling inputs carries ``paper_id``, so this join is the
    caller's to supply; without it the stringified paper id is used, which only
    matches if the caller built its study ids the same way.

    ``rob_source`` is always ``"tool"`` here: the enum records *who produced* the
    label, not what scope it has, so an instrument-supplied study-level label is
    still ``"tool"``.
    """
    rows = conn.execute(_SQL, (qa_run_id,)).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        if (d.get("rob_tool") or "").strip().lower() in EXCLUDED_ROB_TOOLS:
            continue

        paper_id = d.get("paper_id")
        study_id = (study_id_by_paper or {}).get(paper_id)
        if study_id is None:
            study_id = str(paper_id) if paper_id is not None else None
        if not study_id:
            continue

        outcome = _outcome_key(d)

        records.append({
            "study_id": study_id,
            "paper_id": paper_id,
            "outcome": outcome,
            "rob": (d.get("rob") or "").strip(),
            "rob_source": "tool",
            "rob_tool": d.get("rob_tool"),
        })
    return records
