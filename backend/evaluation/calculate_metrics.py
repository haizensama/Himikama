"""
himikama/backend/evaluation/calculate_metrics.py
═══════════════════════════════════════════════════════════════
Production-quality quantitative evaluation metrics for Himikama.

Purpose:
    Compare the three Himikama evaluation variants using the new
    structured final-output design:

        1. single_shot_rag
        2. controlled_no_step9
        3. full_himikama

Reads:
    evaluation/datasets/himikama_eval_set.jsonl

    evaluation/outputs/single_shot_rag_outputs.jsonl
    evaluation/outputs/controlled_no_step9_outputs.jsonl
    evaluation/outputs/full_himikama_outputs.jsonl

Writes:
    evaluation/metrics/metrics_summary.json
    evaluation/metrics/metrics_summary.csv
    evaluation/metrics/per_scenario_metrics.jsonl
    evaluation/metrics/error_report.jsonl
    evaluation/metrics/variant_comparison.md

Usage:
    python -m evaluation.calculate_metrics

    python -m evaluation.calculate_metrics \
        --dataset evaluation/datasets/testeval.jsonl

    python -m evaluation.calculate_metrics \
        --outputs-dir evaluation/outputs \
        --metrics-dir evaluation/metrics

Core evaluation principle:
    DO NOT use articles_identified as the main final-article metric.

    articles_identified is a Step 4 candidate/debug field.

    The main quantitative article metric must use:

        final_potentially_violated_articles

    This file still reports candidate-article debug metrics so that
    Step 4 retrieval/rights-identification behavior can be studied
    separately from the final Step 10 conclusion.

Supported gold dataset fields:
    Preferred new fields:
        gold_timeliness_status
        gold_state_actor_status
        gold_engaged_articles
        gold_final_potentially_violated_articles
        gold_weak_or_uncertain_articles
        gold_rejected_articles
        gold_overall_assessment
        gold_final_case_outcome
        gold_relevant_case_ids
        gold_confidence
        gold_inconsistency

    Backward-compatible aliases:
        gold_articles
        gold_potentially_violated_articles
        gold_final_outcome
        gold_status
        gold_similar_case_ids

Supported prediction output fields:
        status
        final_potentially_violated_articles
        final_weak_or_uncertain_articles
        final_rejected_articles
        overall_assessment
        structured_assessment
        similar_case_ids
        confidence_level
        flags
        step_results
        retrieved_contexts
        articles_identified

    Also supports current API-wrapper rows where final fields are nested
    under summary and confidence is nested under confidence.level.

Outputs explained:
    metrics_summary.json:
        Full nested metric results per variant.

    metrics_summary.csv:
        Flattened summary for spreadsheet/report use.

    per_scenario_metrics.jsonl:
        One row per scenario per variant with TP/FP/FN details.

    error_report.jsonl:
        Failed or missing predictions, invalid statuses, and other
        debugging issues.

    variant_comparison.md:
        Human-readable summary table suitable for reports.
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# ─────────────────────────────────────────────────────────────
# DEFAULT PATHS
# ─────────────────────────────────────────────────────────────

DEFAULT_DATASET = Path("evaluation/datasets/himikama_eval_set.jsonl")
DEFAULT_OUTPUT_DIR = Path("evaluation/outputs")
DEFAULT_METRICS_DIR = Path("evaluation/metrics")


VARIANT_FILES: dict[str, str] = {
    "single_shot_rag": "single_shot_rag_outputs.jsonl",
    "controlled_no_step9": "controlled_no_step9_outputs.jsonl",
    "full_himikama": "full_himikama_outputs.jsonl",
}


# ─────────────────────────────────────────────────────────────
# LABEL CONSTANTS
# ─────────────────────────────────────────────────────────────

VALID_TIMELINESS_STATUSES = {
    "within_time",
    "time_barred",
    "unclear",
    "",
}

VALID_STATE_ACTOR_STATUSES = {
    "state_actor",
    "not_state_actor",
    "unclear",
    "",
}

VALID_OVERALL_ASSESSMENTS = {
    "likely_viable",
    "weak_or_uncertain",
    "not_viable",
    "time_barred",
    "not_state_actor",
    "unclear",
    "",
}

VALID_CONFIDENCE_LEVELS = {
    "",
    "low",
    "medium",
    "high",
}

CONFIDENCE_ORDER = {
    "": 0,
    None: 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}

# Confidence interpreted as probability-like value for calibration.
CONFIDENCE_SCORE = {
    "": 0.0,
    None: 0.0,
    "low": 0.33,
    "medium": 0.66,
    "high": 1.0,
}


# ─────────────────────────────────────────────────────────────
# DATA TYPES
# ─────────────────────────────────────────────────────────────

@dataclass
class SetMetrics:
    """
    Standard set-comparison metrics for article/case predictions.
    """

    tp: int = 0
    fp: int = 0
    fn: int = 0
    exact_matches: int = 0
    evaluated: int = 0

    macro_precision_sum: float = 0.0
    macro_recall_sum: float = 0.0
    macro_f1_sum: float = 0.0

    def add_case(self, gold: set[str], pred: set[str]) -> None:
        """
        Add one scenario's set comparison.
        """
        tp = len(gold & pred)
        fp = len(pred - gold)
        fn = len(gold - pred)

        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        f1 = safe_div(2 * precision * recall, precision + recall)

        self.tp += tp
        self.fp += fp
        self.fn += fn
        self.evaluated += 1

        if gold == pred:
            self.exact_matches += 1

        self.macro_precision_sum += precision
        self.macro_recall_sum += recall
        self.macro_f1_sum += f1

    def to_dict(self, prefix: str) -> dict[str, Any]:
        """
        Return micro/macro metrics using a field prefix.
        """
        micro_precision = safe_div(self.tp, self.tp + self.fp)
        micro_recall = safe_div(self.tp, self.tp + self.fn)
        micro_f1 = safe_div(
            2 * micro_precision * micro_recall,
            micro_precision + micro_recall,
        )

        return {
            f"{prefix}_precision": micro_precision,
            f"{prefix}_recall": micro_recall,
            f"{prefix}_f1": micro_f1,
            f"{prefix}_exact_match": safe_div(
                self.exact_matches,
                self.evaluated,
            ),
            f"{prefix}_macro_precision": safe_div(
                self.macro_precision_sum,
                self.evaluated,
            ),
            f"{prefix}_macro_recall": safe_div(
                self.macro_recall_sum,
                self.evaluated,
            ),
            f"{prefix}_macro_f1": safe_div(
                self.macro_f1_sum,
                self.evaluated,
            ),
            f"{prefix}_tp": self.tp,
            f"{prefix}_fp": self.fp,
            f"{prefix}_fn": self.fn,
            f"{prefix}_evaluated": self.evaluated,
        }


@dataclass
class ClassificationMetrics:
    """
    Accuracy and confusion matrix for single-label classification.
    """

    correct: int = 0
    evaluated: int = 0
    skipped: int = 0
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)

    def add_case(self, gold: str, pred: str, *, skip_unclear_gold: bool = True) -> None:
        """
        Add one classification comparison.
        """
        gold = normalize_label(gold)
        pred = normalize_label(pred)

        if skip_unclear_gold and gold in {"", "unclear"}:
            self.skipped += 1
            return

        self.evaluated += 1

        if gold == pred:
            self.correct += 1

        self.confusion.setdefault(gold, {})
        self.confusion[gold][pred] = self.confusion[gold].get(pred, 0) + 1

    def to_dict(self, prefix: str) -> dict[str, Any]:
        """
        Return accuracy metrics using a field prefix.
        """
        return {
            f"{prefix}_accuracy": safe_div(self.correct, self.evaluated),
            f"{prefix}_correct": self.correct,
            f"{prefix}_evaluated": self.evaluated,
            f"{prefix}_skipped": self.skipped,
            f"{prefix}_confusion": self.confusion,
        }


# ─────────────────────────────────────────────────────────────
# FILE I/O
# ─────────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """
    Load a JSONL file into a list of dicts.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    rows: list[dict[str, Any]] = []

    for line_no, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue

        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSONL at {path}, line {line_no}: {e}"
            ) from e

        if not isinstance(obj, dict):
            raise ValueError(
                f"Invalid JSONL at {path}, line {line_no}: expected object"
            )

        rows.append(obj)

    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """
    Write rows as JSONL.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, data: Any) -> None:
    """
    Write JSON file with indentation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """
    Write list of dictionaries to CSV.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = sorted({key for row in rows for key in row.keys()})

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(flatten_for_csv(row))


def flatten_for_csv(row: dict[str, Any]) -> dict[str, Any]:
    """
    Convert nested values to JSON strings for CSV.
    """
    flat: dict[str, Any] = {}

    for key, value in row.items():
        if isinstance(value, (dict, list)):
            flat[key] = json.dumps(value, ensure_ascii=False)
        else:
            flat[key] = value

    return flat


# ─────────────────────────────────────────────────────────────
# NORMALIZATION HELPERS
# ─────────────────────────────────────────────────────────────

def safe_div(num: float, den: float) -> float:
    """
    Safe division that returns 0.0 for zero denominator.
    """
    return num / den if den else 0.0


def normalize_label(value: Any) -> str:
    """
    Normalize a single classification label.
    """
    if value is None:
        return ""

    return str(value).strip().lower()


def normalize_string_list(value: Any) -> list[str]:
    """
    Convert common list-like values into a clean deduplicated list[str].
    """
    if value is None:
        return []

    if isinstance(value, list):
        raw = value
    elif isinstance(value, tuple):
        raw = list(value)
    elif isinstance(value, set):
        raw = list(value)
    elif isinstance(value, str):
        # Support accidental comma-separated strings.
        if "," in value:
            raw = value.split(",")
        elif value.strip():
            raw = [value]
        else:
            raw = []
    else:
        return []

    cleaned: list[str] = []

    for item in raw:
        text = str(item).strip()
        if text:
            cleaned.append(text)

    return dedupe_preserve_order(cleaned)


def normalize_article_list(value: Any) -> list[str]:
    """
    Normalize article lists.

    We sort article lists for stable metric output.
    """
    articles = normalize_string_list(value)
    return sorted(set(articles))


def normalize_case_id_list(value: Any) -> list[str]:
    """
    Normalize case ID lists while preserving order.
    """
    return dedupe_preserve_order(normalize_string_list(value))


def dedupe_preserve_order(items: list[str]) -> list[str]:
    """
    Deduplicate list[str] while preserving order.
    """
    seen: set[str] = set()
    output: list[str] = []

    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)

    return output


def dict_by_scenario_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Index rows by scenario_id.
    """
    output: dict[str, dict[str, Any]] = {}

    for row in rows:
        scenario_id = row.get("scenario_id")
        if scenario_id:
            output[str(scenario_id)] = row

    return output


