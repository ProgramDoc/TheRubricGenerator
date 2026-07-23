# Generator Skill — Meta-Learner Program

This file is the **human-editable control plane** for the rubric-generator
autoresearch loop. It is re-read on every self-improvement experiment and
injected into the meta-Claude prompt as PROGRAM INSTRUCTIONS. Edit freely.

## Objective
Maximize the quality of benchmark rubrics generated from clinical research
PDFs, measured by: verifiability, specificity, discrimination, clarity.

## Current hypotheses (2026-04)
1. More concrete few-shot examples should improve specificity.
2. Over-long domain guidance degrades focus on numerical extraction.
3. Explicit partial-credit templates in scoring_criteria reduce judge
   validity penalties downstream.

## Search directions (ordered by priority)
- Add/refine ONE concrete good-question exemplar per edit
- Tighten verbose domain descriptions where they repeat each other
- Introduce stricter language about numerical extraction and CI reporting
- Experiment with tighter token budgets on scoring_criteria to force clarity

## Do NOT
- Change the output JSON schema (breaking change for downstream parsers)
- Remove the 11-domain enum — downstream filters depend on the exact keys
- Remove the "exactly 10 questions" count for default challenges
- Make multiple unrelated edits in one proposal (one change at a time)

## Accept criterion
An edit is kept iff lightweight eval quality score strictly improves.
Ties go to the simpler prompt (autoresearch simplicity rule).

## History notes
Append manual observations here after reviewing experiment artifacts in
`experiments/`. The meta-learner will read these as additional context.
