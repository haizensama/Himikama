"""
himikama/backend/ingestion/validator.py
═══════════════════════════════════════════════════════════════
Module 2 — Case Validator

Responsibility:
    Validate every parsed case before it is loaded into ChromaDB.
    Catches data quality issues early so nothing corrupted enters
    the vector database.

    This module produces NO side effects. It only inspects data
    and returns validation results. ChromaDB is never touched here.

Input:
    List of parsed case dicts from parser.py

Output:
    ValidationReport dataclass containing:
        - valid_cases:   list of cases that passed all checks
        - invalid_cases: list of cases that failed with reasons
        - warnings:      list of non-critical issues to review
        - summary:       printed report of all findings

Validation Checks:
    CRITICAL (case excluded from ingestion if failed):
        - summary is present and non-empty
        - metadata JSON parsed successfully
        - case_id is a positive integer
        - judgment is exactly VIOLATED or NOT_VIOLATED
        - articles_cited is a non-empty list
        - year is a plausible integer (1970–2030 or 0)

    WARNINGS (case still ingested, flagged for review):
        - word_count outside expected range (150–300)
        - case_number is NONE (older cases without SC FR number)
        - legal_topic list is empty
        - articles contain unrecognised formats
        - year is 0 (unknown)
        - summary appears too short (under 100 words)

Usage:
    from ingestion.parser import parse_pdf
    from ingestion.validator import validate_cases

    cases = parse_pdf("data/Metadata_Final.pdf")
    report = validate_cases(cases)

    # Only load validated cases into ChromaDB
    valid_cases = report.valid_cases
═══════════════════════════════════════════════════════════════
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

VALID_JUDGMENTS = {"VIOLATED", "NOT_VIOLATED"}

# All approved legal topic strings
VALID_TOPICS = {
    "freedom_of_thought", "freedom_of_conscience", "freedom_of_religion",
    "torture", "cruel_treatment", "inhuman_treatment", "degrading_treatment",
    "equality", "equal_protection", "discrimination",
    "discrimination_employment", "discrimination_education",
    "arrest", "unlawful_arrest", "detention", "unlawful_detention",
    "custody", "personal_liberty", "fair_trial",
    "presumption_of_innocence", "punishment",
    "freedom_of_speech", "freedom_of_expression",
    "freedom_of_assembly", "freedom_of_association",
    "trade_union", "religious_practice", "cultural_rights",
    "language_rights", "occupation", "freedom_of_movement",
    "residence", "national_security", "public_order", "public_health",
    "arbitrary_state_interference",
}

# Recognised article formats for the Sri Lankan Constitution Chapter 3
# Used to warn about potentially malformed article strings
ARTICLE_PATTERN = re.compile(
    r"^\d+(\([0-9]+\))?(\([a-zA-Z]\))?$"
)

YEAR_MIN = 1970
YEAR_MAX = 2030
WORD_COUNT_MIN = 150
WORD_COUNT_MAX = 300
SUMMARY_MIN_WORDS = 100


# ─────────────────────────────────────────────────────────────
# VALIDATION RESULT TYPES
# ─────────────────────────────────────────────────────────────

@dataclass
class CaseIssue:
    """A single validation issue for one case."""
    case_id:   int | str
    position:  int
    severity:  str          # "CRITICAL" or "WARNING"
    field:     str          # which field caused the issue
    message:   str          # human-readable description


@dataclass
class ValidationReport:
    """
    Full validation report for the entire parsed corpus.

    Attributes:
        valid_cases:    Cases that passed all critical checks.
                        Safe to load into ChromaDB.
        invalid_cases:  Cases that failed one or more critical checks.
                        Excluded from ingestion until fixed.
        all_issues:     All issues found (critical + warnings).
        total_input:    Total number of cases that were checked.
    """
    valid_cases:   list[dict] = field(default_factory=list)
    invalid_cases: list[dict] = field(default_factory=list)
    all_issues:    list[CaseIssue] = field(default_factory=list)
    total_input:   int = 0

    @property
    def critical_issues(self) -> list[CaseIssue]:
        return [i for i in self.all_issues if i.severity == "CRITICAL"]

    @property
    def warnings(self) -> list[CaseIssue]:
        return [i for i in self.all_issues if i.severity == "WARNING"]


# ─────────────────────────────────────────────────────────────
# INDIVIDUAL FIELD VALIDATORS
# ─────────────────────────────────────────────────────────────

def _check_summary(case: dict, issues: list[CaseIssue]) -> bool:
    """
    CRITICAL: Summary must be present and non-empty.
    WARNING:  Summary should be at least 100 words.

    Returns:
        True if summary passes critical check, False otherwise.
    """
    summary = case.get("summary", "")
    position = case.get("position", 0)
    meta = case.get("metadata", {})
    case_id = meta.get("case_id", "unknown")

    if not summary or not summary.strip():
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="CRITICAL", field="summary",
            message="Summary is missing or empty"
        ))
        return False

    word_count = len(summary.split())
    if word_count < SUMMARY_MIN_WORDS:
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="WARNING", field="summary",
            message=f"Summary is short — {word_count} words "
                    f"(expected at least {SUMMARY_MIN_WORDS})"
        ))

    return True


def _check_case_id(meta: dict, position: int,
                   issues: list[CaseIssue]) -> bool:
    """
    CRITICAL: case_id must be a positive integer.

    Returns:
        True if case_id passes, False otherwise.
    """
    case_id = meta.get("case_id")

    if not isinstance(case_id, int) or case_id <= 0:
        issues.append(CaseIssue(
            case_id=case_id or "unknown", position=position,
            severity="CRITICAL", field="case_id",
            message=f"case_id must be a positive integer, got: "
                    f"{repr(case_id)}"
        ))
        return False

    return True


def _check_judgment(meta: dict, position: int,
                    issues: list[CaseIssue]) -> bool:
    """
    CRITICAL: judgment must be exactly VIOLATED or NOT_VIOLATED.

    Returns:
        True if judgment passes, False otherwise.
    """
    case_id  = meta.get("case_id", "unknown")
    judgment = meta.get("judgment", "")

    if judgment not in VALID_JUDGMENTS:
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="CRITICAL", field="judgment",
            message=f"Invalid judgment value: '{judgment}'. "
                    f"Must be one of: {VALID_JUDGMENTS}"
        ))
        return False

    return True


def _check_articles(meta: dict, position: int,
                    issues: list[CaseIssue]) -> bool:
    """
    CRITICAL: articles_cited must be a non-empty list.
    WARNING:  Article strings should match expected format.

    Returns:
        True if articles pass critical check, False otherwise.
    """
    case_id  = meta.get("case_id", "unknown")
    articles = meta.get("articles_cited", [])

    if not isinstance(articles, list) or len(articles) == 0:
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="CRITICAL", field="articles_cited",
            message="articles_cited must be a non-empty list"
        ))
        return False

    # Warn about any articles not matching expected pattern
    for article in articles:
        if not isinstance(article, str):
            issues.append(CaseIssue(
                case_id=case_id, position=position,
                severity="WARNING", field="articles_cited",
                message=f"Non-string article entry: {repr(article)}"
            ))
        elif not ARTICLE_PATTERN.match(article.strip()):
            issues.append(CaseIssue(
                case_id=case_id, position=position,
                severity="WARNING", field="articles_cited",
                message=f"Unusual article format: '{article}' — "
                        f"verify this is correct"
            ))

    return True


def _check_year(meta: dict, position: int,
                issues: list[CaseIssue]) -> bool:
    """
    CRITICAL: year must be an integer.
    WARNING:  year of 0 means unknown. year outside 1970-2030
              may indicate extraction error.

    Returns:
        True always (year=0 is acceptable, not critical).
    """
    case_id = meta.get("case_id", "unknown")
    year    = meta.get("year", 0)

    if not isinstance(year, int):
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="CRITICAL", field="year",
            message=f"year must be an integer, got: {repr(year)}"
        ))
        return False

    if year == 0:
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="WARNING", field="year",
            message="year is 0 — could not be determined from source"
        ))
    elif not (YEAR_MIN <= year <= YEAR_MAX):
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="WARNING", field="year",
            message=f"year {year} is outside expected range "
                    f"({YEAR_MIN}–{YEAR_MAX})"
        ))

    return True


def _check_word_count(meta: dict, position: int,
                      issues: list[CaseIssue]) -> None:
    """
    WARNING: word_count outside 150–300 is unusual for this corpus.
    Not a critical failure.
    """
    case_id    = meta.get("case_id", "unknown")
    word_count = meta.get("word_count", 0)

    if not isinstance(word_count, int) or word_count == 0:
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="WARNING", field="word_count",
            message="word_count is 0 or missing"
        ))
    elif not (WORD_COUNT_MIN <= word_count <= WORD_COUNT_MAX):
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="WARNING", field="word_count",
            message=f"word_count {word_count} outside expected range "
                    f"({WORD_COUNT_MIN}–{WORD_COUNT_MAX})"
        ))


def _check_legal_topics(meta: dict, position: int,
                         issues: list[CaseIssue]) -> None:
    """
    WARNING: Empty legal_topic list is unusual.
    WARNING: Any topic not in the approved list is flagged.
    Not a critical failure — topics are used for filtering,
    not for core retrieval.
    """
    case_id = meta.get("case_id", "unknown")
    topics  = meta.get("legal_topic", [])

    if not isinstance(topics, list) or len(topics) == 0:
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="WARNING", field="legal_topic",
            message="legal_topic list is empty — "
                    "topic filtering will not work for this case"
        ))
        return

    for topic in topics:
        if topic not in VALID_TOPICS:
            issues.append(CaseIssue(
                case_id=case_id, position=position,
                severity="WARNING", field="legal_topic",
                message=f"Unrecognised topic: '{topic}' — "
                        f"not in approved list"
            ))


def _check_case_number(meta: dict, position: int,
                        issues: list[CaseIssue]) -> None:
    """
    WARNING: NONE case number means no official SC FR reference.
    Common for older cases. Not a critical failure.
    """
    case_id     = meta.get("case_id", "unknown")
    case_number = meta.get("case_number", "")

    if not case_number or case_number.upper() == "NONE":
        issues.append(CaseIssue(
            case_id=case_id, position=position,
            severity="WARNING", field="case_number",
            message="No SC FR case number — older case without "
                    "official reference"
        ))


# ─────────────────────────────────────────────────────────────
# DUPLICATE DETECTION
# ─────────────────────────────────────────────────────────────

def _check_duplicates(cases: list[dict]) -> list[CaseIssue]:
    """
    Check for duplicate case_ids across the full corpus.
    Duplicates would cause ChromaDB upsert collisions.

    Returns:
        List of CaseIssue for any duplicates found.
    """
    seen     = {}
    issues   = []

    for case in cases:
        meta     = case.get("metadata", {})
        case_id  = meta.get("case_id")
        position = case.get("position", 0)

        if case_id in seen:
            issues.append(CaseIssue(
                case_id=case_id, position=position,
                severity="CRITICAL", field="case_id",
                message=f"Duplicate case_id {case_id} — "
                        f"also found at position {seen[case_id]}"
            ))
        else:
            seen[case_id] = position

    return issues


# ─────────────────────────────────────────────────────────────
# MAIN PUBLIC FUNCTION
# ─────────────────────────────────────────────────────────────

def validate_cases(cases: list[dict]) -> ValidationReport:
    """
    Validate all parsed cases before ChromaDB ingestion.

    Runs all critical and warning checks on every case.
    Cases that fail any critical check are excluded from
    valid_cases and must be fixed before ingestion.

    Args:
        cases: List of parsed case dicts from parser.parse_pdf()

    Returns:
        ValidationReport containing valid_cases, invalid_cases,
        all_issues, and a printed summary report.
    """
    report = ValidationReport(total_input=len(cases))
    all_issues: list[CaseIssue] = []

    # Check for duplicates across the full corpus first
    duplicate_issues = _check_duplicates(cases)
    all_issues.extend(duplicate_issues)

    # Track which case_ids have critical issues
    critical_case_positions = set(
        issue.position for issue in duplicate_issues
        if issue.severity == "CRITICAL"
    )

    # Validate each case individually
    for case in cases:
        meta     = case.get("metadata", {})
        position = case.get("position", 0)
        case_issues: list[CaseIssue] = []

        # ── Critical checks ──────────────────────────────────
        summary_ok  = _check_summary(case, case_issues)
        case_id_ok  = _check_case_id(meta, position, case_issues)
        judgment_ok = _check_judgment(meta, position, case_issues)
        articles_ok = _check_articles(meta, position, case_issues)
        year_ok     = _check_year(meta, position, case_issues)

        # ── Warning checks (always run) ──────────────────────
        _check_word_count(meta, position, case_issues)
        _check_legal_topics(meta, position, case_issues)
        _check_case_number(meta, position, case_issues)

        all_issues.extend(case_issues)

        # Determine if case passes all critical checks
        has_critical = (
            not summary_ok
            or not case_id_ok
            or not judgment_ok
            or not articles_ok
            or not year_ok
            or position in critical_case_positions
        )

        if has_critical:
            report.invalid_cases.append(case)
        else:
            report.valid_cases.append(case)

    report.all_issues = all_issues

    # Print the validation report
    _print_validation_report(report)

    return report


# ─────────────────────────────────────────────────────────────
# INTERNAL — VALIDATION REPORT PRINTER
# ─────────────────────────────────────────────────────────────

def _print_validation_report(report: ValidationReport) -> None:
    """Print a human-readable validation report."""

    criticals = report.critical_issues
    warnings  = report.warnings

    print("\n" + "=" * 55)
    print("VALIDATION REPORT")
    print("=" * 55)
    print(f"  Total cases checked:      {report.total_input}")
    print(f"  Passed (ready to load):   {len(report.valid_cases)}")
    print(f"  Failed (excluded):        {len(report.invalid_cases)}")
    print(f"  Critical issues:          {len(criticals)}")
    print(f"  Warnings:                 {len(warnings)}")

    if criticals:
        print(f"\n  ── CRITICAL ISSUES (must fix before ingestion) ──")
        for issue in criticals:
            print(f"    [pos {issue.position:>3}] "
                  f"case_id={issue.case_id} | "
                  f"{issue.field}: {issue.message}")

    if warnings:
        print(f"\n  ── WARNINGS (case still loaded, review advised) ──")
        for issue in warnings:
            print(f"    [pos {issue.position:>3}] "
                  f"case_id={issue.case_id} | "
                  f"{issue.field}: {issue.message}")

    if not criticals and not warnings:
        print("\n  All cases passed with no issues.")

    print("=" * 55 + "\n")