def get_nested_dict(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    """
    Safely retrieve nested dict.
    """
    current: Any = source

    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})

    return current if isinstance(current, dict) else {}


def get_nested_value(source: dict[str, Any], *keys: str) -> Any:
    """
    Safely retrieve nested value.
    """
    current: Any = source

    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    return current


def extract_api_summary(pred: dict[str, Any]) -> dict[str, Any]:
    """
    Extract the current API-wrapper summary block, if present.

    The API endpoint returns final structured fields under:
        pred["summary"]

    Raw runner/evaluation rows may not have this wrapper.
    """
    summary = pred.get("summary", {})
    return summary if isinstance(summary, dict) else {}


def extract_predicted_flags(pred: dict[str, Any]) -> list[str]:
    """
    Extract prediction flags from both raw runner rows and API-wrapper rows.
    """
    top_level = normalize_string_list(pred.get("flags", []))
    if top_level:
        return top_level

    confidence_flags = normalize_string_list(
        get_nested_value(pred, "confidence", "flags")
    )
    if confidence_flags:
        return confidence_flags

    return normalize_string_list(
        get_nested_value(pred, "confidence", "evaluation", "flags")
    )


def extract_final_answer_text(pred: dict[str, Any]) -> str:
    """
    Extract final answer text from known output shapes.
    """
    return str(
        pred.get("final_answer_with_disclaimer")
        or pred.get("final_answer")
        or pred.get("main_answer")
        or get_nested_value(pred, "summary", "main_answer")
        or ""
    )


# ─────────────────────────────────────────────────────────────
# GOLD FIELD EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_gold_fields(row: dict[str, Any]) -> dict[str, Any]:
    """
    Extract gold fields from the dataset row.

    This supports both the new recommended schema and older aliases.
    """
    return {
        "timeliness_status": normalize_label(
            row.get("gold_timeliness_status", "")
        ),
        "state_actor_status": normalize_label(
            row.get("gold_state_actor_status", "")
        ),
        "engaged_articles": normalize_article_list(
            row.get("gold_engaged_articles", [])
        ),
        "final_potentially_violated_articles": normalize_article_list(
            row.get(
                "gold_final_potentially_violated_articles",
                row.get(
                    "gold_potentially_violated_articles",
                    row.get("gold_articles", []),
                ),
            )
        ),
        "weak_or_uncertain_articles": normalize_article_list(
            row.get("gold_weak_or_uncertain_articles", [])
        ),
        "rejected_articles": normalize_article_list(
            row.get("gold_rejected_articles", [])
        ),
        "overall_assessment": normalize_label(
            row.get(
                "gold_overall_assessment",
                row.get("gold_final_outcome", row.get("gold_status", "")),
            )
        ),
        "final_case_outcome": normalize_label(
            row.get("gold_final_case_outcome", "")
        ),
        "relevant_case_ids": normalize_case_id_list(
            row.get(
                "gold_relevant_case_ids",
                row.get("gold_similar_case_ids", []),
            )
        ),
        "confidence": normalize_label(row.get("gold_confidence", "")),
        "inconsistency": row.get("gold_inconsistency", None),
        "reasoning_summary": str(row.get("gold_reasoning_summary", "") or ""),
        "remedy": str(row.get("gold_remedy", "") or ""),
        "evaluation_notes": str(row.get("evaluation_notes", "") or ""),
    }


def extract_gold_from_prediction_if_needed(pred: dict[str, Any]) -> dict[str, Any]:
    """
    Some normalized output rows include a nested "gold" block.
    This helper extracts it as a fallback when the dataset row is missing.
    """
    gold = pred.get("gold", {})

    if not isinstance(gold, dict):
        return extract_gold_fields({})

    synthetic_row = {
        "gold_timeliness_status": gold.get("gold_timeliness_status"),
        "gold_state_actor_status": gold.get("gold_state_actor_status"),
        "gold_engaged_articles": gold.get("gold_engaged_articles", []),
        "gold_final_potentially_violated_articles": gold.get(
            "gold_final_potentially_violated_articles",
            [],
        ),
        "gold_weak_or_uncertain_articles": gold.get(
            "gold_weak_or_uncertain_articles",
            [],
        ),
        "gold_rejected_articles": gold.get("gold_rejected_articles", []),
        "gold_overall_assessment": gold.get("gold_overall_assessment"),
        "gold_final_case_outcome": gold.get("gold_final_case_outcome"),
        "gold_relevant_case_ids": gold.get("gold_relevant_case_ids", []),
        "gold_confidence": gold.get("gold_confidence"),
        "gold_inconsistency": gold.get("gold_inconsistency"),
        "gold_reasoning_summary": gold.get("gold_reasoning_summary", ""),
        "gold_remedy": gold.get("gold_remedy", ""),
        "evaluation_notes": gold.get("evaluation_notes", ""),
    }

    return extract_gold_fields(synthetic_row)


# ─────────────────────────────────────────────────────────────
# PREDICTION FIELD EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_structured_assessment(pred: dict[str, Any]) -> dict[str, Any]:
    """
    Extract structured assessment from a prediction row.

    Preferred order:
        1. pred["structured_assessment"]
        2. pred["summary"]["structured_assessment"]              # API wrapper
        3. pred["step_results"]["step_10"]["data"]["structured_assessment"]
        4. pred["confidence"]["evaluation"]["structured_assessment"]
    """
    top_level = pred.get("structured_assessment")
    if isinstance(top_level, dict):
        return top_level

    from_summary = get_nested_value(pred, "summary", "structured_assessment")
    if isinstance(from_summary, dict):
        return from_summary

    from_step_10 = get_nested_value(
        pred,
        "step_results",
        "step_10",
        "data",
        "structured_assessment",
    )
    if isinstance(from_step_10, dict):
        return from_step_10

    from_confidence = get_nested_value(
        pred,
        "confidence",
        "evaluation",
        "structured_assessment",
    )
    if isinstance(from_confidence, dict):
        return from_confidence

    return {}
def extract_predicted_final_articles(pred: dict[str, Any]) -> list[str]:
    """
    Extract final potentially violated articles.

    This is the main final-article prediction field.
    Supports both raw runner rows and current API-wrapper rows.
    """
    top_level = normalize_article_list(
        pred.get("final_potentially_violated_articles", [])
    )
    if top_level:
        return top_level

    from_summary = normalize_article_list(
        get_nested_value(pred, "summary", "final_potentially_violated_articles")
    )
    if from_summary:
        return from_summary

    structured = extract_structured_assessment(pred)
    from_structured = normalize_article_list(
        structured.get("final_potentially_violated_articles", [])
    )
    if from_structured:
        return from_structured

    from_step_10 = normalize_article_list(
        get_nested_value(
            pred,
            "step_results",
            "step_10",
            "data",
            "final_potentially_violated_articles",
        )
    )
    if from_step_10:
        return from_step_10

    return normalize_article_list(
        get_nested_value(
            pred,
            "confidence",
            "evaluation",
            "final_potentially_violated_articles",
        )
    )
