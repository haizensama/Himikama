"""
himikama/backend/chain/retrieval.py
═══════════════════════════════════════════════════════════════
Phase 4 — Retrieval Module

Responsibility:
    All ChromaDB retrieval logic for the sub-query chain.
    This is the ONLY module that queries ChromaDB during
    chain execution.

    The LLM NEVER queries ChromaDB directly.
    Retrieval queries are ALWAYS built deterministically
    from intake fields by application code in this module.
    LangChain never calls any function in this file directly —
    it is called by steps.py via controlled lambda functions.

Two types of retrieval:

    1. ARTICLE RETRIEVAL (Steps 2, 4, 5)
       Queries the constitutional_articles collection.
       Combines semantic search with keyword boost map.
       Returns merged, deduplicated article list.

    2. CASE RETRIEVAL (Step 7 — Two Stage)
       Stage A: Semantic search with optional metadata filter.
                Returns top 5 cases after re-ranking.
       Stage B: Targeted direct fetch by case_id.
                Called after LLM identifies similar cases.
                NOT a semantic search — deterministic lookup.

Query Construction:
    All queries built from intake fields only.
    Which fields are used depends on the step:
        Steps 2, 5:  actor_role + what_happened
        Step 4:      what_happened + harm_suffered
        Step 7:      what_happened + harm_suffered + actor_role

Keyword Boost Map:
    Deterministic lookup table — defined once, never changes
    at runtime. When intake text contains legal keywords,
    the corresponding articles are added to retrieval results
    even if semantic similarity is weak. Zero LLM calls.

Public functions (imported by steps.py):
    retrieve_articles()          Steps 2, 4, 5
    retrieve_cases_stage_a()     Step 7 Stage A
    retrieve_cases_stage_b()     Step 7 Stage B
    get_keyword_boost_articles() Step 4 (supplement)
    format_articles_for_prompt() All RAG steps
    format_cases_for_prompt()    Steps 7, 8, 9

Usage:
    from chain.retrieval import (
        retrieve_articles,
        retrieve_cases_stage_a,
        retrieve_cases_stage_b,
        get_keyword_boost_articles,
        format_articles_for_prompt,
        format_cases_for_prompt,
    )
═══════════════════════════════════════════════════════════════
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

# Articles retrieved per semantic search (Steps 2, 4, 5)
ARTICLE_TOP_N = 6

# Cases retrieved in Stage A before re-ranking (Step 7)
CASE_STAGE_A_N = 10

# Top cases passed to LLM after re-ranking (Step 7)
CASE_STAGE_A_PASS_TO_LLM = 5


# ─────────────────────────────────────────────────────────────
# KEYWORD BOOST MAP
#
# Maps legal keywords found in intake fields to article numbers.
# Runs alongside semantic search to catch weak semantic matches.
#
# Rules:
#   - Defined once by legal expert. Never changes at runtime.
#   - Scans: what_happened + harm_suffered + actor_role
#   - Results merged with semantic results and deduplicated.
#   - Zero LLM calls. Pure string matching.
# ─────────────────────────────────────────────────────────────

KEYWORD_MAP: dict[str, list[str]] = {

    # ── Physical liberty ─────────────────────────────────────
    "arrest":       ["13(1)", "13(2)"],
    "arrested":     ["13(1)", "13(2)"],
    "detain":       ["13(1)", "13(2)", "13(3)"],
    "detained":     ["13(1)", "13(2)", "13(3)"],
    "detention":    ["13(1)", "13(2)", "13(3)"],
    "custody":      ["13(1)", "13(2)", "13(3)"],
    "warrant":      ["13(1)"],
    "magistrate":   ["13(2)"],
    "remand":       ["13(2)"],
    "reason":       ["13(3)"],
    "informed":     ["13(3)"],
    "charge":       ["13(3)", "13(4)"],
    "trial":        ["13(3)"],
    "innocent":     ["13(5)"],
    "presumed":     ["13(5)"],

    # ── Physical integrity ───────────────────────────────────
    "hit":          ["11"],
    "beat":         ["11"],
    "beaten":       ["11"],
    "assault":      ["11"],
    "assaulted":    ["11"],
    "torture":      ["11"],
    "tortured":     ["11"],
    "cruel":        ["11"],
    "inhuman":      ["11"],
    "degrading":    ["11"],
    "pain":         ["11"],
    "injury":       ["11"],
    "injured":      ["11"],

    # ── Equality and discrimination ──────────────────────────
    "discriminat":  ["12(1)", "12(2)"],
    "equality":     ["12(1)"],
    "equal":        ["12(1)"],
    "arbitrary":    ["12(1)"],
    "unfair":       ["12(1)"],
    "race":         ["12(2)"],
    "religion":     ["12(2)", "14(1)(e)"],
    "caste":        ["12(2)"],
    "sex":          ["12(2)"],

    # ── Employment and occupation ────────────────────────────
    "employment":   ["12(1)", "14(1)(g)"],
    "dismissed":    ["12(1)", "14(1)(g)"],
    "dismiss":      ["12(1)", "14(1)(g)"],
    "promotion":    ["12(1)", "14(1)(g)"],
    "sacked":       ["12(1)", "14(1)(g)"],
    "fired":        ["12(1)", "14(1)(g)"],
    "occupation":   ["14(1)(g)"],
    "profession":   ["14(1)(g)"],
    "business":     ["14(1)(g)"],
    "license":      ["14(1)(g)"],
    "licence":      ["14(1)(g)"],

    # ── Expression and assembly ──────────────────────────────
    "speech":       ["14(1)(a)"],
    "publish":      ["14(1)(a)"],
    "expression":   ["14(1)(a)"],
    "press":        ["14(1)(a)"],
    "newspaper":    ["14(1)(a)"],
    "broadcast":    ["14(1)(a)"],
    "assembly":     ["14(1)(b)"],
    "protest":      ["14(1)(b)"],
    "association":  ["14(1)(c)"],
    "union":        ["14(1)(c)", "14(1)(d)"],

    # ── Religion and culture ─────────────────────────────────
    "worship":      ["14(1)(e)"],
    "religious":    ["14(1)(e)"],
    "belief":       ["14(1)(e)"],
    "culture":      ["14(1)(f)"],
    "language":     ["14(1)(f)"],

    # ── Movement and residence ───────────────────────────────
    "movement":     ["14(1)(h)"],
    "travel":       ["14(1)(h)"],
    "evict":        ["14(1)(h)"],
    "evicted":      ["14(1)(h)"],

    # ── Education ────────────────────────────────────────────
    "school":       ["12(2)"],
    "education":    ["12(2)"],
    "admission":    ["12(2)"],
    "university":   ["12(1)", "12(2)"],

    # ── Property and state interference ─────────────────────
    "land":         ["12(1)"],
    "property":     ["12(1)"],
    "seized":       ["12(1)"],
    "confiscat":    ["12(1)"],

    # ── Information access ───────────────────────────────────
    "information":  ["14(A)"],
    "access":       ["14(A)"],

    # ── Security legislation ─────────────────────────────────
    "terrorism":    ["13(1)", "13(2)"],
    "pta":          ["13(1)", "13(2)"],
    "emergency":    ["13(1)", "13(2)"],
}


# ─────────────────────────────────────────────────────────────
# QUERY CONSTRUCTION
# ─────────────────────────────────────────────────────────────

def build_article_query(intake: dict, step: str) -> str:
    """
    Build a semantic query string for article retrieval.

    Which intake fields are combined depends on the step:
        step_2: actor_role + what_happened
        step_4: what_happened + harm_suffered
        step_5: what_happened + actor_role
        other:  all three fields

    Args:
        intake: Confirmed intake object dict.
        step:   Step identifier string e.g. "step_4".

    Returns:
        Query string for ChromaDB semantic search.
    """
    what_happened = intake.get("what_happened", "") or ""
    harm_suffered = intake.get("harm_suffered", "") or ""
    actor_role    = intake.get("actor_role", "")    or ""

    if step == "step_2":
        parts = [actor_role, what_happened]
    elif step == "step_4":
        parts = [what_happened, harm_suffered]
    elif step == "step_5":
        parts = [what_happened, actor_role]
    else:
        parts = [what_happened, harm_suffered, actor_role]

    query = " ".join(p.strip() for p in parts if p.strip())
    logger.debug(f"Article query [{step}]: {query[:100]}")
    return query


def build_case_query(intake: dict) -> str:
    """
    Build a semantic query string for case retrieval (Step 7).

    Uses all three primary intake fields for the broadest
    possible semantic match.

    Args:
        intake: Confirmed intake object dict.

    Returns:
        Query string for ChromaDB semantic search.
    """
    what_happened = intake.get("what_happened", "") or ""
    harm_suffered = intake.get("harm_suffered", "") or ""
    actor_role    = intake.get("actor_role", "")    or ""

    parts = [what_happened, harm_suffered, actor_role]
    query = " ".join(p.strip() for p in parts if p.strip())
    logger.debug(f"Case query: {query[:100]}")
    return query


def get_keyword_boost_articles(intake: dict) -> list[str]:
    """
    Scan intake fields for legal keywords and return the
    corresponding article numbers from the keyword map.

    Called by steps.py Step 4 to supplement the articles
    identified by the LLM with keyword-matched articles.
    Ensures no relevant article is missed due to weak
    semantic similarity.

    Args:
        intake: Confirmed intake object dict.

    Returns:
        Deduplicated list of article number strings.
        e.g. ["13(1)", "13(2)", "11"]
    """
    text = " ".join([
        (intake.get("what_happened") or ""),
        (intake.get("harm_suffered") or ""),
        (intake.get("actor_role")    or ""),
    ]).lower()

    boosted: list[str] = []
    for keyword, articles in KEYWORD_MAP.items():
        if keyword in text:
            boosted.extend(articles)

    # Deduplicate preserving first-seen order
    seen:   set[str]  = set()
    result: list[str] = []
    for article in boosted:
        if article not in seen:
            seen.add(article)
            result.append(article)

    logger.debug(f"Keyword boost articles: {result}")
    return result


# ─────────────────────────────────────────────────────────────
# ARTICLE RETRIEVAL (Steps 2, 4, 5)
# ─────────────────────────────────────────────────────────────

def retrieve_articles(
    article_collection: Any,
    intake: dict,
    step: str,
) -> list[dict]:
    """
    Retrieve relevant constitutional articles for a chain step.

    Combines semantic search with keyword boost:
        1. Semantic search → top ARTICLE_TOP_N by similarity
        2. Keyword boost   → articles matched by intake keywords
        3. Merge and deduplicate

    Called by steps.py via a controlled lambda inside
    _run_controlled_rag_chain. Never called by LangChain
    directly.

    Args:
        article_collection: ChromaDB constitutional_articles.
        intake:             Confirmed intake object dict.
        step:               Step identifier e.g. "step_4".

    Returns:
        List of article dicts, each containing:
            article_number: str
            heading:        str
            text:           str  (full article text for prompt)
            similarity:     float | None
            source:         str  "semantic" | "keyword" | "both"
    """
    query = build_article_query(intake, step)

    # Semantic search
    semantic_results = _semantic_article_search(
        article_collection, query
    )
    semantic_numbers = {r["article_number"] for r in semantic_results}

    # Keyword boost — fetch articles not already in semantic results
    boosted_numbers = get_keyword_boost_articles(intake)
    boost_only      = [n for n in boosted_numbers
                       if n not in semantic_numbers]
    boosted_results = _fetch_articles_by_number(
        article_collection, boost_only
    )

    # Tag source for debugging
    for r in semantic_results:
        r["source"] = (
            "both" if r["article_number"] in boosted_numbers
            else "semantic"
        )
    for r in boosted_results:
        r["source"] = "keyword"

    combined = semantic_results + boosted_results

    logger.info(
        f"{step} article retrieval — "
        f"{len(semantic_results)} semantic + "
        f"{len(boosted_results)} keyword-boosted = "
        f"{len(combined)} total"
    )
    return combined


def _semantic_article_search(
    collection: Any,
    query: str,
) -> list[dict]:
    """
    Run a semantic search against constitutional_articles.

    Args:
        collection: ChromaDB constitutional_articles collection.
        query:      Natural language query string.

    Returns:
        List of article dicts sorted by similarity descending.
    """
    if not query.strip():
        return []

    results = collection.query(
        query_texts=[query],
        n_results=ARTICLE_TOP_N,
        include=["documents", "metadatas", "distances"],
    )

    articles = []
    docs  = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances",  [[]])[0]

    for doc, meta, dist in zip(docs, metas, dists):
        articles.append({
            "article_number": meta.get("article_number", ""),
            "heading":        meta.get("heading", ""),
            "text":           doc,
            "similarity":     round(1 - dist, 4),
        })

    return articles


def _fetch_articles_by_number(
    collection: Any,
    article_numbers: list[str],
) -> list[dict]:
    """
    Fetch specific articles by article_number metadata field.
    Used for keyword-boosted articles not in semantic results.

    Args:
        collection:      ChromaDB constitutional_articles.
        article_numbers: List of article number strings to fetch.

    Returns:
        List of article dicts (similarity=None — direct fetch).
    """
    if not article_numbers:
        return []

    fetched = []
    for number in article_numbers:
        try:
            result = collection.get(
                where={"article_number": {"$eq": number}},
                include=["documents", "metadatas"],
            )
            docs  = result.get("documents", [])
            metas = result.get("metadatas", [])

            for doc, meta in zip(docs, metas):
                fetched.append({
                    "article_number": meta.get("article_number", ""),
                    "heading":        meta.get("heading", ""),
                    "text":           doc,
                    "similarity":     None,
                })
        except Exception as e:
            logger.warning(f"Could not fetch article {number}: {e}")

    return fetched

def _normalise_filter_articles(
    filter_articles: list[str] | None,
) -> list[str]:
    """
    Clean Step 4 article output before using it for case filtering.

    Removes broad bare article numbers like "12", "13", "14"
    when sub-articles are available, because the case metadata
    usually stores specific articles such as "13(1)" and "13(2)".

    Example:
        ["13", "13(1)", "13(2)", "13(3)"]
        → ["13(1)", "13(2)", "13(3)"]
    """
    if not filter_articles:
        return []

    cleaned: list[str] = []

    for article in filter_articles:
        article = str(article).strip()

        if not article:
            continue

        # Avoid broad bare articles when sub-articles should be used.
        if article in {"12", "13", "14"}:
            continue

        if article not in cleaned:
            cleaned.append(article)

    return cleaned

def _query_cases(
    case_collection: Any,
    query: str,
    where: dict[str, Any] | None = None,
) -> list[dict]:
    """
    Run a ChromaDB query against case_summaries and return
    ranked case dictionaries.

    This helper is used by retrieve_cases_stage_a() so that
    filtered and unfiltered searches share the same result
    parsing logic.
    """
    query_kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": CASE_STAGE_A_N,
        "include": ["documents", "metadatas", "distances"],
    }

    if where:
        query_kwargs["where"] = where

    results = case_collection.query(**query_kwargs)

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    cases: list[dict] = []

    for doc, meta, dist in zip(docs, metas, dists):
        cases.append({
            "case_id": meta.get("case_id", ""),
            "case_name": meta.get("case_name", ""),
            "case_number": meta.get("case_number", ""),
            "year": meta.get("year", 0),
            "judgment": meta.get("judgment", ""),
            "articles_cited": meta.get("articles_cited", ""),
            "legal_topic": meta.get("legal_topic", ""),
            "summary": doc,
            "similarity": round(1 - dist, 4),
        })

    cases.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    return cases
# ─────────────────────────────────────────────────────────────
# CASE RETRIEVAL — STAGE A (Step 7 broad semantic search)
# ─────────────────────────────────────────────────────────────


def _query_cases(
    case_collection: Any,
    query: str,
    where: dict[str, Any] | None = None,
) -> list[dict]:
    """
    Run a ChromaDB query against case_summaries and return
    ranked case dictionaries.

    Used by retrieve_cases_stage_a() so filtered and unfiltered
    searches share the same result parsing logic.
    """
    query_kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": CASE_STAGE_A_N,
        "include": ["documents", "metadatas", "distances"],
    }

    if where:
        query_kwargs["where"] = where

    results = case_collection.query(**query_kwargs)

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    cases: list[dict] = []

    for doc, meta, dist in zip(docs, metas, dists):
        cases.append({
            "case_id": meta.get("case_id", ""),
            "case_name": meta.get("case_name", ""),
            "case_number": meta.get("case_number", ""),
            "year": meta.get("year", 0),
            "judgment": meta.get("judgment", ""),
            "articles_cited": meta.get("articles_cited", ""),
            "legal_topic": meta.get("legal_topic", ""),
            "summary": doc,
            "similarity": round(1 - dist, 4),
        })

    cases.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    return cases

ARTICLE_FILTER_FIELDS: dict[str, str] = {
    "4(d)": "has_article_4_d",
    "10": "has_article_10",
    "11": "has_article_11",

    "12(1)": "has_article_12_1",
    "12(2)": "has_article_12_2",
    "12(3)": "has_article_12_3",
    "12(4)": "has_article_12_4",

    "13(1)": "has_article_13_1",
    "13(2)": "has_article_13_2",
    "13(3)": "has_article_13_3",
    "13(4)": "has_article_13_4",
    "13(5)": "has_article_13_5",
    "13(6)": "has_article_13_6",

    "14(1)(a)": "has_article_14_1_a",
    "14(1)(b)": "has_article_14_1_b",
    "14(1)(c)": "has_article_14_1_c",
    "14(1)(d)": "has_article_14_1_d",
    "14(1)(e)": "has_article_14_1_e",
    "14(1)(f)": "has_article_14_1_f",
    "14(1)(g)": "has_article_14_1_g",
    "14(1)(h)": "has_article_14_1_h",

    "14(A)": "has_article_14_a",

    "15(1)": "has_article_15_1",
    "15(2)": "has_article_15_2",
    "15(3)": "has_article_15_3",
    "15(4)": "has_article_15_4",
    "15(5)": "has_article_15_5",
    "15(6)": "has_article_15_6",
    "15(7)": "has_article_15_7",

    "16": "has_article_16",
    "17": "has_article_17",

    "126": "has_article_126",
    "126(2)": "has_article_126_2",
}

def _normalise_filter_articles(
    filter_articles: list[str] | None,
) -> list[str]:
    """
    Remove broad bare articles when specific sub-articles exist.

    Example:
        ["13", "13(1)", "13(2)"]
        -> ["13(1)", "13(2)"]
    """
    if not filter_articles:
        return []

    articles = [
        str(article).strip()
        for article in filter_articles
        if str(article).strip()
    ]

    bases_with_subarticles = {
        article.split("(")[0]
        for article in articles
        if "(" in article
    }

    cleaned: list[str] = []
    seen: set[str] = set()

    for article in articles:
        if article in bases_with_subarticles:
            continue

        if article not in seen:
            seen.add(article)
            cleaned.append(article)

    return cleaned

def _build_article_flag_where(
    filter_articles: list[str] | None,
) -> dict[str, Any] | None:
    """
    Build a ChromaDB metadata filter using exact article flags.

    Example:
        ["13(1)", "13(2)"]

    becomes:
        {
            "$or": [
                {"has_article_13_1": {"$eq": 1}},
                {"has_article_13_2": {"$eq": 1}},
            ]
        }
    """
    articles = _normalise_filter_articles(filter_articles)

    conditions: list[dict[str, Any]] = []

    for article in articles:
        field_name = ARTICLE_FILTER_FIELDS.get(article)

        if not field_name:
            logger.debug("No article filter field mapped for %s", article)
            continue

        conditions.append({field_name: {"$eq": 1}})

    if not conditions:
        return None

    if len(conditions) == 1:
        return conditions[0]

    return {"$or": conditions}

def retrieve_cases_stage_a(
    case_collection: Any,
    intake: dict,
    filter_articles: list[str] | None = None,
) -> list[dict]:
    """
    Stage A — broad semantic case search using exact article flag metadata.

    Strategy:
        1. Build case query from intake fields.
        2. Clean Step 4 articles so broad articles like "13" are removed.
        3. Build a ChromaDB metadata filter using boolean/int article flags.
        4. Run semantic search with exact article flags.
        5. If filtered search returns no cases, retry without filter.
        6. Deduplicate by case_id.
        7. Return top cases by similarity.
    """
    query = build_case_query(intake)

    if not query.strip():
        logger.warning("Stage A skipped — empty case query.")
        return []

    where = _build_article_flag_where(filter_articles)

    all_cases: list[dict] = []

    # First try exact article-flag filtered semantic search.
    if where:
        try:
            all_cases = _query_cases(
                case_collection=case_collection,
                query=query,
                where=where,
            )

            logger.info(
                "Stage A article-flag filtered search: %s result(s). Filter=%s",
                len(all_cases),
                where,
            )

        except Exception as e:
            logger.warning(
                "Stage A article-flag filtered query failed: %s. "
                "Retrying without article filter.",
                e,
            )
            all_cases = []

    # If no filter exists or filtered search produced no cases,
    # retry without article filter.
    if not all_cases:
        logger.warning(
            "Stage A article-flag filtered search returned no cases. "
            "Retrying without article filter."
        )

        try:
            all_cases = _query_cases(
                case_collection=case_collection,
                query=query,
                where=None,
            )
        except Exception as e:
            logger.error("Stage A unfiltered query failed: %s", e)
            return []

    # Deduplicate by case_id, keeping the highest similarity result.
    by_id: dict[str, dict] = {}

    for case in all_cases:
        case_id = str(case.get("case_id", ""))

        if not case_id:
            continue

        existing = by_id.get(case_id)

        if (
            existing is None
            or case.get("similarity", 0) > existing.get("similarity", 0)
        ):
            by_id[case_id] = case

    cases = list(by_id.values())
    cases.sort(key=lambda x: x.get("similarity", 0), reverse=True)

    top = cases[:CASE_STAGE_A_PASS_TO_LLM]

    logger.info(
        "Stage A final: %s unique case(s) retrieved, %s passed to LLM.",
        len(cases),
        len(top),
    )

    return top
# ─────────────────────────────────────────────────────────────
# CASE RETRIEVAL — STAGE B (targeted fetch after Step 7 LLM)
# ─────────────────────────────────────────────────────────────

def retrieve_cases_stage_b(
    case_collection: Any,
    case_ids: list[str],
) -> list[dict]:
    """
    Stage B — targeted fetch of specific cases by case_id.

    Called after the LLM in Step 7 identifies the 2-3 most
    similar cases from Stage A results. Fetches their complete
    records for use in Steps 8 and 9.

    This is a direct metadata lookup — NOT a semantic search.
    No new embedding is computed. This is intentionally outside
    LangChain because it is a deterministic fetch, not reasoning.

    Args:
        case_collection: ChromaDB case_summaries collection.
        case_ids:        List of case_id strings identified by
                         the LLM in Step 7.

    Returns:
        List of complete case dicts (same structure as Stage A,
        similarity=None since this is a direct fetch).
    """
    if not case_ids:
        logger.warning("Stage B called with empty case_ids.")
        return []

    fetched = []
    for case_id in case_ids:
        try:
            result = case_collection.get(
                where={"case_id": {"$eq": str(case_id)}},
                include=["documents", "metadatas"],
            )
            docs  = result.get("documents", [])
            metas = result.get("metadatas", [])

            for doc, meta in zip(docs, metas):
                fetched.append({
                    "case_id":        meta.get("case_id", ""),
                    "case_name":      meta.get("case_name", ""),
                    "case_number":    meta.get("case_number", ""),
                    "year":           meta.get("year", 0),
                    "judgment":       meta.get("judgment", ""),
                    "articles_cited": meta.get("articles_cited", ""),
                    "legal_topic":    meta.get("legal_topic", ""),
                    "summary":        doc,
                    "similarity":     None,
                })
        except Exception as e:
            logger.warning(
                f"Stage B could not fetch case_id '{case_id}': {e}"
            )

    logger.info(
        f"Stage B fetched {len(fetched)} cases "
        f"for ids: {case_ids}"
    )
    return fetched


# ─────────────────────────────────────────────────────────────
# PROMPT FORMATTING HELPERS
#
# Convert retrieved content into clean strings for injection
# into LLM prompts by steps.py.
# ─────────────────────────────────────────────────────────────

def format_articles_for_prompt(articles: list[dict]) -> str:
    """
    Format retrieved constitutional articles into a clean
    string ready for injection into an LLM prompt.

    Args:
        articles: List of article dicts from retrieve_articles().

    Returns:
        Formatted multi-line string. Each article on its own
        block with number, heading, and full text.
    """
    if not articles:
        return "No constitutional articles retrieved."

    lines = []
    for a in articles:
        number  = a.get("article_number", "")
        heading = a.get("heading", "")
        text    = a.get("text", "")
        lines.append(f"Article {number} — {heading}:\n{text}")

    return "\n\n".join(lines)


def format_cases_for_prompt(cases: list[dict]) -> str:
    """
    Format retrieved cases into a clean string ready for
    injection into an LLM prompt.

    Args:
        cases: List of case dicts from Stage A or Stage B.

    Returns:
        Formatted multi-block string. Each case separated by
        a divider with name, number, year, judgment, articles,
        and full summary.
    """
    if not cases:
        return "No similar cases retrieved."

    blocks = []
    for i, case in enumerate(cases, start=1):
        name     = case.get("case_name", "Unknown")
        number   = case.get("case_number", "")
        year     = case.get("year", "")
        judgment = case.get("judgment", "")
        articles = case.get("articles_cited", "")
        summary  = case.get("summary", "")

        header = f"Case {i}: {name}"
        if number and number != "NONE":
            header += f" ({number})"
        if year:
            header += f" [{year}]"

        blocks.append(
            f"{header}\n"
            f"Judgment: {judgment}\n"
            f"Articles: {articles}\n"
            f"Summary: {summary}"
        )

    return "\n\n---\n\n".join(blocks)
