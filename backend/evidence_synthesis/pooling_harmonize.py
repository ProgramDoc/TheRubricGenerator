"""Outcome harmonization — cluster differently-worded outcomes into one.

The grouping bridge (``pooling_prep.group_into_bodies``) keys a body of evidence on
the *normalized outcome name*, which merges spelling/case/punctuation variants but
NOT synonyms: "All-cause mortality", "Death from any cause", and "Overall mortality"
land in three separate bodies even though a reviewer would pool them. This module is
the layer that maps such synonyms onto **one canonical outcome** before grouping, by
annotating each outcome object with ``canonical_outcome`` (which grouping then
prefers over the raw name).

Two modes, layered deterministic-first (the same philosophy as the dual-mode
extraction — provided/deterministic first, model fallback for the gaps):

* **Dictionary mode (pure, zero model calls).** The reviewer supplies the target
  outcomes with alias lists; ``harmonize_by_targets`` matches each extracted name to
  a canonical (exact-normalized, then a conservative token-subset / Jaccard fuzzy
  match). This is authoritative and reproducible.
* **LLM clustering mode (one batch call, in ``pooling_extract``).** For names no
  dictionary covered, an LLM clusters the *distinct* names across all studies into
  canonical outcomes (optionally constrained to the reviewer's target list). The
  clusterer lives in ``pooling_extract`` (the model-wired module); the pure apply /
  match / index logic lives here.

Pure module — no model, no framework. Full methodology:
``docs/shareable/pooling_meta_analysis_shareable.md`` §9.7.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .pooling_prep import _norm

# The key harmonization writes onto each outcome object; grouping prefers it.
CANONICAL_KEY = "canonical_outcome"


# ---------------------------------------------------------------------------
# 1. Distinct names + a canonical alias index
# ---------------------------------------------------------------------------

def distinct_outcome_names(studies: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Collect the verbatim outcome names across all studies -> occurrence count.

    Names already carrying a ``canonical_outcome`` are skipped — they are resolved."""
    counts: dict[str, int] = {}
    for s in studies:
        for oc in s.get("outcomes") or []:
            if not isinstance(oc, dict) or oc.get(CANONICAL_KEY):
                continue
            name = oc.get("name") or oc.get("outcome_name")
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


def build_alias_index(targets: Optional[Iterable[Any]]) -> dict[str, str]:
    """Build a normalized-alias -> canonical lookup from review-defined outcomes.

    ``targets`` items are either a plain string (canonical name, no aliases) or a
    dict ``{"canonical"|"name"|"outcome": str, "aliases"|"synonyms": [str, ...]}``.
    Every canonical maps to itself; each alias maps to its canonical. Later entries
    win on collision.
    """
    index: dict[str, str] = {}
    for t in targets or []:
        if isinstance(t, str):
            canon, aliases = t, []
        elif isinstance(t, dict):
            canon = t.get("canonical") or t.get("name") or t.get("outcome")
            aliases = t.get("aliases") or t.get("synonyms") or []
        else:
            continue
        if not canon:
            continue
        index[_norm(canon)] = canon
        for a in aliases:
            if a:
                index[_norm(a)] = canon
    return index


# ---------------------------------------------------------------------------
# 2. Matching one name to a canonical
# ---------------------------------------------------------------------------

def match_outcome_name(
    name: Any,
    alias_index: dict[str, str],
    *,
    fuzzy: bool = True,
    min_jaccard: float = 0.6,
) -> Optional[str]:
    """Resolve one outcome name to a canonical via the alias index, or None.

    Exact normalized match first. If ``fuzzy``, accept a canonical/alias whose token
    set is a subset of the name's (or vice versa) — e.g. "all cause mortality" vs
    "all-cause mortality any cause" — or whose Jaccard token overlap ≥ ``min_jaccard``.
    Conservative by design: it only cleans variants, it does not equate distinct
    concepts (that's what the alias lists / the LLM clusterer are for).
    """
    n = _norm(name)
    if not n:
        return None
    if n in alias_index:
        return alias_index[n]
    if not fuzzy:
        return None
    n_tok = set(n.split())
    if not n_tok:
        return None
    best: Optional[str] = None
    best_score = 0.0
    for alias_norm, canon in alias_index.items():
        a_tok = set(alias_norm.split())
        if not a_tok:
            continue
        inter = len(n_tok & a_tok)
        if inter == 0:
            continue
        subset = a_tok <= n_tok or n_tok <= a_tok
        jac = inter / len(n_tok | a_tok)
        if subset or jac >= min_jaccard:
            score = 1.0 if subset else jac
            if score > best_score:
                best_score, best = score, canon
    return best


# ---------------------------------------------------------------------------
# 3. Applying a name -> canonical map onto studies (pure, copies)
# ---------------------------------------------------------------------------

def apply_canonical_map(
    studies: Iterable[dict[str, Any]],
    name_to_canonical: dict[str, str],
) -> list[dict[str, Any]]:
    """Return copies of ``studies`` with each outcome annotated ``canonical_outcome``
    where its normalized name is in ``name_to_canonical`` (keyed by normalized name).

    Never mutates the caller's dicts; never overwrites an existing canonical."""
    out: list[dict[str, Any]] = []
    for s in studies:
        s2 = dict(s)
        outs = s.get("outcomes")
        if isinstance(outs, list):
            new_outs: list[Any] = []
            for oc in outs:
                if isinstance(oc, dict) and not oc.get(CANONICAL_KEY):
                    name = oc.get("name") or oc.get("outcome_name")
                    canon = name_to_canonical.get(_norm(name)) if name else None
                    if canon:
                        oc = {**oc, CANONICAL_KEY: canon}
                new_outs.append(oc)
            s2["outcomes"] = new_outs
        out.append(s2)
    return out


# ---------------------------------------------------------------------------
# 4. Deterministic (dictionary) harmonization — pure, zero model calls
# ---------------------------------------------------------------------------

def harmonize_by_targets(
    studies: Iterable[dict[str, Any]],
    targets: Optional[Iterable[Any]],
    *,
    fuzzy: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map extracted outcome names onto reviewer-defined canonical outcomes. Pure.

    Returns ``(harmonized_studies, report)`` where ``report`` is one row per distinct
    unresolved name: ``{"name", "canonical" (or None), "count"}``. Unmatched names
    keep their own raw name as the grouping key (so they still pool among themselves)
    and are surfaced in the report for review — never silently dropped.
    """
    studies = list(studies)
    index = build_alias_index(targets)
    mapping: dict[str, str] = {}
    report: list[dict[str, Any]] = []
    for name, count in distinct_outcome_names(studies).items():
        canon = match_outcome_name(name, index, fuzzy=fuzzy)
        if canon:
            mapping[_norm(name)] = canon
        report.append({"name": name, "canonical": canon, "count": count})
    return apply_canonical_map(studies, mapping), report


def clusters_to_map(clusters: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Turn LLM cluster output ``[{"canonical", "members": [...]}]`` into a
    normalized-name -> canonical map (the shape ``apply_canonical_map`` wants)."""
    mapping: dict[str, str] = {}
    for cl in clusters or []:
        if not isinstance(cl, dict):
            continue
        canon = cl.get("canonical") or cl.get("label")
        if not canon:
            continue
        for member in cl.get("members") or []:
            if member:
                mapping[_norm(member)] = canon
        mapping.setdefault(_norm(canon), canon)
    return mapping