def extract_predicted_weak_articles(pred: dict[str, Any]) -> list[str]:
    """
    Extract final weak/uncertain articles.
    Supports both raw runner rows and current API-wrapper rows.
    """
    top_level = normalize_article_list(
        pred.get("final_weak_or_uncertain_articles", [])
    )
    if top_level:
        return top_level

    from_summary = normalize_article_list(
        get_nested_value(pred, "summary", "final_weak_or_uncertain_articles")
    )
    if from_summary:
        return from_summary

    structured = extract_structured_assessment(pred)
    from_structured = normalize_article_list(
        structured.get("final_weak_or_uncertain_articles", [])
    )
    if from_structured:
        return from_structured

    from_step_10 = normalize_article_list(
        get_nested_value(
            pred,
            "step_results",
            "step_10",
            "data",
            "final_weak_or_uncertain_articles",
        )
    )
    if from_step_10:
        return from_step_10

    return normalize_article_list(
        get_nested_value(
            pred,
            "confidence",
            "evaluation",
            "final_weak_or_uncertain_articles",
        )
    )
def extract_predicted_rejected_articles(pred: dict[str, Any]) -> list[str]:
    """
    Extract final rejected articles.
    Supports both raw runner rows and current API-wrapper rows.
    """
    top_level = normalize_article_list(pred.get("final_rejected_articles", []))
    if top_level:
        return top_level

    from_summary = normalize_article_list(
        get_nested_value(pred, "summary", "final_rejected_articles")
    )
    if from_summary:
        return from_summary

    structured = extract_structured_assessment(pred)
    from_structured = normalize_article_list(
        structured.get("final_rejected_articles", [])
    )
    if from_structured:
        return from_structured

    from_step_10 = normalize_article_list(
        get_nested_value(
            pred,
            "step_results",
            "step_10",
            "data",
            "final_rejected_articles",
        )
    )
    if from_step_10:
        return from_step_10

    return normalize_article_list(
        get_nested_value(
            pred,
            "confidence",
            "evaluation",
            "final_rejected_articles",
        )
    )
def extract_predicted_overall_assessment(pred: dict[str, Any]) -> str:
    """
    Extract predicted overall assessment.
    Supports both raw runner rows and current API-wrapper rows.
    """
    top_level = normalize_label(pred.get("overall_assessment", ""))
    if top_level:
        return top_level

    from_summary = normalize_label(
        get_nested_value(pred, "summary", "overall_assessment")
    )
    if from_summary:
        return from_summary

    structured = extract_structured_assessment(pred)
    from_structured = normalize_label(structured.get("overall_assessment", ""))
    if from_structured:
        return from_structured

    from_step_10 = normalize_label(
        get_nested_value(
            pred,
            "step_results",
            "step_10",
            "data",
            "overall_assessment",
        )
    )
    if from_step_10:
        return from_step_10

    return normalize_label(
        get_nested_value(
            pred,
            "confidence",
            "evaluation",
            "overall_assessment",
        )
    )
def extract_predicted_precedent_alignment(pred: dict[str, Any]) -> str:
    """
    Extract predicted precedent alignment.
    Supports both raw runner rows and current API-wrapper rows.
    """
    top_level = normalize_label(pred.get("precedent_alignment", ""))
    if top_level:
        return top_level

    from_summary = normalize_label(
        get_nested_value(pred, "summary", "precedent_alignment")
    )
    if from_summary:
        return from_summary

    structured = extract_structured_assessment(pred)
    from_structured = normalize_label(structured.get("precedent_alignment", ""))
    if from_structured:
        return from_structured

    from_step_10 = normalize_label(
        get_nested_value(
            pred,
            "step_results",
            "step_10",
            "data",
            "precedent_alignment",
        )
    )
    if from_step_10:
        return from_step_10

    return normalize_label(
        get_nested_value(
            pred,
            "confidence",
            "evaluation",
            "precedent_alignment",
        )
    )
def extract_predicted_candidate_articles(pred: dict[str, Any]) -> list[str]:
    """
    Extract Step 4 candidate articles.

    This is used only for debug/diagnostic metrics.
    Supports both raw runner rows and current API-wrapper rows.
    """
    top_level = normalize_article_list(pred.get("articles_identified", []))
    if top_level:
        return top_level

    from_summary = normalize_article_list(
        get_nested_value(pred, "summary", "articles_identified")
    )
    if from_summary:
        return from_summary

    return normalize_article_list(
        get_nested_value(
            pred,
            "step_results",
            "step_4",
            "data",
            "articles_identified",
        )
    )
def extract_predicted_case_ids(pred: dict[str, Any]) -> list[str]:
    """
    Extract selected similar case IDs.
    Supports both raw runner rows and current API-wrapper rows.
    """
    top_level = normalize_case_id_list(pred.get("similar_case_ids", []))
    if top_level:
        return top_level

    from_summary = normalize_case_id_list(
        get_nested_value(pred, "summary", "similar_case_ids")
    )
    if from_summary:
        return from_summary

    return normalize_case_id_list(
        get_nested_value(
            pred,
            "step_results",
            "step_7",
            "data",
            "case_ids",
        )
    )
def extract_predicted_confidence(pred: dict[str, Any]) -> str:
    """
    Extract predicted confidence level.
    Supports both raw runner rows and current API-wrapper rows.
    """
    top_level = normalize_label(pred.get("confidence_level", ""))
    if top_level:
        return top_level

    from_confidence_block = normalize_label(
        get_nested_value(pred, "confidence", "level")
    )
    if from_confidence_block:
        return from_confidence_block

    return normalize_label(
        get_nested_value(pred, "confidence", "evaluation", "confidence_level")
    )
def extract_prediction_status(pred: dict[str, Any]) -> str:
    """
    Extract chain run status.
    """
    return normalize_label(pred.get("status", ""))


def derive_predicted_timeliness_status(pred: dict[str, Any]) -> str:
    """
    Derive predicted timeliness status from chain status and Step 1.

    API-wrapper rows often omit step_results. In that shape,
    status="complete" implies the timeliness hard gate passed,
    because a time-barred run would terminate before completion.
    """
    status = extract_prediction_status(pred)
    if status == "time_barred":
        return "time_barred"

    step_1 = get_nested_dict(pred, "step_results", "step_1")
    if step_1:
        passed = step_1.get("passed")
        if passed is False:
            return "time_barred"
        if passed is True:
            return "within_time"

    if status == "complete":
        return "within_time"

    return ""
def derive_predicted_state_actor_status(pred: dict[str, Any]) -> str:
    """
    Derive predicted state actor status from chain status and Step 2.

    API-wrapper rows often omit step_results. In that shape,
    status="complete" implies the state-actor hard gate passed,
    because a non-state-actor run would terminate before completion.
    """
    status = extract_prediction_status(pred)
    if status == "not_state_actor":
        return "not_state_actor"

    step_2 = get_nested_dict(pred, "step_results", "step_2")
    if step_2:
        passed = step_2.get("passed")
        if passed is False:
            return "not_state_actor"
        if passed is True:
            return "state_actor"

    if status == "complete":
        return "state_actor"

    return ""
def predicted_inconsistent(pred: dict[str, Any]) -> bool | None:
    """
    Determine whether Step 9 predicted an inconsistency.

    Returns:
        True  -> inconsistency found
        False -> Step 9 ran and found no inconsistency
        None  -> Step 9 missing or intentionally skipped

    For API-wrapper rows without step_results, confidence flags are used
    as a fallback only when they explicitly encode Step 9 inconsistency.
    """
    flags = extract_predicted_flags(pred)

    if any(
        flag in {
            "inconsistent",
            "candidate_inconsistency",
            "final_supported_inconsistent",
        }
        for flag in flags
    ):
        return True

    step_9_data = get_nested_dict(pred, "step_results", "step_9", "data")

    if not step_9_data:
        return None

    if step_9_data.get("skipped") is True:
        return None

    value = step_9_data.get("inconsistencies_found")

    if value is None:
        return None

    return bool(value)
def extract_error(pred: dict[str, Any]) -> str:
    """
    Extract prediction error string.
    """
    error = pred.get("error")
    return str(error) if error else ""


# ─────────────────────────────────────────────────────────────
# VALIDATION / ERROR REPORTING
# ─────────────────────────────────────────────────────────────

