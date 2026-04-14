"""Obsidian vault writer. Dumps challenges, skills, and papers as markdown
files to a vault directory on persistent disk. User syncs the vault externally
via Obsidian Sync / iCloud / rsync / git."""

import difflib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _slug(text: str) -> str:
    """Slugify for filenames."""
    s = re.sub(r"[^\w\s-]", "", text or "").strip().lower()
    s = re.sub(r"[-\s]+", "-", s)
    return s[:60] or "untitled"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def write_challenge_note(vault_dir: Path, challenge: dict, rubric: dict,
                         participants: list[dict], papers: list[dict],
                         user_info: dict | None = None,
                         generator_skill: dict | None = None,
                         judge_skill: dict | None = None,
                         experiment_history: list[dict] | None = None,
                         cost_estimate: dict | None = None) -> Path:
    """Write one markdown note summarizing a completed challenge.

    challenge: row dict from the challenges table
    rubric: the full rubric dict (parsed JSON)
    participants: list of model_participants rows with grades
    papers: list of paper rows {id, filename}
    user_info: optional {display_name, email}
    generator_skill: optional skill dict with version, prompt_text, avg_performance
    judge_skill: optional skill dict
    experiment_history: optional list of recent skill_experiments rows
    cost_estimate: optional cost dict

    Returns the path of the written file.
    """
    cid = challenge["id"]
    theme = challenge.get("theme") or "untitled"
    run_id = challenge.get("run_id") or f"#{cid}"
    filename = f"{cid:04d}_{_slug(theme)}.md"
    path = vault_dir / "challenges" / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("---")
    lines.append(f"id: {cid}")
    lines.append(f"run_id: \"{run_id}\"")
    lines.append(f"title: \"{challenge.get('title','')}\"")
    lines.append(f"theme: \"{theme}\"")
    lines.append(f"status: {challenge.get('status','')}")
    lines.append(f"created_at: {challenge.get('created_at','')}")
    lines.append(f"completed_at: {challenge.get('completed_at','')}")
    lines.append(f"kind: {challenge.get('kind','manual')}")
    lines.append("tags: [challenge, benchmark]")
    lines.append("---")
    lines.append("")
    lines.append(f"# Challenge #{cid}: {challenge.get('title','')}")
    lines.append(f"**Run ID:** `{run_id}`")
    lines.append("")
    lines.append(f"**Theme:** {theme}")
    lines.append(f"**Generator score:** {challenge.get('generator_score','—')}")
    lines.append(f"**Judge score:** {challenge.get('judge_score','—')}")
    lines.append("")

    # User info
    if user_info:
        lines.append("## User")
        lines.append(f"- **Name:** {user_info.get('display_name', '—')}")
        lines.append(f"- **Email:** {user_info.get('email', '—')}")
        lines.append("")

    # Cost estimate
    if cost_estimate:
        lines.append("## Cost")
        lines.append(f"- **Estimated total:** {cost_estimate.get('total', '—')} credits")
        if "generator_cost" in cost_estimate:
            lines.append(f"- **Generator:** {cost_estimate['generator_cost']} credits")
        if "participant_cost" in cost_estimate:
            lines.append(f"- **Participants:** {cost_estimate['participant_cost']} credits")
        if "judge_cost" in cost_estimate:
            lines.append(f"- **Judge:** {cost_estimate['judge_cost']} credits")
        lines.append("")

    # Generator agent state
    if generator_skill:
        lines.append("## Generator Agent")
        lines.append(f"- **Skill version:** v{generator_skill.get('version', '?')}")
        lines.append(f"- **Avg performance:** {generator_skill.get('avg_performance', '—')}")
        lines.append(f"- **Times used:** {generator_skill.get('times_used', '—')}")
        prompt_preview = (generator_skill.get("prompt_text") or "")[:200]
        if prompt_preview:
            lines.append(f"- **Prompt preview:** `{prompt_preview}...`")
        lines.append("")

    # Judge agent state
    if judge_skill:
        lines.append("## Judge Agent")
        lines.append(f"- **Skill version:** v{judge_skill.get('version', '?')}")
        lines.append(f"- **Avg performance:** {judge_skill.get('avg_performance', '—')}")
        lines.append(f"- **Times used:** {judge_skill.get('times_used', '—')}")
        prompt_preview = (judge_skill.get("prompt_text") or "")[:200]
        if prompt_preview:
            lines.append(f"- **Prompt preview:** `{prompt_preview}...`")
        lines.append("")

    # Autoresearch experiment history
    if experiment_history:
        lines.append("## Recent Autoresearch Experiments")
        lines.append("")
        lines.append("| # | Agent | Version | Before | After | Status | Description |")
        lines.append("|---|-------|---------|--------|-------|--------|-------------|")
        for i, exp in enumerate(experiment_history[:10]):
            lines.append(
                f"| {i+1} | {exp.get('agent_type','')} | v{exp.get('skill_version','')} "
                f"| {exp.get('metric_before','—')} | {exp.get('metric_after','—')} "
                f"| {exp.get('status','')} | {(exp.get('description','') or '')[:60]} |"
            )
        lines.append("")

    # Papers
    lines.append("## Papers")
    lines.append("")
    for p in papers:
        lines.append(f"- `{p.get('filename','?')}` (paper #{p.get('id','?')})")
    lines.append("")

    # Rubric
    lines.append("## Rubric")
    lines.append("")
    for q in rubric.get("questions", []):
        lines.append(f"### {q.get('id','?')} · {q.get('domain','')}")
        lines.append("")
        lines.append(f"**Paper:** {q.get('paper_ref','')}")
        lines.append(f"**Max points:** {q.get('max_points','?')}")
        lines.append("")
        lines.append(f"**Question:** {q.get('question','')}")
        lines.append("")
        lines.append(f"**Ideal answer:** {q.get('ideal_answer','')}")
        lines.append("")
        lines.append(f"**Scoring criteria:** {q.get('scoring_criteria','')}")
        lines.append("")

    # Participants
    lines.append("## Model Results")
    lines.append("")
    for mp in sorted(participants, key=lambda p: -(p.get("total_score") or 0)):
        lines.append(f"### {mp.get('model_id','?')} ({mp.get('provider','?')})")
        lines.append("")
        lines.append(f"- **Accuracy:** {mp.get('accuracy','?')}")
        lines.append(f"- **Speed bonus:** {mp.get('speed_bonus','?')}")
        lines.append(f"- **Total score:** {mp.get('total_score','?')}")
        lines.append(f"- **Answer time:** {mp.get('answer_time_ms','?')} ms")
        lines.append(f"- **Judge time:** {mp.get('judge_time_ms','?')} ms")
        lines.append(f"- **Status:** {mp.get('status','?')}")
        if mp.get("error_message"):
            lines.append(f"- **Error:** {mp['error_message']}")
        lines.append("")

        # Answers and grades per question
        try:
            answers = json.loads(mp.get("answer_json") or "{}").get("responses", [])
            grades  = json.loads(mp.get("grade_json") or "{}").get("grades", [])
            grade_by_q = {g.get("question_id"): g for g in grades}
            for a in answers:
                qid = a.get("question_id", "?")
                g = grade_by_q.get(qid, {})
                lines.append(f"**{qid}** — score {g.get('score','?')}/{g.get('max_points','?')}")
                lines.append("")
                lines.append(f"> {a.get('answer','')[:500]}")
                lines.append("")
                if g.get("reasoning"):
                    lines.append(f"_Judge reasoning:_ {g['reasoning']}")
                    lines.append("")
        except Exception:
            lines.append("_(answer/grade JSON could not be parsed)_")
            lines.append("")

    lines.append(f"\n---\n_Written to vault at {_now()}_\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_skill_note(vault_dir: Path, agent_type: str, active_skill: dict,
                     version_history: list[dict]) -> Path:
    """Write/overwrite SKILL_{agent_type}.md with the active prompt and history."""
    path = vault_dir / f"SKILL_{agent_type}.md"
    lines: list[str] = []
    lines.append("---")
    lines.append(f"agent_type: {agent_type}")
    lines.append(f"active_version: {active_skill.get('version','?')}")
    lines.append(f"last_updated: \"{_now()}\"")
    lines.append("tags: [skill, agent]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {agent_type.capitalize()} Agent Skill")
    lines.append("")
    lines.append(f"**Active version:** v{active_skill.get('version','?')}")
    lines.append("")
    lines.append("## Active Prompt")
    lines.append("")
    lines.append("```")
    lines.append(active_skill.get("prompt_text", ""))
    lines.append("```")
    lines.append("")
    lines.append("## Version History")
    lines.append("")
    lines.append("| Version | Active | Avg Performance | Times Used | Created |")
    lines.append("|---------|--------|-----------------|------------|---------|")
    for v in version_history:
        active_mark = "✓" if v.get("active") else ""
        lines.append(
            f"| v{v.get('version','?')} | {active_mark} | "
            f"{v.get('avg_performance', 0):.3f} | {v.get('times_used', 0)} | "
            f"{v.get('created_at','')} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────
# Agent SKILL.md + program.md + history.md + experiments/
# Anthropic skill-creator format + Karpathy autoresearch pattern
# ─────────────────────────────────────────────────────────────

def _skill_dir(vault_dir: Path, agent_type: str) -> Path:
    """Return the per-agent skill directory, creating it if needed."""
    p = vault_dir / "skills" / agent_type
    (p / "experiments").mkdir(parents=True, exist_ok=True)
    return p


def _timestamp_slug() -> str:
    """Compact UTC timestamp for filenames: 20260410T142100Z"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _yaml_str(s: str) -> str:
    """Escape a string for single-line YAML double-quoted form."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def write_agent_skill_file(vault_dir: Path, agent_type: str,
                           active_skill: dict,
                           description: str | None = None,
                           metadata: dict | None = None) -> Path:
    """Write skills/{agent_type}/SKILL.md in Anthropic skill-creator format.

    YAML frontmatter: name, description, version, agent_type, avg_performance,
      times_used, last_updated, tags.
    Body sections: Overview, When to Use, How It Works, Examples, Output Format,
      Active Prompt.

    Regenerated on every call — no merge.
    """
    sdir = _skill_dir(vault_dir, agent_type)
    path = sdir / "SKILL.md"

    name = "rubric-generator" if agent_type == "generator" else "rubric-judge"
    title = "Rubric Generator" if agent_type == "generator" else "Rubric Judge"

    # Description is the Anthropic trigger mechanism — pull from explicit arg
    # or fall back to a sane default per agent.
    if not description:
        if agent_type == "generator":
            description = (
                "Use when generating a clinical research evaluation rubric from open-access PDFs. "
                "Triggers on: creating benchmark questions across 11 evaluation domains, "
                "grading-ready ideal answers, scoring criteria."
            )
        else:
            description = (
                "Use when grading a competing LLM's answers against a rubric's ideal answers and "
                "scoring criteria. Triggers on: partial-credit scoring, numerical strictness, "
                "domain-aware grading of clinical research responses."
            )

    version = active_skill.get("version", "?")
    avg_perf = active_skill.get("avg_performance", 0) or 0
    times_used = active_skill.get("times_used", 0) or 0
    prompt_text = active_skill.get("prompt_text", "") or ""

    lines: list[str] = []
    lines.append("---")
    lines.append(f'name: {name}')
    lines.append(f'description: "{_yaml_str(description)}"')
    lines.append(f'version: {version}')
    lines.append(f'agent_type: {agent_type}')
    lines.append(f'avg_performance: {avg_perf:.4f}' if isinstance(avg_perf, (int, float)) else f'avg_performance: 0')
    lines.append(f'times_used: {times_used}')
    lines.append(f'last_updated: "{_now()}"')
    lines.append("tags: [skill, agent, " + agent_type + "]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    if agent_type == "generator":
        lines.append("## Overview")
        lines.append("")
        lines.append(
            "Given a set of open-access clinical research papers on a shared theme, generate "
            "a rigorous evaluation rubric that discriminates between frontier LLMs across "
            "11 structured evaluation domains."
        )
        lines.append("")
        lines.append("## When to Use")
        lines.append("")
        lines.append("- Building a benchmark rubric from one or more PDF papers")
        lines.append("- Generating 10-question assessments with domain coverage")
        lines.append("- Creating scoring criteria with ideal answers verifiable from paper content")
        lines.append("")
        lines.append("## How It Works")
        lines.append("")
        lines.append("1. Reads the PDF content and the challenge theme")
        lines.append("2. Selects question domains (hypothesis, study_design, risk_of_bias, …) appropriate to the papers")
        lines.append("3. Emits a JSON rubric with question, ideal_answer, scoring_criteria, max_points")
        lines.append("4. Enforces verifiability and discrimination via the Active Prompt instructions")
        lines.append("")
        lines.append("## Examples")
        lines.append("")
        lines.append("See `experiments/challenge_*.md` for real input/output pairs captured from prior runs.")
        lines.append("")
        lines.append("## Output Format")
        lines.append("")
        lines.append("JSON object with keys: `rubric_type`, `title`, `theme`, `total_max_points`, `questions[]`.")
        lines.append("Each question: `id`, `domain`, `paper_ref`, `question`, `ideal_answer`, `scoring_criteria`, `max_points`.")
        lines.append("")
    else:  # judge
        lines.append("## Overview")
        lines.append("")
        lines.append(
            "Given a rubric with ideal answers and scoring criteria, grade a competing LLM's "
            "answers against the rubric rigorously and consistently, with domain-aware strictness."
        )
        lines.append("")
        lines.append("## When to Use")
        lines.append("")
        lines.append("- Scoring LLM answers against a benchmark rubric")
        lines.append("- Applying partial credit rules from scoring_criteria")
        lines.append("- Flagging ambiguous or unverifiable ideal answers (rubric_validity)")
        lines.append("")
        lines.append("## How It Works")
        lines.append("")
        lines.append("1. Receives the rubric + one model's answers")
        lines.append("2. Grades each question using the ideal_answer and scoring_criteria")
        lines.append("3. Applies domain-specific strictness (factual for extraction; reasoning-chain for RoB/GRADE; cross-paper refs required for synthesis)")
        lines.append("4. Emits per-question scores with reasoning and a rubric_validity flag")
        lines.append("")
        lines.append("## Examples")
        lines.append("")
        lines.append("See `experiments/challenge_*.md` for real grading pairs captured from prior runs.")
        lines.append("")
        lines.append("## Output Format")
        lines.append("")
        lines.append("JSON object with keys: `grades[]`, `total_score`, `max_score`, `percentage`, `avg_rubric_validity`, `overall_comments`.")
        lines.append("Each grade: `question_id`, `score`, `max_points`, `reasoning`, `rubric_validity`.")
        lines.append("")

    lines.append("## Active Prompt")
    lines.append("")
    lines.append("```")
    lines.append(prompt_text)
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append(f"_Auto-generated from `agent_skills` table at {_now()}._")
    lines.append("_See `program.md` for human-editable meta-learner guidance, "
                 "`history.md` for version table, and `experiments/` for per-run artifacts._")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_agent_program_file(vault_dir: Path, agent_type: str,
                             default_content: str) -> Path:
    """Write skills/{agent_type}/program.md ONLY if it doesn't exist.

    The program.md file is the human-editable meta-learner control plane.
    Automation must never overwrite human edits, so this is idempotent
    after the first call.
    """
    sdir = _skill_dir(vault_dir, agent_type)
    path = sdir / "program.md"
    if not path.exists():
        path.write_text(default_content, encoding="utf-8")
    return path


def write_agent_history_file(vault_dir: Path, agent_type: str,
                             versions: list[dict]) -> Path:
    """Write skills/{agent_type}/history.md with the version table.
    Regenerated on every call."""
    sdir = _skill_dir(vault_dir, agent_type)
    path = sdir / "history.md"

    lines: list[str] = []
    lines.append("---")
    lines.append(f"agent_type: {agent_type}")
    lines.append(f'last_updated: "{_now()}"')
    lines.append("tags: [skill, history]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {agent_type.capitalize()} Skill — Version History")
    lines.append("")
    lines.append("| Version | Active | Avg Performance | Times Used | Created |")
    lines.append("|---------|--------|-----------------|------------|---------|")
    for v in versions:
        active_mark = "✓" if v.get("active") else ""
        avg = v.get("avg_performance", 0) or 0
        lines.append(
            f"| v{v.get('version','?')} | {active_mark} | "
            f"{avg:.3f} | {v.get('times_used', 0)} | "
            f"{v.get('created_at','')} |"
        )
    lines.append("")
    lines.append(f"_Auto-generated at {_now()}._")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_experiment_note(vault_dir: Path, agent_type: str,
                          experiment_data: dict) -> Path:
    """Write a per-experiment record for the autoresearch loop.

    Filename: skills/{agent_type}/experiments/{timestamp}_{status}_v{version}.md

    experiment_data keys:
        timestamp (iso), status (keep|discard|crash), skill_version,
        metric_before, metric_after, agent_type, baseline_prompt,
        candidate_prompt (str | None), description, eval_description,
        keep_reason, challenge_id (int | None)
    """
    sdir = _skill_dir(vault_dir, agent_type)
    status = experiment_data.get("status", "unknown")
    version = experiment_data.get("skill_version", "?")
    ts = _timestamp_slug()
    filename = f"{ts}_{status}_v{version}.md"
    path = sdir / "experiments" / filename

    baseline_prompt = experiment_data.get("baseline_prompt", "") or ""
    candidate_prompt = experiment_data.get("candidate_prompt", "") or ""

    lines: list[str] = []
    lines.append("---")
    lines.append(f'timestamp: "{experiment_data.get("timestamp", _now())}"')
    lines.append(f"status: {status}")
    lines.append(f"agent_type: {agent_type}")
    lines.append(f"skill_version: {version}")
    lines.append(f'metric_before: {experiment_data.get("metric_before", 0) or 0:.4f}')
    lines.append(f'metric_after: {experiment_data.get("metric_after", 0) or 0:.4f}')
    cid = experiment_data.get("challenge_id")
    if cid is not None:
        lines.append(f"challenge_id: {cid}")
    lines.append("tags: [experiment, autoresearch]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {agent_type.capitalize()} Experiment — {status.upper()} (v{version})")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Status:** {status}")
    lines.append(f"- **Metric before:** {experiment_data.get('metric_before', 0) or 0:.4f}")
    lines.append(f"- **Metric after:** {experiment_data.get('metric_after', 0) or 0:.4f}")
    lines.append(f"- **Decision reason:** {experiment_data.get('keep_reason', '—')}")
    if experiment_data.get("description"):
        lines.append(f"- **Description:** {experiment_data['description']}")
    if experiment_data.get("eval_description"):
        lines.append(f"- **Eval notes:** {experiment_data['eval_description']}")
    lines.append("")

    # Unified diff (most useful view)
    if baseline_prompt and candidate_prompt:
        lines.append("## Diff (baseline → candidate)")
        lines.append("")
        lines.append("```diff")
        diff_lines = list(difflib.unified_diff(
            baseline_prompt.splitlines(),
            candidate_prompt.splitlines(),
            fromfile="baseline",
            tofile="candidate",
            lineterm="",
            n=2,
        ))
        # Cap at 200 lines to keep notes scannable
        lines.extend(diff_lines[:200])
        if len(diff_lines) > 200:
            lines.append(f"... (truncated, {len(diff_lines) - 200} more diff lines)")
        lines.append("```")
        lines.append("")

    # Baseline prompt
    lines.append("## Baseline Prompt")
    lines.append("")
    lines.append("```")
    lines.append(baseline_prompt)
    lines.append("```")
    lines.append("")

    # Candidate prompt (if any)
    if candidate_prompt:
        lines.append("## Candidate Prompt")
        lines.append("")
        lines.append("```")
        lines.append(candidate_prompt)
        lines.append("```")
        lines.append("")

    lines.append(f"_Written at {_now()}_")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_challenge_agent_note(vault_dir: Path, agent_type: str,
                               challenge_id: int,
                               challenge_data: dict) -> Path:
    """Write a per-challenge autoresearch snapshot for one agent.

    Filename: skills/{agent_type}/experiments/challenge_{cid:04d}.md

    For generator, challenge_data keys:
        challenge_id, theme, skill_version, skill_id, paper_filenames,
        rubric (full dict), gen_score, gen_time_ms, avg_rubric_validity
    For judge, challenge_data keys:
        challenge_id, theme, skill_version, skill_id,
        graded_participants (list), judge_score, judge_time_ms
    """
    sdir = _skill_dir(vault_dir, agent_type)
    path = sdir / "experiments" / f"challenge_{challenge_id:04d}.md"

    theme = challenge_data.get("theme", "")
    version = challenge_data.get("skill_version", "?")

    lines: list[str] = []
    lines.append("---")
    lines.append(f"challenge_id: {challenge_id}")
    lines.append(f"agent_type: {agent_type}")
    lines.append(f"skill_version: {version}")
    lines.append(f'theme: "{_yaml_str(theme)}"')
    if agent_type == "generator":
        lines.append(f'metric: {challenge_data.get("gen_score", 0) or 0:.4f}')
    else:
        lines.append(f'metric: {challenge_data.get("judge_score", 0) or 0:.4f}')
    lines.append("tags: [experiment, autoresearch, challenge-snapshot]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {agent_type.capitalize()} — Challenge #{challenge_id}")
    lines.append("")
    lines.append(f"**Theme:** {theme}")
    lines.append(f"**Skill version:** v{version}")
    lines.append("")
    # Link back to the combined challenge note
    lines.append(f"**Full challenge note:** [../../../challenges/{challenge_id:04d}](../../challenges/)")
    lines.append("")

    if agent_type == "generator":
        # Input summary
        lines.append("## Input")
        lines.append("")
        papers = challenge_data.get("paper_filenames", [])
        lines.append(f"- **Papers ({len(papers)}):**")
        for fn in papers[:10]:
            lines.append(f"  - `{fn}`")
        if len(papers) > 10:
            lines.append(f"  - … and {len(papers) - 10} more")
        lines.append("")

        # Output summary
        rubric = challenge_data.get("rubric", {}) or {}
        questions = rubric.get("questions", [])
        lines.append("## Output")
        lines.append("")
        lines.append(f"- **Rubric type:** {rubric.get('rubric_type', 'benchmark')}")
        lines.append(f"- **Total questions:** {len(questions)}")
        lines.append(f"- **Total max points:** {rubric.get('total_max_points', 0)}")
        lines.append("")
        lines.append("### Sample Questions (first 2)")
        lines.append("")
        for q in questions[:2]:
            lines.append(f"**{q.get('id','?')}** ({q.get('domain','?')}, {q.get('max_points','?')} pts)")
            lines.append(f"- **Q:** {q.get('question','')}")
            lines.append(f"- **Ideal:** {(q.get('ideal_answer','') or '')[:300]}")
            lines.append("")

        # Metrics
        lines.append("## Metrics")
        lines.append("")
        lines.append(f"- **Generator score:** {challenge_data.get('gen_score', 0) or 0:.4f}")
        lines.append(f"- **Generation time:** {challenge_data.get('gen_time_ms', 0)} ms")
        lines.append(f"- **Avg rubric validity:** {challenge_data.get('avg_rubric_validity', 0) or 0:.4f}")
        lines.append("")

    else:  # judge
        participants = challenge_data.get("graded_participants", [])
        lines.append("## Input")
        lines.append("")
        lines.append(f"- **Participants graded:** {len(participants)}")
        for p in participants[:5]:
            lines.append(f"  - `{p.get('model_id','?')}` ({p.get('provider','?')})")
        lines.append("")

        # Output summary — first 2 graded participants' grades
        lines.append("## Output (first 2 participants)")
        lines.append("")
        for p in participants[:2]:
            lines.append(f"### {p.get('model_id','?')}")
            lines.append(f"- **Accuracy:** {p.get('accuracy', 0) or 0:.4f}")
            lines.append(f"- **Total score:** {p.get('total_score', 0) or 0:.2f}")
            try:
                grade_data = json.loads(p.get("grade_json") or "{}")
                grades = grade_data.get("grades", [])[:3]
                lines.append("- **Sample grades (first 3):**")
                for g in grades:
                    lines.append(
                        f"  - {g.get('question_id','?')}: "
                        f"{g.get('score','?')}/{g.get('max_points','?')}"
                    )
            except Exception:
                pass
            lines.append("")

        # Metrics
        lines.append("## Metrics")
        lines.append("")
        lines.append(f"- **Judge score:** {challenge_data.get('judge_score', 0) or 0:.4f}")
        lines.append(f"- **Judge time:** {challenge_data.get('judge_time_ms', 0)} ms")
        lines.append("")

    lines.append(f"_Written at {_now()}_")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────
# Lab conversation notes
# ─────────────────────────────────────────────────────────────

def write_lab_conversation_note(vault_dir: Path, agent_type: str,
                                 session: dict, messages: list[dict],
                                 user_info: dict | None = None) -> Path:
    """Write a lab conversation as a markdown note.

    Path: lab/{agent_type}/{session_id}_{slug}.md
    """
    sid = session.get("id", 0)
    title = session.get("title", "untitled")
    filename = f"{sid:04d}_{_slug(title)}.md"
    lab_dir = vault_dir / "lab" / agent_type
    lab_dir.mkdir(parents=True, exist_ok=True)
    path = lab_dir / filename

    lines: list[str] = []
    lines.append("---")
    lines.append(f"id: {sid}")
    lines.append(f"agent_type: {agent_type}")
    lines.append(f'title: "{_yaml_str(title)}"')
    if user_info:
        lines.append(f'user: "{user_info.get("display_name", user_info.get("email", ""))}"')
    lines.append(f'created_at: "{session.get("created_at", "")}"')
    lines.append(f"tags: [lab, {agent_type}]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    agent_labels = {
        "search_strategist": "AI Search Strategist",
        "statistician": "AI Statistician",
        "study_appraiser": "Study Appraiser",
        "hypothesis_generator": "Hypothesis Generator",
        "literature_reviewer": "Literature Reviewer",
    }
    lines.append(f"**Agent:** {agent_labels.get(agent_type, agent_type)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            lines.append("### User")
        elif role == "assistant":
            lines.append("### Assistant")
        else:
            lines.append(f"### {role}")
        lines.append("")
        lines.append(content)
        lines.append("")

        # Include metadata summaries
        meta = msg.get("metadata", {})
        if meta.get("analysis_plan"):
            lines.append("**Analysis Plan:**")
            plan = meta["analysis_plan"]
            for k, v in plan.items():
                lines.append(f"- {k}: {v}")
            lines.append("")
        if meta.get("hypotheses"):
            lines.append("**Hypotheses:**")
            for h in meta["hypotheses"]:
                lines.append(f"- **{h.get('id', '?')}**: {h.get('statement', '')}")
            lines.append("")
        if meta.get("citation_list"):
            lines.append("**Citations:**")
            for c in meta["citation_list"]:
                lines.append(f"- {c.get('authors', '')} ({c.get('year', '')}) {c.get('title', '')}")
            lines.append("")

    lines.append(f"\n---\n_Written to vault at {_now()}_\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
