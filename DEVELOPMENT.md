# TheRubricGenerator — Development Log

**Repository:** https://github.com/ProgramDoc/TheRubricGenerator  
**Local path:** `/Users/thomaskingsley/Desktop/TheRubricGenerator`

---

## v1.0 — Initial build (current)

**Date:** 2026-04-02  
**Files:** `main.py`, `frontend/rubric_generator.html`, `frontend/login.html`

### Overview

First implementation of TheRubricGenerator — a two-LLM evaluation platform for clinical research papers.

### Architecture decisions

**Two-LLM design (non-circular)**
- Claude generates the rubric (reads PDF, produces questions + ideal answers + scoring criteria)
- OpenAI GPT-4o (or any configured model) answers the rubric questions as the evaluatee
- Claude grades the responses as an independent judge
- The human reviewer can edit the rubric between generation and evaluation to prevent full circularity

**Why not use Claude as both generator and evaluatee?**
Using the same model to generate questions, answer them, and grade them creates circularity — the model would likely align its answers with its own question framing. By using a different model (OpenAI) as the evaluatee, we get an independent signal.

Allan's observation: even with different models, there's a residual circularity risk if the grader (Claude) has a systematic bias toward its own rubric framing. The human-editable rubric is the mitigation: a domain expert reviews and adjusts the rubric before the evaluation runs.

**Session cookie name**
Used `rubricgen_session` (not `ogai_session`) to prevent cookie conflicts when running the annotator and rubric generator on the same domain/port in development.

**SQLite schema**
Fresh schema — no legacy TEXT PRIMARY KEY issues from the annotator. All tables use `INTEGER PRIMARY KEY AUTOINCREMENT` from the start.

### Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/rubrics/generate` | Claude reads PDF → generates rubric JSON |
| GET | `/api/rubrics?paper_id=N` | List rubrics for a paper |
| PUT | `/api/rubrics/{id}` | Save edited rubric |
| POST | `/api/evaluations/run` | Run second LLM against rubric |
| POST | `/api/evaluations/{id}/grade` | Claude grades responses |
| GET | `/api/evaluations/{id}/export` | Export CSV |
| POST | `/api/batch/evaluate` | Batch: generate + evaluate + grade multiple papers |

### Known limitations

- OpenAI does not natively accept PDF base64 in the same way Claude does. The evaluator currently receives the question text only, without the PDF. For full PDF access by GPT-4o, a file upload to OpenAI's Files API or extraction of text first would be needed. This is a v1.1 improvement.
- Batch evaluation is synchronous — large batches (>10 papers) will timeout on Render's free tier. An async job queue (Celery or background tasks) is the correct fix for v1.2.

### Next steps

- [ ] OpenAI Files API integration for PDF-aware evaluation
- [ ] Async batch evaluation with progress tracking
- [ ] IRR mode: multiple human rubric reviews before eval runs
- [ ] Export batch results as combined CSV/Excel
- [ ] Render deployment at `rubricgenerator.onrender.com`
