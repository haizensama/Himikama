"""
himikama/backend/evaluation/preprocess_outputs.py
═══════════════════════════════════════════════════════════════
Pre-process evaluation outputs.

Main use:
    Extract constitutional article numbers from ONLY:

        SECTION 1 — RIGHTS ASSESSMENT

    in single_shot_rag outputs.

Strict extraction rule:
    Extract only article headings that follow this pattern:

        Article 12(1) —
        Article 13(1) -
        Article 14(1)(g) —
        Article 11 —

Why:
    This avoids accidentally extracting article mentions from sentences like:

        Article 12(2), Freedom from discrimination, does not appear...

Reads:
    evaluation/outputs/single_shot_rag_outputs.jsonl

Writes:
    evaluation/outputs/single_shot_rag_outputs_extracted.jsonl

Usage:
    python -m evaluation.preprocess_outputs
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("evaluation/outputs/single_shot_rag_outputs.jsonl")
DEFAULT_OUTPUT = Path("evaluation/outputs/single_shot_rag_outputs_extracted.jsonl")


ALLOWED_ARTICLES = {
    "10", "11",
    "12(1)", "12(2)", "12(3)", "12(4)",
    "13(1)", "13(2)", "13(3)", "13(4)", "13(5)", "13(6)", "13(7)",
    "14(1)(a)", "14(1)(b)", "14(1)(c)", "14(1)(d)",
    "14(1)(e)", "14(1)(f)", "14(1)(g)", "14(1)(h)",
    "14(A)",
    "15(1)", "15(2)", "15(3)", "15(4)", "15(5)", "15(6)", "15(7)",
    "16", "17",
    "126", "126(2)",
}


# Extract only heading-like article declarations, for example:
#   Article 13(1) — Freedom from arbitrary arrest
#   Article 13(2) - Right to be produced before a judge
#   Article 11 — Freedom from torture...
#   Article 14(1)(g) — Freedom to engage in occupation
#
# It deliberately does NOT match:
#   Article 12(2), Freedom from discrimination, does not appear...
# because that uses a comma, not a dash heading.
ARTICLE_HEADING_PATTERN = re.compile(
    r"""
    ^\s*
    (?:[-*]\s*)?                              # optional bullet
    (?:\d+\.\s*)?                             # optional numbered list item
    \**                                       # optional markdown bold opening
    Article\s+
    (
        14\s*\(\s*A\s*\)                                      |   # Article 14(A)
        14A                                                    |   # Article 14A
        \d{1,3}\s*\(\s*\d+\s*\)\s*\(\s*[a-hA-H]\s*\)          |   # Article 14(1)(g)
        \d{1,3}\s*\(\s*\d+\s*\)                               |   # Article 13(1)
        \d{1,3}                                                   # Article 11
    )
    \**                                       # optional markdown bold closing
    \s*
    [—–-]                                     # em dash, en dash, or hyphen
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)


