"""
himikama/backend/ingestion/run_ingestion.py
═══════════════════════════════════════════════════════════════
Module 6 of 6 — Ingestion Pipeline Orchestrator

Responsibility:
    Wire all five ingestion modules together and run the full
    pipeline in the correct order:

        1. parse_pdf()          — extract cases from PDF
        2. validate_cases()     — catch data quality issues
        3. embed_cases()        — load cases into ChromaDB
        4. embed_articles()     — load constitutional articles
        5. run_audit()          — verify corpus coverage
        6. run_retrieval_tests()— verify retrieval quality

    This is the ONLY file that imports from all other modules.
    It produces no side effects of its own — all writes go
    through embedder.py, all reads through retrieval_test.py.

Usage — CLI:
    python -m ingestion.run_ingestion --pdf data/Metadata_Final.pdf
    python -m ingestion.run_ingestion --pdf data/Metadata_Final.pdf --reset
    python -m ingestion.run_ingestion --pdf data/Metadata_Final.pdf --db db/ --skip-tests
    python -m ingestion.run_ingestion --pdf data/Metadata_Final.pdf --skip-audit --skip-tests

Usage — Programmatic:
    from ingestion.run_ingestion import run_pipeline, IngestionConfig

    config = IngestionConfig(pdf_path="data/Metadata_Final.pdf", db_path="db/")
    result = run_pipeline(config)

Exit Codes:
    0 — pipeline completed successfully (retrieval pass rate ≥ 80%)
    1 — pipeline aborted: no valid cases after validation
    2 — pipeline completed but retrieval quality is insufficient
        (pass rate < 80%) — safe to inspect, unsafe to build chain
═══════════════════════════════════════════════════════════════
"""

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

RETRIEVAL_PASS_THRESHOLD: float = 80.0  # minimum acceptable retrieval pass rate
EXPECTED_CASE_COUNT: int = 392          # sanity check — known corpus size


# ─────────────────────────────────────────────────────────────
# CONFIGURATION DATACLASS
# ─────────────────────────────────────────────────────────────

@dataclass
class IngestionConfig:
    """
    Configuration for a full ingestion pipeline run.

    Attributes:
        pdf_path:     Path to Metadata_Final.pdf.
        db_path:      Directory for ChromaDB persistent storage.
        reset:        If True, delete and recreate both collections
                      before loading. Use when re-ingesting from scratch.
        skip_audit:   If True, skip the corpus coverage audit (Step 5).
                      Useful for fast re-runs when audit results are known.
        skip_tests:   If True, skip retrieval quality tests (Step 6).
                      Useful for quick re-ingestion without full verification.
        log_level:    Python logging level string (e.g. "INFO", "DEBUG").
    """
    pdf_path:   str  = "data/Metadata_Final.pdf"
    db_path:    str  = "db/"
    reset:      bool = False
    skip_audit: bool = False
    skip_tests: bool = False
    log_level:  str  = "INFO"


