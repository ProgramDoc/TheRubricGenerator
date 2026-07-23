# Search Strategist Skill — Meta-Learner Program

Human-editable control plane for the search strategist autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize search query recall and precision: the strategist should produce
Boolean queries that retrieve relevant papers while minimizing noise,
and should extract accurate PICO elements from research questions.

## Current hypotheses (2026-04)
1. Including both MeSH and free-text synonyms improves recall.
2. Explicit PICO extraction guidance reduces errors in complex questions.
3. Follow-up suggestions that are too generic reduce user engagement.

## Search directions
- Improve MeSH term selection accuracy
- Add guidance for handling multi-concept queries
- Refine follow-up question specificity

## Do NOT
- Change the output JSON schema (text, pico, search_query, follow_up_questions)
- Remove PICO extraction capability
- Make multiple unrelated edits in one proposal
