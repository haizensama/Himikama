"""
retrieval_test.py — Module 5 of 6 (Himikama Ingestion Pipeline)
================================================================
READ-ONLY verification of ChromaDB retrieval quality after ingestion.

Tests two collections:
  - case_summaries          (semantic + optional metadata filter)
  - constitutional_articles (semantic only)

Never writes to ChromaDB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

CASE_TOP_N: int = 10       # results fetched per case query
ARTICLE_TOP_N: int = 5     # results fetched per article query
CASE_PASS_RANK: int = 5    # expected article must appear within top-5 results
ARTICLE_PASS_RANK: int = 3 # expected article must appear within top-3 results


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    """Result of a single retrieval test case."""

    description: str
    """Human-readable label for the test scenario."""

    query: str
    """Natural-language query sent to ChromaDB."""

    passed: bool
    """True if the expected article was found within the pass-rank threshold."""

    top_results: list[dict[str, Any]]
    """
    List of dicts for the top fetched results.
    Each dict has keys: rank (int), score (float), metadata (dict).
    """

    expected: list[str]
    """Article number(s) that should appear in the top results."""

    found_at: int | None
    """
    1-based rank at which the first expected article was found.
    None if none were found within the fetched window.
    """

    note: str = ""
    """Optional diagnostic note (e.g. filter applied, edge-case info)."""


@dataclass
class RetrievalTestResult:
    """Aggregated results from all case and article retrieval tests."""

    case_tests: list[TestResult] = field(default_factory=list)
    """Results for each case_summaries test."""

    article_tests: list[TestResult] = field(default_factory=list)
    """Results for each constitutional_articles test."""

    @property
    def total(self) -> int:
        """Total number of tests run."""
        return len(self.case_tests) + len(self.article_tests)

    @property
    def passed(self) -> int:
        """Number of tests that passed."""
        return sum(1 for t in self.case_tests + self.article_tests if t.passed)

    @property
    def failed(self) -> int:
        """Number of tests that failed."""
        return self.total - self.passed


# ─────────────────────────────────────────────────────────────────────────────
# TEST DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

# Each case test dict:
#   description     : str
#   query           : str  (natural language, mirrors intake field combination)
#   expected_articles: list[str]  (at least one must appear in top-CASE_PASS_RANK)
#   filter_article  : str | None  (metadata filter on articles_cited)

_CASE_TESTS: list[dict[str, Any]] = [
    {
        "description": "Arbitrary arrest, no warrant produced",
        "query": (
            "Person was arrested without a warrant being produced or shown. "
            "They were held in detention without lawful authority. "
            "The arresting officer was a police officer acting under state power."
        ),
        "expected_articles": ["13(1)", "13(2)"],
        "filter_article": None,
    },
    {
        "description": "Custodial torture, physical assault",
        "query": (
            "Detainee was physically assaulted and tortured while in police custody. "
            "Suffered serious bodily harm inflicted by state agents during interrogation."
        ),
        "expected_articles": ["11"],
        "filter_article": None,
    },
    {
        "description": "Employment discrimination, promotion denied",
        "query": (
            "Public servant was denied a promotion despite being eligible. "
            "The denial was based on discriminatory grounds by a state employer. "
            "Suffered career harm and unequal treatment compared to similarly placed colleagues."
        ),
        "expected_articles": ["12(1)"],
        "filter_article": None,
    },
    {
        "description": "Freedom of expression suppressed",
        "query": (
            "Individual was prevented from publishing or disseminating their views. "
            "State actor suppressed speech and freedom of expression. "
            "Suffered harm to their right to communicate and express opinion freely."
        ),
        "expected_articles": ["14(1)(a)"],
        "filter_article": None,
    },
    {
        "description": "University admission denied arbitrarily",
        "query": (
            "Applicant was denied admission to a state university without valid reason. "
            "The selection process was arbitrary and discriminatory. "
            "Suffered denial of educational opportunity by a state institution."
        ),
        "expected_articles": ["12(1)"],
        "filter_article": None,
    },
    {
        "description": "Land seized by army, property rights violated",
        "query": (
            "Military forces seized private land without due process or compensation. "
            "Landowner suffered loss of property at the hands of state security forces. "
            "The acquisition was arbitrary and without lawful authority."
        ),
        "expected_articles": ["12(1)"],
        "filter_article": None,
    },
    {
        "description": "Trade union activity prevented",
        "query": (
            "Workers were prevented from forming or joining a trade union. "
            "Employer obstructed union activity and collective bargaining. "
            "Suffered harm to freedom of association and right to organise."
        ),
        "expected_articles": ["14(1)(c)", "14(1)(d)"],
        "filter_article": None,
    },
    {
        "description": "Custodial torture with metadata filter on article 11",
        "query": (
            "Detainee was physically assaulted and tortured while in police custody. "
            "Suffered serious bodily harm inflicted by state agents during interrogation."
        ),
        "expected_articles": ["11"],
        "filter_article": "11",
    },
    {
        "description": "Arbitrary arrest with metadata filter on article 13(1)",
        "query": (
            "Person was arrested without a warrant being produced or shown. "
            "They were held in detention without lawful authority. "
            "The arresting officer was a police officer acting under state power."
        ),
        "expected_articles": ["13(1)", "13(2)"],
        "filter_article": "13(1)",
    },
    {
        "description": "PTA detention, national security grounds",
        "query": (
            "Suspect was detained under the Prevention of Terrorism Act on national security grounds. "
            "Detained for extended period without being produced before a magistrate. "
            "Suffered prolonged detention by state authorities under emergency powers."
        ),
        "expected_articles": ["13(1)", "13(2)"],
        "filter_article": None,
    },
]


# Each article test dict:
#   description      : str
#   query            : str
#   expected_article : str  (must appear in top-ARTICLE_PASS_RANK results)

_ARTICLE_TESTS: list[dict[str, Any]] = [
    {
        "description": "Arbitrary arrest scenario",
        "query": (
            "Person arrested without warrant by police. "
            "Detained without being informed of the reason for arrest."
        ),
        "expected_article": "13(1)",
    },
    {
        "description": "Torture in custody scenario",
        "query": (
            "Prisoner tortured and subjected to cruel treatment while in custody. "
            "Physical harm inflicted by state agents during detention."
        ),
        "expected_article": "11",
    },
    {
        "description": "Unequal treatment by state",
        "query": (
            "Citizen treated unequally before the law by a state institution. "
            "Discriminatory action without reasonable justification."
        ),
        "expected_article": "12(1)",
    },
    {
        "description": "Speech and expression suppressed",
        "query": (
            "Individual's freedom to speak and publish their views was suppressed. "
            "State actor restricted the right to express opinion publicly."
        ),
        "expected_article": "14(1)(a)",
    },
    {
        "description": "Dismissed from employment by state",
        "query": (
            "Public employee dismissed from their job without lawful cause. "
            "Suffered loss of livelihood and right to engage in occupation."
        ),
        "expected_article": "14(1)(g)",
    },
    {
        "description": "Police officer as state actor causing unequal treatment",
        "query": (
            "Police officer acting in official capacity treated citizens unequally. "
            "Discriminatory enforcement of law by a state agent."
        ),
        "expected_article": "12(1)",
    },
    {
        "description": "Restriction in the interest of public order",
        "query": (
            "Fundamental right restricted by law in the interest of public order "
            "and national security. Restriction imposed by the state for public safety."
        ),
        "expected_article": "15(7)",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _similarity(distance: float) -> float:
    """Convert cosine distance returned by ChromaDB to a similarity score.

    ChromaDB returns cosine *distance* (0 = identical, 2 = opposite).
    Similarity = 1 - distance gives an intuitive [−1, 1] score.

    Args:
        distance: Cosine distance value from ChromaDB query results.

    Returns:
        Similarity score as a float.
    """
    return round(1.0 - distance, 4)


def _build_top_results(
    ids: list[str],
    distances: list[float],
    metadatas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Zip ChromaDB result lists into a ranked list of result dicts.

    Args:
        ids:       ChromaDB document IDs (inner list, already flattened).
        distances: Cosine distances (inner list, already flattened).
        metadatas: Metadata dicts (inner list, already flattened).

    Returns:
        List of dicts with keys: rank, id, score, metadata.
    """
    return [
        {
            "rank": rank,
            "id": doc_id,
            "score": _similarity(dist),
            "metadata": meta,
        }
        for rank, (doc_id, dist, meta) in enumerate(
            zip(ids, distances, metadatas), start=1
        )
    ]


