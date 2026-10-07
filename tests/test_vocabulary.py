"""
GET /vocabulary — what the search field can name (locations, cameras,
dates), read from the RAG's Qdrant index via rag.filters.vocabulary().

The RAG package itself is mocked: store.connect() returns a MagicMock
client and filters.vocabulary() returns a hand-built Vocabulary, so no
Qdrant is needed. get_current_user is overridden, except in the auth test.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from rag import filters, store
from rag.filters import Vocabulary

from app.api.deps import CurrentUser, get_current_user
from app.main import app

client = TestClient(app)

USER_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _override_user():
    # Restore whatever was there before: other test modules set this
    # override once at import time, and pytest imports them all before
    # running any test, so popping it here would break every later file.
    previous = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=USER_ID, email="test@example.com")
    yield
    if previous is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = previous


def _get_with(vocab: Vocabulary):
    qdrant = MagicMock()
    with patch.object(store, "connect", return_value=qdrant), patch.object(filters, "vocabulary", return_value=vocab):
        return client.get("/api/v1/vocabulary"), qdrant


def test_vocabulary_returns_the_four_fields_sorted():
    vocab = Vocabulary(
        scenes=frozenset({"school", "bus", "admin", "hospital"}),
        cameras=frozenset({"G420", "G328", "G341"}),
        dates=frozenset({"2018-03-07", "2018-03-05"}),
    )

    resp, qdrant = _get_with(vocab)

    assert resp.status_code == 200
    body = resp.json()
    assert body["scenes"] == ["admin", "bus", "hospital", "school"]
    assert body["cameras"] == ["G328", "G341", "G420"]
    assert body["dates"] == ["2018-03-05", "2018-03-07"]
    assert list(body["synonyms"]) == sorted(body["synonyms"])
    qdrant.close.assert_called_once()


def test_synonyms_only_name_scenes_that_are_in_the_index():
    vocab = Vocabulary(scenes=frozenset({"school", "hospital"}))

    resp, _ = _get_with(vocab)

    synonyms = resp.json()["synonyms"]
    assert synonyms["campus"] == "school"
    assert synonyms["clinic"] == "hospital"
    assert "depot" not in synonyms
    assert "bus station" not in synonyms
    assert set(synonyms.values()) <= {"school", "hospital"}


def test_empty_index_is_200_with_empty_fields():
    resp, _ = _get_with(Vocabulary())

    assert resp.status_code == 200
    assert resp.json() == {"scenes": [], "synonyms": {}, "cameras": [], "dates": []}


def test_connect_failure_is_502():
    with patch.object(store, "connect", side_effect=ValueError("bad QDRANT_URL")):
        resp = client.get("/api/v1/vocabulary")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Search service unreachable: bad QDRANT_URL"


def test_vocabulary_failure_is_502_and_closes_the_client():
    qdrant = MagicMock()
    with (
        patch.object(store, "connect", return_value=qdrant),
        patch.object(filters, "vocabulary", side_effect=ConnectionError("Qdrant down")),
    ):
        resp = client.get("/api/v1/vocabulary")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Search service unreachable: Qdrant down"
    qdrant.close.assert_called_once()


def test_invalid_token_is_401():
    app.dependency_overrides.pop(get_current_user, None)

    resp = client.get("/api/v1/vocabulary", headers={"Authorization": "Bearer not-a-jwt"})

    assert resp.status_code == 401
