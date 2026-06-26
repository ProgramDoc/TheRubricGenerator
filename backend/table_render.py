"""Pure renderers for the ASCO body-of-evidence tables (no LLM / DB / I/O).

Turn the JSON emitted by the synthesis agents into the two ASCO table layouts:

  * **Table 5** — GRADE Evidence Summary / Summary-of-Findings (one row per
    outcome), from :func:`backend.synthesis_agents.build_sof_row` outputs.
  * **Table 3** — Risk of Bias (one row per study, tool-routed domains), from
    ``/api/agents/appraise`` outputs.

Each renderer returns a structured row list **plus** an HTML fragment and CSV so
the UI can render natively or drop in the markup. Class names (``asco-t5`` /
``asco-t3``) are stable hooks for the UI team's stylesheet.
"""
from __future__ import annotations

import csv
import html
import io
from typing import Any

# ⊕ pip notation for the four GRADE certainty levels (Cochrane SoF convention).
GRADE_SYMBOL = {"High": "⊕⊕⊕⊕", "Moderate": "⊕⊕⊕○",
                "Low": "⊕⊕○○", "Very low": "⊕○○○"}


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _per1000(v: Any) -> str:
    return "—" if v is None else f"{v} per 1000"


def _ci(est: Any, lo: Any, hi: Any) -> str:
    if est is None:
        return "—"
    if lo is None or hi is None:
        return f"{est}"
    return f"{est} ({lo} to {hi})"


# ---------------------------------------------------------------------------
# Table 5 — GRADE Evidence Summary / Summary-of-Findings
# ---------------------------------------------------------------------------

TABLE5_COLUMNS = [
    "Outcome", "№ of participants (studies)", "Relative effect (95% CI)",
    "Assumed risk", "Risk with intervention", "Risk difference (95% CI)",
    "Certainty (GRADE)", "What happens",
]


def _rel_label(measure: Any, rel: dict) -> str:
    est = rel.get("estimate")
    if est is None:
        return "—"
    base = f"{measure} {est}" if measure else f"{est}"
    lo, hi = rel.get("ci_low"), rel.get("ci_high")
    return f"{base} ({lo} to {hi})" if lo is not None and hi is not None else base


def table5_rows(sof_rows: list[dict]) -> list[dict]:
    """Normalise SoF-row JSON to the Table-5 column set (one dict per outcome)."""
    out = []
    for s in sof_rows or []:
        rel = s.get("relative_effect") or {}
        ae = s.get("absolute_effects") or {}
        rd_ci = ae.get("rd_ci_per_1000") or [None, None]
        n_studies = s.get("n_studies") or 0
        outcome = s.get("outcome") or ""
        if s.get("timeframe"):
            outcome = f"{outcome} ({s['timeframe']})"
        out.append({
            "outcome": outcome,
            "participants": f"{s.get('n_participants') or '—'} "
                            f"({n_studies} stud{'y' if n_studies == 1 else 'ies'})",
            "relative_effect": _rel_label(s.get("measure"), rel),
            "assumed_risk": _per1000(ae.get("baseline_per_1000")),
            "intervention_risk": _per1000(ae.get("intervention_per_1000")),
            "risk_difference": _ci(ae.get("risk_difference_per_1000"), rd_ci[0], rd_ci[1]),
            "certainty": s.get("certainty") or "",
            "certainty_symbol": GRADE_SYMBOL.get(s.get("certainty"), ""),
            "reasons": "; ".join(
                f"{r.get('domain')} {'−' if r.get('direction') == 'downgrade' else '+'}{r.get('levels')}"
                for r in (s.get("certainty_reasons") or [])),
            "what_happens": s.get("explanation") or "",
        })
    return out


def _table5_html(rows: list[dict], pico: dict | None) -> str:
    p = " · ".join(f"{k.title()}: {_esc(v)}" for k, v in (pico or {}).items() if v)
    parts = ['<table class="asco-t5"><caption>Table 5. GRADE Evidence Summary</caption>']
    if p:
        parts.append(f'<thead><tr><th class="pico" colspan="{len(TABLE5_COLUMNS)}">{p}</th></tr></thead>')
    parts.append("<thead><tr>" + "".join(f"<th>{_esc(c)}</th>" for c in TABLE5_COLUMNS) + "</tr></thead><tbody>")
    for r in rows:
        cells = [r["outcome"], r["participants"], r["relative_effect"], r["assumed_risk"],
                 r["intervention_risk"], r["risk_difference"],
                 f"{r['certainty']} {r['certainty_symbol']}".strip(), r["what_happens"]]
        parts.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _table5_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TABLE5_COLUMNS + ["Certainty reasons"])
    for r in rows:
        w.writerow([r["outcome"], r["participants"], r["relative_effect"], r["assumed_risk"],
                    r["intervention_risk"], r["risk_difference"],
                    f"{r['certainty']} {r['certainty_symbol']}".strip(), r["what_happens"], r["reasons"]])
    return buf.getvalue()


