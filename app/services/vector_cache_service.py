import json
import logging
from typing import Dict, Any, Optional

# We wrap import in a try block just in case chromadb takes a second to install
try:
    import chromadb
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    chromadb = None  # Pyright fix: ensure it's always bound

logger = logging.getLogger(__name__)

# Initialize ChromaDB Persistent Client (saves to ./.chroma directory)
if CHROMA_AVAILABLE and chromadb is not None:
    try:
        chroma_client = chromadb.PersistentClient(path="./.chroma")
        # get_or_create prevents errors if it already exists
        collection = chroma_client.get_or_create_collection(name="semantic_cache")
        logger.info("ChromaDB initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize ChromaDB: {e}")
        CHROMA_AVAILABLE = False


def get_semantic_cache(repo_url: str, question: str) -> Optional[Dict[str, Any]]:
    """
    Checks if a semantically identical question has been asked for this repo.
    """
    if not CHROMA_AVAILABLE:
        logger.warning("ChromaDB not available. Skipping semantic cache check.")
        return None

    try:
        # We query the vector database for the closest matching question
        # We filter specifically by repo_url so we don't mix up answers from different repos!
        results = collection.query(
            query_texts=[question],
            n_results=1,
            where={"repo_url": repo_url}
        )

        documents = results.get("documents")
        if not documents or len(documents) == 0 or not documents[0] or len(documents[0]) == 0:
            logger.info(f"Semantic Cache Miss: No questions found for {repo_url}")
            return None

        distances = results.get("distances")
        if not distances or len(distances) == 0 or not distances[0] or len(distances[0]) == 0:
            return None

        distance = distances[0][0]
        matched_question = documents[0][0]

        logger.info(f"Semantic Cache Search: Closest match is '{matched_question}' with distance {distance}")

        if distance < 0.2:
            logger.info("Semantic Cache HIT! Question is semantically identical.")
            metadatas = results.get("metadatas")
            if not metadatas or not metadatas[0] or not metadatas[0][0]:
                return None

            metadata = metadatas[0][0]
            # Pyright fix: Ensure metadata is a dict before accessing
            if isinstance(metadata, dict):
                answer_json = metadata.get("answer_data")
                if isinstance(answer_json, str):
                    return json.loads(answer_json)

        logger.info("Semantic Cache Miss: Closest match was not similar enough.")
        return None

    except Exception as e:
        logger.error(f"Error checking semantic cache: {e}")
        return None


def set_semantic_cache(repo_url: str, question: str, answer_data: Dict[str, Any]) -> None:
    """
    Embeds the question and stores the answer in ChromaDB.
    """
    if not CHROMA_AVAILABLE:
        return

    try:
        # We need a unique ID for Chroma. We can just hash the repo_url + question
        import hashlib
        doc_id = hashlib.md5(f"{repo_url}:{question}".encode()).hexdigest()

        # Chroma's default embedding function automatically embeds the document string!
        collection.add(
            documents=[question],
            metadatas=[{
                "repo_url": repo_url,
                "answer_data": json.dumps(answer_data)
            }],
            ids=[doc_id]
        )
        logger.info(f"Saved to Semantic Cache (ChromaDB): '{question}'")

    except Exception as e:
        logger.error(f"Error saving to semantic cache: {e}")
