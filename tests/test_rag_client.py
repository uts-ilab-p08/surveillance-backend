"""
app/services/rag_client.py: the RAG package's trimmed source shape
({score, annotation, video_id, event_id} only — see that repo's
pipeline.py) enriched via a bronze lookup by event_id (frontend spec
§5.1), plus the post-enrichment filtering §5.1 also asks for.

The RAG package itself and bronze are both stubbed out here — this test
is about the enrichment/mapping/filtering logic in rag_client.py, not
about Qdrant, the embedding model, or a real database.
"""
from unittest.mock import patch

import pytest

from app.services import bronze, rag_client
from app.services.rag_client import RagServiceUnavailable, SearchFilters


def _rag_source(**overrides) -> dict:
    """One entry from answer_query()["sources"], real shape only."""
    source = {
        "score": 0.812,
        "annotation": "A person in a dark jacket exits a white SUV.",
        "video_id": "20c38d5f-fake-video-id",
        "event_id": "b159b039-fake-event-id",
    }
    source.update(overrides)
    return source


def _bronze_event(**overrides) -> dict:
    """A row from bronze.get_event_with_video."""
    event = {
        "event_id": "b159b039-fake-event-id",
        "event_name": "Person exits a white SUV",
        "description": "A person in a dark jacket is seen exiting a white SUV.",
        "start_seconds": 41.844,
        "end_seconds": 52.306,
        "video_id": "20c38d5f-fake-video-id",
        "camera_id": "G336",
        "scene": "school",
        "capture_start_local": None,
        "capture_time_zone": None,
        "video_url": "https://example.com/clip.mp4",
    }
    event.update(overrides)
    return event


def _answer_query_response(sources: list[dict]) -> dict:
    return {
        "query": "did anyone get out of a vehicle?",
        "answer": "Yes, a person got out of a vehicle in event [1].",
        "sources": sources,
        "filters": {"scenes": [], "cameras": [], "notes": []},
    }


@pytest.fixture(autouse=True)
def _no_object_types_by_default():
    """_to_item() also calls bronze.get_object_types_for_event() for
    tags — stub it to [] unless a test overrides it, so tests that don't
    care about tags don't need a real database either."""
    with patch.object(bronze, "get_object_types_for_event", return_value=[]):
        yield


def test_query_enriches_sources_from_bronze():
    source = _rag_source()
    event = _bronze_event()

    db = object()
    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=event) as get_event:
        load_fn.return_value = lambda q: _answer_query_response([source])

        result = rag_client.query(db=db, query_text="did anyone get out of a vehicle?")

    get_event.assert_called_once_with(db, "b159b039-fake-event-id")
    assert result.answer == "Yes, a person got out of a vehicle in event [1]."
    assert len(result.results) == 1

    item = result.results[0]
    assert item.video_id == "20c38d5f-fake-video-id"
    assert item.video_url == "https://example.com/clip.mp4"
    assert item.start_seconds == 41.844
    assert item.end_seconds == 52.306
    assert item.score == 0.812
    # annotation from RAG is preferred over a fresh bronze description
    assert item.caption == "A person in a dark jacket exits a white SUV."
    # §5.1 enrichment fields
    assert item.event_id == "b159b039-fake-event-id"
    assert item.event_name == "Person exits a white SUV"
    assert item.description == event["description"]
    assert item.camera == "G336"
    assert item.scene == "school"


def test_query_falls_back_to_bronze_description_if_annotation_missing():
    source = _rag_source(annotation=None)
    event = _bronze_event()

    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=event):
        load_fn.return_value = lambda q: _answer_query_response([source])

        result = rag_client.query(db=object(), query_text="q")

    assert result.results[0].caption == event["description"]


def test_query_drops_sources_whose_event_no_longer_exists_in_bronze():
    source = _rag_source()

    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=None):
        load_fn.return_value = lambda q: _answer_query_response([source])

        result = rag_client.query(db=object(), query_text="q")

    assert result.results == []
    # the answer text itself is untouched even though the source was dropped
    assert result.answer == "Yes, a person got out of a vehicle in event [1]."