def render_table5(sof_rows: list[dict], pico: dict | None = None) -> dict:
    rows = table5_rows(sof_rows)
    return {"columns": TABLE5_COLUMNS, "rows": rows,
            "html": _table5_html(rows, pico), "csv": _table5_csv(rows)}


# ---------------------------------------------------------------------------
# Table 3 — Risk of Bias (tool-routed; one sub-table per RoB tool)
# ---------------------------------------------------------------------------

def _domain_sort_key(k: str):
    # Domain ids are usually "1".."7"; sort numerically when possible.
    try:
        return (0, float(k))
    except (TypeError, ValueError):
        return (1, str(k))


def _judgement(cell: Any) -> str:
    if isinstance(cell, dict):
        return cell.get("judgement") or cell.get("overall") or ""
    return "" if cell is None else str(cell)


def _study_label(a: dict) -> str:
    return str(a.get("study_id") or a.get("study_label")
              or (f"Paper {a['paper_id']}" if a.get("paper_id") else a.get("assessed_outcome") or "Study"))


def table3_groups(appraisals: list[dict]) -> list[dict]:
    """Group appraisal outputs by RoB tool; each group is a study x domain matrix.

    Tools differ in their domains (RoB 2 = 5, ROBINS-I = 7, ...), so each tool is
    its own sub-table with that tool's domain names as columns.
    """
    by_tool: dict[str, list[dict]] = {}
    for a in appraisals or []:
        by_tool.setdefault(a.get("rob_tool") or "unknown", []).append(a)

    groups = []
    for tool, items in by_tool.items():
        # Column order/names from the union of domains seen (stable across studies).
        seen: dict[str, str] = {}
        for a in items:
            for k, v in (a.get("domains") or {}).items():
                seen.setdefault(k, (v.get("name") if isinstance(v, dict) else None) or f"Domain {k}")
        cols = sorted(seen.items(), key=lambda kv: _domain_sort_key(kv[0]))
        rows = []
        for a in items:
            d = a.get("domains") or {}
            rows.append({"study": _study_label(a),
                         "study_type": a.get("study_type"),
                         "domains": [_judgement(d.get(k)) for k, _ in cols],
                         "overall": a.get("overall") or ""})
        groups.append({"tool": tool, "domain_columns": [name for _, name in cols], "rows": rows})
    return groups


def _table3_html(groups: list[dict]) -> str:
    parts = ['<div class="asco-t3"><h4>Table 3. Risk of Bias</h4>']
    for g in groups:
        cols = ["Study"] + g["domain_columns"] + ["Overall"]
        parts.append(f'<table class="asco-t3-tool" data-tool="{_esc(g["tool"])}">')
        parts.append(f'<caption>{_esc(g["tool"])}</caption>')
        parts.append("<thead><tr>" + "".join(f"<th>{_esc(c)}</th>" for c in cols) + "</tr></thead><tbody>")
        for r in g["rows"]:
            cells = [r["study"], *r["domains"], r["overall"]]
            parts.append("<tr>" + "".join(
                f'<td class="rob-{_esc(str(c).lower().replace(" ", "-"))}">{_esc(c)}</td>' for c in cells) + "</tr>")
        parts.append("</tbody></table>")
    parts.append("</div>")
    return "".join(parts)


def _table3_csv(groups: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    for g in groups:
        w.writerow([f"Tool: {g['tool']}"])
        w.writerow(["Study"] + g["domain_columns"] + ["Overall"])
        for r in g["rows"]:
            w.writerow([r["study"], *r["domains"], r["overall"]])
        w.writerow([])
    return buf.getvalue()


def render_table3(appraisals: list[dict]) -> dict:
    groups = table3_groups(appraisals)
    return {"groups": groups, "html": _table3_html(groups), "csv": _table3_csv(groups)}
