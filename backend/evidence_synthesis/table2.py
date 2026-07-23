"""Table 2 (per-study evidence table) — pure calculation + assembly core.

One row = (study x outcome x comparison x timepoint), transcribing each study's
REPORTED results. This module is the pure-Python, model-free half of the Table 2
agent: study-id building, metric canonicalization, direction inference, statistical
reconciliation, quality-rating mapping, row explosion, dedupe, dual-mode merge, and
the top-level ``assemble_table2`` composer.

It has NO model dependency and imports only the standard library, so it can be unit
-tested in isolation. The model-touching extraction wiring lives in
``backend/evidence_synthesis/table2_extract.py``.

Full methodology + rationale: ``docs/shareable/table2_evidence_table_shareable.md``.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# 1. Study id
# ---------------------------------------------------------------------------

def build_study_id(authors: Any, year: Any) -> str:
    """Build a "First-author et al., YYYY" study label.

    1 author -> "Smith, YYYY"; 2 -> "Smith & Jones, YYYY"; 3+ -> "Smith et al., YYYY".
    Missing year omits the ", YYYY" suffix. Never fabricates an absent author.
    """
    names = _normalize_authors(authors)
    yr = _clean_year(year)
    yr_suffix = f", {yr}" if yr else ""

    if not names:
        return f"Unknown study{yr_suffix}"
    if len(names) == 1:
        core = _surname(names[0])
    elif len(names) == 2:
        core = f"{_surname(names[0])} & {_surname(names[1])}"
    else:
        core = f"{_surname(names[0])} et al."
    return f"{core}{yr_suffix}"


def _normalize_authors(authors: Any) -> list[str]:
    """Coerce list/str author input into a clean list of non-empty name strings.

    Delimiter precedence avoids the Vancouver double-count trap: a "Family, Given;
    Family, Given" list splits on ';' (or ' and '/'&'), NOT on the commas separating
    each surname from its initials. Comma-splitting is the last resort; a lone
    "Smith, JQ" then yields one author because the initials fragment is filtered out.
    """
    if authors is None:
        return []
    if isinstance(authors, (list, tuple)):
        raw_list = [str(a) for a in authors]
    else:
        s = str(authors)
        if ";" in s:
            raw_list = re.split(r"\s*;\s*", s)
        elif re.search(r"\band\b|&", s):
            raw_list = re.split(r"\s*(?:\band\b|&)\s*", s)
        else:
            raw_list = re.split(r"\s*,\s*", s)
    out: list[str] = []
    for a in raw_list:
        a = a.strip()
        if a and not re.fullmatch(r"[A-Z]\.?(?:\s*[A-Z]\.?)*", a):
            out.append(a)
    return out


def _surname(name: str) -> str:
    """Best-effort surname extraction from a single author string."""
    name = name.strip().strip(".")
    if not name:
        return ""
    if "," in name:                       # "Family, Given/Initials" -> family before comma
        return name.split(",")[0].strip()
    parts = name.split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]\.?(?:[A-Z]\.?)*", parts[-1]):
        return parts[0]                   # "Smith JQ" -> "Smith"
    return parts[-1]                       # "Jane Q. Smith" -> "Smith"


def _clean_year(year: Any) -> str:
    """Return a 4-digit year string if one can be recovered, else ""."""
    if year is None:
        return ""
    m = re.search(r"(1[89]\d{2}|20\d{2})", str(year))
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 2. Effect-measure families + canonicalization
# ---------------------------------------------------------------------------

METRIC_FAMILIES: dict[str, str] = {
    "HR": "time_to_event",
    "OR": "ratio",
    "RR": "ratio",
    "IRR": "ratio",
    "MD": "difference",
    "SMD": "difference",
    "RD": "difference",
}

_METRIC_SYNONYMS: dict[str, str] = {
    "hr": "HR", "hazard ratio": "HR",
    "or": "OR", "odds ratio": "OR",
    "rr": "RR", "risk ratio": "RR", "relative risk": "RR",
    "irr": "IRR", "rate ratio": "IRR", "incidence rate ratio": "IRR",
    "md": "MD", "mean difference": "MD",
    "wmd": "MD", "weighted mean difference": "MD",
    "smd": "SMD",
    "standardized mean difference": "SMD", "standardised mean difference": "SMD",
    "cohen's d": "SMD", "cohens d": "SMD",
    "hedges g": "SMD", "hedges' g": "SMD", "hedges's g": "SMD",
    "rd": "RD", "risk difference": "RD",
    "absolute risk difference": "RD", "absolute difference": "RD", "ard": "RD",
}


def canonicalize_metric(raw: Any) -> tuple[Optional[str], str]:
    """Map a raw effect-measure name onto (canonical_metric, family) where family in
    {ratio, difference, time_to_event, narrative, unknown}."""
    if raw is None:
        return None, "unknown"
    s = re.sub(r"\s+", " ", str(raw)).strip()
    if not s:
        return None, "unknown"

    low = s.lower().strip(".").replace("’", "'")   # normalize curly apostrophes

    narrative_markers = {
        "narrative", "qualitative", "descriptive", "nr", "not reported",
        "not estimable", "ne", "n/a", "na",
    }
    if low in narrative_markers:
        return None, "narrative"
    if low in _METRIC_SYNONYMS:
        canon = _METRIC_SYNONYMS[low]
        return canon, METRIC_FAMILIES[canon]

    upper = s.upper()
    if upper in METRIC_FAMILIES:
        return upper, METRIC_FAMILIES[upper]
    return s, "unknown"


def null_value_for(family: str) -> Optional[float]:
    """ratio/time_to_event -> 1.0; difference -> 0.0; narrative/unknown -> None."""
    if family in ("ratio", "time_to_event"):
        return 1.0
    if family == "difference":
        return 0.0
    return None


# ---------------------------------------------------------------------------
# 3. Direction of effect
# ---------------------------------------------------------------------------

FAVOURS_INTERVENTION = "favours_intervention"
FAVOURS_COMPARATOR = "favours_comparator"
NO_DIFFERENCE = "no_difference"
NOT_ESTIMABLE = "not_estimable"

DIRECTIONS = (FAVOURS_INTERVENTION, FAVOURS_COMPARATOR, NO_DIFFERENCE, NOT_ESTIMABLE)


def infer_direction(
    family: str,
    estimate: Optional[float],
    ci_lower: Optional[float],
    ci_upper: Optional[float],
    reported_direction: Optional[str] = None,
    outcome_favorable_direction: str = "lower",
) -> str:
    """Infer which arm an effect favours. Returns one of DIRECTIONS.

    ``outcome_favorable_direction`` sets desirability: "lower" (default; adverse
    outcomes like mortality/symptom burden -> below-null favours intervention),
    "higher" (desirable outcomes like survival/response), or "neutral" (unknown ->
    not_estimable). A CI touching the null is treated as no_difference. A confidently
    parsed ``reported_direction`` wins; else the CI/estimate computation; else
    not_estimable.
    """
    reported = _parse_reported_direction(reported_direction)

    null = null_value_for(family)
    if family in ("narrative", "unknown") or null is None:
        return reported or NOT_ESTIMABLE

    lo, hi = _order_ci(ci_lower, ci_upper)
    computed: Optional[str] = None
    if lo is not None and hi is not None:
        if lo > null and hi > null:
            computed = _side_to_favour("above", outcome_favorable_direction)
        elif lo < null and hi < null:
            computed = _side_to_favour("below", outcome_favorable_direction)
        else:
            computed = NO_DIFFERENCE
    elif estimate is not None:
        if estimate > null:
            computed = _side_to_favour("above", outcome_favorable_direction)
        elif estimate < null:
            computed = _side_to_favour("below", outcome_favorable_direction)
        else:
            computed = NO_DIFFERENCE

    if reported is not None:
        return reported
    return computed if computed is not None else NOT_ESTIMABLE


def _side_to_favour(side: str, favorable_direction: Optional[str]) -> str:
    fd = (favorable_direction or "").strip().lower()
    if fd == "lower":
        return FAVOURS_INTERVENTION if side == "below" else FAVOURS_COMPARATOR
    if fd == "higher":
        return FAVOURS_INTERVENTION if side == "above" else FAVOURS_COMPARATOR
    return NOT_ESTIMABLE


def _parse_reported_direction(reported: Optional[str]) -> Optional[str]:
    """Normalize a free-text reported direction into a canonical constant, or None.

    Short tokens (ns/ne/nr/na/null) match only as WHOLE WORDS — substring matching
    would mislabel benign words ("consistent" contains "ns") and, because reported
    direction wins, silently flip a significant result to no_difference.
    """
    if not reported:
        return None
    r = str(reported).strip().lower()
    if r in DIRECTIONS:
        return r
    if any(k in r for k in ("favours intervention", "favors intervention",
                            "favour treatment", "favor treatment",
                            "intervention better", "in favour of intervention",
                            "in favor of intervention")):
        return FAVOURS_INTERVENTION
    if any(k in r for k in ("favours comparator", "favors comparator",
                            "favours control", "favors control",
                            "control better", "comparator better", "placebo better")):
        return FAVOURS_COMPARATOR
    if any(k in r for k in ("no difference", "no significant",
                            "non-significant", "not significant")):
        return NO_DIFFERENCE
    if any(k in r for k in ("not estimable", "not reported", "cannot be estimated")):
        return NOT_ESTIMABLE
    tokens = set(re.findall(r"[a-z']+", r))
    if tokens & {"ns", "null"}:
        return NO_DIFFERENCE
    if tokens & {"ne", "nr", "na"}:
        return NOT_ESTIMABLE
    return None


def _order_ci(lo: Optional[float], hi: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    if lo is not None and hi is not None and lo > hi:
        return hi, lo
    return lo, hi


# ---------------------------------------------------------------------------
# 4. Parsing a free-text effect cell
# ---------------------------------------------------------------------------

_NUM = r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?|[-+]?\d*\.\d+|[-+]?\d+"
_CI_SEP = r"\s*(?:–|—|-|to|,)\s*"    # en/em dash, hyphen, "to", comma
_P_OP = {"<": "lt", "<=": "le", "≤": "le", ">": "gt", ">=": "ge", "≥": "ge", "=": "eq"}


def parse_effect_cell(text: Any) -> Optional[dict[str, Any]]:
    """Parse a reported effect string into
    {estimate, ci_lower, ci_upper, p_value, p_operator}. Never guesses a missing part.

    The p-value requires an explicit comparator ('p='/'p<'/'p<='), so 'grp2' is not
    read as 'p=2', and any captured p outside (0, 1] is rejected. Handles bracketed
    and unbracketed CIs (the latter needs a "CI"/"confidence interval" anchor).
    """
    if text is None:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    if not s:
        return None

    result: dict[str, Any] = {
        "estimate": None, "ci_lower": None, "ci_upper": None,
        "p_value": None, "p_operator": None,
    }
    work = s

    p_match = re.search(
        r"(?:^|[^A-Za-z])[pP]\s*(<=|>=|≤|≥|<|>|=)\s*(" + _NUM + r")", s
    )
    if p_match:
        pv = _to_float(p_match.group(2))
        if pv is not None and 0.0 < pv <= 1.0:
            result["p_value"] = pv
            result["p_operator"] = _P_OP.get(p_match.group(1), "eq")
            work = (work[: p_match.start()] + " " + work[p_match.end():]).strip()

    ci_match = re.search(
        r"[\(\[]\s*(?:\d{1,3}%?\s*(?:CI|confidence interval)[:\s]*)?"
        r"(" + _NUM + r")" + _CI_SEP + r"(" + _NUM + r")\s*[\)\]]",
        work, flags=re.IGNORECASE,
    )
    if not ci_match:
        ci_match = re.search(
            r"(?:\d{1,3}\s*%?\s*)?(?:CI|confidence interval)[:\s]*"
            r"(" + _NUM + r")" + _CI_SEP + r"(" + _NUM + r")",
            work, flags=re.IGNORECASE,
        )
    if ci_match:
        lo = _to_float(ci_match.group(1))
        hi = _to_float(ci_match.group(2))
        result["ci_lower"], result["ci_upper"] = _order_ci(lo, hi)
        work = (work[: ci_match.start()] + " " + work[ci_match.end():]).strip()

    est_match = re.search(_NUM, work)
    if est_match:
        result["estimate"] = _to_float(est_match.group(0))

    if all(v is None for v in result.values()):
        return None
    return result


def _to_float(token: Any) -> Optional[float]:
    if token is None:
        return None
    t = str(token).replace(",", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _split_p(value: Any) -> tuple[Optional[float], Optional[str]]:
    """Split a reported p value into (float, operator). "<0.001" -> (0.001, "lt")."""
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        v = float(value)
        return (v, "eq") if 0.0 < v <= 1.0 else (None, None)
    s = str(value).strip()
    m = re.search(r"(<=|>=|≤|≥|<|>|=)?\s*(" + _NUM + r")", s)
    if not m:
        return None, None
    v = _to_float(m.group(2))
    if v is None or not (0.0 < v <= 1.0):
        return None, None
    return v, _P_OP.get(m.group(1) or "=", "eq")


# ---------------------------------------------------------------------------
# 5. Statistical reconciliation (fill ONLY missing values)
# ---------------------------------------------------------------------------

_Z_95 = 1.959964    # two-sided 95% normal quantile


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inv_phi(p: float) -> Optional[float]:
    """Inverse standard normal CDF via Acklam's rational approximation (no scipy)."""
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)