def validate_prediction_row(
    *,
    scenario_id: str,
    variant: str,
    pred: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Return warnings/errors for one prediction row.
    """
    issues: list[dict[str, Any]] = []

    status = extract_prediction_status(pred)
    error = extract_error(pred)

    if not pred:
        issues.append({
            "scenario_id": scenario_id,
            "variant": variant,
            "severity": "error",
            "issue": "missing_prediction",
            "details": "No prediction row found for this scenario.",
        })
        return issues

    if status == "failed" or error:
        issues.append({
            "scenario_id": scenario_id,
            "variant": variant,
            "severity": "error",
            "issue": "prediction_failed",
            "details": error or "Prediction status is failed.",
        })

    final_articles = extract_predicted_final_articles(pred)
    candidate_articles = extract_predicted_candidate_articles(pred)

    if not final_articles and candidate_articles and status == "complete":
        issues.append({
            "scenario_id": scenario_id,
            "variant": variant,
            "severity": "warning",
            "issue": "empty_final_articles_with_candidate_articles",
            "details": (
                "Step 4/candidate articles exist, but final_potentially_"
                "violated_articles is empty. This may be correct for weak/"
                "not viable cases, but should be reviewed."
            ),
        })

    overall = extract_predicted_overall_assessment(pred)
    if overall not in VALID_OVERALL_ASSESSMENTS:
        issues.append({
            "scenario_id": scenario_id,
            "variant": variant,
            "severity": "warning",
            "issue": "invalid_overall_assessment",
            "details": f"overall_assessment={overall!r}",
        })

    confidence = extract_predicted_confidence(pred)
    if confidence not in VALID_CONFIDENCE_LEVELS:
        issues.append({
            "scenario_id": scenario_id,
            "variant": variant,
            "severity": "warning",
            "issue": "invalid_confidence_level",
            "details": f"confidence_level={confidence!r}",
        })

    return issues


# ─────────────────────────────────────────────────────────────
# METRIC CALCULATIONS
# ─────────────────────────────────────────────────────────────

def calculate_gate_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate timeliness and state actor gate accuracy.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)

    timeliness = ClassificationMetrics()
    state_actor = ClassificationMetrics()

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold = extract_gold_fields(gold_row)

        timeliness.add_case(
            gold["timeliness_status"],
            derive_predicted_timeliness_status(pred),
        )
        state_actor.add_case(
            gold["state_actor_status"],
            derive_predicted_state_actor_status(pred),
        )

    metrics: dict[str, Any] = {}
    metrics.update(timeliness.to_dict("timeliness_gate"))
    metrics.update(state_actor.to_dict("state_actor_gate"))
    return metrics


def calculate_overall_assessment_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate final overall assessment accuracy.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)
    classifier = ClassificationMetrics()

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold = extract_gold_fields(gold_row)

        classifier.add_case(
            gold["overall_assessment"],
            extract_predicted_overall_assessment(pred),
        )

    return classifier.to_dict("overall_assessment")


def calculate_final_article_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate main final article metrics.

    This is the primary article metric for the research evaluation.
    It compares:
        gold_final_potentially_violated_articles
    against:
        final_potentially_violated_articles
    """
    pred_by_id = dict_by_scenario_id(pred_rows)
    set_metrics = SetMetrics()

    empty_gold_empty_pred = 0
    empty_gold_nonempty_pred = 0
    nonempty_gold_empty_pred = 0

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold = set(extract_gold_fields(gold_row)["final_potentially_violated_articles"])
        predicted = set(extract_predicted_final_articles(pred))

        set_metrics.add_case(gold, predicted)

        if not gold and not predicted:
            empty_gold_empty_pred += 1
        elif not gold and predicted:
            empty_gold_nonempty_pred += 1
        elif gold and not predicted:
            nonempty_gold_empty_pred += 1

    metrics = set_metrics.to_dict("final_article")
    metrics.update({
        "final_article_empty_gold_empty_pred": empty_gold_empty_pred,
        "final_article_empty_gold_nonempty_pred": empty_gold_nonempty_pred,
        "final_article_nonempty_gold_empty_pred": nonempty_gold_empty_pred,
    })

    return metrics


def calculate_weak_article_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate weak/uncertain article classification metrics.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)
    set_metrics = SetMetrics()

    evaluated = 0

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold = set(extract_gold_fields(gold_row)["weak_or_uncertain_articles"])
        predicted = set(extract_predicted_weak_articles(pred))

        set_metrics.add_case(gold, predicted)
        evaluated += 1

    metrics = set_metrics.to_dict("weak_article")
    metrics["weak_article_cases_evaluated"] = evaluated
    return metrics


def calculate_rejected_article_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate rejected article classification metrics.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)
    set_metrics = SetMetrics()

    evaluated = 0

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold = set(extract_gold_fields(gold_row)["rejected_articles"])
        predicted = set(extract_predicted_rejected_articles(pred))

        set_metrics.add_case(gold, predicted)
        evaluated += 1

    metrics = set_metrics.to_dict("rejected_article")
    metrics["rejected_article_cases_evaluated"] = evaluated
    return metrics


def calculate_overclaiming_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate article overclaiming metrics.

    Main idea:
        A serious overclaim occurs when the system marks an article as
        finally supported even though the gold data says that article was
        rejected or only weak/uncertain.

    Reported metrics:
        rejected_article_overclaim_rate_vs_gold_rejected:
            Of all gold rejected article labels, how many were predicted
            as final supported?

        weak_article_overclaim_rate_vs_gold_weak:
            Of all gold weak/uncertain labels, how many were predicted
            as final supported?

        unsupported_final_article_rate:
            Of all predicted final supported articles, how many were not
            in the gold final supported set?

        severe_overclaim_count:
            Count of predicted supported articles that were gold rejected.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)

    gold_rejected_total = 0
    gold_rejected_pred_supported = 0

    gold_weak_total = 0
    gold_weak_pred_supported = 0

    predicted_supported_total = 0
    predicted_supported_not_gold_supported = 0

    severe_overclaim_count = 0
    weak_overclaim_count = 0

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold = extract_gold_fields(gold_row)

        gold_supported = set(gold["final_potentially_violated_articles"])
        gold_weak = set(gold["weak_or_uncertain_articles"])
        gold_rejected = set(gold["rejected_articles"])

        pred_supported = set(extract_predicted_final_articles(pred))

        gold_rejected_total += len(gold_rejected)
        gold_rejected_pred_supported += len(gold_rejected & pred_supported)

        gold_weak_total += len(gold_weak)
        gold_weak_pred_supported += len(gold_weak & pred_supported)

        predicted_supported_total += len(pred_supported)
        predicted_supported_not_gold_supported += len(
            pred_supported - gold_supported
        )

        severe_overclaim_count += len(gold_rejected & pred_supported)
        weak_overclaim_count += len(gold_weak & pred_supported)

    return {
        "rejected_article_overclaim_rate_vs_gold_rejected": safe_div(
            gold_rejected_pred_supported,
            gold_rejected_total,
        ),
        "weak_article_overclaim_rate_vs_gold_weak": safe_div(
            gold_weak_pred_supported,
            gold_weak_total,
        ),
        "unsupported_final_article_rate": safe_div(
            predicted_supported_not_gold_supported,
            predicted_supported_total,
        ),
        "severe_overclaim_count": severe_overclaim_count,
        "weak_overclaim_count": weak_overclaim_count,
        "gold_rejected_article_total": gold_rejected_total,
        "gold_weak_article_total": gold_weak_total,
        "predicted_supported_article_total": predicted_supported_total,
    }



def classify_gold_case_bucket(gold: dict[str, Any]) -> str:
    """
    Assign a scenario to a broad evaluation bucket.

    This is used for ablation reporting, not as a replacement for the
    primary metrics. The goal is to show where Step 9 helps most:
    viable cases, no-violation cases, weak/uncertain cases, hard-gate
    cases, or partial-violation cases.
    """
    timeliness = gold.get("timeliness_status", "")
    state_actor = gold.get("state_actor_status", "")
    overall = gold.get("overall_assessment", "")
    final_outcome = gold.get("final_case_outcome", "")

    final_articles = set(gold.get("final_potentially_violated_articles", []))
    weak_articles = set(gold.get("weak_or_uncertain_articles", []))
    rejected_articles = set(gold.get("rejected_articles", []))

    if timeliness == "time_barred" or overall == "time_barred":
        return "time_barred"

    if state_actor == "not_state_actor" or overall == "not_state_actor":
        return "not_state_actor"

    if weak_articles or overall == "weak_or_uncertain":
        return "weak_or_uncertain"

    if final_outcome == "partial_violation":
        return "partial_violation"

    no_violation_outcomes = {
        "no_violation",
        "dismissed",
        "procedural_failure",
    }

    if (
        overall == "not_viable"
        or final_outcome in no_violation_outcomes
        or (not final_articles and rejected_articles)
    ):
        return "no_violation_or_not_viable"

    if final_articles or overall == "likely_viable" or final_outcome == "violation_found":
        return "likely_viable_or_violation"

    return "other_or_unclear"


def is_no_violation_like_gold_case(gold: dict[str, Any]) -> bool:
    """
    True when the gold labels represent a no-violation / not-viable merits
    case where predicting final supported articles would be a false
    violation claim.

    Hard-gate cases are excluded because they are measured separately by
    timeliness/state-actor gate accuracy.
    """
    bucket = classify_gold_case_bucket(gold)
    return bucket == "no_violation_or_not_viable"


def calculate_no_violation_false_violation_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate false-violation control on no-violation / not-viable cases.

    This is a key hallucination-aware metric:
        Among gold no-violation or not-viable cases, how often did the
        system still predict at least one final supported violated article?

    Lower false_violation_rate is better.
    Higher non_overclaim_accuracy is better.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)

    evaluated = 0
    false_violation_count = 0
    non_overclaim_correct = 0

    overall_evaluated = 0
    overall_correct = 0

    predicted_article_total = 0

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})
        gold = extract_gold_fields(gold_row)

        if not is_no_violation_like_gold_case(gold):
            continue

        evaluated += 1

        pred_final = set(extract_predicted_final_articles(pred))
        predicted_article_total += len(pred_final)

        if pred_final:
            false_violation_count += 1
        else:
            non_overclaim_correct += 1

        gold_overall = gold.get("overall_assessment", "")
        pred_overall = extract_predicted_overall_assessment(pred)

        if gold_overall not in {"", "unclear"}:
            overall_evaluated += 1
            if pred_overall == gold_overall:
                overall_correct += 1

    return {
        "no_violation_cases_evaluated": evaluated,
        "no_violation_false_violation_count": false_violation_count,
        "no_violation_false_violation_rate": safe_div(
            false_violation_count,
            evaluated,
        ),
        "no_violation_non_overclaim_accuracy": safe_div(
            non_overclaim_correct,
            evaluated,
        ),
        "no_violation_predicted_supported_article_total": predicted_article_total,
        "no_violation_overall_assessment_accuracy": safe_div(
            overall_correct,
            overall_evaluated,
        ),
        "no_violation_overall_assessment_evaluated": overall_evaluated,
    }


