"""
himikama/backend/evaluation/run_all_variants.py
═══════════════════════════════════════════════════════════════
Batch evaluation runner for Himikama.

Reads:
    evaluation/datasets/himikama_eval_set.jsonl

Runs:
    1. single_shot_rag
    2. controlled_no_step9
    3. full_himikama

Writes:
    evaluation/outputs/single_shot_rag_outputs.jsonl
    evaluation/outputs/controlled_no_step9_outputs.jsonl
    evaluation/outputs/full_himikama_outputs.jsonl

Usage:
    Run all variants:
        python -m evaluation.run_all_variants

    Run only one variant:
        python -m evaluation.run_all_variants --variant single_shot_rag
        python -m evaluation.run_all_variants --variant controlled_no_step9
        python -m evaluation.run_all_variants --variant full_himikama

    Run only first N cases:
        python -m evaluation.run_all_variants --limit 3
        python -m evaluation.run_all_variants --variant single_shot_rag --limit 3

Dataset compatibility:
    This runner accepts either of these dataset formats:

    Old format:
        {
            "scenario_id": "T001",
            "intake": {...},
            ...
        }

    New recommended evaluation format:
        {
            "scenario_id": "T001",
            "test_intake": {...},
            "gold_final_potentially_violated_articles": [...],
            ...
        }

Prediction fields:
    This runner preserves Step 4 candidate articles:

        articles_identified

    and also stores final Step 10 structured conclusions:

        structured_assessment
        final_potentially_violated_articles
        final_weak_or_uncertain_articles
        final_rejected_articles
        overall_assessment
        precedent_alignment
        article_assessments

    For evaluation, use final_potentially_violated_articles,
    not articles_identified.
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from chain.runner import run_full_chain
from evaluation.variants.controlled_no_step9 import run_controlled_chain_no_step9
from evaluation.variants.single_shot_rag import run_single_shot_rag


DATASET_PATH = Path("evaluation/datasets/himikama_eval_set.jsonl")
OUTPUT_DIR = Path("evaluation/outputs")

VariantRunner = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


VARIANTS: dict[str, VariantRunner] = {
    "single_shot_rag": run_single_shot_rag,
    "controlled_no_step9": run_controlled_chain_no_step9,
    "full_himikama": run_full_chain,
}


# ─────────────────────────────────────────────────────────────
# DATASET LOADING
# ─────────────────────────────────────────────────────────────

def load_dataset(path: Path) -> list[dict[str, Any]]:
    """
    Load JSONL evaluation dataset.
    Each line must be one valid JSON object.

    Required:
        scenario_id

    Intake field:
        Either "intake" or "test_intake" is accepted.
    """
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

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
                f"Invalid JSON in {path} on line {line_no}: {e}"
            ) from e

        if "scenario_id" not in obj:
            raise ValueError(f"Missing scenario_id on line {line_no}")

        intake = _extract_intake_from_dataset_row(obj)
        if not isinstance(intake, dict):
            raise ValueError(
                f"Missing or invalid intake/test_intake on line {line_no}"
            )

        rows.append(obj)

    return rows


def _extract_intake_from_dataset_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Extract intake from either old or new dataset schema.
    """
    intake = row.get("intake")

    if isinstance(intake, dict):
        return intake

    test_intake = row.get("test_intake")

    if isinstance(test_intake, dict):
        return test_intake

    return {}


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """
    Append one row to a JSONL file.
    """
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────────────────────
# SAFE EXTRACTION HELPERS
# ─────────────────────────────────────────────────────────────

def _safe_dict(value: Any) -> dict[str, Any]:
    """
    Return value if it is a dict, otherwise return {}.
    """
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    """
    Return value if it is a list, otherwise return [].
    """
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    """
    Convert list-like values into a clean string list.
    """
    if isinstance(value, list):
        return _dedupe_preserve_order([
            str(item).strip()
            for item in value
            if str(item).strip()
        ])

    if isinstance(value, tuple):
        return _dedupe_preserve_order([
            str(item).strip()
            for item in value
            if str(item).strip()
        ])

    if isinstance(value, str) and value.strip():
        return [value.strip()]

    return []


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """
    Deduplicate list values while preserving order.
    """
    seen: set[str] = set()
    output: list[str] = []

    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)

    return output


