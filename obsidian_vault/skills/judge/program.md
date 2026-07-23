# Judge Skill — Meta-Learner Program

Human-editable control plane for the rubric-judge autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize grading consistency and discrimination: the judge should score
clearly-correct answers high and clearly-wrong answers low, with stable
partial-credit decisions under re-grading.

## Current hypotheses (2026-04)
1. Explicit per-domain rubric-interpretation guidance reduces variance.
2. Examples of partial-credit decisions improve cross-run consistency.
3. Overly strict numerical language causes false zeros on rounding.

## Search directions
- Tighten per-domain guidance where variance is highest
- Add one worked-example partial-credit decision per edit
- Refine the rubric_validity flag language so flags are rare but reliable
- Experiment with simpler overall_comments instructions

## Do NOT
- Change the output JSON schema (grades[], total_score, max_score, etc.)
- Remove rubric_validity — downstream generator scoring depends on it
- Collapse per-domain guidance into one block (kills domain-specific signal)
- Make multiple unrelated edits in one proposal

## Accept criterion
An edit is kept iff the lightweight discrimination score strictly
improves (correct_answer_score - wrong_answer_score grows).
Ties go to the simpler prompt.

## History notes
Append observations here; the meta-learner will see them.
