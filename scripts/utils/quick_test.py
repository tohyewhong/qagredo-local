import os
import sys
from pathlib import Path

os.environ['PYDANTIC_DISABLE_PLUGIN_LOADING'] = '1'

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.result_analyzer import evaluate_document_quality

# Test case 1: Should be "needs_attention"
doc1 = {
    "document": {"id": "test1"},
    "qa_pairs": [
        {"question": "Q1", "answer": "A" * 100, "grading": {"confidence": 0.9, "is_grounded": True}},
        {"question": "Q2", "answer": "A" * 100, "grading": {"confidence": 0.6, "is_grounded": True}},
        {"question": "Q3", "answer": "A" * 100, "grading": {"confidence": 0.4, "is_grounded": True}},
    ],
    "grading_summary": {"overall_confidence": 0.9},
}
result1 = evaluate_document_quality(doc1)
print(f"Test 1 [0.9, 0.6, 0.4]: {result1['quality_band']} (expected: needs_attention)")
print(f"  Mean: {result1['overall_confidence']:.3f}, Warnings: {len(result1['warnings'])}")

# Test case 2: Should be "review"
doc2 = {
    "document": {"id": "test2"},
    "qa_pairs": [
        {"question": "Q1", "answer": "A" * 100, "grading": {"confidence": 0.7, "is_grounded": True}},
        {"question": "Q2", "answer": "A" * 100, "grading": {"confidence": 0.72, "is_grounded": True}},
        {"question": "Q3", "answer": "A" * 100, "grading": {"confidence": 0.74, "is_grounded": True}},
    ],
    "grading_summary": {"overall_confidence": 0.74},
}
from utils.result_analyzer import DEFAULT_THRESHOLDS
strict = {**DEFAULT_THRESHOLDS, "review_confidence": 0.95}
result2 = evaluate_document_quality(doc2, thresholds=strict)
print(f"Test 2 [0.7, 0.72, 0.74] (review_thresh=0.95): {result2['quality_band']} (expected: review)")
print(f"  Mean: {result2['overall_confidence']:.3f}, Warnings: {len(result2['warnings'])}")