def _get_step_data(output: dict[str, Any], step_key: str) -> dict[str, Any]:
    """
    Safely read output["step_results"][step_key]["data"].
    """
    step_results = _safe_dict(output.get("step_results", {}))
    step = _safe_dict(step_results.get(step_key, {}))
    return _safe_dict(step.get("data", {}))


def _extract_confidence_evaluation(output: dict[str, Any]) -> dict[str, Any]:
    """
    Safely read output["confidence"]["evaluation"].
    """
    confidence = _safe_dict(output.get("confidence", {}))
    return _safe_dict(confidence.get("evaluation", {}))


# ─────────────────────────────────────────────────────────────
# STRUCTURED ASSESSMENT EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_structured_assessment(output: dict[str, Any]) -> dict[str, Any]:
    """
    Extract normalized structured assessment from a variant output.

    Preferred order:
        1. output["structured_assessment"]
        2. output["step_results"]["step_10"]["data"]["structured_assessment"]
        3. output["confidence"]["evaluation"]["structured_assessment"]

    Returns a default empty assessment if none is available.
    """
    top_level = output.get("structured_assessment")
    if isinstance(top_level, dict):
        return _with_default_structured_fields(top_level)

    step_10_data = _get_step_data(output, "step_10")
    from_step_10 = step_10_data.get("structured_assessment")
    if isinstance(from_step_10, dict):
        return _with_default_structured_fields(from_step_10)

    confidence_evaluation = _extract_confidence_evaluation(output)
    from_confidence = confidence_evaluation.get("structured_assessment")
    if isinstance(from_confidence, dict):
        return _with_default_structured_fields(from_confidence)

    return _empty_structured_assessment()


def _empty_structured_assessment() -> dict[str, Any]:
    """
    Return an empty structured assessment with all expected keys.
    """
    return {
        "final_potentially_violated_articles": [],
        "final_weak_or_uncertain_articles": [],
        "final_rejected_articles": [],
        "overall_assessment": "",
        "precedent_alignment": "",
        "article_assessments": [],
        "key_strengths": [],
        "key_weaknesses": [],
        "faithfulness_notes": [],
    }


def _with_default_structured_fields(
    structured_assessment: dict[str, Any],
) -> dict[str, Any]:
    """
    Ensure structured assessment always has the expected fields.
    """
    default = _empty_structured_assessment()
    merged = dict(default)
    merged.update(structured_assessment)

    merged["final_potentially_violated_articles"] = _string_list(
        merged.get("final_potentially_violated_articles", [])
    )
    merged["final_weak_or_uncertain_articles"] = _string_list(
        merged.get("final_weak_or_uncertain_articles", [])
    )
    merged["final_rejected_articles"] = _string_list(
        merged.get("final_rejected_articles", [])
    )
    merged["overall_assessment"] = str(
        merged.get("overall_assessment", "")
    ).strip()
    merged["precedent_alignment"] = str(
        merged.get("precedent_alignment", "")
    ).strip()
    merged["article_assessments"] = [
        item
        for item in _safe_list(merged.get("article_assessments", []))
        if isinstance(item, dict)
    ]
    merged["key_strengths"] = _string_list(merged.get("key_strengths", []))
    merged["key_weaknesses"] = _string_list(merged.get("key_weaknesses", []))
    merged["faithfulness_notes"] = _string_list(
        merged.get("faithfulness_notes", [])
    )

    return merged


