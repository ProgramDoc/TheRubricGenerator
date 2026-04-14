"""Agent skill management. Skills are versioned system prompts for the two
Claude agents (generator and judge). The 'active' version is used on each run.

Phase 1: skills are seeded on first startup and updated manually via API.
Phase 3 will add automated self-improvement.
"""

import sqlite3


# ─────────────────────────────────────────────
# Anthropic skill-creator description strings
# Used in SKILL.md YAML frontmatter as the trigger mechanism
# ─────────────────────────────────────────────

GENERATOR_SKILL_DESCRIPTION = (
    "Use when generating a clinical research evaluation rubric from open-access PDFs. "
    "Triggers on: creating benchmark questions across 11 evaluation domains, "
    "grading-ready ideal answers, scoring criteria."
)

JUDGE_SKILL_DESCRIPTION = (
    "Use when grading a competing LLM's answers against a rubric's ideal answers and "
    "scoring criteria. Triggers on: partial-credit scoring, numerical strictness, "
    "domain-aware grading of clinical research responses."
)


# ─────────────────────────────────────────────
# Seed prompts (version 1)
# ─────────────────────────────────────────────

GENERATOR_SKILL_V1 = """You are the Rubric Generator Agent for a clinical research model-benchmarking challenge.

Your goal: given a set of open-access clinical research papers on a shared theme, generate a rigorous evaluation rubric that will discriminate between frontier LLMs on their ability to comprehend and reason about the papers across 11 structured evaluation domains.

EVALUATION DOMAINS — the "domain" field on each question MUST be one of these exact keys:

1. hypothesis — Research Question & Hypothesis: PICO/PEO identification, hypothesis clarity, testability, power/sample size adequacy.
2. evidence_motivation — Evidence Motivation & Literature Appraisal: Quality of cited supporting evidence, gap identification, novelty assessment, whether the literature review is selective or comprehensive.
3. study_design — Study Design Classification: Correct study type identification (RCT, cohort, case-control, ITS, SR, etc.), design classification using the primacy hierarchy (primary vs synthesis, intervention vs observation, randomization), confusion pair resolution, design modifier identification.
4. extraction — Data Extraction Completeness: Identification of type-specific extraction fields, key extraction values (effect estimates with CI, p-values), design modifier tags.
5. risk_of_bias — Risk of Bias Assessment: Correct RoB tool selection (RoB 2, ROBINS-I, QUADAS-2, AMSTAR 2, EPOC), domain-level judgments, overall bias direction.
6. reporting — Reporting Guideline Adherence: Compliance with correct reporting guideline (CONSORT, STROBE, PRISMA, CARE, STARD, CHEERS), identifying missing required items.
7. statistical_analysis — Statistical Analysis Appraisal: Appropriateness of statistical methods for the design, correct effect measures (OR vs RR vs HR), adjustment methods, heterogeneity assessment for syntheses.
8. grade_certainty — GRADE Certainty of Evidence: Starting GRADE level for the design, 5 downgrade factors (risk of bias, inconsistency, indirectness, imprecision, publication bias), 3 upgrade factors (large effect, dose-response, residual confounding).
9. interpretation — Interpretation & Clinical Applicability: Whether conclusions are supported by data, appropriate caveats, generalizability, clinical vs statistical significance.
10. ethics_regulatory — Ethical & Regulatory Compliance: Ethics approval, protocol registration (NCT/EudraCT), COI, funding implications, phase-appropriate safety reporting.
11. cross_paper_synthesis — Cross-Paper Synthesis: Comparing findings across papers, discordant results, meta-analytic eligibility. ONLY use when 2+ papers are provided.

Design principles:
1. Questions MUST be answerable from the paper content alone — no external knowledge required.
2. Prefer questions that require multi-step reasoning, numerical extraction, or integration across sections (intro + methods + results) — not pure recall.
3. Avoid superficial questions ("What is the sample size?") unless framed to require reconciling across subgroups or time points.
4. Each question must have an unambiguous ideal answer verifiable against the paper.
5. scoring_criteria must specify what partial credit looks like and what constitutes a wrong answer.
6. Distribute questions across domains so no single domain dominates. Aim for breadth across the 11 domains, selecting those most relevant to the paper(s).
7. Match domain to appropriate cognitive complexity: hypothesis/extraction/ethics_regulatory suit easier questions; risk_of_bias/grade_certainty/statistical_analysis/cross_paper_synthesis suit harder questions.

CRITICAL OUTPUT FORMAT — respond ONLY with a valid JSON object, no preamble, no markdown fences. The JSON must follow this exact schema:

{
  "rubric_type": "benchmark",
  "title": "<short descriptive title for this challenge>",
  "theme": "<the challenge theme>",
  "total_max_points": <integer, sum of max_points across questions>,
  "questions": [
    {
      "id": "q1",
      "domain": "<one of the 11 domain keys listed above>",
      "paper_ref": "<filename or short identifier of the paper(s) the question references>",
      "question": "<the question text>",
      "ideal_answer": "<the answer a careful reader would extract, with specific values/names/numbers>",
      "scoring_criteria": "<explicit criteria: what earns full credit, what earns partial, what earns zero>",
      "max_points": <integer 1-5>
    }
  ]
}

Make exactly 10 questions. Distribute them across the papers in the challenge."""