def test_query_respects_limit_before_enrichment():
    sources = [_rag_source(event_id=f"event-{i}") for i in range(5)]
    event = _bronze_event()

    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=event) as get_event:
        load_fn.return_value = lambda q: _answer_query_response(sources)

        result = rag_client.query(db=object(), query_text="q", limit=2)

    assert len(result.results) == 2
    assert get_event.call_count == 2


def test_query_raises_service_unavailable_if_package_not_loadable():
    with patch.object(rag_client, "_load_answer_query", side_effect=ImportError("no rag")):
        with pytest.raises(RagServiceUnavailable):
            rag_client.query(db=object(), query_text="q")


def test_query_raises_service_unavailable_if_answer_query_fails():
    with patch.object(rag_client, "_load_answer_query") as load_fn:
        def boom(q):
            raise RuntimeError("qdrant unreachable")

        load_fn.return_value = boom

        with pytest.raises(RagServiceUnavailable):
            rag_client.query(db=object(), query_text="q")


# --- timestamp handling (§5.1's "or null with a documented reason") -------


def test_timestamp_is_none_without_a_real_timezone():
    from datetime import datetime

    event = _bronze_event(capture_start_local=datetime(2018, 3, 5, 13, 20, 0), capture_time_zone="unknown")
    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=event):
        load_fn.return_value = lambda q: _answer_query_response([_rag_source()])
        result = rag_client.query(db=object(), query_text="q")

    assert result.results[0].timestamp is None


def test_timestamp_is_built_with_a_real_iana_zone():
    from datetime import datetime

    event = _bronze_event(
        capture_start_local=datetime(2018, 3, 5, 13, 20, 0),
        capture_time_zone="America/New_York",
        start_seconds=60.0,
    )
    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=event):
        load_fn.return_value = lambda q: _answer_query_response([_rag_source()])
        result = rag_client.query(db=object(), query_text="q")

    ts = result.results[0].timestamp
    assert ts is not None
    assert ts.startswith("2018-03-05T13:21:00")
    assert ts.endswith(("-05:00", "-04:00"))  # DST-dependent offset, either is a real one


# --- tags -------------------------------------------------------------------


def test_tags_come_from_bronze_object_types():
    event = _bronze_event()
    with patch.object(bronze, "get_object_types_for_event", return_value=["car"]), \
         patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=event):
        load_fn.return_value = lambda q: _answer_query_response([_rag_source()])
        result = rag_client.query(db=object(), query_text="q")

    assert "Vehicle" in result.results[0].tags


# --- filters (§5.1's optional query params) ---------------------------------


def test_camera_filter_excludes_non_matching_results():
    events = [_bronze_event(event_id=f"e{i}", camera_id=cam) for i, cam in enumerate(["G336", "G419"])]
    sources = [_rag_source(event_id=f"e{i}") for i in range(2)]

    def fake_get_event(db, event_id):
        return events[int(event_id[1:])]

    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", side_effect=fake_get_event):
        load_fn.return_value = lambda q: _answer_query_response(sources)
        result = rag_client.query(db=object(), query_text="q", filters=SearchFilters(cameras=["G419"]))

    assert len(result.results) == 1
    assert result.results[0].camera == "G419"


def test_min_confidence_filter_excludes_low_scores():
    sources = [_rag_source(event_id="e0", score=0.30), _rag_source(event_id="e1", score=0.90)]
    event = _bronze_event()

    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=event):
        load_fn.return_value = lambda q: _answer_query_response(sources)
        result = rag_client.query(db=object(), query_text="q", filters=SearchFilters(min_confidence=70))

    assert len(result.results) == 1
    assert result.results[0].score == 0.90


def test_no_filters_means_no_filtering_applied():
    sources = [_rag_source(event_id="e0")]
    event = _bronze_event()

    with patch.object(rag_client, "_load_answer_query") as load_fn, \
         patch.object(bronze, "get_event_with_video", return_value=event):
        load_fn.return_value = lambda q: _answer_query_response(sources)
        result = rag_client.query(db=object(), query_text="q", filters=SearchFilters())

    assert len(result.results) == 1
