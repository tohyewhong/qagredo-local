"""LangGraph-based per-document orchestration for QAG.

This module wraps generation/grading functions into a per-document state graph
with conditional routing and fallback grading.
"""

from __future__ import annotations

import functools
import time
from typing import Any, Dict, Tuple, TypedDict

from . import (
    generate_answers_from_results,
    generate_questions,
    grade_qa_results,
)


class DocumentState(TypedDict, total=False):
    document: Dict[str, Any]
    config: Dict[str, Any]
    halluc_method: str
    question_result: Dict[str, Any] | None
    qa_result: Dict[str, Any] | None
    grading_result: Dict[str, Any] | None
    skip_reason: str | None
    timings: Dict[str, float]


@functools.lru_cache(maxsize=1)
def _langgraph_probe() -> Tuple[bool, str]:
    try:
        import langgraph  # noqa: F401
    except BaseException as e:
        return False, f"{type(e).__name__}: {e}"
    return True, ""


def is_langgraph_available() -> bool:
    return _langgraph_probe()[0]


def langgraph_import_error() -> str:
    ok, err = _langgraph_probe()
    return "" if ok else err


def run_document_graph(
    *,
    document: Dict[str, Any],
    config: Dict[str, Any],
    halluc_method: str,
) -> Dict[str, Any]:
    """Run one document through a LangGraph state graph.

    If LangGraph is unavailable, this function raises RuntimeError and callers
    should use the legacy direct loop.
    """
    if not is_langgraph_available():
        raise RuntimeError("LangGraph is not available in this environment.")

    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(DocumentState)

    graph.add_node("generate_questions", _node_generate_questions)
    graph.add_node("generate_answers", _node_generate_answers)
    graph.add_node("grade_primary", _node_grade_primary)
    graph.add_node("grade_fallback_llm", _node_grade_fallback_llm)

    graph.add_edge(START, "generate_questions")
    graph.add_conditional_edges(
        "generate_questions",
        _route_after_questions,
        {"generate_answers": "generate_answers", "end": END},
    )
    graph.add_conditional_edges(
        "generate_answers",
        _route_after_answers,
        {"grade_primary": "grade_primary", "end": END},
    )
    graph.add_conditional_edges(
        "grade_primary",
        _route_after_primary_grade,
        {"grade_fallback_llm": "grade_fallback_llm", "end": END},
    )
    graph.add_edge("grade_fallback_llm", END)

    app = graph.compile()
    initial: DocumentState = {
        "document": document,
        "config": config,
        "halluc_method": halluc_method,
        "question_result": None,
        "qa_result": None,
        "grading_result": None,
        "skip_reason": None,
        "timings": {},
    }
    return app.invoke(initial)


def _node_generate_questions(state: DocumentState) -> DocumentState:
    start = time.time()
    question_results = generate_questions(
        [state["document"]],
        config=state["config"],
    )
    elapsed = time.time() - start
    next_state: DocumentState = {
        "timings": {
            **state.get("timings", {}),
            "question_generation": elapsed,
        }
    }
    if not question_results:
        next_state["skip_reason"] = "no_questions_generated"
        next_state["question_result"] = None
        return next_state
    next_state["question_result"] = question_results[0]
    return next_state


def _node_generate_answers(state: DocumentState) -> DocumentState:
    question_result = state.get("question_result")
    if not question_result:
        return {"skip_reason": "no_question_result"}
    start = time.time()
    qa_results = generate_answers_from_results(
        [question_result],
        config=state["config"],
    )
    elapsed = time.time() - start
    next_state: DocumentState = {
        "timings": {
            **state.get("timings", {}),
            "answer_generation": elapsed,
        }
    }
    if not qa_results:
        next_state["skip_reason"] = "no_answers_generated"
        next_state["qa_result"] = None
        return next_state
    next_state["qa_result"] = qa_results[0]
    return next_state


def _node_grade_primary(state: DocumentState) -> DocumentState:
    qa_result = state.get("qa_result")
    if not qa_result:
        return {"skip_reason": "no_qa_result"}
    method = state.get("halluc_method", "hybrid")
    start = time.time()
    graded = grade_qa_results([qa_result], method=method)
    elapsed = time.time() - start
    result = graded[0] if graded else None
    return {
        "grading_result": result,
        "timings": {**state.get("timings", {}), "grading": elapsed},
    }


def _node_grade_fallback_llm(state: DocumentState) -> DocumentState:
    """Escalate to llm judge if semantic-only confidence is low."""
    qa_result = state.get("qa_result")
    if not qa_result:
        return {}
    start = time.time()
    graded = grade_qa_results([qa_result], method="llm")
    elapsed = time.time() - start
    result = graded[0] if graded else None
    if result:
        result["grading_method"] = "semantic->llm_fallback"
    return {
        "grading_result": result,
        "timings": {
            **state.get("timings", {}),
            "grading_fallback_llm": elapsed,
        },
    }


def _route_after_questions(state: DocumentState) -> str:
    return "end" if state.get("skip_reason") else "generate_answers"


def _route_after_answers(state: DocumentState) -> str:
    return "end" if state.get("skip_reason") else "grade_primary"


def _route_after_primary_grade(state: DocumentState) -> str:
    """Route to fallback only for semantic runs with low confidence."""
    cfg = state.get("config", {})
    framework_cfg = (
        (cfg.get("framework") or {}) if isinstance(cfg, dict) else {}
    )
    lg_cfg = (
        (framework_cfg.get("langgraph") or {})
        if isinstance(framework_cfg, dict)
        else {}
    )
    enable_routing = bool(lg_cfg.get("enable_dynamic_routing", True))
    try:
        threshold = float(lg_cfg.get("semantic_fallback_threshold", 0.7))
    except (TypeError, ValueError):
        threshold = 0.7

    if not enable_routing:
        return "end"
    if state.get("halluc_method") != "semantic":
        return "end"
    grading = state.get("grading_result") or {}
    try:
        overall_conf = float(grading.get("overall_confidence", 0.0))
    except (TypeError, ValueError):
        overall_conf = 0.0
    if overall_conf < threshold:
        return "grade_fallback_llm"
    return "end"