def _z_from_p(p: float) -> Optional[float]:
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    return _inv_phi(1.0 - p / 2.0)


def reconcile_stats(
    estimate: Optional[float],
    ci_lower: Optional[float],
    ci_upper: Optional[float],
    p_value: Optional[float],
    family: str,
    p_operator: Optional[str] = "eq",
) -> dict[str, Any]:
    """Fill ONLY missing stats from the reported ones. Never overwrite a reported value.

    Log scale for ratio/time_to_event, identity for difference, z = 1.959964. A
    *bounded* p (operator lt/gt/le/ge) is never used to derive an SE. Every derived
    value is named in the returned ``derived`` set. Never fabricates.
    """
    is_log = family in ("ratio", "time_to_event")
    is_diff = family == "difference"
    derived: set[str] = set()
    out: dict[str, Any] = {
        "estimate": estimate, "ci_lower": ci_lower, "ci_upper": ci_upper,
        "p_value": p_value, "p_operator": p_operator if p_value is not None else None,
        "se": None, "derived": derived,
    }
    if not (is_log or is_diff):
        return out

    ci_lower, ci_upper = _order_ci(ci_lower, ci_upper)
    out["ci_lower"], out["ci_upper"] = ci_lower, ci_upper

    if is_log:
        def fwd(v: Optional[float]) -> Optional[float]:
            return math.log(v) if (v is not None and v > 0) else None
        inv = math.exp
    else:
        def fwd(v: Optional[float]) -> Optional[float]:
            return v
        def inv(v: float) -> float:
            return v

    t_est = fwd(estimate) if estimate is not None else None
    t_lo = fwd(ci_lower) if ci_lower is not None else None
    t_hi = fwd(ci_upper) if ci_upper is not None else None

    if is_log:
        if estimate is not None and t_est is None:
            return out
        if (ci_lower is not None and t_lo is None) or (ci_upper is not None and t_hi is None):
            t_lo = t_hi = None

    se: Optional[float] = None
    if t_lo is not None and t_hi is not None:
        se = (t_hi - t_lo) / (2.0 * _Z_95)
    if se is None and t_est is not None and p_value is not None and p_operator == "eq":
        z_p = _z_from_p(p_value)
        if z_p and z_p != 0 and abs(t_est) > 0:
            se = abs(t_est) / z_p
    out["se"] = se

    if se is not None and t_est is not None and ci_lower is None and ci_upper is None:
        lo, hi = _order_ci(inv(t_est - _Z_95 * se), inv(t_est + _Z_95 * se))
        out["ci_lower"], out["ci_upper"] = lo, hi
        derived.update({"ci_lower", "ci_upper"})

    if p_value is None and se is not None and se > 0 and t_est is not None:
        z = t_est / se
        p = 2.0 * (1.0 - _phi(abs(z)))
        out["p_value"] = min(max(p, 1e-300), 1.0 - 1e-16)
        out["p_operator"] = "eq"
        derived.add("p_value")

    return out