def calculate_case_bucket_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate bucket-level diagnostics for the ablation study.

    These metrics help explain whether Step 9 mainly improves:
        - no-violation false-positive control,
        - viable-case article accuracy,
        - weak/uncertain caution,
        - hard-gate behavior.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)

    bucket_counts: dict[str, int] = {}
    bucket_stats: dict[str, dict[str, Any]] = {}

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})
        gold = extract_gold_fields(gold_row)

        bucket = classify_gold_case_bucket(gold)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        stats = bucket_stats.setdefault(bucket, {
            "evaluated": 0,
            "final_article_exact": 0,
            "overall_correct": 0,
            "false_violation_count": 0,
            "unsupported_final_article_count": 0,
            "predicted_supported_article_total": 0,
        })

        gold_final = set(gold["final_potentially_violated_articles"])
        pred_final = set(extract_predicted_final_articles(pred))

        stats["evaluated"] += 1

        if gold_final == pred_final:
            stats["final_article_exact"] += 1

        gold_overall = gold["overall_assessment"]
        pred_overall = extract_predicted_overall_assessment(pred)
        if gold_overall not in {"", "unclear"} and gold_overall == pred_overall:
            stats["overall_correct"] += 1

        if bucket == "no_violation_or_not_viable" and pred_final:
            stats["false_violation_count"] += 1

        unsupported = pred_final - gold_final
        stats["unsupported_final_article_count"] += len(unsupported)
        stats["predicted_supported_article_total"] += len(pred_final)

    normalized_bucket_stats: dict[str, dict[str, Any]] = {}

    for bucket, stats in bucket_stats.items():
        evaluated = int(stats["evaluated"])
        predicted_total = int(stats["predicted_supported_article_total"])
        normalized_bucket_stats[bucket] = {
            **stats,
            "final_article_exact_match_rate": safe_div(
                stats["final_article_exact"],
                evaluated,
            ),
            "overall_accuracy": safe_div(stats["overall_correct"], evaluated),
            "false_violation_rate": safe_div(
                stats["false_violation_count"],
                evaluated,
            ),
            "unsupported_final_article_rate": safe_div(
                stats["unsupported_final_article_count"],
                predicted_total,
            ),
        }

    return {
        "case_bucket_counts": bucket_counts,
        "case_bucket_metrics": normalized_bucket_stats,
    }



def calculate_candidate_article_debug_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate diagnostic Step 4/candidate article metrics.

    These are NOT the main article metrics.

    They help answer:
        Did the candidate stage retrieve or identify the right broad
        article space before Step 10 filtered the result?
    """
    pred_by_id = dict_by_scenario_id(pred_rows)
    set_metrics = SetMetrics()

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold = extract_gold_fields(gold_row)

        # Prefer gold_engaged_articles for candidate-stage evaluation.
        # Fallback to final articles if no engaged list exists.
        gold_candidates = set(gold["engaged_articles"])
        if not gold_candidates:
            gold_candidates = set(gold["final_potentially_violated_articles"])

        predicted_candidates = set(extract_predicted_candidate_articles(pred))

        set_metrics.add_case(gold_candidates, predicted_candidates)

    metrics = set_metrics.to_dict("candidate_article_debug")
    metrics["candidate_article_debug_note"] = (
        "Diagnostic only. Do not use this as the main final article metric."
    )
    return metrics


def calculate_case_retrieval_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
    k: int = 3,
) -> dict[str, Any]:
    """
    Calculate case retrieval metrics.

    Only scenarios with at least one gold relevant case ID are evaluated.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)

    evaluated = 0
    hit_count = 0
    precision_sum = 0.0
    recall_sum = 0.0
    reciprocal_rank_sum = 0.0

    total_predicted = 0
    total_false_positive = 0

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold_cases = set(extract_gold_fields(gold_row)["relevant_case_ids"])

        if not gold_cases:
            continue

        predicted_cases_ordered = extract_predicted_case_ids(pred)[:k]
        predicted_cases = set(predicted_cases_ordered)

        found = predicted_cases & gold_cases

        if found:
            hit_count += 1

        precision_sum += safe_div(len(found), k)
        recall_sum += safe_div(len(found), len(gold_cases))

        reciprocal_rank = 0.0
        for rank, case_id in enumerate(predicted_cases_ordered, start=1):
            if case_id in gold_cases:
                reciprocal_rank = 1.0 / rank
                break

        reciprocal_rank_sum += reciprocal_rank

        total_predicted += len(predicted_cases)
        total_false_positive += len(predicted_cases - gold_cases)

        evaluated += 1

    return {
        f"case_hit@{k}": safe_div(hit_count, evaluated),
        f"case_precision@{k}": safe_div(precision_sum, evaluated),
        f"case_recall@{k}": safe_div(recall_sum, evaluated),
        "case_mrr": safe_div(reciprocal_rank_sum, evaluated),
        "case_false_positive_rate": safe_div(
            total_false_positive,
            total_predicted,
        ),
        "case_evaluated": evaluated,
    }


def calculate_confidence_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate confidence metrics.

    If gold_confidence exists:
        Compare predicted confidence with gold confidence.

    Also calculates a simple confidence calibration proxy:
        correctness = 1 if both final articles exact-match and
        overall assessment matches, otherwise 0.

        confidence_score:
            high   -> 1.00
            medium -> 0.66
            low    -> 0.33
            empty  -> 0.00

        calibration error = abs(confidence_score - correctness)

    This is not a full probabilistic calibration curve, but it gives
    a useful project-level diagnostic.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)

    gold_conf_evaluated = 0
    confidence_exact = 0
    overconfident = 0
    underconfident = 0

    calibration_evaluated = 0
    calibration_error_sum = 0.0

    high_total = high_correct = 0
    medium_total = medium_correct = 0
    low_total = low_correct = 0

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold = extract_gold_fields(gold_row)

        gold_confidence = gold["confidence"]
        pred_confidence = extract_predicted_confidence(pred)

        if gold_confidence in CONFIDENCE_ORDER and gold_confidence:
            gold_conf_evaluated += 1

            if pred_confidence == gold_confidence:
                confidence_exact += 1
            elif CONFIDENCE_ORDER.get(pred_confidence, 0) > CONFIDENCE_ORDER.get(
                gold_confidence,
                0,
            ):
                overconfident += 1
            else:
                underconfident += 1

        gold_final_articles = set(gold["final_potentially_violated_articles"])
        pred_final_articles = set(extract_predicted_final_articles(pred))

        gold_overall = gold["overall_assessment"]
        pred_overall = extract_predicted_overall_assessment(pred)

        if gold_overall in {"", "unclear"}:
            article_correct = gold_final_articles == pred_final_articles
            correctness = 1.0 if article_correct else 0.0
        else:
            article_correct = gold_final_articles == pred_final_articles
            outcome_correct = gold_overall == pred_overall
            correctness = 1.0 if article_correct and outcome_correct else 0.0

        confidence_score = CONFIDENCE_SCORE.get(pred_confidence, 0.0)
        calibration_error_sum += abs(confidence_score - correctness)
        calibration_evaluated += 1

        if pred_confidence == "high":
            high_total += 1
            high_correct += int(correctness == 1.0)
        elif pred_confidence == "medium":
            medium_total += 1
            medium_correct += int(correctness == 1.0)
        elif pred_confidence == "low":
            low_total += 1
            low_correct += int(correctness == 1.0)

    return {
        "gold_confidence_accuracy": safe_div(
            confidence_exact,
            gold_conf_evaluated,
        ),
        "gold_confidence_overconfidence_rate": safe_div(
            overconfident,
            gold_conf_evaluated,
        ),
        "gold_confidence_underconfidence_rate": safe_div(
            underconfident,
            gold_conf_evaluated,
        ),
        "gold_confidence_evaluated": gold_conf_evaluated,
        "confidence_calibration_mean_absolute_error": safe_div(
            calibration_error_sum,
            calibration_evaluated,
        ),
        "confidence_calibration_evaluated": calibration_evaluated,
        "high_confidence_correct_rate": safe_div(high_correct, high_total),
        "medium_confidence_correct_rate": safe_div(medium_correct, medium_total),
        "low_confidence_correct_rate": safe_div(low_correct, low_total),
        "high_confidence_count": high_total,
        "medium_confidence_count": medium_total,
        "low_confidence_count": low_total,
    }


