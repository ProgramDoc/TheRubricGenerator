"""Risk-of-bias tools used by Quality Appraisal AI.

Each tool module exposes:
- a ``DOMAINS`` data structure describing signaling questions + elaborations,
- a pure-Python decision-tree function per domain,
- an ``overall`` function aggregating domain judgements,
- a ``run(pdf_bytes, fields, classification, primary_outcome, progress)`` entry
  point that the orchestrator calls.

Adding a new tool (ROBINS-I, QUADAS-2, AMSTAR-2, …) is a new module here plus
an entry in ``backend/quality_appraisal.py:STUDY_TYPE_REGISTRY``.
"""
