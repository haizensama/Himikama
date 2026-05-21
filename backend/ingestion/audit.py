"""
himikama/backend/ingestion/audit.py
═══════════════════════════════════════════════════════════════
Module 4 — Coverage Auditor

Responsibility:
    After cases are loaded into ChromaDB, run a comprehensive
    coverage audit to identify weak areas in the corpus before
    the retrieval chain is built on top of it.

    Weak coverage = retrieval bias = unreliable Step 7 output.
    This module surfaces that risk explicitly before the system
    goes into use.

    This module only READS from ChromaDB. It never writes,
    modifies, or deletes any data.

    Also provides run_audit_from_cases() which runs the same
    audit directly from parsed case dicts — useful for testing
    without a running ChromaDB instance.

Audits Performed:
    1. Article Coverage
       Cases per article. Flags articles below threshold.
       Low coverage = weak retrieval = fewer similar cases.

    2. Judgment Distribution
       VIOLATED vs NOT_VIOLATED ratio across the corpus.

    3. Legal Topic Coverage
       Cases per topic tag. Flags sparse topics.

    4. Year Distribution
       Cases grouped by decade.

    5. Article-Judgment Cross Analysis
       Per article: how often VIOLATED vs NOT_VIOLATED.
       Reveals the precedent landscape for Step 10 synthesis.

Input:
    ChromaDB collection (via run_audit)
    OR validated case list (via run_audit_from_cases)

Output:
    AuditResult dataclass with all counts.
    Printed human-readable report.

Usage:
    from ingestion.embedder import get_case_collection
    from ingestion.audit import run_audit

    collection = get_case_collection(db_path="db/")
    result = run_audit(collection)
═══════════════════════════════════════════════════════════════
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

LOW_ARTICLE_THRESHOLD = 10   # articles below this are flagged
LOW_TOPIC_THRESHOLD   = 5    # topics below this are flagged


# ─────────────────────────────────────────────────────────────
# AUDIT RESULT
# ─────────────────────────────────────────────────────────────

@dataclass
class AuditResult:
    """
    Structured result of a full corpus coverage audit.

    Attributes:
        total_cases:             Total records audited.
        article_counts:          article_number → case count.
        judgment_counts:         judgment value → case count.
        topic_counts:            legal_topic → case count.
        decade_counts:           decade string → case count.
        article_judgment:        article → {judgment → count}.
        low_coverage_articles:   Articles below LOW_ARTICLE_THRESHOLD.
        low_coverage_topics:     Topics below LOW_TOPIC_THRESHOLD.
    """
    total_cases:           int  = 0
    article_counts:        dict = field(default_factory=dict)
    judgment_counts:       dict = field(default_factory=dict)
    topic_counts:          dict = field(default_factory=dict)
    decade_counts:         dict = field(default_factory=dict)
    article_judgment:      dict = field(default_factory=dict)
    low_coverage_articles: list = field(default_factory=list)
    low_coverage_topics:   list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# DATA FETCHING FROM CHROMADB
# ─────────────────────────────────────────────────────────────

def _fetch_all_metadata(collection) -> list[dict]:
    """
    Fetch all metadata records from a ChromaDB collection.

    Args:
        collection: ChromaDB Collection object.

    Returns:
        List of metadata dicts, one per record.
    """
    total = collection.count()
    if total == 0:
        logger.warning("Collection is empty — nothing to audit.")
        return []

    logger.info(f"Fetching metadata for {total} records...")
    result = collection.get(
        limit=total,
        include=["metadatas"],
    )
    return result.get("metadatas", [])


# ─────────────────────────────────────────────────────────────
# INDIVIDUAL AUDIT FUNCTIONS
# All accept a list of metadata dicts (str/int values only,
# as stored in ChromaDB — lists are comma-separated strings)
# ─────────────────────────────────────────────────────────────

def _audit_articles(metadatas: list[dict]) -> dict[str, int]:
    """
    Count cases per article from articles_cited field.
    articles_cited stored as comma-separated string in ChromaDB.
    """
    counts: dict[str, int] = defaultdict(int)
    for meta in metadatas:
        articles_str = meta.get("articles_cited", "")
        if not articles_str:
            continue
        for article in articles_str.split(","):
            article = article.strip()
            if article:
                counts[article] += 1
    return dict(counts)


def _audit_judgments(metadatas: list[dict]) -> dict[str, int]:
    """Count cases per judgment outcome."""
    counts: dict[str, int] = defaultdict(int)
    for meta in metadatas:
        judgment = meta.get("judgment", "UNKNOWN")
        counts[judgment] += 1
    return dict(counts)


def _audit_topics(metadatas: list[dict]) -> dict[str, int]:
    """
    Count cases per legal topic from legal_topic field.
    legal_topic stored as comma-separated string in ChromaDB.
    """
    counts: dict[str, int] = defaultdict(int)
    for meta in metadatas:
        topics_str = meta.get("legal_topic", "")
        if not topics_str:
            continue
        for topic in topics_str.split(","):
            topic = topic.strip()
            if topic:
                counts[topic] += 1
    return dict(counts)


def _audit_years(metadatas: list[dict]) -> dict[str, int]:
    """
    Group cases by decade.
    year=0 means unknown — grouped as 'Unknown'.
    """
    counts: dict[str, int] = defaultdict(int)
    for meta in metadatas:
        year = meta.get("year", 0)
        if not year or int(year) == 0:
            counts["Unknown"] += 1
        else:
            decade = f"{(int(year) // 10) * 10}s"
            counts[decade] += 1
    return dict(counts)


def _audit_article_judgment(
    metadatas: list[dict],
) -> dict[str, dict[str, int]]:
    """
    Cross-analysis: for each article, count VIOLATED vs
    NOT_VIOLATED outcomes.

    Reveals precedent landscape per article — useful for
    calibrating Step 10 synthesis confidence.

    Returns:
        {
          "12(1)": {"VIOLATED": 145, "NOT_VIOLATED": 146},
          "13(1)": {"VIOLATED": 72,  "NOT_VIOLATED": 30},
          ...
        }
    """
    cross: dict = defaultdict(lambda: defaultdict(int))
    for meta in metadatas:
        articles_str = meta.get("articles_cited", "")
        judgment     = meta.get("judgment", "UNKNOWN")
        if not articles_str:
            continue
        for article in articles_str.split(","):
            article = article.strip()
            if article:
                cross[article][judgment] += 1
    return {a: dict(j) for a, j in cross.items()}


# ─────────────────────────────────────────────────────────────
# CORE AUDIT RUNNER
# ─────────────────────────────────────────────────────────────

def _run_audit_on_metadatas(metadatas: list[dict]) -> AuditResult:
    """
    Run all audits on a list of metadata dicts.
    Used internally by both run_audit() and
    run_audit_from_cases().

    Args:
        metadatas: List of metadata dicts.
                   Keys and values must match ChromaDB format
                   (str/int only, lists as comma-separated str).

    Returns:
        Populated AuditResult.
    """
    result = AuditResult(total_cases=len(metadatas))

    result.article_counts  = _audit_articles(metadatas)
    result.judgment_counts = _audit_judgments(metadatas)
    result.topic_counts    = _audit_topics(metadatas)
    result.decade_counts   = _audit_years(metadatas)
    result.article_judgment = _audit_article_judgment(metadatas)

    result.low_coverage_articles = [
        a for a, c in result.article_counts.items()
        if c < LOW_ARTICLE_THRESHOLD
    ]
    result.low_coverage_topics = [
        t for t, c in result.topic_counts.items()
        if c < LOW_TOPIC_THRESHOLD
    ]

    return result


# ─────────────────────────────────────────────────────────────
# PUBLIC FUNCTIONS
# ─────────────────────────────────────────────────────────────

def run_audit(collection) -> AuditResult:
    """
    Run full coverage audit on a loaded ChromaDB collection.

    Args:
        collection: ChromaDB Collection object (case_summaries).

    Returns:
        AuditResult dataclass with all counts.
        Prints a full human-readable report.
    """
    metadatas = _fetch_all_metadata(collection)
    if not metadatas:
        print("Collection is empty. Run ingestion first.")
        return AuditResult()

    result = _run_audit_on_metadatas(metadatas)
    _print_audit_report(result)
    return result


def run_audit_from_cases(valid_cases: list[dict]) -> AuditResult:
    """
    Run the same audit directly from validated case dicts.
    Used for testing without a running ChromaDB instance.

    Args:
        valid_cases: List of validated case dicts from validator.py
                     Each must contain a 'chroma_metadata' key.

    Returns:
        AuditResult dataclass with all counts.
        Prints a full human-readable report.
    """
    metadatas = [
        c["chroma_metadata"]
        for c in valid_cases
        if "chroma_metadata" in c
    ]

    if not metadatas:
        print("No chroma_metadata found in cases.")
        return AuditResult()

    result = _run_audit_on_metadatas(metadatas)
    _print_audit_report(result)
    return result


# ─────────────────────────────────────────────────────────────
# REPORT PRINTER
# ─────────────────────────────────────────────────────────────

def _bar(count: int, divisor: int = 5, width: int = 20) -> str:
    """Generate a simple ASCII bar for counts."""
    filled = min(count // divisor, width)
    return "█" * filled


def _print_audit_report(result: AuditResult) -> None:
    """Print a full human-readable audit report."""

    W = 58  # report width

    def section(title: str) -> None:
        print(f"\n{'=' * W}")
        print(f"  {title}")
        print(f"{'=' * W}")

    section("CORPUS COVERAGE AUDIT")
    print(f"  Total cases in corpus: {result.total_cases}")

    # ── 1. Article Coverage ───────────────────────────────────
    section("1. ARTICLE COVERAGE")
    print(f"  {'Article':<14} {'Cases':>6}  {'Bar':<22} Status")
    print(f"  {'-' * 52}")

    for article, count in sorted(
        result.article_counts.items(), key=lambda x: -x[1]
    ):
        bar    = _bar(count)
        status = "⚠  LOW" if count < LOW_ARTICLE_THRESHOLD else "✓"
        print(f"  {article:<14} {count:>6}  {bar:<22} {status}")

    if result.low_coverage_articles:
        low = ", ".join(sorted(result.low_coverage_articles))
        print(
            f"\n  ⚠  Low coverage (< {LOW_ARTICLE_THRESHOLD} cases):\n"
            f"     {low}\n"
            f"\n  Step 7 retrieval will be weak for these articles.\n"
            f"  Flag in the confidence layer when these are identified\n"
            f"  in Step 4 so users are informed of limited precedent."
        )

    # ── 2. Judgment Distribution ──────────────────────────────
    section("2. JUDGMENT DISTRIBUTION")
    total = result.total_cases
    for judgment, count in sorted(result.judgment_counts.items()):
        pct = (count / total * 100) if total else 0
        bar = "█" * int(pct / 2)
        print(f"  {judgment:<20} {count:>5}  ({pct:.1f}%)  {bar}")

    # ── 3. Legal Topic Coverage ───────────────────────────────
    section("3. LEGAL TOPIC COVERAGE")
    print(f"  {'Topic':<40} {'Cases':>6}  Status")
    print(f"  {'-' * 52}")

    for topic, count in sorted(
        result.topic_counts.items(), key=lambda x: -x[1]
    ):
        status = "⚠  LOW" if count < LOW_TOPIC_THRESHOLD else ""
        print(f"  {topic:<40} {count:>6}  {status}")

    if result.low_coverage_topics:
        low = ", ".join(sorted(result.low_coverage_topics))
        print(
            f"\n  ⚠  Low coverage topics (< {LOW_TOPIC_THRESHOLD} "
            f"cases):\n     {low}"
        )

    # ── 4. Year / Decade Distribution ─────────────────────────
    section("4. CASE LAW BY DECADE")
    for decade, count in sorted(result.decade_counts.items()):
        bar = _bar(count, divisor=3, width=25)
        print(f"  {decade:<12} {count:>5}  {bar}")

    # ── 5. Article-Judgment Cross Analysis ────────────────────
    section("5. ARTICLE-JUDGMENT CROSS ANALYSIS")
    print(
        f"  {'Article':<14} {'VIOLATED':>10} "
        f"{'NOT_VIOLATED':>14}  {'Win Rate':>10}"
    )
    print(f"  {'-' * 52}")

    for article, judgments in sorted(
        result.article_judgment.items(),
        key=lambda x: -sum(x[1].values()),
    ):
        v   = judgments.get("VIOLATED", 0)
        nv  = judgments.get("NOT_VIOLATED", 0)
        tot = v + nv
        wr  = f"{v / tot * 100:.0f}%" if tot else "N/A"
        print(
            f"  {article:<14} {v:>10} {nv:>14}  {wr:>10}"
        )

    print(f"\n{'=' * W}")
    print("  Audit complete.")
    print(f"{'=' * W}\n")
