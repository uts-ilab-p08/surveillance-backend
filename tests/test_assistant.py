"""
POST /assistant/ask, POST /assistant/ask/stream, and POST
/assistant/suggestions (frontend spec §4.2/§4.3). rag.llm.complete() is
stubbed throughout — these tests are about this backend's own validation,
prompt-building, citation parsing and rule-based suggestions, not about a
real LLM or OpenRouter.

DB access (app.services.bronze, for annotation-driven suggestions) is
mocked the same way tests/test_search.py does it: a MagicMock db is
installed by default via the autouse _default_db_override fixture below,
and individual tests patch app.services.assistant.bronze's functions
directly when they need specific object_types/confidence data back.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.services import assistant

client = TestClient(app)
app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=uuid.uuid4(), email="test@example.com")


def _override_db(mock_db):
    def _get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _get_db


@pytest.fixture(autouse=True)
def _default_db_override():
    """Every test gets a plain MagicMock db unless it calls _override_db
    itself with a more specific one — same default-then-override pattern
    as tests/test_search.py. Moments with no event_id never touch this
    mock at all (see _gather_annotations's early skip), so this is purely
    a safety net for the route requiring Depends(get_db) at all now."""
    _override_db(MagicMock())
    yield
    app.dependency_overrides.pop(get_db, None)


def _moment(**overrides) -> dict:
    moment = {
        "moment_id": "v1:28",
        "video_id": "v1",
        "start_seconds": 28,
        "end_seconds": 35,
        "caption": "A white car pulls into the parking area.",
        "score": 0.79,
        "camera": None,
        "event_id": None,
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


# --- Suggestions driven by real bronze annotations (event_id), not caption -


def test_suggestions_uses_real_object_types_over_caption_when_event_id_present():
    """Caption deliberately says nothing about a vehicle — only the real
    (mocked) bronze object_types say "car". Proves the annotation lookup
    is actually driving the suggestion, not the caption fallback."""
    mock_db = MagicMock()
    _override_db(mock_db)
    moment = _moment(caption="Something happens near the entrance.", event_id="evt-1")

    with patch.object(assistant.bronze, "get_object_types_for_event", return_value=["car"]), \
         patch.object(assistant.bronze, "get_avg_detection_confidence", return_value=0.9):
        resp = client.post(
            "/api/v1/assistant/suggestions",
            json={"query": "q", "scope": "results", "focus_moment_id": None, "moments": [moment], "history": []},
        )

    assert resp.status_code == 200
    assert "Narrow this to vehicle events only" in resp.json()["suggested_questions"]


def test_suggestions_falls_back_to_caption_when_moment_has_no_event_id():
    resp = client.post(
        "/api/v1/assistant/suggestions",
        json={
            "query": "q",
            "scope": "results",
            "focus_moment_id": None,
            "moments": [_moment(event_id=None)],  # caption says "car"
            "history": [],
        },
    )
    assert resp.status_code == 200
    assert "Narrow this to vehicle events only" in resp.json()["suggested_questions"]


def test_suggestions_results_scope_flags_left_behind_objects_from_annotations():
    moment = _moment(caption="A person walks by.", event_id="evt-2")
    with patch.object(assistant.bronze, "get_object_types_for_event", return_value=["person", "backpack"]), \
         patch.object(assistant.bronze, "get_avg_detection_confidence", return_value=0.9):
        resp = client.post(
            "/api/v1/assistant/suggestions",
            json={"query": "q", "scope": "results", "focus_moment_id": None, "moments": [moment], "history": []},
        )
    assert resp.status_code == 200
    assert "Was anything left behind in these results?" in resp.json()["suggested_questions"]


def test_suggestions_moment_scope_flags_left_behind_object_for_focused_moment():
    moment = _moment(caption="A person walks by.", event_id="evt-3")
    with patch.object(assistant.bronze, "get_object_types_for_event", return_value=["person", "suitcase"]), \
         patch.object(assistant.bronze, "get_avg_detection_confidence", return_value=0.9):
        resp = client.post(
            "/api/v1/assistant/suggestions",
            json={"query": "q", "scope": "moment", "focus_moment_id": "v1:28", "moments": [moment], "history": []},
        )
    assert resp.status_code == 200
    assert "Was anything left behind at this location?" in resp.json()["suggested_questions"]


def test_suggestions_moment_scope_promotes_confidence_question_when_actually_low():
    moment = _moment(event_id="evt-4")
    with patch.object(assistant.bronze, "get_object_types_for_event", return_value=["car"]), \
         patch.object(assistant.bronze, "get_avg_detection_confidence", return_value=0.2):
        resp = client.post(
            "/api/v1/assistant/suggestions",
            json={"query": "q", "scope": "moment", "focus_moment_id": "v1:28", "moments": [moment], "history": []},
        )
    assert resp.status_code == 200
    assert "How confident is this match?" in resp.json()["suggested_questions"]


def test_suggestions_degrades_gracefully_when_bronze_lookup_raises():
    """A DB hiccup during suggestions shouldn't 500 the request — it
    should just fall back to the caption heuristic, same as no event_id."""
    moment = _moment(event_id="evt-5")  # caption says "car"
    with patch.object(assistant.bronze, "get_object_types_for_event", side_effect=RuntimeError("db down")):
        resp = client.post(
            "/api/v1/assistant/suggestions",
            json={"query": "q", "scope": "results", "focus_moment_id": None, "moments": [moment], "history": []},
        )
    assert resp.status_code == 200
    assert "Narrow this to vehicle events only" in resp.json()["suggested_questions"]


# --- POST /assistant/ask/stream (SSE) ---------------------------------------


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    import json as _json

    events = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        event_line, data_line = block.split("\n", 1)
        event = event_line.removeprefix("event: ")
        data = _json.loads(data_line.removeprefix("data: "))
        events.append((event, data))
    return events


def test_ask_stream_emits_status_events_then_a_result_event():
    raw_llm_reply = "The white car is the only vehicle event.\nCITED: v1:28"
    with patch.object(assistant.llm, "complete", return_value=raw_llm_reply):
        resp = client.post("/api/v1/assistant/ask/stream", json=_ask_body())

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds.count("status") >= 3
    assert kinds[-1] == "result"

    result_data = events[-1][1]
    assert result_data["answer"] == "The white car is the only vehicle event."
    assert result_data["citations"] == [{"moment_id": "v1:28"}]


def test_ask_stream_emits_error_event_instead_of_result_when_llm_unavailable():
    with patch.object(assistant.llm, "complete", side_effect=assistant.llm.LLMError("boom")):
        resp = client.post("/api/v1/assistant/ask/stream", json=_ask_body())

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[-1][0] == "error"
    assert events[-1][1] == {"message": "The assistant couldn't answer that. Try again."}
    assert not any(e == "result" for e, _ in events)