# ─────────────────────────────────────────────────────────────
# PIPELINE RESULT DATACLASS
# ─────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """
    Structured result of a full pipeline run.

    Attributes:
        parsed_count:    Total case blocks found in the PDF.
        valid_count:     Cases that passed validation.
        invalid_count:   Cases excluded due to critical issues.
        case_col_size:   Final record count in case_summaries collection.
        article_col_size:Final record count in constitutional_articles collection.
        audit_result:    AuditResult from corpus coverage audit, or None.
        retrieval_result:RetrievalTestResult from retrieval tests, or None.
        elapsed_seconds: Total wall-clock time for the pipeline run.
        success:         True if pipeline completed without critical failures.
        exit_code:       Recommended process exit code (0, 1, or 2).
        warnings:        List of non-fatal warning messages collected.
    """
    parsed_count:     int   = 0
    valid_count:      int   = 0
    invalid_count:    int   = 0
    case_col_size:    int   = 0
    article_col_size: int   = 0
    audit_result:     object = None
    retrieval_result: object = None
    elapsed_seconds:  float = 0.0
    success:          bool  = False
    exit_code:        int   = 0
    warnings:         list  = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# STEP HELPERS
# ─────────────────────────────────────────────────────────────

def _banner(step: int, total: int, title: str) -> None:
    """Print a clearly delimited step banner to stdout."""
    print(f"\n{'═' * 66}")
    print(f"  STEP {step}/{total} — {title}")
    print(f"{'═' * 66}")


def _warn(result: PipelineResult, message: str) -> None:
    """Append a warning to PipelineResult and log it."""
    result.warnings.append(message)
    logger.warning(message)


# ─────────────────────────────────────────────────────────────
# INDIVIDUAL PIPELINE STEPS
# ─────────────────────────────────────────────────────────────

def _step_parse(config: IngestionConfig, result: PipelineResult) -> list[dict]:
    """
    Step 1 — Parse the PDF into structured case dicts.

    Args:
        config: Pipeline configuration.
        result: PipelineResult to update in place.

    Returns:
        List of parsed case dicts from parser.parse_pdf().

    Raises:
        SystemExit(1): If PDF is not found or parsing produces zero cases.
    """
    from ingestion.parser import parse_pdf

    _banner(1, 6, "PDF PARSING")
    cases = parse_pdf(config.pdf_path)

    result.parsed_count = len(cases)

    if not cases:
        print(
            "\n  ✗  ABORT: parse_pdf() returned zero cases.\n"
            "     Check that the PDF path is correct and the file\n"
            "     contains <<<CASE_START>>> / <<<CASE_END>>> delimiters."
        )
        result.exit_code = 1
        sys.exit(1)

    if result.parsed_count != EXPECTED_CASE_COUNT:
        _warn(
            result,
            f"Parsed {result.parsed_count} cases — expected {EXPECTED_CASE_COUNT}. "
            f"Check for missing or extra delimiters in the PDF."
        )

    return cases


def _step_validate(
    cases: list[dict],
    result: PipelineResult,
) -> list[dict]:
    """
    Step 2 — Validate all parsed cases before ingestion.

    Args:
        cases:  Parsed case dicts from Step 1.
        result: PipelineResult to update in place.

    Returns:
        List of valid case dicts safe for ChromaDB ingestion.

    Raises:
        SystemExit(1): If zero cases pass validation.
    """
    from ingestion.validator import validate_cases

    _banner(2, 6, "CASE VALIDATION")
    report = validate_cases(cases)

    result.valid_count   = len(report.valid_cases)
    result.invalid_count = len(report.invalid_cases)

    if result.valid_count == 0:
        print(
            "\n  ✗  ABORT: No cases passed validation.\n"
            "     Fix all CRITICAL issues reported above before re-running."
        )
        result.exit_code = 1
        sys.exit(1)

    if report.invalid_cases:
        _warn(
            result,
            f"{result.invalid_count} case(s) failed validation and will not be ingested. "
            f"Review CRITICAL issues in the validation report above."
        )

    return report.valid_cases


def _step_embed_cases(
    valid_cases: list[dict],
    config: IngestionConfig,
    result: PipelineResult,
) -> object:
    """
    Step 3 — Embed valid cases into the case_summaries ChromaDB collection.

    Args:
        valid_cases: Validated case dicts from Step 2.
        config:      Pipeline configuration.
        result:      PipelineResult to update in place.

    Returns:
        ChromaDB Collection object for case_summaries.
    """
    from ingestion.embedder import embed_cases

    _banner(3, 6, "EMBEDDING CASES → case_summaries")
    case_collection = embed_cases(
        valid_cases=valid_cases,
        db_path=config.db_path,
        reset=config.reset,
    )

    result.case_col_size = case_collection.count()

    if result.case_col_size == 0:
        _warn(result, "case_summaries collection is empty after embedding.")

    return case_collection


def _step_embed_articles(
    config: IngestionConfig,
    result: PipelineResult,
) -> object:
    """
    Step 4 — Embed constitutional articles into the constitutional_articles
    ChromaDB collection.

    Articles come from constitutional_articles.CHAPTER_3_ARTICLES —
    a curated list of Chapter III article dicts prepared manually.

    Args:
        config: Pipeline configuration.
        result: PipelineResult to update in place.

    Returns:
        ChromaDB Collection object for constitutional_articles.
    """
    from ingestion.embedder import embed_articles
    from ingestion.constitutional_articles import CHAPTER_3_ARTICLES

    _banner(4, 6, "EMBEDDING ARTICLES → constitutional_articles")
    article_collection = embed_articles(
        articles=CHAPTER_3_ARTICLES,
        db_path=config.db_path,
        reset=config.reset,
    )

    result.article_col_size = article_collection.count()

    if result.article_col_size == 0:
        _warn(result, "constitutional_articles collection is empty after embedding.")

    return article_collection


def _step_audit(
    case_collection: object,
    config: IngestionConfig,
    result: PipelineResult,
) -> None:
    """
    Step 5 — Run the corpus coverage audit on the case_summaries collection.

    Identifies articles and topics with low case coverage before the
    retrieval chain is built on top of the database.

    Skipped if config.skip_audit is True.

    Args:
        case_collection: ChromaDB Collection for case_summaries.
        config:          Pipeline configuration.
        result:          PipelineResult to update in place.
    """
    _banner(5, 6, "CORPUS COVERAGE AUDIT")

    if config.skip_audit:
        print("  Skipped (--skip-audit flag set).")
        return

    from ingestion.audit import run_audit

    audit_result = run_audit(case_collection)
    result.audit_result = audit_result

    if audit_result.low_coverage_articles:
        _warn(
            result,
            f"Low coverage articles (< 10 cases): "
            f"{', '.join(sorted(audit_result.low_coverage_articles))}. "
            f"Retrieval will be weak for these in Step 7."
        )

    if audit_result.low_coverage_topics:
        _warn(
            result,
            f"Low coverage topics (< 5 cases): "
            f"{', '.join(sorted(audit_result.low_coverage_topics))}."
        )


def _step_retrieval_tests(
    case_collection: object,
    article_collection: object,
    config: IngestionConfig,
    result: PipelineResult,
) -> None:
    """
    Step 6 — Run retrieval quality tests against both collections.

    Tests semantic search accuracy for both case_summaries and
    constitutional_articles. A pass rate below 80% indicates the
    embedding quality is insufficient for the LLM chain.

    Skipped if config.skip_tests is True.

    Args:
        case_collection:    ChromaDB Collection for case_summaries.
        article_collection: ChromaDB Collection for constitutional_articles.
        config:             Pipeline configuration.
        result:             PipelineResult to update in place.
    """
    _banner(6, 6, "RETRIEVAL QUALITY TESTS")

    if config.skip_tests:
        print("  Skipped (--skip-tests flag set).")
        return

    from ingestion.retrieval_test import run_retrieval_tests

    retrieval_result = run_retrieval_tests(case_collection, article_collection)
    result.retrieval_result = retrieval_result

    total = retrieval_result.total
    pass_rate = (retrieval_result.passed / total * 100) if total else 0.0

    if pass_rate < RETRIEVAL_PASS_THRESHOLD:
        result.exit_code = 2
        _warn(
            result,
            f"Retrieval pass rate {pass_rate:.1f}% is below the {RETRIEVAL_PASS_THRESHOLD}% "
            f"threshold. Do NOT build the LLM chain until this is resolved."
        )


# ─────────────────────────────────────────────────────────────
# FINAL SUMMARY PRINTER
# ─────────────────────────────────────────────────────────────

def _print_pipeline_summary(result: PipelineResult, config: IngestionConfig) -> None:
    """
    Print the final pipeline summary after all steps complete.

    Args:
        result: Completed PipelineResult.
        config: Pipeline configuration used for this run.
    """
    W = 66

    print(f"\n{'═' * W}")
    print("  INGESTION PIPELINE — FINAL SUMMARY")
    print(f"{'═' * W}")
    print(f"  PDF parsed:              {result.parsed_count} case blocks")
    print(f"  Valid cases:             {result.valid_count}")
    print(f"  Invalid / excluded:      {result.invalid_count}")
    print(f"  case_summaries size:     {result.case_col_size} records")
    print(f"  constitutional_articles: {result.article_col_size} records")
    print(f"  DB path:                 {config.db_path}")
    print(f"  Reset collections:       {'Yes' if config.reset else 'No'}")
    print(f"  Elapsed:                 {result.elapsed_seconds:.1f}s")

    # Retrieval summary
    if result.retrieval_result is not None:
        rt = result.retrieval_result
        total = rt.total
        pass_rate = (rt.passed / total * 100) if total else 0.0
        print(
            f"\n  Retrieval tests:         "
            f"{rt.passed}/{total} passed ({pass_rate:.1f}%)"
        )
    elif config.skip_tests:
        print("\n  Retrieval tests:         Skipped")

    # Warnings
    if result.warnings:
        print(f"\n  ⚠  Warnings ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"     • {w}")

    # Final status line
    print()
    if result.exit_code == 0:
        print("  ✓  Pipeline complete. Safe to build the LLM chain.")
    elif result.exit_code == 2:
        print(
            "  ✗  Pipeline complete but retrieval quality is insufficient.\n"
            "     Resolve retrieval test failures before building the LLM chain."
        )
    # exit_code == 1 never reaches here (sys.exit called in step)

    print(f"{'═' * W}\n")


# ─────────────────────────────────────────────────────────────
# MAIN PUBLIC FUNCTION
# ─────────────────────────────────────────────────────────────

def run_pipeline(config: IngestionConfig) -> PipelineResult:
    """
    Run the full Himikama ingestion pipeline.

    Executes all six steps in order:
        1. Parse PDF
        2. Validate cases
        3. Embed cases → case_summaries
        4. Embed articles → constitutional_articles
        5. Corpus coverage audit
        6. Retrieval quality tests

    Steps 5 and 6 can be skipped via config flags for faster re-runs.
    The function never modifies ChromaDB in steps 5 or 6.

    Args:
        config: IngestionConfig with all pipeline settings.

    Returns:
        PipelineResult with counts, results, timing, and exit code.

    Raises:
        SystemExit(1): If no cases are found or all cases fail validation.
                       (exits before returning)
    """
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    result = PipelineResult()
    start_time = time.monotonic()

    print(f"\n{'═' * 66}")
    print("  HIMIKAMA — FUNDAMENTAL RIGHTS INGESTION PIPELINE")
    print(f"{'═' * 66}")
    print(f"  PDF:    {config.pdf_path}")
    print(f"  DB:     {config.db_path}")
    print(f"  Reset:  {'Yes — collections will be deleted and recreated' if config.reset else 'No'}")

    # ── Step 1: Parse ─────────────────────────────────────────
    cases = _step_parse(config, result)

    # ── Step 2: Validate ──────────────────────────────────────
    valid_cases = _step_validate(cases, result)

    # ── Step 3: Embed cases ───────────────────────────────────
    case_collection = _step_embed_cases(valid_cases, config, result)

    # ── Step 4: Embed articles ────────────────────────────────
    article_collection = _step_embed_articles(config, result)

    # ── Step 5: Audit ─────────────────────────────────────────
    _step_audit(case_collection, config, result)

    # ── Step 6: Retrieval tests ───────────────────────────────
    _step_retrieval_tests(case_collection, article_collection, config, result)

    # ── Finalise ──────────────────────────────────────────────
    result.elapsed_seconds = time.monotonic() - start_time
    result.success = result.exit_code in (0,)

    _print_pipeline_summary(result, config)

    return result


# ─────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    """
    Build and return the argument parser for the CLI entry point.

    Returns:
        Configured argparse.ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="run_ingestion",
        description=(
            "Himikama — Fundamental Rights Ingestion Pipeline\n"
            "Parses, validates, embeds, audits, and tests the Sri Lankan\n"
            "FR case corpus and constitutional articles in ChromaDB."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m ingestion.run_ingestion --pdf data/Metadata_Final.pdf\n"
            "  python -m ingestion.run_ingestion --pdf data/Metadata_Final.pdf --reset\n"
            "  python -m ingestion.run_ingestion --pdf data/Metadata_Final.pdf --skip-tests\n"
            "  python -m ingestion.run_ingestion --pdf data/Metadata_Final.pdf --skip-audit --skip-tests\n"
        ),
    )

    parser.add_argument(
        "--pdf",
        type=str,
        default="data/Metadata_Final.pdf",
        metavar="PATH",
        help=(
            "Path to the Metadata_Final.pdf case summary document. "
            "(default: data/Metadata_Final.pdf)"
        ),
    )

    parser.add_argument(
        "--db",
        type=str,
        default="db/",
        metavar="PATH",
        help=(
            "Directory for ChromaDB persistent storage. "
            "Created automatically if it does not exist. "
            "(default: db/)"
        ),
    )

    parser.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help=(
            "Delete and recreate both ChromaDB collections before loading. "
            "Use when re-ingesting from scratch. "
            "WARNING: this permanently deletes all existing embeddings."
        ),
    )

    parser.add_argument(
        "--skip-audit",
        action="store_true",
        default=False,
        help=(
            "Skip the corpus coverage audit (Step 5). "
            "Useful for fast re-runs when audit results are already known."
        ),
    )

    parser.add_argument(
        "--skip-tests",
        action="store_true",
        default=False,
        help=(
            "Skip retrieval quality tests (Step 6). "
            "Useful for quick re-ingestion without full verification. "
            "The LLM chain should not be deployed without tests passing."
        ),
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        metavar="LEVEL",
        help=(
            "Python logging level. "
            "Use DEBUG for verbose output during development. "
            "(default: INFO)"
        ),
    )

    return parser


def main() -> None:
    """
    CLI entry point for run_ingestion.

    Parses command-line arguments, builds an IngestionConfig,
    runs the pipeline, and exits with the appropriate exit code.

    Exit codes:
        0 — success
        1 — abort: zero valid cases (see step output for details)
        2 — complete but retrieval pass rate below 80%
    """
    parser = _build_arg_parser()
    args = parser.parse_args()

    config = IngestionConfig(
        pdf_path=args.pdf,
        db_path=args.db,
        reset=args.reset,
        skip_audit=args.skip_audit,
        skip_tests=args.skip_tests,
        log_level=args.log_level,
    )

    result = run_pipeline(config)
    sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