SECTION_1_PATTERN = re.compile(
    r"""
    SECTION\s*1
    \s*[—\-:]*\s*
    RIGHTS\s+ASSESSMENT
    (?P<section_text>.*?)
    (?=
        SECTION\s*2
        \s*[—\-:]*\s*
        PRECEDENT
        |
        \Z
    )
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    for line_no, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue

        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSONL at {path}, line {line_no}: {e}"
            ) from e

    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_article(raw: str) -> str | None:
    """
    Normalize article formats.

    Examples:
        13 ( 1 )  -> 13(1)
        14A       -> 14(A)
        14(1)(G)  -> 14(1)(g)
    """
    article = raw.strip()
    article = re.sub(r"\s+", "", article)

    if article.upper() == "14A":
        article = "14(A)"

    if article.upper() == "14(A)":
        article = "14(A)"

    match = re.match(r"^(\d+)\((\d+)\)\(([A-Za-z])\)$", article)
    if match:
        article = (
            f"{match.group(1)}"
            f"({match.group(2)})"
            f"({match.group(3).lower()})"
        )

    if article in ALLOWED_ARTICLES:
        return article

    return None


def remove_bare_articles_when_subarticles_exist(
    articles: list[str],
) -> list[str]:
    """
    If 13 and 13(1) both appear, keep only the specific sub-article.

    Example:
        ["13", "13(1)", "13(2)"] -> ["13(1)", "13(2)"]
    """
    article_set = set(articles)

    for base in ["12", "13", "14", "15"]:
        has_subarticle = any(
            article.startswith(base + "(")
            for article in article_set
        )

        if has_subarticle and base in article_set:
            article_set.remove(base)

    return sorted(article_set)


def extract_section_1_rights_assessment(answer_text: str) -> tuple[str, bool]:
    """
    Extract only SECTION 1 — RIGHTS ASSESSMENT.

    Returns:
        (section_text, found_section)

    If SECTION 1 is not found, returns ("", False).
    """
    if not answer_text:
        return "", False

    match = SECTION_1_PATTERN.search(answer_text)

    if not match:
        return "", False

    return match.group("section_text").strip(), True


def extract_articles_from_text(text: str) -> list[str]:
    """
    Extract only article heading declarations inside Section 1.

    Valid examples:
        Article 12(1) —
        Article 13(1) -
        Article 11 —
        **Article 13(2) — Right to be produced before a judge:**

    Invalid examples:
        Article 12(2), Freedom from discrimination, does not appear...
        Article 13(3) may be considered but is weak...
    """
    found: list[str] = []

    for match in ARTICLE_HEADING_PATTERN.finditer(text or ""):
        normalized = normalize_article(match.group(1))

        if normalized:
            found.append(normalized)

    return remove_bare_articles_when_subarticles_exist(found)


def extract_answer_text(row: dict[str, Any]) -> str:
    """
    Collect generated answer text from the normalized output row.
    """
    parts: list[str] = []

    for key in ["final_answer", "final_answer_with_disclaimer"]:
        value = row.get(key)
        if isinstance(value, str):
            parts.append(value)

    step_results = row.get("step_results", {})
    if isinstance(step_results, dict):
        single_shot = step_results.get("single_shot", {})
        if isinstance(single_shot, dict):
            answer = single_shot.get("answer")
            if isinstance(answer, str):
                parts.append(answer)

    return "\n".join(parts)


def process_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    processed: list[dict[str, Any]] = []

    for row in rows:
        copied = dict(row)

        answer_text = extract_answer_text(copied)

        section_1_text, found_section_1 = extract_section_1_rights_assessment(
            answer_text
        )

        if found_section_1:
            extracted_articles = extract_articles_from_text(section_1_text)
            extraction_scope = "section_1_article_heading_dash_only"
        else:
            extracted_articles = []
            extraction_scope = "section_1_not_found_no_extraction"

        copied["articles_identified_original"] = copied.get(
            "articles_identified",
            [],
        )
        copied["articles_identified"] = extracted_articles
        copied["article_extraction_method"] = "strict_regex_article_dash_heading"
        copied["article_extraction_scope"] = extraction_scope
        copied["section_1_found"] = found_section_1
        copied["section_1_rights_assessment_text"] = section_1_text

        processed.append(copied)

    return processed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract article numbers from strict Article X — headings "
            "inside SECTION 1 — RIGHTS ASSESSMENT."
        )
    )

    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Input JSONL file.",
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSONL file.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    rows = load_jsonl(input_path)
    processed = process_rows(rows)
    write_jsonl(output_path, processed)

    print(f"Read {len(rows)} row(s) from {input_path}")
    print(f"Wrote {len(processed)} row(s) to {output_path}")

    print("\nPreview:")
    for row in processed[:10]:
        print(
            row.get("scenario_id"),
            "| section_1_found:",
            row.get("section_1_found"),
            "| articles:",
            row.get("articles_identified", []),
        )


if __name__ == "__main__":
    main()
