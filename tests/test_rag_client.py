"""
app/services/rag_client.py adapts the in-process `rag` package
(ilabs-cctv-rag) to the backend's own RagQueryResult contract. The real
package needs DATABASE_URL + a Qdrant collection at import/query time, so
these tests swap a fake `rag.pipeline` module into sys.modules instead.
"""
import sys
import types

import pytest

from app.core.config import get_settings
from app.services import rag_client


def _install_fake_pipeline(monkeypatch, answer_query):
    rag_pkg = types.ModuleType("rag")
    pipeline = types.ModuleType("rag.pipeline")
    pipeline.answer_query = answer_query
    rag_pkg.pipeline = pipeline
    monkeypatch.setitem(sys.modules, "rag", rag_pkg)
    monkeypatch.setitem(sys.modules, "rag.pipeline", pipeline)


def _source(**overrides) -> dict:
    return {
        "score": 0.81,
        "description": "A person opens a car door",
        "event_name": "person_opens_vehicle_door",
        "video_id": "ab" * 32,
        "video_name": "2018-03-07.16-50-00.16-55-00.school.G330",
        "camera_id": "G330",
        "scene": "school",
        "start_seconds": 12.0,
        "end_seconds": 18.5,
        "object_types": ["person", "vehicle"],
        "video_url": "https://r2.example/video.mp4",
        **overrides,
    }


@pytest.fixture
def env(monkeypatch):
    def _set(environment: str):
        monkeypatch.setenv("ENVIRONMENT", environment)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def test_maps_package_response_to_backend_contract(monkeypatch, env):
    env("production")
    _install_fake_pipeline(
        monkeypatch,
        lambda q: {"query": q, "answer": "Found 1 matching moment(s).", "sources": [_source()]},
    )

    result = rag_client.query("person opening a car")

    assert result.answer == "Found 1 matching moment(s)."
    [item] = result.results
    assert item.video_id == "ab" * 32
    assert item.caption == "A person opens a car door"
    assert (item.start_seconds, item.end_seconds) == (12.0, 18.5)
    assert item.score == 0.81
    assert item.video_url == "https://r2.example/video.mp4"


def test_null_description_and_timespan_do_not_break_the_response(monkeypatch, env):
    env("production")
    _install_fake_pipeline(
        monkeypatch,
        lambda q: {
            "query": q,
            "answer": "...",
            "sources": [_source(description=None, start_seconds=None, end_seconds=None)],
        },
    )

    [item] = rag_client.query("anything").results

    assert item.caption == "person_opens_vehicle_door"  # falls back to event_name
    assert item.start_seconds is None and item.end_seconds is None


def test_limit_caps_the_number_of_results(monkeypatch, env):
    env("production")
    _install_fake_pipeline(
        monkeypatch,
        lambda q: {"query": q, "answer": "...", "sources": [_source(score=s) for s in (0.9, 0.8, 0.7)]},
    )

    assert [r.score for r in rag_client.query("anything", limit=2).results] == [0.9, 0.8]


def test_pipeline_failure_raises_service_unavailable(monkeypatch, env):
    env("production")

    def boom(_q):
        raise ConnectionError("qdrant unreachable")

    _install_fake_pipeline(monkeypatch, boom)

    with pytest.raises(rag_client.RagServiceUnavailable, match="qdrant unreachable"):
        rag_client.query("anything")


def test_unconfigured_package_is_an_error_in_production(monkeypatch, env):
    env("production")
    monkeypatch.setattr(rag_client, "_load_answer_query", _raise_missing_database_url)

    with pytest.raises(rag_client.RagServiceUnavailable, match="DATABASE_URL"):
        rag_client.query("anything")


def test_unconfigured_package_falls_back_to_mock_in_development(monkeypatch, env):
    env("development")
    monkeypatch.setattr(rag_client, "_load_answer_query", _raise_missing_database_url)

    result = rag_client.query("anything")

    assert result.answer.startswith("[mock")
    assert result.results == []


def _raise_missing_database_url():
    raise KeyError("DATABASE_URL")
