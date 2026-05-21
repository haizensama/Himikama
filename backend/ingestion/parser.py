"""
himikama/backend/ingestion/parser.py
═══════════════════════════════════════════════════════════════
Module 1 — PDF Parser

Responsibility:
    Read the normalized case summary PDF, clean all artifacts
    introduced by Google Docs and PDF export, split into
    individual cases, and extract the SUMMARY and METADATA
    from each case block.

    This module produces NO side effects. It only reads and
    returns structured data. ChromaDB is never touched here.

Input:
    Path to Metadata_Final.pdf

Output:
    List of dicts, one per case:
    {
        "summary":  str,   — clean paragraph text for embedding
        "metadata": dict,  — parsed JSON fields for ChromaDB
        "position": int,   — 1-based position in file (for debugging)
    }

Usage:
    from ingestion.parser import parse_pdf

    cases = parse_pdf("data/Metadata_Final.pdf")
    print(f"Parsed {len(cases)} cases")
═══════════════════════════════════════════════════════════════
"""

import re
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

CASE_START_DELIMITER = "<<<CASE_START>>>"
CASE_END_DELIMITER   = "<<<CASE_END>>>"

# Articles that must be expanded when cited without sub-article.
# Article 11 is intentionally excluded — it has no sub-articles.
ARTICLE_EXPANSION = {
    "12":  ["12(1)"],
    "13":  ["13(1)", "13(2)"],
    "14":  ["14(1)(a)"],
}

# Normalise non-standard article formats to canonical form
ARTICLE_NORMALISATION = {
    "14A": "14(A)",
}


