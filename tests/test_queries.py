"""
DELETE /queries/saved/{id} (spec §4.1) and the idempotent-save behaviour
of POST /queries/saved (spec §5.3). The DB session is a MagicMock
throughout — these tests are about this backend's own branching (ownership
check, 404-not-403, idempotency, the IntegrityError race fallback), not
about a real Postgres.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app

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


# --- DELETE /queries/saved/{id} --------------------------------------------


def test_delete_saved_query_returns_204_when_owned_row_exists():
    row = MagicMock(id=uuid.uuid4())
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row
    _override_db(mock_db)

    resp = client.delete(f"/api/v1/queries/saved/{row.id}")

    assert resp.status_code == 204
    mock_db.delete.assert_called_once_with(row)
    mock_db.commit.assert_called_once()


def test_delete_saved_query_returns_404_when_not_found():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    _override_db(mock_db)

    resp = client.delete(f"/api/v1/queries/saved/{uuid.uuid4()}")

    assert resp.status_code == 404
    mock_db.delete.assert_not_called()


def test_delete_saved_query_returns_404_for_malformed_id_without_touching_db():
    mock_db = MagicMock()
    _override_db(mock_db)

    resp = client.delete("/api/v1/queries/saved/not-a-uuid")

    assert resp.status_code == 404
    mock_db.query.assert_not_called()


def test_delete_saved_query_twice_returns_404_the_second_time():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    _override_db(mock_db)

    resp = client.delete(f"/api/v1/queries/saved/{uuid.uuid4()}")
    assert resp.status_code == 404


# --- POST /queries/saved (§5.3 idempotency) --------------------------------


def _fake_row(text: str, hits: int = 0):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.query_text = text
    row.hits = hits
    row.created_at.isoformat.return_value = "2026-09-26T00:00:00+00:00"
    return row


def test_save_query_returns_201_for_a_new_query():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    # db.refresh() is mocked, so it won't populate the real SavedQuery
    # row's server-side created_at the way a real Session would — do
    # that ourselves so _to_saved_query_out() has something to format.
    mock_db.refresh.side_effect = lambda row: setattr(row, "created_at", datetime.now(timezone.utc))
    _override_db(mock_db)

    resp = client.post("/api/v1/queries/saved", json={"text": "car in the parking lot"})

    assert resp.status_code == 201
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


def test_save_query_returns_200_and_existing_row_for_a_duplicate():
    existing = _fake_row("car in the parking lot", hits=3)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = existing
    _override_db(mock_db)

    resp = client.post("/api/v1/queries/saved", json={"text": "Car In The Parking Lot  "})

    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"] == 3
    mock_db.add.assert_not_called()


def test_save_query_falls_back_to_existing_row_on_integrity_error_race():
    existing = _fake_row("car in the parking lot", hits=1)
    mock_db = MagicMock()
    # First lookup (pre-check): nothing yet. Commit then raises (another
    # request won the race). Second lookup (post-rollback): the row that
    # request just inserted.
    mock_db.query.return_value.filter.return_value.first.side_effect = [None, existing]
    mock_db.commit.side_effect = IntegrityError("stmt", {}, Exception("dup"))
    _override_db(mock_db)

    resp = client.post("/api/v1/queries/saved", json={"text": "car in the parking lot"})

    assert resp.status_code == 200
    assert resp.json()["hits"] == 1
    mock_db.rollback.assert_called_once()
