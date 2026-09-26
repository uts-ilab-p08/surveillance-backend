"""
POST /assistant/ask and POST /assistant/suggestions (frontend spec
§4.2/§4.3). rag.llm.complete() is stubbed throughout — these tests are
about this backend's own validation, prompt-building, citation parsing
and rule-based suggestions, not about a real LLM or OpenRouter.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user
from app.main import app
from app.services import assistant

client = TestClient(app)
app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=uuid.uuid4(), email="test@example.com")


def _moment(**overrides) -> dict:
    moment = {
        "moment_id": "v1:28",
        "video_id": "v1",
        "start_seconds": 28,
        "end_seconds": 35,
        "caption": "A white car pulls into the parking area.",
        "score": 0.79,
        "camera": None,
    }
    moment.update(overrides)
    return moment


def _ask_body(**overrides) -> dict:
    body = {
        "query": "car parked in the parking area",
        "question": "Show only the highest-confidence event",
        "scope": "results",
        "focus_moment_id": None,
        "moments": [_moment()],
        "history": [],
    }
    body.update(overrides)
    return body


# --- POST /assistant/ask ----------------------------------------------------


def test_ask_returns_answer_and_valid_citations():
    raw_llm_reply = (
        "The white car is the only vehicle event and has the highest score.\n"
        "CITED: v1:28"
    )
    with patch.object(assistant.llm, "complete", return_value=raw_llm_reply):
        resp = client.post("/api/v1/assistant/ask", json=_ask_body())

    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "The white car is the only vehicle event and has the highest score."
    assert data["citations"] == [{"moment_id": "v1:28"}]
    assert 2 <= len(data["suggested_questions"]) <= 4


def test_ask_drops_citations_not_in_request():
    raw_llm_reply = "Only one event matches.\nCITED: v1:28, some-hallucinated-id"
    with patch.object(assistant.llm, "complete", return_value=raw_llm_reply):
        resp = client.post("/api/v1/assistant/ask", json=_ask_body())

    assert resp.status_code == 200
    assert resp.json()["citations"] == [{"moment_id": "v1:28"}]


def test_ask_scope_moment_requires_focus_moment_id():
    body = _ask_body(scope="moment", focus_moment_id=None)
    resp = client.post("/api/v1/assistant/ask", json=body)
    assert resp.status_code == 422


def test_ask_scope_moment_rejects_unknown_focus_moment_id():
    body = _ask_body(scope="moment", focus_moment_id="not-a-real-moment")
    resp = client.post("/api/v1/assistant/ask", json=body)
    assert resp.status_code == 422


def test_ask_scope_moment_accepts_matching_focus_moment_id():
    body = _ask_body(scope="moment", focus_moment_id="v1:28", question="What happened here?")
    with patch.object(assistant.llm, "complete", return_value="A car arrives.\nCITED: v1:28"):
        resp = client.post("/api/v1/assistant/ask", json=body)
    assert resp.status_code == 200


def test_ask_returns_502_when_llm_unavailable():
    with patch.object(assistant.llm, "complete", side_effect=assistant.llm.LLMError("boom")):
        resp = client.post("/api/v1/assistant/ask", json=_ask_body())
    assert resp.status_code == 502
    assert resp.json() == {"detail": "The assistant couldn't answer that. Try again."}


def test_ask_requires_auth():
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = client.post("/api/v1/assistant/ask", json=_ask_body())
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=uuid.uuid4(), email="test@example.com")


# --- POST /assistant/suggestions -------------------------------------------


def test_suggestions_scope_results_offers_vehicle_question_when_data_supports_it():
    resp = client.post(
        "/api/v1/assistant/suggestions",
        json={
            "query": "car parked in the parking area",
            "scope": "results",
            "focus_moment_id": None,
            "moments": [_moment()],
            "history": [],
        },
    )
    assert resp.status_code == 200
    questions = resp.json()["suggested_questions"]
    assert "Narrow this to vehicle events only" in questions
    assert "Narrow this to person events only" not in questions


def test_suggestions_scope_results_skips_camera_question_with_one_camera():
    resp = client.post(
        "/api/v1/assistant/suggestions",
        json={
            "query": "q",
            "scope": "results",
            "focus_moment_id": None,
            "moments": [_moment(camera="G336"), _moment(moment_id="v1:50", camera="G336")],
            "history": [],
        },
    )
    assert resp.status_code == 200
    assert "Which camera has the most matches?" not in resp.json()["suggested_questions"]


def test_suggestions_scope_results_offers_camera_question_with_multiple_cameras():
    resp = client.post(
        "/api/v1/assistant/suggestions",
        json={
            "query": "q",
            "scope": "results",
            "focus_moment_id": None,
            "moments": [_moment(camera="G336"), _moment(moment_id="v1:50", camera="G419")],
            "history": [],
        },
    )
    assert resp.status_code == 200
    assert "Which camera has the most matches?" in resp.json()["suggested_questions"]


def test_suggestions_never_repeats_a_question_already_in_history():
    resp = client.post(
        "/api/v1/assistant/suggestions",
        json={
            "query": "q",
            "scope": "results",
            "focus_moment_id": None,
            "moments": [_moment()],
            "history": [{"role": "user", "text": "Show only the highest-confidence event"}],
        },
    )
    assert resp.status_code == 200
    assert "Show only the highest-confidence event" not in resp.json()["suggested_questions"]


def test_suggestions_scope_moment_is_about_the_focused_moment():
    resp = client.post(
        "/api/v1/assistant/suggestions",
        json={
            "query": "q",
            "scope": "moment",
            "focus_moment_id": "v1:28",
            "moments": [_moment()],
            "history": [],
        },
    )
    assert resp.status_code == 200
    questions = resp.json()["suggested_questions"]
    assert len(questions) >= 2
    assert all("this moment" in q.lower() or "this location" in q.lower() or "this match" in q.lower() for q in questions)


def test_suggestions_empty_moments_returns_no_suggestions_for_results_scope():
    resp = client.post(
        "/api/v1/assistant/suggestions",
        json={"query": "q", "scope": "results", "focus_moment_id": None, "moments": [], "history": []},
    )
    assert resp.status_code == 200
    assert resp.json()["suggested_questions"] == []