def calculate_inconsistency_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate Step 9 inconsistency detection metrics.

    Only scenarios with gold_inconsistency set to True/False are evaluated.
    Variants where Step 9 is skipped produce None and are skipped for
    precision/recall calculations.
    """
    pred_by_id = dict_by_scenario_id(pred_rows)

    tp = fp = fn = tn = 0
    evaluated = 0
    skipped = 0

    for gold_row in gold_rows:
        scenario_id = str(gold_row.get("scenario_id", ""))
        pred = pred_by_id.get(scenario_id, {})

        gold_value = extract_gold_fields(gold_row)["inconsistency"]
        if gold_value is None:
            continue

        pred_value = predicted_inconsistent(pred)

        if pred_value is None:
            skipped += 1
            continue

        gold_bool = bool(gold_value)

        if gold_bool and pred_value:
            tp += 1
        elif not gold_bool and pred_value:
            fp += 1
        elif gold_bool and not pred_value:
            fn += 1
        else:
            tn += 1

        evaluated += 1

    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    f1 = safe_div(2 * precision * recall, precision + recall)

    return {
        "inconsistency_precision": precision,
        "inconsistency_recall": recall,
        "inconsistency_f1": f1,
        "inconsistency_tp": tp,
        "inconsistency_fp": fp,
        "inconsistency_fn": fn,
        "inconsistency_tn": tn,
        "inconsistency_evaluated": evaluated,
        "inconsistency_skipped": skipped,
    }


def calculate_context_retention_metrics(
    *,
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate simple text-retention diagnostics.

    This checks whether the final answer mentions the final article
    predictions and selected case IDs. It is a weak diagnostic, not
    a substitute for faithfulness evaluation.

    Supports both raw runner rows and current API-wrapper rows.
    """
    article_total = 0
    article_mentioned = 0

    case_total = 0
    case_mentioned = 0

    for pred in pred_rows:
        final_answer = extract_final_answer_text(pred).lower()

        final_articles = extract_predicted_final_articles(pred)
        article_total += len(final_articles)

        for article in final_articles:
            if article.lower() in final_answer:
                article_mentioned += 1

        case_ids = extract_predicted_case_ids(pred)
        case_total += len(case_ids)

        for case_id in case_ids:
            if case_id.lower() in final_answer:
                case_mentioned += 1

    return {
        "final_article_text_retention": safe_div(
            article_mentioned,
            article_total,
        ),
        "case_id_text_retention": safe_div(case_mentioned, case_total),
        "text_retention_article_items": article_total,
        "text_retention_case_items": case_total,
        "context_retention_note": (
            "Simple text-presence diagnostic only. Use RAGAS or manual "
            "faithfulness labels for stronger faithfulness evaluation."
        ),
    }
