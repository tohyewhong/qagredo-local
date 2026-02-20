"""
Duplicate question detection utility.

This module provides functions to detect and filter duplicate or similar questions
using text similarity algorithms.
"""

from typing import List, Tuple, Dict


def normalize_text(text: str) -> str:
    normalized = (text or "").lower()
    contractions = {
        "'s": " is",
        "'re": " are",
        "'ve": " have",
        "'ll": " will",
        "'d": " would",
        "'m": " am",
        "n't": " not",
        "'t": " not",
    }
    for contraction, expansion in contractions.items():
        normalized = normalized.replace(contraction, expansion)
    normalized = " ".join(normalized.split())
    normalized = "".join(c for c in normalized if c.isalnum() or c.isspace())
    return normalized


def calculate_jaccard_similarity(text1: str, text2: str) -> float:
    words1 = set(normalize_text(text1).split())
    words2 = set(normalize_text(text2).split())
    if not words1 and not words2:
        return 1.0
    if not words1 or not words2:
        return 0.0
    intersection = len(words1 & words2)
    union = len(words1 | words2)
    return intersection / union if union > 0 else 0.0


def calculate_semantic_similarity(
    text1: str,
    text2: str,
    model_name: str = "all-MiniLM-L6-v2",
) -> float:
    try:
        from sentence_transformers import SentenceTransformer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return -1.0

    try:
        import os

        if not hasattr(calculate_semantic_similarity, "_model_cache"):
            calculate_semantic_similarity._model_cache = {}

        offline_mode = os.getenv("OFFLINE_MODE", "").lower() in ("1", "true", "yes", "on")
        if offline_mode:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        else:
            os.environ.setdefault("HF_HUB_OFFLINE", "0")

        local_model_path = os.getenv("SENTENCE_TRANSFORMERS_MODEL_PATH", "").strip()

        cache: Dict[str, SentenceTransformer] = calculate_semantic_similarity._model_cache
        if model_name not in cache:
            if offline_mode and local_model_path and os.path.isdir(local_model_path):
                cache[model_name] = SentenceTransformer(local_model_path, device="cpu")
            else:
                cache[model_name] = SentenceTransformer(model_name, device="cpu")

        model = cache[model_name]
        embeddings = model.encode([text1, text2])
        similarity = cosine_similarity([embeddings[0]], [embeddings[1]])[0][0]
        return float(max(0.0, similarity))
    except Exception:
        return -1.0


def is_duplicate(
    question1: str,
    question2: str,
    similarity_threshold: float = 0.85,
    exact_match: bool = True,
    method: str = "semantic",
) -> bool:
    if exact_match:
        if normalize_text(question1) == normalize_text(question2):
            return True

    if method in ("semantic", "both"):
        semantic_sim = calculate_semantic_similarity(question1, question2)
        if semantic_sim >= 0:
            if method == "both":
                jaccard_sim = calculate_jaccard_similarity(question1, question2)
                return semantic_sim >= similarity_threshold or jaccard_sim >= similarity_threshold
            return semantic_sim >= similarity_threshold
        if method == "semantic":
            jaccard_sim = calculate_jaccard_similarity(question1, question2)
            return jaccard_sim >= similarity_threshold

    similarity = calculate_jaccard_similarity(question1, question2)
    return similarity >= similarity_threshold


def detect_duplicate_questions(
    questions: List[str],
    similarity_threshold: float = 0.85,
    exact_match: bool = True,
    method: str = "semantic",
) -> Tuple[List[str], List[int]]:
    if len(questions) <= 1:
        return questions, []

    parent = list(range(len(questions)))

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in range(len(questions)):
        for j in range(i + 1, len(questions)):
            if is_duplicate(questions[i], questions[j], similarity_threshold, exact_match, method):
                union(i, j)

    clusters: Dict[int, List[int]] = {}
    for idx in range(len(questions)):
        root = find(idx)
        clusters.setdefault(root, []).append(idx)

    unique_questions: List[str] = []
    duplicate_indices: List[int] = []
    for cluster_indices in clusters.values():
        cluster_indices.sort()
        unique_questions.append(questions[cluster_indices[0]])
        duplicate_indices.extend(cluster_indices[1:])
    duplicate_indices.sort()
    return unique_questions, duplicate_indices


def filter_duplicates_from_new_questions(
    existing_questions: List[str],
    new_questions: List[str],
    similarity_threshold: float = 0.85,
    method: str = "semantic",
) -> List[str]:
    if not existing_questions:
        unique_new, _ = detect_duplicate_questions(new_questions, similarity_threshold, method=method)
        return unique_new
    if not new_questions:
        return []

    combined = existing_questions + new_questions
    unique_combined, _ = detect_duplicate_questions(combined, similarity_threshold, method=method)

    existing_set = set(normalize_text(q) for q in existing_questions)
    filtered: List[str] = []
    for q in unique_combined:
        if normalize_text(q) not in existing_set:
            filtered.append(q)
    return filtered

