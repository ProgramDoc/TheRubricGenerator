"""Reporting-guideline adherence checklists used by Quality Appraisal AI.

Each module exposes an ``ITEMS`` list, a ``run(pdf_bytes, fields, classification)``
entry point that returns ``{item_id: {adhered, evidence}}``, and a
``prompt_catalog()`` helper for the developer view.

Adherence is scored as ``adhered_count / applicable_count`` where ``applicable``
excludes items the AI legitimately marks N/A for the paper.

Adding a new guideline (STROBE, PRISMA, STARD, SPIRIT, TRIPOD, …) is a new
module here plus an entry in ``backend/quality_appraisal.py:STUDY_TYPE_REGISTRY``.
"""