def build_per_scenario_rows(
    *,
    gold_rows: list[dict[str, Any]],
    all_predictions: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """
    Build detailed per-scenario metric rows.
    """
    rows: list[dict[str, Any]] = []

    gold_by_id = dict_by_scenario_id(gold_rows)

    for variant, pred_rows in all_predictions.items():
        pred_by_id = dict_by_scenario_id(pred_rows)

        for scenario_id, gold_row in gold_by_id.items():
            pred = pred_by_id.get(scenario_id, {})
            gold = extract_gold_fields(gold_row)

            gold_final = set(gold["final_potentially_violated_articles"])
            pred_final = set(extract_predicted_final_articles(pred))

            gold_weak = set(gold["weak_or_uncertain_articles"])
            pred_weak = set(extract_predicted_weak_articles(pred))

            gold_rejected = set(gold["rejected_articles"])
            pred_rejected = set(extract_predicted_rejected_articles(pred))

            gold_cases = set(gold["relevant_case_ids"])
            pred_cases = set(extract_predicted_case_ids(pred))

            candidate_articles = set(extract_predicted_candidate_articles(pred))

            final_tp = sorted(gold_final & pred_final)
            final_fp = sorted(pred_final - gold_final)
            final_fn = sorted(gold_final - pred_final)

            weak_tp = sorted(gold_weak & pred_weak)
            weak_fp = sorted(pred_weak - gold_weak)
            weak_fn = sorted(gold_weak - pred_weak)

            rejected_tp = sorted(gold_rejected & pred_rejected)
            rejected_fp = sorted(pred_rejected - gold_rejected)
            rejected_fn = sorted(gold_rejected - pred_rejected)

            case_tp = sorted(gold_cases & pred_cases)
            case_fp = sorted(pred_cases - gold_cases)
            case_fn = sorted(gold_cases - pred_cases)

            severe_overclaims = sorted(gold_rejected & pred_final)
            weak_overclaims = sorted(gold_weak & pred_final)

            gold_overall = gold["overall_assessment"]
            pred_overall = extract_predicted_overall_assessment(pred)

            rows.append({
                "scenario_id": scenario_id,
                "variant": variant,

                # Gate labels.
                "gold_timeliness_status": gold["timeliness_status"],
                "pred_timeliness_status": derive_predicted_timeliness_status(pred),
                "timeliness_correct": (
                    gold["timeliness_status"]
                    == derive_predicted_timeliness_status(pred)
                    if gold["timeliness_status"] not in {"", "unclear"}
                    else None
                ),
                "gold_state_actor_status": gold["state_actor_status"],
                "pred_state_actor_status": derive_predicted_state_actor_status(pred),
                "state_actor_correct": (
                    gold["state_actor_status"]
                    == derive_predicted_state_actor_status(pred)
                    if gold["state_actor_status"] not in {"", "unclear"}
                    else None
                ),

                # Overall assessment.
                "gold_overall_assessment": gold_overall,
                "pred_overall_assessment": pred_overall,
                "overall_assessment_correct": (
                    gold_overall == pred_overall
                    if gold_overall not in {"", "unclear"}
                    else None
                ),

                # Final supported articles.
                "gold_final_potentially_violated_articles": sorted(gold_final),
                "pred_final_potentially_violated_articles": sorted(pred_final),
                "final_article_tp": final_tp,
                "final_article_fp": final_fp,
                "final_article_fn": final_fn,
                "final_article_precision": safe_div(
                    len(final_tp),
                    len(final_tp) + len(final_fp),
                ),
                "final_article_recall": safe_div(
                    len(final_tp),
                    len(final_tp) + len(final_fn),
                ),
                "final_article_exact_match": gold_final == pred_final,

                # Weak articles.
                "gold_weak_or_uncertain_articles": sorted(gold_weak),
                "pred_weak_or_uncertain_articles": sorted(pred_weak),
                "weak_article_tp": weak_tp,
                "weak_article_fp": weak_fp,
                "weak_article_fn": weak_fn,

                # Rejected articles.
                "gold_rejected_articles": sorted(gold_rejected),
                "pred_rejected_articles": sorted(pred_rejected),
                "rejected_article_tp": rejected_tp,
                "rejected_article_fp": rejected_fp,
                "rejected_article_fn": rejected_fn,

                # Overclaiming.
                "severe_overclaimed_rejected_articles": severe_overclaims,
                "weak_overclaimed_as_supported_articles": weak_overclaims,
                "unsupported_final_articles": sorted(pred_final - gold_final),
                "unsupported_final_article_count": len(pred_final - gold_final),

                # Ablation / hallucination-control bucket diagnostics.
                "gold_case_bucket": classify_gold_case_bucket(gold),
                "no_violation_false_violation": (
                    bool(pred_final) if is_no_violation_like_gold_case(gold) else None
                ),
                "no_violation_non_overclaim_correct": (
                    not bool(pred_final) if is_no_violation_like_gold_case(gold) else None
                ),

                # Candidate/debug articles.
                "candidate_articles_identified": sorted(candidate_articles),
                "candidate_article_note": (
                    "Diagnostic only; not the final article metric."
                ),

                # Retrieval.
                "gold_relevant_case_ids": sorted(gold_cases),
                "pred_similar_case_ids": sorted(pred_cases),
                "case_tp": case_tp,
                "case_fp": case_fp,
                "case_fn": case_fn,

                # Confidence / flags.
                "gold_confidence": gold["confidence"],
                "pred_confidence": extract_predicted_confidence(pred),
                "flags": extract_predicted_flags(pred),
                "pred_inconsistent": predicted_inconsistent(pred),
                "gold_inconsistency": gold["inconsistency"],

                # Run state.
                "status": extract_prediction_status(pred),
                "error": extract_error(pred),
            })

    return rows


def build_error_report(
    *,
    gold_rows: list[dict[str, Any]],
    all_predictions: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """
    Build error/warning report across variants.
    """
    errors: list[dict[str, Any]] = []
    gold_by_id = dict_by_scenario_id(gold_rows)

    for variant, pred_rows in all_predictions.items():
        pred_by_id = dict_by_scenario_id(pred_rows)

        for scenario_id in gold_by_id:
            pred = pred_by_id.get(scenario_id, {})
            errors.extend(
                validate_prediction_row(
                    scenario_id=scenario_id,
                    variant=variant,
                    pred=pred,
                )
            )

    return errors


# ─────────────────────────────────────────────────────────────
# SUMMARY / REPORTING
# ─────────────────────────────────────────────────────────────

def calculate_variant_metrics(
    *,
    gold_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Calculate all metrics for one variant.
    """
    metrics: dict[str, Any] = {}

    metrics.update(calculate_gate_metrics(gold_rows=gold_rows, pred_rows=pred_rows))
    metrics.update(
        calculate_overall_assessment_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_final_article_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_weak_article_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_rejected_article_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_overclaiming_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_no_violation_false_violation_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_case_bucket_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_candidate_article_debug_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_case_retrieval_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
            k=3,
        )
    )
    metrics.update(
        calculate_confidence_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_inconsistency_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )
    )
    metrics.update(
        calculate_context_retention_metrics(
            pred_rows=pred_rows,
        )
    )

    return metrics


def build_summary_csv_rows(summary: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Flatten selected summary metrics into CSV rows.

    Includes ablation-relevant hallucination-control metrics.
    """
    rows: list[dict[str, Any]] = []

    for variant, metrics in summary.items():
        row = {
            "variant": variant,

            # Main final-output metrics.
            "final_article_precision": metrics.get("final_article_precision"),
            "final_article_recall": metrics.get("final_article_recall"),
            "final_article_f1": metrics.get("final_article_f1"),
            "final_article_exact_match": metrics.get("final_article_exact_match"),
            "overall_assessment_accuracy": metrics.get(
                "overall_assessment_accuracy"
            ),

            # Hallucination / legal overclaiming proxies.
            "unsupported_final_article_rate": metrics.get(
                "unsupported_final_article_rate"
            ),
            "rejected_article_overclaim_rate_vs_gold_rejected": metrics.get(
                "rejected_article_overclaim_rate_vs_gold_rejected"
            ),
            "weak_article_overclaim_rate_vs_gold_weak": metrics.get(
                "weak_article_overclaim_rate_vs_gold_weak"
            ),
            "severe_overclaim_count": metrics.get("severe_overclaim_count"),
            "weak_overclaim_count": metrics.get("weak_overclaim_count"),

            # No-violation false-positive control.
            "no_violation_cases_evaluated": metrics.get(
                "no_violation_cases_evaluated"
            ),
            "no_violation_false_violation_rate": metrics.get(
                "no_violation_false_violation_rate"
            ),
            "no_violation_non_overclaim_accuracy": metrics.get(
                "no_violation_non_overclaim_accuracy"
            ),
            "no_violation_overall_assessment_accuracy": metrics.get(
                "no_violation_overall_assessment_accuracy"
            ),

            # Retrieval / gates / confidence.
            "case_hit@3": metrics.get("case_hit@3"),
            "case_mrr": metrics.get("case_mrr"),
            "timeliness_gate_accuracy": metrics.get("timeliness_gate_accuracy"),
            "state_actor_gate_accuracy": metrics.get("state_actor_gate_accuracy"),
            "confidence_calibration_mean_absolute_error": metrics.get(
                "confidence_calibration_mean_absolute_error"
            ),
            "inconsistency_f1": metrics.get("inconsistency_f1"),

            # Candidate debug metric.
            "candidate_article_debug_f1": metrics.get(
                "candidate_article_debug_f1"
            ),

            # Bucket diagnostics are JSON-encoded by flatten_for_csv().
            "case_bucket_counts": metrics.get("case_bucket_counts", {}),
        }

        rows.append(row)

    return rows



def _float_metric(metrics: dict[str, Any], key: str) -> float:
    """
    Safely read a numeric metric as float for report formatting.
    """
    try:
        return float(metrics.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_ablation_comparison(
    summary: dict[str, dict[str, Any]],
    *,
    full_variant: str = "full_himikama",
    ablation_variant: str = "controlled_no_step9",
) -> dict[str, Any]:
    """
    Build a direct full-vs-no-Step-9 comparison.

    Convention:
        - For good metrics, positive delta means full_himikama improved.
        - For bad metrics, positive reduction means full_himikama reduced harm.
    """
    if full_variant not in summary or ablation_variant not in summary:
        return {}

    full = summary[full_variant]
    ablated = summary[ablation_variant]

    good_metrics = [
        "final_article_precision",
        "final_article_recall",
        "final_article_f1",
        "final_article_exact_match",
        "overall_assessment_accuracy",
        "no_violation_non_overclaim_accuracy",
        "case_hit@3",
        "case_mrr",
    ]

    bad_metrics = [
        "unsupported_final_article_rate",
        "rejected_article_overclaim_rate_vs_gold_rejected",
        "weak_article_overclaim_rate_vs_gold_weak",
        "no_violation_false_violation_rate",
        "confidence_calibration_mean_absolute_error",
    ]

    good_deltas = {
        key: _float_metric(full, key) - _float_metric(ablated, key)
        for key in good_metrics
    }

    bad_reductions = {
        key: _float_metric(ablated, key) - _float_metric(full, key)
        for key in bad_metrics
    }

    h1_primary_metrics = [
        "unsupported_final_article_rate",
        "rejected_article_overclaim_rate_vs_gold_rejected",
        "weak_article_overclaim_rate_vs_gold_weak",
        "no_violation_false_violation_rate",
    ]

    h1_improved = [
        key for key in h1_primary_metrics
        if bad_reductions.get(key, 0.0) > 0
    ]

    h1_worsened = [
        key for key in h1_primary_metrics
        if bad_reductions.get(key, 0.0) < 0
    ]

    recall_delta = good_deltas.get("final_article_recall", 0.0)
    precision_delta = good_deltas.get("final_article_precision", 0.0)
    safety_reduction = bad_reductions.get("unsupported_final_article_rate", 0.0)

    h2_tradeoff_pattern = (
        recall_delta <= 0
        and (precision_delta > 0 or safety_reduction > 0)
    )

    return {
        "full_variant": full_variant,
        "ablation_variant": ablation_variant,
        "good_metric_deltas_full_minus_ablation": good_deltas,
        "bad_metric_reductions_ablation_minus_full": bad_reductions,
        "h1_primary_metrics_improved": h1_improved,
        "h1_primary_metrics_worsened": h1_worsened,
        "h1_supported_by_primary_overclaim_metrics": (
            len(h1_improved) > 0 and len(h1_worsened) == 0
        ),
        "h2_safety_recall_tradeoff_pattern": h2_tradeoff_pattern,
        "interpretation_note": (
            "For good metrics, positive means full_himikama is higher. "
            "For bad metrics, positive means full_himikama reduced the bad "
            "rate compared with controlled_no_step9."
        ),
    }


def build_variant_comparison_markdown(
    summary: dict[str, dict[str, Any]],
) -> str:
    """
    Build a concise markdown comparison table.

    This version is ablation-study aware and highlights hallucination
    control metrics for H1/H2.
    """
    lines: list[str] = []

    lines.append("# Himikama Variant Evaluation Summary")
    lines.append("")
    lines.append(
        "Main article metrics use `final_potentially_violated_articles`, "
        "not `articles_identified`."
    )
    lines.append("")
    lines.append(
        "| Variant | Precision | Recall | F1 | Outcome Acc. | Unsupported Final | "
        "Rejected Overclaim | Weak Overclaim | No-Violation False Violation | "
        "Case Hit@3 | Case MRR |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    for variant, metrics in summary.items():
        lines.append(
            "| {variant} | {precision:.3f} | {recall:.3f} | {f1:.3f} | "
            "{outcome:.3f} | {unsupported:.3f} | {rej:.3f} | {weak:.3f} | "
            "{false_violation:.3f} | {hit:.3f} | {mrr:.3f} |".format(
                variant=variant,
                precision=_float_metric(metrics, "final_article_precision"),
                recall=_float_metric(metrics, "final_article_recall"),
                f1=_float_metric(metrics, "final_article_f1"),
                outcome=_float_metric(metrics, "overall_assessment_accuracy"),
                unsupported=_float_metric(
                    metrics,
                    "unsupported_final_article_rate",
                ),
                rej=_float_metric(
                    metrics,
                    "rejected_article_overclaim_rate_vs_gold_rejected",
                ),
                weak=_float_metric(
                    metrics,
                    "weak_article_overclaim_rate_vs_gold_weak",
                ),
                false_violation=_float_metric(
                    metrics,
                    "no_violation_false_violation_rate",
                ),
                hit=_float_metric(metrics, "case_hit@3"),
                mrr=_float_metric(metrics, "case_mrr"),
            )
        )

    ablation = build_ablation_comparison(summary)

    if ablation:
        good = ablation["good_metric_deltas_full_minus_ablation"]
        bad = ablation["bad_metric_reductions_ablation_minus_full"]

        lines.append("")
        lines.append("## Full Himikama vs No-Step-9 Ablation Delta")
        lines.append("")
        lines.append(
            "For good metrics, positive means `full_himikama` is higher. "
            "For bad metrics, positive means `full_himikama` reduced the bad rate."
        )
        lines.append("")
        lines.append("| Metric | Direction | Delta / Reduction |")
        lines.append("|---|---|---:|")

        for key in [
            "final_article_precision",
            "final_article_recall",
            "final_article_f1",
            "overall_assessment_accuracy",
            "no_violation_non_overclaim_accuracy",
        ]:
            lines.append(
                f"| {key} | higher is better | {good.get(key, 0.0):.3f} |"
            )

        for key in [
            "unsupported_final_article_rate",
            "rejected_article_overclaim_rate_vs_gold_rejected",
            "weak_article_overclaim_rate_vs_gold_weak",
            "no_violation_false_violation_rate",
            "confidence_calibration_mean_absolute_error",
        ]:
            lines.append(
                f"| {key} | lower is better | {bad.get(key, 0.0):.3f} |"
            )

        lines.append("")
        lines.append("## H1/H2 Interpretation")
        lines.append("")
        if ablation.get("h1_supported_by_primary_overclaim_metrics"):
            lines.append(
                "- H1 is supported by the primary overclaiming metrics: "
                "`full_himikama` reduced at least one hallucination/overclaiming "
                "metric without worsening the others."
            )
        else:
            lines.append(
                "- H1 is not fully supported by the primary overclaiming metrics. "
                "Review the per-scenario rows to identify where Step 9 helped or hurt."
            )

        if ablation.get("h2_safety_recall_tradeoff_pattern"):
            lines.append(
                "- H2 trade-off pattern is present: recall is equal/lower while "
                "precision and/or safety improved."
            )
        else:
            lines.append(
                "- H2 trade-off pattern is not clearly present from aggregate metrics."
            )

    lines.append("")
    lines.append("## Interpretation Notes")
    lines.append("")
    lines.append("- `Final Article F1` is the main article prediction score.")
    lines.append("- `Unsupported Final` is a hallucination/overclaiming proxy: lower is better.")
    lines.append("- `Rejected Overclaim` should be as low as possible.")
    lines.append("- `Weak Overclaim` should be as low as possible.")
    lines.append("- `No-Violation False Violation` should be as low as possible.")
    lines.append("- `articles_identified` is diagnostic only and is not the final article metric.")
    lines.append("- Case retrieval metrics are evaluated only where gold relevant case IDs exist.")

    return "\n".join(lines) + "\n"



def print_summary(summary: dict[str, dict[str, Any]]) -> None:
    """
    Print important metrics to the terminal.
    """
    print("\n=== HIMIKAMA METRIC SUMMARY ===")

    for variant, metrics in summary.items():
        print(f"\nVariant: {variant}")
        print(f"  Final Article F1:             {metrics.get('final_article_f1', 0.0):.4f}")
        print(f"  Final Article Precision:      {metrics.get('final_article_precision', 0.0):.4f}")
        print(f"  Final Article Recall:         {metrics.get('final_article_recall', 0.0):.4f}")
        print(f"  Outcome Accuracy:             {metrics.get('overall_assessment_accuracy', 0.0):.4f}")
        print(
            "  Unsupported Final Rate:       "
            f"{metrics.get('unsupported_final_article_rate', 0.0):.4f}"
        )
        print(
            "  Rejected Overclaim:           "
            f"{metrics.get('rejected_article_overclaim_rate_vs_gold_rejected', 0.0):.4f}"
        )
        print(
            "  Weak Overclaim:               "
            f"{metrics.get('weak_article_overclaim_rate_vs_gold_weak', 0.0):.4f}"
        )
        print(
            "  No-Violation False Violation: "
            f"{metrics.get('no_violation_false_violation_rate', 0.0):.4f}"
        )
        print(f"  Case Hit@3:                   {metrics.get('case_hit@3', 0.0):.4f}")
        print(f"  Case MRR:                     {metrics.get('case_mrr', 0.0):.4f}")

    ablation = build_ablation_comparison(summary)
    if ablation:
        print("\n=== FULL VS NO-STEP-9 ABLATION DELTAS ===")
        print("Good metrics: positive means full_himikama is higher.")
        for key, value in ablation.get(
            "good_metric_deltas_full_minus_ablation",
            {},
        ).items():
            print(f"  Δ {key}: {value:.4f}")

        print("Bad metrics: positive means full_himikama reduced the bad rate.")
        for key, value in ablation.get(
            "bad_metric_reductions_ablation_minus_full",
            {},
        ).items():
            print(f"  reduction {key}: {value:.4f}")

        print(
            "  H1 supported by primary overclaim metrics: "
            f"{ablation.get('h1_supported_by_primary_overclaim_metrics')}"
        )
        print(
            "  H2 safety/recall trade-off pattern: "
            f"{ablation.get('h2_safety_recall_tradeoff_pattern')}"
        )


# ─────────────────────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────────────────────

def load_predictions(
    *,
    output_dir: Path,
    selected_variants: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Load prediction files for selected variants.
    """
    selected = selected_variants or list(VARIANT_FILES.keys())
    predictions: dict[str, list[dict[str, Any]]] = {}

    for variant in selected:
        if variant not in VARIANT_FILES:
            raise ValueError(
                f"Unknown variant {variant!r}. Valid variants: "
                f"{', '.join(VARIANT_FILES)}"
            )

        path = output_dir / VARIANT_FILES[variant]

        if not path.exists():
            raise FileNotFoundError(
                f"Prediction file for variant {variant!r} not found: {path}. "
                "Run python -m evaluation.run_all_variants first."
            )

        predictions[variant] = load_jsonl(path)

    return predictions


def calculate_all_metrics(
    *,
    dataset_path: Path,
    output_dir: Path,
    metrics_dir: Path,
    selected_variants: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Main metric calculation orchestration.
    """
    gold_rows = load_jsonl(dataset_path)
    all_predictions = load_predictions(
        output_dir=output_dir,
        selected_variants=selected_variants,
    )

    summary: dict[str, dict[str, Any]] = {}

    for variant, pred_rows in all_predictions.items():
        summary[variant] = calculate_variant_metrics(
            gold_rows=gold_rows,
            pred_rows=pred_rows,
        )

    per_scenario_rows = build_per_scenario_rows(
        gold_rows=gold_rows,
        all_predictions=all_predictions,
    )

    error_report = build_error_report(
        gold_rows=gold_rows,
        all_predictions=all_predictions,
    )

    ablation_comparison = build_ablation_comparison(summary)

    metrics_dir.mkdir(parents=True, exist_ok=True)

    summary_json_path = metrics_dir / "metrics_summary.json"
    summary_csv_path = metrics_dir / "metrics_summary.csv"
    per_scenario_path = metrics_dir / "per_scenario_metrics.jsonl"
    error_report_path = metrics_dir / "error_report.jsonl"
    comparison_md_path = metrics_dir / "variant_comparison.md"
    ablation_json_path = metrics_dir / "ablation_comparison.json"

    write_json(summary_json_path, summary)
    write_csv(summary_csv_path, build_summary_csv_rows(summary))
    write_jsonl(per_scenario_path, per_scenario_rows)
    write_jsonl(error_report_path, error_report)
    comparison_md_path.write_text(
        build_variant_comparison_markdown(summary),
        encoding="utf-8",
    )

    if ablation_comparison:
        write_json(ablation_json_path, ablation_comparison)

    print(f"Saved summary JSON:       {summary_json_path}")
    print(f"Saved summary CSV:        {summary_csv_path}")
    print(f"Saved per-scenario JSONL: {per_scenario_path}")
    print(f"Saved error report JSONL: {error_report_path}")
    print(f"Saved markdown summary:   {comparison_md_path}")

    if ablation_comparison:
        print(f"Saved ablation JSON:      {ablation_json_path}")

    print_summary(summary)

    if error_report:
        print(
            f"\nWarnings/errors found: {len(error_report)}. "
            f"See {error_report_path}"
        )

    return summary


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Calculate Himikama structured evaluation metrics."
    )

    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help="Path to gold-label dataset JSONL.",
    )

    parser.add_argument(
        "--outputs-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing variant output JSONL files.",
    )

    parser.add_argument(
        "--metrics-dir",
        default=str(DEFAULT_METRICS_DIR),
        help="Directory where metric results will be saved.",
    )

    parser.add_argument(
        "--variant",
        choices=list(VARIANT_FILES.keys()) + ["all", "ablation"],
        default="all",
        help="Calculate metrics for one variant or all variants.",
    )

    return parser.parse_args()


def main() -> None:
    """
    CLI entrypoint.
    """
    args = parse_args()

    # CLI ABLATION PATCH: restrict ablation mode to the two Step 9 variants.
    # This allows:
    #     python -m evaluation.calculate_metrics --variant ablation
    # without requiring single_shot_rag_outputs.jsonl.
    if args.variant == "ablation":
        VARIANT_FILES.clear()
        VARIANT_FILES.update({
            "controlled_no_step9": "controlled_no_step9_outputs.jsonl",
            "full_himikama": "full_himikama_outputs.jsonl",
        })
        args.variant = "all"

    selected_variants = None
    if args.variant != "all":
        selected_variants = [args.variant]

    calculate_all_metrics(
        dataset_path=Path(args.dataset),
        output_dir=Path(args.outputs_dir),
        metrics_dir=Path(args.metrics_dir),
        selected_variants=selected_variants,
    )


if __name__ == "__main__":
    main()