def _articles_cited_contains(articles_cited: str, target: str) -> bool:
    """Check whether *target* appears as a token in *articles_cited*.

    articles_cited is a comma-separated string such as "11,13(1),13(2)".
    Splits on commas and strips whitespace before comparing, so
    "13(1)" is not falsely matched by "113(1)".

    Args:
        articles_cited: Comma-separated article numbers from metadata.
        target:         Article number to search for.

    Returns:
        True if target is present as a distinct token.
    """
    tokens = [a.strip() for a in articles_cited.split(",")]
    return target in tokens


# ─────────────────────────────────────────────────────────────────────────────
# CORE TEST RUNNERS
# ─────────────────────────────────────────────────────────────────────────────

def _run_case_test(collection: Any, test: dict[str, Any]) -> TestResult:
    """Run a single case_summaries retrieval test.

    Performs a semantic query (with optional metadata filter on articles_cited).
    Checks whether at least one expected article appears within the top
    CASE_PASS_RANK results.

    Args:
        collection: ChromaDB Collection object for case_summaries.
        test:       Test definition dict (see _CASE_TESTS).

    Returns:
        A populated TestResult dataclass instance.
    """
    description: str = test["description"]
    query: str = test["query"]
    expected: list[str] = test["expected_articles"]
    filter_article: str | None = test.get("filter_article")

    # Build optional where-clause
    where: dict[str, Any] | None = None
    note: str = ""
    if filter_article:
        where = {"articles_cited": {"$contains": filter_article}}
        note = f"Metadata filter applied: articles_cited $contains '{filter_article}'"

    # Query ChromaDB
    query_kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": CASE_TOP_N,
        "include": ["metadatas", "distances"],
    }
    if where:
        query_kwargs["where"] = where

    raw = collection.query(**query_kwargs)

    # Flatten inner lists (ChromaDB wraps results in an outer list per query)
    distances: list[float] = raw["distances"][0]
    metadatas: list[dict[str, Any]] = raw["metadatas"][0]

    top_results = _build_top_results(
        ids=raw.get("ids", [[]])[0],
        distances=distances,
        metadatas=metadatas,
    )

    # Determine pass/fail: check if any expected article appears in top-CASE_PASS_RANK
    found_at: int | None = None
    for result in top_results[:CASE_PASS_RANK]:
        cited: str = result["metadata"].get("articles_cited", "")
        for exp_article in expected:
            if _articles_cited_contains(cited, exp_article):
                found_at = result["rank"]
                break
        if found_at is not None:
            break

    passed: bool = found_at is not None

    # Print report for this test
    status = "PASS ✓" if passed else "FAIL ✗"
    print(f"\n  [{status}] {description}")
    print(f"    Query   : {query[:120]}{'...' if len(query) > 120 else ''}")
    print(f"    Expected: {expected}")
    if note:
        print(f"    Note    : {note}")
    print(f"    Top 3 results:")
    for r in top_results[:3]:
        cited = r["metadata"].get("articles_cited", "N/A")
        name = r["metadata"].get("case_name", "N/A")
        print(f"      Rank {r['rank']} | Score {r['score']:.4f} | "
              f"articles_cited={cited!r} | {name}")
    if found_at:
        print(f"    → Expected article found at rank {found_at}")
    else:
        print(f"    → Expected article NOT found within top {CASE_PASS_RANK}")

    return TestResult(
        description=description,
        query=query,
        passed=passed,
        top_results=top_results,
        expected=expected,
        found_at=found_at,
        note=note,
    )


