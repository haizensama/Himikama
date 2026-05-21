"""
himikama/backend/ingestion/embedder.py
═══════════════════════════════════════════════════════════════
Module 3 — ChromaDB Embedder

Responsibility:
    Take the validated cases from validator.py and load them
    into ChromaDB. Creates two collections:

        1. case_summaries
           - Document: SUMMARY paragraph (gets embedded)
           - Metadata: all structured fields (used for filtering)

        2. constitutional_articles
           - Document: article text (gets embedded)
           - Metadata: article number, chapter
           - NOTE: articles are embedded manually as plain text
                   strings passed directly to this module.
                   They do not come from the PDF.

    This module is the ONLY place that touches ChromaDB.
    Parser and validator never write to the database.

Input:
    - List of validated case dicts from validator.py
    - Optional: list of constitutional article dicts (for
      constitutional_articles collection)

Output:
    - ChromaDB PersistentClient with both collections loaded
    - Printed loading report

Embedding Model:
    Uses BAAI/bge-small-en-v1.5 via
    SentenceTransformerEmbeddingFunction.

    Runs locally with no API key required.
    Optimized for semantic retrieval tasks and
    performs significantly better than the default
    MiniLM model for legal and formal text.

Usage:
    from ingestion.parser import parse_pdf
    from ingestion.validator import validate_cases
    from ingestion.embedder import embed_cases, get_client

    cases  = parse_pdf("data/Metadata_Final.pdf")
    report = validate_cases(cases)
    client = embed_cases(report.valid_cases, db_path="db/")
═══════════════════════════════════════════════════════════════
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

CASE_COLLECTION_NAME    = "case_summaries"
ARTICLE_COLLECTION_NAME = "constitutional_articles"
BATCH_SIZE              = 50  # cases loaded per ChromaDB upsert call


# ─────────────────────────────────────────────────────────────
# CLIENT INITIALISATION
# ─────────────────────────────────────────────────────────────

def get_client(db_path: str = "db/"):
    """
    Initialise and return a ChromaDB PersistentClient.

    PersistentClient stores the database on disk at db_path.
    Data survives between runs — safe to call multiple times.

    Args:
        db_path: Directory where ChromaDB stores its data.
                 Created automatically if it does not exist.

    Returns:
        chromadb.PersistentClient instance.

    Raises:
        ImportError: If chromadb is not installed.
    """
    try:
        import chromadb
    except ImportError:
        raise ImportError(
            "chromadb is not installed. Run: pip install chromadb"
        )

    Path(db_path).mkdir(parents=True, exist_ok=True)
    logger.info(f"ChromaDB initialised at: {db_path}")
    return chromadb.PersistentClient(path=db_path)


# ─────────────────────────────────────────────────────────────
# COLLECTION SETUP
# ─────────────────────────────────────────────────────────────

def _get_or_create_collection(client, name: str):
    """
    Get or create ChromaDB collection using BGE-small embeddings.
    """

    try:
        from chromadb.utils.embedding_functions import (
            SentenceTransformerEmbeddingFunction
        )
    except ImportError:
        raise ImportError(
            "chromadb/sentence-transformers missing. "
            "Run: pip install chromadb sentence-transformers"
        )

    # BAAI/bge-small-en-v1.5
    # Better semantic retrieval for legal text than MiniLM
    ef = SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-small-en-v1.5"
    )

    collection = client.get_or_create_collection(
        name=name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    logger.info(
        f"Collection '{name}' ready — "
        f"current size: {collection.count()} records"
    )

    return collection

# ─────────────────────────────────────────────────────────────
# CASE SUMMARY EMBEDDING
# ─────────────────────────────────────────────────────────────

def embed_cases(
    valid_cases: list[dict],
    db_path: str = "db/",
    reset: bool = False,
) -> object:
    """
    Load all validated cases into the case_summaries collection.

    Each case is stored as:
        document  = SUMMARY paragraph (natural language prose)
                    This is what gets embedded into a vector.
        metadata  = ChromaDB-compatible dict (str/int only)
                    Used for metadata filtering in Step 7.
        id        = "case_{case_id}" — unique identifier

    Uses upsert — safe to run multiple times. Existing records
    with the same id are overwritten, not duplicated.

    Args:
        valid_cases: List of validated case dicts from validator.py
        db_path:     Directory for ChromaDB storage.
        reset:       If True, delete and recreate the collection
                     before loading. Use when re-ingesting from scratch.

    Returns:
        The populated ChromaDB collection object.
    """
    client = get_client(db_path)

    if reset:
        _reset_collection(client, CASE_COLLECTION_NAME)
        logger.info(f"Collection '{CASE_COLLECTION_NAME}' reset.")

    collection = _get_or_create_collection(client, CASE_COLLECTION_NAME)

    total   = len(valid_cases)
    loaded  = 0
    skipped = 0

    print(f"\nLoading {total} cases into '{CASE_COLLECTION_NAME}'...")

    # Process in batches to avoid memory issues with large corpora
    for batch_start in range(0, total, BATCH_SIZE):
        batch = valid_cases[batch_start:batch_start + BATCH_SIZE]

        documents = []
        metadatas = []
        ids       = []

        for case in batch:
            summary        = case.get("summary", "")
            chroma_metadata = case.get("chroma_metadata", {})
            metadata        = case.get("metadata", {})
            case_id         = metadata.get("case_id", "")

            # Skip if summary or case_id missing
            if not summary or not case_id:
                skipped += 1
                logger.warning(
                    f"Skipping case at position {case.get('position')} "
                    f"— missing summary or case_id"
                )
                continue

            documents.append(summary)
            metadatas.append(chroma_metadata)
            ids.append(f"case_{case_id}")

        if documents:
            collection.upsert(
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )
            loaded += len(documents)

        batch_end = min(batch_start + BATCH_SIZE, total)
        print(f"  Embedded {batch_end}/{total} cases...")

    _print_loading_report(
        collection_name=CASE_COLLECTION_NAME,
        total_input=total,
        loaded=loaded,
        skipped=skipped,
        final_count=collection.count(),
    )

    return collection


# ─────────────────────────────────────────────────────────────
# CONSTITUTIONAL ARTICLES EMBEDDING
# ─────────────────────────────────────────────────────────────

def embed_articles(
    articles: list[dict],
    db_path: str = "db/",
    reset: bool = False,
) -> object:
    """
    Load constitutional articles into the constitutional_articles
    collection.

    Articles are NOT read from the PDF — they must be passed
    in as a list of dicts. The constitutional article text
    is prepared separately (see note below).

    Each article is stored as:
        document = Full article text (natural language)
                   This is what gets embedded.
        metadata = {
            "article_number": "13(1)",
            "chapter":        "3",
            "heading":        "Freedom from arbitrary arrest"
        }
        id = "article_13_1" (normalised article number)

    NOTE ON PREPARING ARTICLES:
        The constitutional articles for Chapter 3 need to be
        typed out as plain text strings — one per article/
        sub-article. This is a one-time manual task.
        See the ARTICLE FORMAT section below for the expected
        dict structure.

    ARTICLE FORMAT:
        {
            "article_number": "13(1)",
            "chapter":        "3",
            "heading":        "Freedom from arbitrary arrest",
            "text":           "Every person is entitled to..."
        }

    Args:
        articles: List of article dicts in the format above.
        db_path:  Directory for ChromaDB storage.
        reset:    If True, delete and recreate the collection.

    Returns:
        The populated ChromaDB collection object.
    """
    client = get_client(db_path)

    if reset:
        _reset_collection(client, ARTICLE_COLLECTION_NAME)
        logger.info(f"Collection '{ARTICLE_COLLECTION_NAME}' reset.")

    collection = _get_or_create_collection(
        client, ARTICLE_COLLECTION_NAME
    )

    total   = len(articles)
    loaded  = 0
    skipped = 0

    print(f"\nLoading {total} articles into "
          f"'{ARTICLE_COLLECTION_NAME}'...")

    documents = []
    metadatas = []
    ids       = []

    for article in articles:
        article_number = article.get("article_number", "")
        text           = article.get("text", "")
        heading        = article.get("heading", "")
        chapter        = article.get("chapter", "3")

        if not article_number or not text:
            skipped += 1
            logger.warning(
                f"Skipping article — missing article_number or text: "
                f"{article}"
            )
            continue

        # Build the document string — heading + full text
        # Including the heading improves semantic matching
        document = f"{heading}. {text}" if heading else text

        # Build ChromaDB-compatible metadata
        metadata = {
            "article_number": str(article_number),
            "chapter":        str(chapter),
            "heading":        str(heading),
        }

        # Normalise article number for use as ID
        # e.g. "13(1)" → "article_13_1"
        article_id = (
            "article_"
            + article_number
            .replace("(", "_")
            .replace(")", "")
            .replace(" ", "_")
        )

        documents.append(document)
        metadatas.append(metadata)
        ids.append(article_id)
        loaded += 1

    if documents:
        collection.upsert(
            documents=documents,
            metadatas=metadatas,
            ids=ids,
        )

    _print_loading_report(
        collection_name=ARTICLE_COLLECTION_NAME,
        total_input=total,
        loaded=loaded,
        skipped=skipped,
        final_count=collection.count(),
    )

    return collection


# ─────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────

def _reset_collection(client, name: str) -> None:
    """
    Delete a collection if it exists.
    Used when re-ingesting from scratch.

    Args:
        client: ChromaDB PersistentClient.
        name:   Collection name to delete.
    """
    try:
        client.delete_collection(name)
        logger.info(f"Deleted existing collection: '{name}'")
    except Exception:
        # Collection did not exist — nothing to delete
        pass


def get_case_collection(db_path: str = "db/"):
    """
    Get the case_summaries collection from an existing database.
    Used by retrieval modules — does not create or modify data.

    Args:
        db_path: Directory where ChromaDB is stored.

    Returns:
        ChromaDB Collection object for case_summaries.

    Raises:
        Exception: If collection does not exist.
    """
    client = get_client(db_path)
    return _get_or_create_collection(client, CASE_COLLECTION_NAME)


def get_article_collection(db_path: str = "db/"):
    """
    Get the constitutional_articles collection from an existing
    database. Used by retrieval modules.

    Args:
        db_path: Directory where ChromaDB is stored.

    Returns:
        ChromaDB Collection object for constitutional_articles.

    Raises:
        Exception: If collection does not exist.
    """
    client = get_client(db_path)
    return _get_or_create_collection(client, ARTICLE_COLLECTION_NAME)


# ─────────────────────────────────────────────────────────────
# INTERNAL — LOADING REPORT
# ─────────────────────────────────────────────────────────────

def _print_loading_report(
    collection_name: str,
    total_input: int,
    loaded: int,
    skipped: int,
    final_count: int,
) -> None:
    """Print a summary after loading a collection."""
    print("\n" + "=" * 55)
    print(f"EMBEDDER REPORT — {collection_name}")
    print("=" * 55)
    print(f"  Input records:    {total_input}")
    print(f"  Successfully loaded: {loaded}")
    print(f"  Skipped:          {skipped}")
    print(f"  Collection size:  {final_count}")
    print("=" * 55 + "\n")
