"""Versioned reference data and deterministic scoring, independent of agent prompts."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

GRADES = ["Very low", "Low", "Moderate", "High"]
DOMAINS = ["Risk of bias", "Inconsistency", "Indirectness", "Imprecision", "Publication bias"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Target(StrictModel):
    estimate: float
    ci_low: float
    ci_high: float
    grade: Literal["High", "Moderate", "Low", "Very low"] | None = None
    k: int | None = Field(default=None, ge=1)
    i2: float | None = Field(default=None, ge=0, le=100)
    domains: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self):
        if not self.ci_low <= self.estimate <= self.ci_high or self.ci_low == self.ci_high:
            raise ValueError("Reference interval must contain the estimate and have positive width")
        if any(k not in DOMAINS or v not in (0, 1, 2, 3) for k, v in self.domains.items()):
            raise ValueError("Use the five named GRADE domains and downgrade levels 0–3")
        return self


class Outcome(StrictModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=500)
    outcome_type: Literal["binary", "continuous", "time_to_event"] = "binary"
    effect_measure: Literal["RR", "OR", "RD", "MD", "SMD", "HR"]
    model_choice: Literal["fixed", "random"] = "random"
    tau2_method: Literal["REML", "DL", "PM"] = "REML"
    fe_method: Literal["IV", "MH"] = "IV"
    re_ci_method: Literal["wald", "knapp_hartung"] = "wald"
    continuity_correction: float = Field(default=0.5, ge=0, le=1)
    mid_benefit: float | None = None
    mid_harm: float | None = None
    baseline_risk_per_1000: float | None = Field(default=None, ge=0, le=1000)
    method_verified: bool = False
    method_note: str = Field(default="Pooling settings need verification against the publication", max_length=1500)
    source_locator: str = Field(min_length=1, max_length=1500)
    target: Target
    # Absolute tolerance on the analysis scale (log for ratio measures).
    tolerance: float = Field(default=0.05, gt=0, le=1)

    @model_validator(mode="after")
    def valid(self):
        allowed = {"binary": {"RR", "OR", "RD"}, "continuous": {"MD", "SMD"}, "time_to_event": {"HR"}}
        if self.effect_measure not in allowed[self.outcome_type]:
            raise ValueError("Effect measure does not match outcome type")
        if self.effect_measure in ("RR", "OR", "HR") and self.target.ci_low <= 0:
            raise ValueError("Ratio estimates and interval bounds must be positive")
        return self


class StudyOutcome(StrictModel):
    raw: dict[str, float] = Field(default_factory=dict)
    rob: str | None = None
    source_locator: str = ""


class Study(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    citation: str = Field(min_length=1, max_length=1000)
    expected_included: bool
    study_type: str | None = None
    outcomes: dict[str, StudyOutcome] = Field(default_factory=dict)


class Dataset(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=60)
    topic: str = Field(min_length=1, max_length=100)
    citation: str = Field(min_length=1, max_length=1500)
    source_url: str
    guideline_url: str | None = None
    curation: Literal["published_targets", "adjudicated"] = "published_targets"
    curator_notes: str = Field(default="", max_length=4000)
    adjudicators: list[str] = Field(default_factory=list, max_length=10)
    split: Literal["development", "holdout"] = "development"
    pico: dict[str, str]
    outcomes: list[Outcome] = Field(min_length=1, max_length=10)
    studies: list[Study] = Field(default_factory=list, max_length=100)

    @field_validator("source_url", "guideline_url")
    @classmethod
    def url(cls, v):
        from urllib.parse import urlparse
        if v is not None and (urlparse(v).scheme != "https" or not urlparse(v).netloc):
            raise ValueError("Reference links must be HTTPS URLs")
        return v

    @model_validator(mode="after")
    def valid(self):
        keys = [o.key for o in self.outcomes]
        if len(set(keys)) != len(keys) or len({s.key for s in self.studies}) != len(self.studies):
            raise ValueError("Outcome and study keys must be unique")
        if not self.pico.get("population") or not self.pico.get("intervention"):
            raise ValueError("Population and intervention are required")
        if set(self.pico) - {"population", "intervention", "comparator", "inclusion_text", "exclusion_text"}:
            raise ValueError("PICO contains unknown fields")
        for s in self.studies:
            if set(s.outcomes) - set(keys):
                raise ValueError("Study outcome keys must match reference outcomes")
        if self.curation == "adjudicated" and (
                len(set(self.adjudicators)) < 2 or not self.studies or
                not all(o.method_verified for o in self.outcomes)):
            raise ValueError("Adjudicated benchmarks require two reviewers, a study manifest, and verified pooling settings")
        return self


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def agent_protocol(dataset: dict) -> dict:
    """Explicit allowlist: published effects, grades, citations and study labels NEVER enter prompts."""
    d = Dataset.model_validate(dataset)
    config_fields = ("name", "outcome_type", "effect_measure", "model_choice", "tau2_method", "fe_method",
                     "re_ci_method", "continuity_correction", "mid_benefit", "mid_harm", "baseline_risk_per_1000")
    return {"pico": {**d.pico, "outcomes": [o.name for o in d.outcomes]},
            "outcomes": [{k: getattr(o, k) for k in config_fields} for o in d.outcomes]}


def score_outcome(reference: dict, prediction: dict | None) -> dict:
    oc = Outcome.model_validate(reference)
    result = {"key": oc.key, "name": oc.name, "measure": oc.effect_measure,
              "reference": oc.target.model_dump(), "prediction": prediction,
              "tolerance": oc.tolerance, "method_verified": oc.method_verified,
              "status": "missing", "effect_match": False, "ci_match": False,
              "grade_match": None, "grade_distance": None, "overconfident": None,
              "domain_matches": {}, "analysis_error": None, "ci_error": None}
    if not prediction:
        return result
    numeric = [prediction.get(k) for k in ("estimate", "ci_low", "ci_high")]
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in numeric):
        result["status"] = "invalid"
        return result
    if not numeric[1] <= numeric[0] <= numeric[2] or numeric[1] == numeric[2]:
        result["status"] = "invalid"
        return result
    transform = math.log if oc.effect_measure in ("RR", "OR", "HR") else float
    if transform == math.log and min(numeric) <= 0:
        result["status"] = "invalid"
        return result
    effect_err = abs(transform(numeric[0]) - transform(oc.target.estimate))
    ci_err = max(abs(transform(numeric[1]) - transform(oc.target.ci_low)),
                 abs(transform(numeric[2]) - transform(oc.target.ci_high)))
    result.update(status="compared", analysis_error=effect_err, ci_error=ci_err,
                  effect_match=effect_err <= oc.tolerance, ci_match=ci_err <= oc.tolerance)
    ref_grade, pred_grade = oc.target.grade, prediction.get("grade")
    if ref_grade:
        result["grade_match"] = pred_grade == ref_grade
        if pred_grade in GRADES:
            diff = GRADES.index(pred_grade) - GRADES.index(ref_grade)
            result.update(grade_distance=abs(diff), overconfident=diff > 0)
    result["domain_matches"] = {k: (prediction.get("domains") or {}).get(k) == v
                                for k, v in oc.target.domains.items()}
    result["k_match"] = prediction.get("k") == oc.target.k if oc.target.k else None
    result["i2_error"] = (abs(prediction["i2"] - oc.target.i2)
                          if oc.target.i2 is not None and prediction.get("i2") is not None else None)
    return result


def summarize(scores: list[dict]) -> dict:
    """Denominators include missing outputs; no success-only accuracy."""
    n = len(scores)
    grade_targets = [s for s in scores if s["reference"].get("grade")]
    confusion = [[0 for _ in GRADES] for _ in GRADES]
    for s in grade_targets:
        pred = (s.get("prediction") or {}).get("grade")
        if s["status"] == "compared" and pred in GRADES:
            confusion[GRADES.index(s["reference"]["grade"])][GRADES.index(pred)] += 1
    total_paired = sum(map(sum, confusion))
    kappa = None
    if total_paired:
        observed = sum(confusion[i][j] * (i-j)**2 / 9 for i in range(4) for j in range(4)) / total_paired
        expected = sum(sum(confusion[i]) * sum(row[j] for row in confusion) * (i-j)**2 / 9
                       for i in range(4) for j in range(4)) / total_paired**2
        if expected:
            kappa = 1-observed/expected
    return {"outcomes": n, "compared": sum(s["status"] == "compared" for s in scores),
            "effect_matches": sum(s["effect_match"] for s in scores),
            "ci_matches": sum(s["ci_match"] for s in scores),
            "grade_targets": len(grade_targets), "grade_matches": sum(s["grade_match"] is True for s in grade_targets),
            "overconfident": sum(s["overconfident"] is True for s in grade_targets),
            "unrated": sum(s["status"] != "compared" or (s.get("prediction") or {}).get("grade") not in GRADES for s in grade_targets),
            "quadratic_weighted_kappa": kappa, "kappa_pairs": total_paired,
            "grade_labels": GRADES, "confusion": confusion}