# ---------------------------------------------------------------------------
# 6. Quality rating (risk-of-bias overall -> 3-level quality band)
# ---------------------------------------------------------------------------

_QUALITY_HIGH = "High"
_QUALITY_INTERMEDIATE = "Intermediate"
_QUALITY_LOW = "Low"


def map_quality_rating(rob_overall: Any, rob_tool: Optional[str] = None) -> Optional[str]:
    """Map a risk-of-bias overall label onto a 3-level quality band (High/Intermediate/Low).

    INVERSION for risk-of-bias tools: low risk of bias -> High quality. AMSTAR-2 is
    NOT inverted (High confidence -> High quality). ``rob_tool`` resolves a bare
    "High"/"Low"; without it, only unambiguous labels resolve (bare High/Low -> None).
    """
    if rob_overall is None:
        return None
    label = re.sub(r"\s+", " ", str(rob_overall)).strip().lower()
    tool = (rob_tool or "").strip().lower()

    if label == "moderate":
        return _QUALITY_INTERMEDIATE

    if tool:
        if "amstar" in tool:
            if label == "high":
                return _QUALITY_HIGH
            if label in ("low", "critically low"):
                return _QUALITY_LOW
            return None
        if label == "low":
            return _QUALITY_HIGH
        if label in ("some concerns", "some concern", "unclear", "insufficient information"):
            return _QUALITY_INTERMEDIATE
        if label in ("high", "serious", "critical"):
            return _QUALITY_LOW
        return None

    if label == "critically low":
        return _QUALITY_LOW
    if label in ("some concerns", "some concern", "unclear", "insufficient information"):
        return _QUALITY_INTERMEDIATE
    if label in ("serious", "critical"):
        return _QUALITY_LOW
    return None