def _run_article_test(collection: Any, test: dict[str, Any]) -> TestResult:
    """Run a single constitutional_articles retrieval test.

    Performs a pure semantic query (no metadata filter).
    Checks whether the expected article_number appears within the top
    ARTICLE_PASS_RANK results.

    Args:
        collection: ChromaDB Collection object for constitutional_articles.
        test:       Test definition dict (see _ARTICLE_TESTS).

    Returns:
        A populated TestResult dataclass instance.
    """
    description: str = test["description"]
    query: str = test["query"]
    expected_article: str = test["expected_article"]

    raw = collection.query(
        query_texts=[query],
        n_results=ARTICLE_TOP_N,
        include=["metadatas", "distances"],
    )

    distances: list[float] = raw["distances"][0]
    metadatas: list[dict[str, Any]] = raw["metadatas"][0]

    top_results = _build_top_results(
        ids=raw.get("ids", [[]])[0],
        distances=distances,
        metadatas=metadatas,
    )

    # Determine pass/fail
    found_at: int | None = None
    for result in top_results[:ARTICLE_PASS_RANK]:
        art_num: str = result["metadata"].get("article_number", "")
        if art_num == expected_article:
            found_at = result["rank"]
            break

    passed: bool = found_at is not None

    status = "PASS ✓" if passed else "FAIL ✗"
    print(f"\n  [{status}] {description}")
    print(f"    Query   : {query[:120]}{'...' if len(query) > 120 else ''}")
    print(f"    Expected: article_number={expected_article!r}")
    print(f"    Top 3 results:")
    for r in top_results[:3]:
        art = r["metadata"].get("article_number", "N/A")
        heading = r["metadata"].get("heading", "N/A")
        print(f"      Rank {r['rank']} | Score {r['score']:.4f} | "
              f"article_number={art!r} | {heading}")
    if found_at:
        print(f"    → Expected article found at rank {found_at}")
    else:
        print(f"    → Expected article NOT found within top {ARTICLE_PASS_RANK}")

    return TestResult(
        description=description,
        query=query,
        passed=passed,
        top_results=top_results,
        expected=[expected_article],
        found_at=found_at,
        note="",
    )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run_retrieval_tests(
    case_col: Any,
    article_col: Any,
) -> RetrievalTestResult:
    """Run all retrieval tests against both ChromaDB collections.

    This is the only public function in this module. Called by run_ingestion.py
    after ingestion is complete to validate that ChromaDB returns semantically
    correct results before the LLM chain is built on top of it.

    Args:
        case_col:    ChromaDB Collection for case_summaries.
        article_col: ChromaDB Collection for constitutional_articles.

    Returns:
        RetrievalTestResult containing all TestResult objects and summary stats.
    """
    result = RetrievalTestResult()

    # ── Case summaries tests ──────────────────────────────────────────────────
    print("\n" + "═" * 66)
    print("  CASE SUMMARIES RETRIEVAL TESTS")
    print("═" * 66)

    for test_def in _CASE_TESTS:
        tr = _run_case_test(case_col, test_def)
        result.case_tests.append(tr)

    # ── Constitutional articles tests ─────────────────────────────────────────
    print("\n" + "═" * 66)
    print("  CONSTITUTIONAL ARTICLES RETRIEVAL TESTS")
    print("═" * 66)

    for test_def in _ARTICLE_TESTS:
        tr = _run_article_test(article_col, test_def)
        result.article_tests.append(tr)

    # ── Summary ───────────────────────────────────────────────────────────────
    pass_rate: float = (result.passed / result.total * 100) if result.total else 0.0

    print("\n" + "═" * 66)
    print("  RETRIEVAL TEST SUMMARY")
    print("═" * 66)
    print(f"  Total  : {result.total}")
    print(f"  Passed : {result.passed}")
    print(f"  Failed : {result.failed}")
    print(f"  Rate   : {pass_rate:.1f}%")

    if pass_rate < 80.0:
        print("\n  ⚠  WARNING: Pass rate below 80 %. Retrieval quality is insufficient.")
        print("     Debug steps:")
        print("     1. Confirm embedder.py used 'all-MiniLM-L6-v2' (or project model).")
        print("     2. Re-run parser.py + embedder.py — collection may be stale/empty.")
        print("     3. Check ChromaDB distance metric is 'cosine' in both collections.")
        print("     4. Inspect failing queries — verify metadata fields are populated.")
        print("     5. Run audit.py to confirm document counts match expected totals.")
    else:
        print("\n  ✓  Retrieval quality is acceptable. Safe to build LLM chain.")

    print("═" * 66 + "\n")

    return result
