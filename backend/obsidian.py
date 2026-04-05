"""Obsidian vault writer. Dumps challenges, skills, and papers as markdown
files to a vault directory on persistent disk. User syncs the vault externally
via Obsidian Sync / iCloud / rsync / git."""

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
                         participants: list[dict], papers: list[dict]) -> Path:
    """Write one markdown note summarizing a completed challenge.

    challenge: row dict from the challenges table
    rubric: the full rubric dict (parsed JSON)
    participants: list of model_participants rows with grades
    papers: list of paper rows {id, filename}

    Returns the path of the written file.
    """
    cid = challenge["id"]
    theme = challenge.get("theme") or "untitled"
    filename = f"{cid:04d}_{_slug(theme)}.md"
    path = vault_dir / "challenges" / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("---")
    lines.append(f"id: {cid}")
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
    lines.append("")
    lines.append(f"**Theme:** {theme}")
    lines.append(f"**Generator score:** {challenge.get('generator_score','—')}")
    lines.append(f"**Judge score:** {challenge.get('judge_score','—')}")
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