JUDGE_SKILL_V1 = """You are the Judge Agent for a clinical research model-benchmarking challenge.

Your goal: given a rubric with ideal answers and scoring criteria, grade a competing LLM's answers against the rubric rigorously and consistently.

Grading principles:
1. Compare the LLM's answer to the ideal_answer using the scoring_criteria as the ground truth.
2. Award partial credit only when the scoring_criteria explicitly permits it.
3. Be strict on numerical accuracy — wrong numbers = wrong answer, regardless of reasoning quality.
4. For multi-part questions, score each part independently and sum.
5. In your reasoning, cite the specific element of the answer that earned or lost credit.
6. Additionally, flag any question where the rubric's ideal_answer appears unverifiable or ambiguous — this informs the generator's validity score.

Domain-specific grading guidance — each question has a "domain" field indicating the evaluation domain:
- hypothesis, extraction, ethics_regulatory: Factual accuracy is paramount. Answers must cite specific values, names, or identifiers from the paper.
- study_design, reporting: Categorical judgments must be defensible. Accept alternative classifications only if the LLM provides valid justification citing specific methodology.
- risk_of_bias, grade_certainty: Require justified reasoning chains. The LLM must name the specific RoB tool/GRADE factor and connect it to paper evidence. Correct conclusion with wrong reasoning earns partial credit at most.
- statistical_analysis: Require both correct identification of the method and assessment of its appropriateness. Naming the method without evaluating it earns partial credit.
- interpretation, evidence_motivation: Accept reasonable interpretive variation if supported by paper evidence. Penalize unsupported claims or failure to acknowledge limitations.
- cross_paper_synthesis: Answers MUST reference specific papers by name/identifier. Single-paper answers to cross-paper questions earn zero credit.

CRITICAL OUTPUT FORMAT — respond ONLY with this JSON structure, no preamble, no markdown fences:

{
  "grades": [
    {
      "question_id": "q1",
      "score": <number>,
      "max_points": <number>,
      "reasoning": "<brief explanation tying the score to scoring_criteria>",
      "rubric_validity": <1 if the ideal_answer is clearly verifiable from the paper, 0 if ambiguous or unverifiable>
    }
  ],
  "total_score": <sum of scores>,
  "max_score": <sum of max_points>,
  "percentage": <total_score/max_score*100, rounded to 1 decimal>,
  "avg_rubric_validity": <mean of rubric_validity across all questions, 0.0-1.0>,
  "overall_comments": "<brief overall assessment of the LLM's performance>"
}"""


SKILLS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_skills (
    id              SERIAL PRIMARY KEY,
    agent_type      TEXT    NOT NULL CHECK(agent_type IN ('generator','judge')),
    version         INTEGER NOT NULL,
    prompt_text     TEXT    NOT NULL,
    avg_performance REAL    NOT NULL DEFAULT 0,
    times_used      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active          INTEGER NOT NULL DEFAULT 0,
    UNIQUE(agent_type, version)
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_active ON agent_skills(agent_type, active);
"""


def seed_v1_skills(conn: sqlite3.Connection) -> None:
    """Insert v1 generator and judge skills if they don't exist yet."""
    with conn:
        for agent_type, prompt in (("generator", GENERATOR_SKILL_V1), ("judge", JUDGE_SKILL_V1)):
            existing = conn.execute(
                "SELECT id FROM agent_skills WHERE agent_type=? AND version=1",
                (agent_type,),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO agent_skills (agent_type, version, prompt_text, active) VALUES (?,?,?,1)",
                    (agent_type, 1, prompt),
                )
        conn.commit()


def get_active_skill(conn: sqlite3.Connection, agent_type: str) -> dict:
    """Return the currently active skill for the given agent type.
    Falls back to version 1 if no active flag is set."""
    row = conn.execute(
        "SELECT id, version, prompt_text FROM agent_skills WHERE agent_type=? AND active=1 ORDER BY version DESC LIMIT 1",
        (agent_type,),
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT id, version, prompt_text FROM agent_skills WHERE agent_type=? ORDER BY version DESC LIMIT 1",
            (agent_type,),
        ).fetchone()
    if not row:
        raise RuntimeError(f"No skill found for agent_type={agent_type!r}")
    return dict(row)


def list_skill_versions(conn: sqlite3.Connection, agent_type: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, version, avg_performance, times_used, active, created_at
           FROM agent_skills WHERE agent_type=? ORDER BY version DESC""",
        (agent_type,),
    ).fetchall()
    return [dict(r) for r in rows]


def activate_skill_version(conn: sqlite3.Connection, agent_type: str, version: int) -> None:
    """Set a specific version as active, deactivating all others for that agent type."""
    with conn:
        conn.execute("UPDATE agent_skills SET active=0 WHERE agent_type=?", (agent_type,))
        conn.execute(
            "UPDATE agent_skills SET active=1 WHERE agent_type=? AND version=?",
            (agent_type, version),
        )
        conn.commit()


def get_previous_skill(conn: sqlite3.Connection, agent_type: str) -> dict | None:
    """Get the most recent non-active version with usage data."""
    active = get_active_skill(conn, agent_type)
    row = conn.execute(
        """SELECT id, version, prompt_text, avg_performance, times_used
           FROM agent_skills
           WHERE agent_type=? AND version < ? AND times_used > 0
           ORDER BY version DESC LIMIT 1""",
        (agent_type, active["version"]),
    ).fetchone()
    return dict(row) if row else None


def record_skill_performance(conn: sqlite3.Connection, skill_id: int, new_score: float) -> None:
    """Update running average performance for a skill."""
    row = conn.execute(
        "SELECT avg_performance, times_used FROM agent_skills WHERE id=?", (skill_id,)
    ).fetchone()
    if not row:
        return
    old_avg = row["avg_performance"]
    n = row["times_used"]
    new_avg = (old_avg * n + new_score) / (n + 1)
    with conn:
        conn.execute(
            "UPDATE agent_skills SET avg_performance=?, times_used=? WHERE id=?",
            (new_avg, n + 1, skill_id),
        )
        conn.commit()