def extract_final_potentially_violated_articles(
    output: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> list[str]:
    """
    Extract final supported/potentially violated articles.

    Preferred order:
        1. output top-level
        2. structured_assessment
        3. step_10 data
        4. confidence evaluation
    """
    top_level = _string_list(output.get("final_potentially_violated_articles", []))
    if top_level:
        return top_level

    from_structured = _string_list(
        structured_assessment.get("final_potentially_violated_articles", [])
    )
    if from_structured:
        return from_structured

    step_10_data = _get_step_data(output, "step_10")
    from_step_10 = _string_list(
        step_10_data.get("final_potentially_violated_articles", [])
    )
    if from_step_10:
        return from_step_10

    confidence_eval = _extract_confidence_evaluation(output)
    return _string_list(
        confidence_eval.get("final_potentially_violated_articles", [])
    )


def extract_final_weak_or_uncertain_articles(
    output: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> list[str]:
    """
    Extract final weak/uncertain articles.
    """
    top_level = _string_list(output.get("final_weak_or_uncertain_articles", []))
    if top_level:
        return top_level

    from_structured = _string_list(
        structured_assessment.get("final_weak_or_uncertain_articles", [])
    )
    if from_structured:
        return from_structured

    step_10_data = _get_step_data(output, "step_10")
    from_step_10 = _string_list(
        step_10_data.get("final_weak_or_uncertain_articles", [])
    )
    if from_step_10:
        return from_step_10

    confidence_eval = _extract_confidence_evaluation(output)
    return _string_list(
        confidence_eval.get("final_weak_or_uncertain_articles", [])
    )


def extract_final_rejected_articles(
    output: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> list[str]:
    """
    Extract final rejected articles.
    """
    top_level = _string_list(output.get("final_rejected_articles", []))
    if top_level:
        return top_level

    from_structured = _string_list(
        structured_assessment.get("final_rejected_articles", [])
    )
    if from_structured:
        return from_structured

    step_10_data = _get_step_data(output, "step_10")
    from_step_10 = _string_list(step_10_data.get("final_rejected_articles", []))
    if from_step_10:
        return from_step_10

    confidence_eval = _extract_confidence_evaluation(output)
    return _string_list(confidence_eval.get("final_rejected_articles", []))


def extract_overall_assessment(
    output: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> str:
    """
    Extract final overall assessment label.
    """
    top_level = str(output.get("overall_assessment", "") or "").strip()
    if top_level:
        return top_level

    from_structured = str(
        structured_assessment.get("overall_assessment", "") or ""
    ).strip()
    if from_structured:
        return from_structured

    step_10_data = _get_step_data(output, "step_10")
    from_step_10 = str(step_10_data.get("overall_assessment", "") or "").strip()
    if from_step_10:
        return from_step_10

    confidence_eval = _extract_confidence_evaluation(output)
    return str(confidence_eval.get("overall_assessment", "") or "").strip()


def extract_precedent_alignment(
    output: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> str:
    """
    Extract precedent alignment label.
    """
    top_level = str(output.get("precedent_alignment", "") or "").strip()
    if top_level:
        return top_level

    from_structured = str(
        structured_assessment.get("precedent_alignment", "") or ""
    ).strip()
    if from_structured:
        return from_structured

    confidence_eval = _extract_confidence_evaluation(output)
    return str(confidence_eval.get("precedent_alignment", "") or "").strip()


# ─────────────────────────────────────────────────────────────
# RETRIEVED CONTEXT EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_retrieved_contexts(output: dict[str, Any]) -> list[str]:
    """
    Extract text contexts for later RAGAS / faithfulness evaluation.

    Works with:
        - full_himikama output
        - controlled_no_step9 output
        - single_shot_rag output
    """
    contexts: list[str] = []

    step_results = output.get("step_results", {})
    if isinstance(step_results, dict):
        step_4_data = (
            step_results.get("step_4", {})
            .get("data", {})
            if isinstance(step_results.get("step_4", {}), dict)
            else {}
        )

        for article in step_4_data.get("retrieved_articles", []):
            if not isinstance(article, dict):
                continue

            text = (
                article.get("text")
                or article.get("content")
                or article.get("document")
                or article.get("summary")
            )

            if text:
                contexts.append(str(text))

        step_7_data = (
            step_results.get("step_7", {})
            .get("data", {})
            if isinstance(step_results.get("step_7", {}), dict)
            else {}
        )

        for case in step_7_data.get("stage_b_cases", []):
            if not isinstance(case, dict):
                continue

            text = (
                case.get("summary")
                or case.get("text")
                or case.get("document")
                or case.get("content")
            )

            if text:
                contexts.append(str(text))

    # Single-shot fallback
    for article in output.get("retrieved_articles", []):
        if not isinstance(article, dict):
            continue

        text = (
            article.get("text")
            or article.get("content")
            or article.get("document")
            or article.get("summary")
        )

        if text:
            contexts.append(str(text))

    for case in output.get("retrieved_cases", []):
        if not isinstance(case, dict):
            continue

        text = (
            case.get("summary")
            or case.get("text")
            or case.get("document")
            or case.get("content")
        )

        if text:
            contexts.append(str(text))

    return contexts


# ─────────────────────────────────────────────────────────────
# GOLD FIELD EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_gold_fields(row: dict[str, Any]) -> dict[str, Any]:
    """
    Extract gold labels from the dataset row.

    Supports the new recommended evaluation schema and older aliases.
    """
    return {
        "gold_timeliness_status": row.get("gold_timeliness_status"),
        "gold_state_actor_status": row.get("gold_state_actor_status"),

        "gold_engaged_articles": _string_list(
            row.get("gold_engaged_articles", [])
        ),

        "gold_final_potentially_violated_articles": _string_list(
            row.get(
                "gold_final_potentially_violated_articles",
                row.get(
                    "gold_potentially_violated_articles",
                    row.get("gold_articles", []),
                ),
            )
        ),

        "gold_weak_or_uncertain_articles": _string_list(
            row.get("gold_weak_or_uncertain_articles", [])
        ),

        "gold_rejected_articles": _string_list(
            row.get("gold_rejected_articles", [])
        ),

        "gold_overall_assessment": row.get(
            "gold_overall_assessment",
            row.get("gold_final_outcome"),
        ),

        "gold_final_case_outcome": row.get("gold_final_case_outcome"),

        "gold_relevant_case_ids": _string_list(
            row.get(
                "gold_relevant_case_ids",
                row.get("gold_similar_case_ids", []),
            )
        ),

        "gold_reasoning_summary": row.get("gold_reasoning_summary", ""),
        "gold_remedy": row.get("gold_remedy", ""),
        "evaluation_notes": row.get("evaluation_notes", ""),
    }


# ─────────────────────────────────────────────────────────────
# OUTPUT NORMALIZATION
# ─────────────────────────────────────────────────────────────

def normalize_output(
    *,
    scenario_id: str,
    variant: str,
    dataset_row: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    """
    Normalize all variant outputs into one shared structure.

    This makes metric scripts simpler later.

    Important:
        articles_identified is a Step 4 candidate/debug field.
        final_potentially_violated_articles is the final prediction field.
    """
    structured_assessment = extract_structured_assessment(output)

    final_potentially_violated_articles = (
        extract_final_potentially_violated_articles(
            output,
            structured_assessment,
        )
    )
    final_weak_or_uncertain_articles = (
        extract_final_weak_or_uncertain_articles(
            output,
            structured_assessment,
        )
    )
    final_rejected_articles = extract_final_rejected_articles(
        output,
        structured_assessment,
    )
    overall_assessment = extract_overall_assessment(
        output,
        structured_assessment,
    )
    precedent_alignment = extract_precedent_alignment(
        output,
        structured_assessment,
    )

    # Keep structured_assessment internally consistent after extraction.
    structured_assessment["final_potentially_violated_articles"] = (
        final_potentially_violated_articles
    )
    structured_assessment["final_weak_or_uncertain_articles"] = (
        final_weak_or_uncertain_articles
    )
    structured_assessment["final_rejected_articles"] = final_rejected_articles
    structured_assessment["overall_assessment"] = overall_assessment
    structured_assessment["precedent_alignment"] = precedent_alignment

    return {
        "scenario_id": scenario_id,
        "variant": variant,

        # Source/gold fields for metric scripts.
        "source_case_name": dataset_row.get("source_case_name", ""),
        "source_case_number": dataset_row.get("source_case_number", ""),
        "year": dataset_row.get("year"),
        "intake": _extract_intake_from_dataset_row(dataset_row),
        "gold": extract_gold_fields(dataset_row),

        # General run output.
        "status": output.get("status"),
        "error": output.get("error"),
        "started_at": output.get("started_at"),
        "completed_at": output.get("completed_at"),

        # Final answer text.
        "final_answer": output.get("final_answer", ""),
        "final_answer_with_disclaimer": output.get(
            "final_answer_with_disclaimer",
            output.get("final_answer", ""),
        ),

        # Confidence output.
        "confidence": output.get("confidence", {}),
        "confidence_level": output.get("confidence_level", ""),
        "flags": output.get("flags", []),

        # Step 4 candidate/debug articles.
        # Do not use this as the main final article prediction metric.
        "articles_identified": output.get("articles_identified", []),

        # Final Step 10 structured prediction fields.
        "structured_assessment": structured_assessment,
        "final_potentially_violated_articles": (
            final_potentially_violated_articles
        ),
        "final_weak_or_uncertain_articles": final_weak_or_uncertain_articles,
        "final_rejected_articles": final_rejected_articles,
        "overall_assessment": overall_assessment,
        "precedent_alignment": precedent_alignment,
        "article_assessments": structured_assessment.get(
            "article_assessments",
            [],
        ),
        "key_strengths": structured_assessment.get("key_strengths", []),
        "key_weaknesses": structured_assessment.get("key_weaknesses", []),
        "faithfulness_notes": structured_assessment.get(
            "faithfulness_notes",
            [],
        ),

        # Retrieval / trace output.
        "similar_case_ids": output.get("similar_case_ids", []),
        "step_results": output.get("step_results", {}),
        "retrieved_articles": output.get("retrieved_articles", []),
        "retrieved_cases": output.get("retrieved_cases", []),
        "retrieved_contexts": extract_retrieved_contexts(output),
    }


def _failed_output(
    *,
    variant_name: str,
    error: Exception,
) -> dict[str, Any]:
    """
    Build a normalized failed output object if a variant raises.
    """
    return {
        "variant": variant_name,
        "status": "failed",
        "final_answer": "",
        "final_answer_with_disclaimer": "",
        "structured_assessment": _empty_structured_assessment(),
        "final_potentially_violated_articles": [],
        "final_weak_or_uncertain_articles": [],
        "final_rejected_articles": [],
        "overall_assessment": "",
        "precedent_alignment": "",
        "article_assessments": [],
        "confidence": {},
        "confidence_level": "",
        "flags": [],
        "articles_identified": [],
        "similar_case_ids": [],
        "step_results": {},
        "retrieved_articles": [],
        "retrieved_cases": [],
        "error": str(error),
        "started_at": None,
        "completed_at": None,
    }


# ─────────────────────────────────────────────────────────────
# VARIANT EXECUTION
# ─────────────────────────────────────────────────────────────

async def run_variant(
    *,
    variant_name: str,
    runner: VariantRunner,
    dataset: list[dict[str, Any]],
) -> None:
    """
    Run one variant on the whole dataset.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_path = OUTPUT_DIR / f"{variant_name}_outputs.jsonl"

    if output_path.exists():
        output_path.unlink()

    print(f"\n=== Running variant: {variant_name} ===")
    print(f"Dataset size: {len(dataset)}")
    print(f"Output file: {output_path}")

    for index, row in enumerate(dataset, start=1):
        scenario_id = row["scenario_id"]
        intake = _extract_intake_from_dataset_row(row)

        print(f"[{variant_name}] {index}/{len(dataset)} — {scenario_id}")

        try:
            result = await runner(intake)

        except Exception as e:
            result = _failed_output(
                variant_name=variant_name,
                error=e,
            )

        normalized = normalize_output(
            scenario_id=scenario_id,
            variant=variant_name,
            dataset_row=row,
            output=result,
        )

        append_jsonl(output_path, normalized)

    print(f"Completed variant: {variant_name}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Himikama evaluation variants."
    )

    parser.add_argument(
        "--variant",
        choices=list(VARIANTS.keys()) + ["all"],
        default="all",
        help="Which variant to run.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only run the first N cases. Useful for trial runs.",
    )

    parser.add_argument(
        "--dataset",
        type=str,
        default=str(DATASET_PATH),
        help=(
            "Path to evaluation JSONL dataset. Defaults to "
            "evaluation/datasets/himikama_eval_set.jsonl"
        ),
    )

    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    dataset_path = Path(args.dataset)
    dataset = load_dataset(dataset_path)

    if args.limit is not None:
        dataset = dataset[: args.limit]

    print(f"Loaded {len(dataset)} evaluation case(s) from {dataset_path}")

    if args.variant == "all":
        selected = VARIANTS
    else:
        selected = {
            args.variant: VARIANTS[args.variant],
        }

    for variant_name, runner in selected.items():
        await run_variant(
            variant_name=variant_name,
            runner=runner,
            dataset=dataset,
        )

    print("\nAll requested variant runs completed.")


if __name__ == "__main__":
    asyncio.run(main())