# ─────────────────────────────────────────────────────────────
# STEP 1 — PDF TEXT EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract raw text from PDF using pdfplumber.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Full extracted text as a single string.

    Raises:
        FileNotFoundError: If the PDF does not exist.
        ImportError: If pdfplumber is not installed.
    """
    try:
        import pdfplumber
    except ImportError:
        raise ImportError(
            "pdfplumber is not installed. Run: pip install pdfplumber"
        )

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info(f"Extracting text from: {pdf_path}")

    pages = []
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
            if (i + 1) % 50 == 0:
                logger.info(f"  Extracted {i + 1}/{total} pages")

    full_text = "\n".join(pages)
    logger.info(f"Extraction complete — {len(full_text):,} characters")
    return full_text


# ─────────────────────────────────────────────────────────────
# STEP 2 — TEXT CLEANING
# ─────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """
    Remove all artifacts introduced by Google Docs and PDF export.

    Artifacts cleaned:
        \\u200b  Zero-width space         (Google Docs formatting)
        \\u200c  Zero-width non-joiner    (Google Docs formatting)
        \\u200d  Zero-width joiner        (Google Docs formatting)
        \\ufeff  Byte order mark          (Google Docs export)
        \\x0c   Form feed / page break   (PDF page boundary)
        \\u201c  Left double quote "      (Google Docs smart quotes)
        \\u201d  Right double quote "     (Google Docs smart quotes)
        \\u2018  Left single quote '      (Google Docs smart quotes)
        \\u2019  Right single quote '     (Google Docs smart quotes)

    Args:
        text: Raw extracted PDF text.

    Returns:
        Cleaned text with all artifacts removed.
    """
    # Invisible formatting characters
    text = text.replace("\u200b", "")
    text = text.replace("\u200c", "")
    text = text.replace("\u200d", "")
    text = text.replace("\ufeff", "")

    # PDF page break character
    text = text.replace("\x0c", "")

    # Smart/curly quotes → straight quotes
    # Must be done before JSON parsing
    text = text.replace("\u201c", '"')
    text = text.replace("\u201d", '"')
    text = text.replace("\u2018", "'")
    text = text.replace("\u2019", "'")

    return text


# ─────────────────────────────────────────────────────────────
# STEP 3 — CASE SPLITTING
# ─────────────────────────────────────────────────────────────

def split_into_raw_cases(text: str) -> list[str]:
    """
    Split the full document text into individual raw case blocks
    using the <<<CASE_START>>> and <<<CASE_END>>> delimiters.

    Args:
        text: Cleaned full document text.

    Returns:
        List of raw case text strings, one per case.
        Each string contains the text between the delimiters.

    Raises:
        ValueError: If delimiter counts do not match.
    """
    start_count = text.count(CASE_START_DELIMITER)
    end_count   = text.count(CASE_END_DELIMITER)

    if start_count != end_count:
        raise ValueError(
            f"Delimiter mismatch — "
            f"{start_count} CASE_START vs {end_count} CASE_END. "
            f"Check the PDF for missing or extra delimiters."
        )

    logger.info(f"Found {start_count} case delimiters")

    # Split on CASE_START — first element is empty (before first delimiter)
    raw_blocks = text.split(CASE_START_DELIMITER)[1:]

    # Remove the CASE_END delimiter and trailing whitespace from each block
    raw_cases = [
        block.split(CASE_END_DELIMITER)[0].strip()
        for block in raw_blocks
    ]

    # Filter out any empty blocks
    raw_cases = [c for c in raw_cases if c]

    logger.info(f"Split into {len(raw_cases)} case blocks")
    return raw_cases


# ─────────────────────────────────────────────────────────────
# STEP 4 — SUMMARY EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_summary(raw_case: str) -> str | None:
    """
    Extract the SUMMARY paragraph from a raw case block.

    The summary is the text between 'SUMMARY:' and 'METADATA:'.
    Multiple PDF line breaks are collapsed into a single space
    to produce a clean continuous paragraph.

    Args:
        raw_case: Raw text of a single case block.

    Returns:
        Clean summary paragraph string, or None if not found.
    """
    match = re.search(
        r"SUMMARY:\s*(.*?)\s*METADATA:",
        raw_case,
        re.DOTALL
    )

    if not match:
        return None

    summary = match.group(1).strip()

    # Collapse line breaks within the paragraph into single spaces.
    # PDF export wraps long lines — this undoes that wrapping.
    summary = re.sub(r"\s*\n\s*", " ", summary)

    # Collapse multiple spaces
    summary = re.sub(r" {2,}", " ", summary)

    return summary.strip()


# ─────────────────────────────────────────────────────────────
# STEP 5 — METADATA EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_metadata(raw_case: str) -> dict | None:
    """
    Extract and parse the METADATA JSON block from a raw case.

    Uses strict=False to allow newlines inside JSON string values —
    a known artifact of Google Docs PDF export where long field
    values (e.g. case_name) are wrapped across lines.

    After parsing, applies:
        - Article normalisation  (14A → 14(A))
        - Article expansion      (bare "13" → ["13(1)", "13(2)"])
        - Legal topic cleaning   (strip newline artifacts from tags)

    Args:
        raw_case: Raw text of a single case block.

    Returns:
        Parsed and cleaned metadata dict, or None if parsing fails.
    """
    # Find the JSON block — everything between the first { and last }
    json_match = re.search(r"\{.*\}", raw_case, re.DOTALL)
    if not json_match:
        return None

    try:
        # strict=False allows control characters (e.g. \n) inside
        # JSON string values — required for this corpus
        meta = json.loads(json_match.group(), strict=False)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {e}")
        return None

    # Apply post-parse cleaning
    meta = _normalise_articles(meta)
    meta = _clean_legal_topics(meta)

    return meta


def _normalise_articles(meta: dict) -> dict:
    """
    Normalise articles_cited field:
        1. Standardise non-canonical formats (14A → 14(A))
        2. Expand bare article numbers to include sub-articles
           where the sub-article is implied by context
           (e.g. bare "13" → ["13(1)", "13(2)"])

    Article 11 is intentionally NOT expanded — it has no sub-articles
    in the Sri Lankan Constitution.

    Args:
        meta: Parsed metadata dict.

    Returns:
        Metadata dict with normalised articles_cited.
    """
    articles = meta.get("articles_cited", [])
    if not isinstance(articles, list):
        return meta

    normalised = []
    for article in articles:
        article = article.strip()

        # Apply format normalisation (e.g. 14A → 14(A))
        article = ARTICLE_NORMALISATION.get(article, article)

        # Apply expansion for bare article numbers
        if article in ARTICLE_EXPANSION:
            expanded = ARTICLE_EXPANSION[article]
            logger.debug(f"Expanding bare '{article}' → {expanded}")
            normalised.extend(expanded)
        else:
            normalised.append(article)

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for a in normalised:
        if a not in seen:
            seen.add(a)
            deduped.append(a)

    meta["articles_cited"] = deduped
    return meta


def _clean_legal_topics(meta: dict) -> dict:
    """
    Clean legal_topic field by removing line break artifacts.

    PDF export occasionally inserts newlines inside topic strings
    e.g. 'fr\\needom_of_association' → 'freedom_of_association'

    Args:
        meta: Parsed metadata dict.

    Returns:
        Metadata dict with cleaned legal_topic list.
    """
    topics = meta.get("legal_topic", [])
    if not isinstance(topics, list):
        return meta

    cleaned = []
    for topic in topics:
        # Remove any newlines and strip whitespace
        topic_clean = topic.replace("\n", "").strip()
        if topic_clean:
            cleaned.append(topic_clean)

    meta["legal_topic"] = cleaned
    return meta


# ─────────────────────────────────────────────────────────────
# STEP 6 — CHROMADB METADATA PREPARATION
# ─────────────────────────────────────────────────────────────

ARTICLE_FLAG_FIELDS: dict[str, str] = {
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

def build_article_flag_metadata(articles: list[str]) -> dict[str, int]:
    """
    Build exact article metadata flags for ChromaDB filtering.

    Example:
        ["13(1)", "13(2)"]

    becomes:
        {
            "has_article_13_1": 1,
            "has_article_13_2": 1,
            ...
        }

    We use 1/0 integers instead of True/False to keep metadata simple
    and Chroma-friendly.
    """
    article_set = {
        str(article).strip()
        for article in articles
        if str(article).strip()
    }

    flags: dict[str, int] = {}

    for article, field_name in ARTICLE_FLAG_FIELDS.items():
        flags[field_name] = 1 if article in article_set else 0

    return flags

def prepare_chroma_metadata(meta: dict) -> dict:
    """
    Convert parsed metadata into ChromaDB-compatible metadata.

    Keeps articles_cited as a comma-separated string for display/audit,
    but also adds exact article flags for reliable Step 7 filtering.
    """
    articles = meta.get("articles_cited", [])
    topics = meta.get("legal_topic", [])

    if not isinstance(articles, list):
        articles = []

    if not isinstance(topics, list):
        topics = []

    chroma_metadata = {
        # Core string fields
        "case_id": str(meta.get("case_id", "")),
        "case_name": str(meta.get("case_name", "") or ""),
        "case_number": str(meta.get("case_number", "") or ""),
        "judgment": str(meta.get("judgment", "") or ""),

        # Core integer fields
        "year": int(meta.get("year") or 0),
        "word_count": int(meta.get("word_count") or 0),

        # Preserve original list-like metadata as strings
        "articles_cited": ",".join(str(a).strip() for a in articles if str(a).strip()),
        "legal_topic": ",".join(str(t).strip() for t in topics if str(t).strip()),
    }

    # Add exact filter flags:
    # has_article_13_1, has_article_13_2, etc.
    chroma_metadata.update(build_article_flag_metadata(articles))

    return chroma_metadata

# ─────────────────────────────────────────────────────────────
# MAIN PUBLIC FUNCTION
# ─────────────────────────────────────────────────────────────

def parse_pdf(pdf_path: str) -> list[dict]:
    """
    Full parsing pipeline — from PDF path to structured case list.

    Runs all steps in order:
        1. Extract text from PDF
        2. Clean all artifacts
        3. Split into individual case blocks
        4. Extract SUMMARY from each block
        5. Extract and parse METADATA from each block
        6. Prepare ChromaDB-compatible metadata

    Args:
        pdf_path: Path to Metadata_Final.pdf

    Returns:
        List of dicts, one per successfully parsed case:
        {
            "summary":        str,   clean paragraph for embedding
            "metadata":       dict,  original parsed metadata (Python types)
            "chroma_metadata": dict, ChromaDB-compatible metadata (str/int only)
            "position":       int,   1-based position in file
        }

        Cases that fail parsing are logged and excluded from output.
        A parsing report is printed on completion.
    """
    # Step 1 — Extract
    raw_text = extract_text_from_pdf(pdf_path)

    # Step 2 — Clean
    cleaned_text = clean_text(raw_text)

    # Step 3 — Split
    raw_cases = split_into_raw_cases(cleaned_text)

    # Steps 4-6 — Parse each case
    parsed_cases  = []
    failed_cases  = []

    for i, raw in enumerate(raw_cases):
        position = i + 1

        summary = extract_summary(raw)
        metadata = extract_metadata(raw)

        # Track failures with reasons
        if summary is None:
            failed_cases.append((position, "SUMMARY not found"))
            continue

        if metadata is None:
            failed_cases.append((position, "METADATA parse failed"))
            continue

        if not summary.strip():
            failed_cases.append((position, "SUMMARY is empty"))
            continue

        chroma_metadata = prepare_chroma_metadata(metadata)

        parsed_cases.append({
            "summary":         summary,
            "metadata":        metadata,
            "chroma_metadata": chroma_metadata,
            "position":        position,
        })

    # Parsing report
    _print_parsing_report(parsed_cases, failed_cases, len(raw_cases))

    return parsed_cases


# ─────────────────────────────────────────────────────────────
# INTERNAL — PARSING REPORT
# ─────────────────────────────────────────────────────────────

def _print_parsing_report(
    parsed: list[dict],
    failed: list[tuple],
    total: int
) -> None:
    """Print a summary report after parsing completes."""
    print("\n" + "=" * 55)
    print("PARSER REPORT")
    print("=" * 55)
    print(f"  Total case blocks found:  {total}")
    print(f"  Successfully parsed:      {len(parsed)}")
    print(f"  Failed / skipped:         {len(failed)}")

    if failed:
        print(f"\n  Failed cases (need investigation):")
        for position, reason in failed:
            print(f"    Position {position:>4}: {reason}")

    print("=" * 55 + "\n")
