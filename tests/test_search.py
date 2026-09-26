"""
GET /search — the route itself: wiring the new filter query params
through to app/services/rag_client.query() (§5.1), the 502 mapping, and
the recent-query/saved-query-hits side effect (§5.1's "Also check the
side effects" — confirmed pre-existing gap: nothing wrote either row
before this change).

rag_client.query() is mocked throughout; its own enrichment/filtering
logic is covered in tests/test_rag_client.py. The DB session is a
MagicMock — these tests check what gets written, not real Postgres.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.api.routes import search as search_route
from app.main import app
from app.schemas.rag import RagQueryResult, RagResultItem
from app.services.rag_client import RagServiceUnavailable

client = TestClient(app)

USER_ID = uuid.uuid4()
app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="test@example.com")


def _override_db(mock_db):
    def _get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _get_db


@pytest.fixture(autouse=True)
def _clear_db_override():
    yield
    app.dependency_overrides.pop(get_db, None)


def _result_item(**overrides) -> RagResultItem:
    item = dict(
        video_id="v1",
        video_url="https://example.com/clip.mp4",
        start_seconds=28.0,
        end_seconds=35.0,
        caption="A white car parks.",
        score=0.79,
        camera="G424",
    )
    item.update(overrides)
    return RagResultItem(**item)


def test_search_returns_200_and_passes_filters_through():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None  # no saved query match
    _override_db(mock_db)

    fake_result = RagQueryResult(answer="Found it.", results=[_result_item()])
    with patch.object(search_route, "rag_query", return_value=fake_result) as mock_query:
        resp = client.get(
            "/api/v1/search",
            params={"q": "car parked", "cameras": ["G424", "G329"], "min_confidence": 70},
        )

    assert resp.status_code == 200
    assert resp.json()["answer"] == "Found it."

    _, kwargs = mock_query.call_args
    assert kwargs["filters"].cameras == ["G424", "G329"]
    assert kwargs["filters"].min_confidence == 70


def test_search_records_recent_query_and_camera_count():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    _override_db(mock_db)

    fake_result = RagQueryResult(
        answer="ok", results=[_result_item(camera="G424"), _result_item(camera="G329")]
    )
    with patch.object(search_route, "rag_query", return_value=fake_result):
        resp = client.get("/api/v1/search", params={"q": "car parked"})

    assert resp.status_code == 200
    mock_db.add.assert_called_once()
    recent_query_row = mock_db.add.call_args[0][0]
    assert recent_query_row.query_text == "car parked"
    assert recent_query_row.camera_count == 2  # G424 and G329, distinct
    mock_db.commit.assert_called_once()


def test_search_bumps_hits_on_matching_saved_query():
    saved = MagicMock(hits=3)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = saved
    _override_db(mock_db)

    fake_result = RagQueryResult(answer="ok", results=[])
    with patch.object(search_route, "rag_query", return_value=fake_result):
        resp = client.get("/api/v1/search", params={"q": "car parked"})

    assert resp.status_code == 200
    assert saved.hits == 4


def test_search_side_effect_failure_does_not_break_the_response():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    mock_db.commit.side_effect = Exception("db hiccup")
    _override_db(mock_db)

    fake_result = RagQueryResult(answer="ok", results=[])
    with patch.object(search_route, "rag_query", return_value=fake_result):
        resp = client.get("/api/v1/search", params={"q": "car parked"})

    assert resp.status_code == 200
    mock_db.rollback.assert_called_once()


def test_search_returns_502_when_rag_unavailable():
    mock_db = MagicMock()
    _override_db(mock_db)

    with patch.object(search_route, "rag_query", side_effect=RagServiceUnavailable("qdrant down")):
        resp = client.get("/api/v1/search", params={"q": "car parked"})

    assert resp.status_code == 502