# ---------------------------------------------------------------------------
# 7. Seeding outcomes[] from single-outcome universal fields
# ---------------------------------------------------------------------------

def seed_outcomes_from_universal(tags: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a 1-element outcomes[] from single-outcome universal fields (no model call).

    Returns [] when there is no primary-outcome signal at all. Seeded rows carry no
    verbatim source_quote and no per-outcome confidence (both None).
    """
    name = _get(tags, "primary_outcome_definition")
    metric = _get(tags, "key_findings_metric")
    estimate = _coerce_num(_get(tags, "key_findings_effect_estimate"))
    if not name and estimate is None and not metric:
        return []

    p_val, p_op = _split_p(_get(tags, "key_findings_pvalue"))
    return [{
        "name": name,
        "instrument": _get(tags, "primary_outcome_measurement"),
        "timing": _get(tags, "primary_outcome_timing") or _get(tags, "follow_up_duration"),
        "comparison": None,
        "effect_metric": metric,
        "effect_estimate": estimate,
        "direction": _get(tags, "key_findings_direction"),
        "ci_lower": _coerce_num(_get(tags, "key_findings_ci_lower")),
        "ci_upper": _coerce_num(_get(tags, "key_findings_ci_upper")),
        "p_value": p_val,
        "p_operator": p_op,
        "source_quote": None,
        "confidence": None,
        "is_subgroup": False,
        "subgroup_label": None,
    }]


# ---------------------------------------------------------------------------
# 8. N cell + display composers
# ---------------------------------------------------------------------------

def format_n_cell(study_type: Any, sample_size_total: Any, included_studies_n: Any) -> str:
    """Participant N for a primary study; "k=12 (N=3450)" for an SR/meta-analysis row."""
    st = (str(study_type) or "").lower()
    k = _coerce_num(included_studies_n)
    n = _coerce_num(sample_size_total)
    is_review = "systematic review" in st or "meta-analysis" in st or "meta analysis" in st
    if is_review and k is not None:
        return f"k={_fmt_num(k)} (N={_fmt_num(n)})" if n is not None else f"k={_fmt_num(k)}"
    if n is not None:
        return _fmt_num(n)
    return f"k={_fmt_num(k)}" if k is not None else ""


# ---------------------------------------------------------------------------
# 9. Exploding rows + dedupe
# ---------------------------------------------------------------------------

def explode_rows(
    study_level: dict[str, Any],
    outcomes: Iterable[dict[str, Any]],
    provenance: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Explode one study into flat Table 2 rows — one per outcome object, with the
    study-level fields DENORMALIZED across them. Effects are reported AS REPORTED."""
    study_id = study_level.get("study_id") or build_study_id(
        study_level.get("citation_authors"), study_level.get("citation_year"))
    default_comparison = study_level.get("population_comparator")
    n_cell = format_n_cell(
        study_level.get("study_type") or study_level.get("design"),
        study_level.get("sample_size_total"),
        study_level.get("included_studies_n"),
    )

    rows: list[dict[str, Any]] = []
    for oc in outcomes:
        canon_metric, family = canonicalize_metric(oc.get("effect_metric"))
        comparison = oc.get("comparison") or default_comparison
        p_val, p_op = _read_p(oc)

        rec = reconcile_stats(
            _coerce_num(oc.get("effect_estimate")),
            _coerce_num(oc.get("ci_lower")),
            _coerce_num(oc.get("ci_upper")),
            p_val, family, p_op,
        )
        direction = infer_direction(
            family, rec["estimate"], rec["ci_lower"], rec["ci_upper"],
            reported_direction=oc.get("direction"),
            outcome_favorable_direction=oc.get("favorable_direction", "lower"),
        )
        rows.append({
            "study_id": study_id,
            "design": study_level.get("study_type") or study_level.get("design"),
            "population": study_level.get("population_participants"),
            "n": n_cell,
            "eligibility_threshold": study_level.get("eligibility_threshold"),
            "intervention": study_level.get("population_intervention_exposure"),
            "comparator": comparison,
            "statistical_method": study_level.get("statistical_method"),
            "quality_rating": study_level.get("quality_rating"),
            "outcome_name": oc.get("name"),
            "outcome_instrument": oc.get("instrument"),
            "outcome_timing": oc.get("timing"),
            "comparison": comparison,
            "effect_metric": canon_metric,
            "effect_family": family,
            "effect_estimate": rec["estimate"] if rec["estimate"] is not None
                               else oc.get("effect_estimate"),
            "ci_lower": rec["ci_lower"],
            "ci_upper": rec["ci_upper"],
            "p_value": rec["p_value"],
            "p_operator": rec["p_operator"],
            "direction": direction,
            "derived_stats": sorted(rec["derived"]),
            "is_subgroup": bool(oc.get("is_subgroup") or oc.get("subgroup")),
            "subgroup_label": oc.get("subgroup_label") or oc.get("subgroup"),
            "source_quote": oc.get("source_quote"),
            "confidence": oc.get("confidence"),
            "provenance": provenance,
            "result_effect": _compose_effect(canon_metric, rec["estimate"], direction, oc),
            "result_ci_p": _compose_ci_p(rec["ci_lower"], rec["ci_upper"],
                                         rec["p_value"], rec["p_operator"]),
        })
    return rows


def _read_p(oc: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    if oc.get("p_operator") and oc.get("p_value") is not None:
        return _coerce_num(oc.get("p_value")), oc.get("p_operator")
    return _split_p(oc.get("p_value"))


def _compose_effect(metric: Optional[str], estimate: Optional[float],
                    direction: str, oc: dict[str, Any]) -> str:
    if estimate is None:
        return (oc.get("source_quote") or oc.get("narrative")
                or (str(oc.get("effect_estimate")) if oc.get("effect_estimate") else "")
                or oc.get("name") or "").strip() or "Not estimable"
    metric_str = f"{metric} " if metric else ""
    dir_str = {
        FAVOURS_INTERVENTION: "favours intervention",
        FAVOURS_COMPARATOR: "favours comparator",
        NO_DIFFERENCE: "no difference",
        NOT_ESTIMABLE: "",
    }.get(direction, "")
    tail = f" ({dir_str})" if dir_str else ""
    return f"{metric_str}{_fmt_num(estimate)}{tail}".strip()


def _compose_ci_p(lo: Optional[float], hi: Optional[float],
                  p: Optional[float], p_op: Optional[str]) -> str:
    pieces: list[str] = []
    if lo is not None and hi is not None:
        pieces.append(f"95% CI {_fmt_num(lo)}–{_fmt_num(hi)}")
    if p is not None:
        pieces.append(_fmt_p(p, p_op or "eq"))
    return "; ".join(pieces)


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop only TRULY identical rows. The key includes the subgroup flag/label and
    the metric so a subgroup row and the main analysis both survive."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = (r.get("study_id"), r.get("outcome_name"), r.get("comparison"),
               r.get("outcome_timing"), bool(r.get("is_subgroup")),
               r.get("subgroup_label"), r.get("effect_metric"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 10. Dual-mode merge
# ---------------------------------------------------------------------------

def merge_injected_and_extracted(
    injected: Optional[dict[str, Any]],
    extracted: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Merge injected (upstream) tags with self-extracted tags.

    Injected study-level scalars win field-by-field; a self-extracted value fills a
    gap only where the injected tag is blank. outcomes[] is taken whole from the
    highest-priority source (injected -> extracted -> seed) — never element-wise.
    """
    injected = injected or {}
    extracted = extracted or {}
    merged: dict[str, Any] = dict(extracted)
    for k, v in injected.items():
        if k == "outcomes":
            continue
        if _present(v):
            merged[k] = v

    inj, ext = injected.get("outcomes"), extracted.get("outcomes")
    if isinstance(inj, list) and inj:
        merged["outcomes"] = inj
    elif isinstance(ext, list) and ext:
        merged["outcomes"] = ext
    else:
        merged["outcomes"] = seed_outcomes_from_universal(merged)
    return merged


# ---------------------------------------------------------------------------
# 11. Top-level assembler (pure Python — NO model call)
# ---------------------------------------------------------------------------

def assemble_table2(
    study_level_tags: dict[str, Any],
    outcomes: Optional[list[dict[str, Any]]] = None,
    rob: Optional[dict[str, Any]] = None,
    provenance: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Compose the Table 2 rows for one study. Pure Python; NEVER calls the model.

    Used in both modes. Resolves outcomes[] (passed / on tags / seeded), derives
    study_id + quality_rating (tool-routed), explodes + denormalizes, then dedupes.
    """
    tags = dict(study_level_tags or {})
    if outcomes is None:
        outcomes = tags.get("outcomes")
    if not (isinstance(outcomes, list) and outcomes):
        outcomes = seed_outcomes_from_universal(tags)

    if not tags.get("study_id"):
        tags["study_id"] = build_study_id(tags.get("citation_authors"), tags.get("citation_year"))
    if rob:
        tags["quality_rating"] = map_quality_rating(rob.get("rob_overall"), rob.get("rob_tool"))

    return dedupe_rows(explode_rows(tags, outcomes, provenance=provenance))


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _get(d: dict[str, Any], key: str) -> Any:
    v = d.get(key)
    return None if isinstance(v, str) and not v.strip() else v


def _present(v: Any) -> bool:
    return not (v is None or (isinstance(v, str) and not v.strip()))


def _coerce_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return _to_float(v)


def _fmt_num(v: Optional[float]) -> str:
    if v is None:
        return ""
    if not math.isfinite(v):
        return ""
    if v == int(v):
        return str(int(v))
    return f"{v:.3g}"


def _fmt_p(p: Optional[float], op: str = "eq") -> str:
    if p is None:
        return ""
    sym = {"lt": "<", "le": "≤", "gt": ">", "ge": "≥", "eq": "="}.get(op, "=")
    if op == "eq" and p < 0.001:
        return "p<0.001"
    if p < 0.001:
        return f"p{sym}{p:.1g}"
    return f"p{sym}{p:.3f}"
