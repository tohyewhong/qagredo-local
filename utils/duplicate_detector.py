"""
Duplicate question detection using lexical similarity (Jaccard).

Embedding-based similarity was removed from QAG; use ``deduplication_method:
\"llm\"`` in config for semantic duplicate judgment via the generator LLM.
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


def is_duplicate(
    question1: str,
    question2: str,
    similarity_threshold: float = 0.85,
    exact_match: bool = True,
    method: str = "jaccard",
) -> bool:
    """Return True if two questions are duplicates.

    ``method``:
      - ``jaccard`` (default): Jaccard similarity on token sets
      - ``both``: Jaccard OR exact normalized match after exact_match pass
    Legacy values ``semantic`` / ``embedding`` are treated as ``jaccard``.
    """
    legacy = str(method or "jaccard").lower()
    if legacy in ("semantic", "embedding", "both"):
        method = "jaccard" if legacy != "both" else "both"

    if exact_match:
        if normalize_text(question1) == normalize_text(question2):
            return True

    jaccard_sim = calculate_jaccard_similarity(question1, question2)
    if method == "both":
        return jaccard_sim >= similarity_threshold
    return jaccard_sim >= similarity_threshold


def detect_duplicate_questions(
    questions: List[str],
    similarity_threshold: float = 0.85,
    exact_match: bool = True,
    method: str = "jaccard",
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
            if is_duplicate(
                questions[i],
                questions[j],
                similarity_threshold,
                exact_match,
                method,
            ):
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
    method: str = "jaccard",
) -> List[str]:
    if not existing_questions:
        unique_new, _ = detect_duplicate_questions(
            new_questions, similarity_threshold, method=method
        )
        return unique_new
    if not new_questions:
        return []

    combined = existing_questions + new_questions
    unique_combined, _ = detect_duplicate_questions(
        combined, similarity_threshold, method=method
    )

    existing_set = set(normalize_text(q) for q in existing_questions)
    filtered: List[str] = []
    for q in unique_combined:
        if normalize_text(q) not in existing_set:
            filtered.append(q)
    return filtered
