"""
app/services/tracks.py — the bbox-format conversion, label reduction and
downsampling logic behind GET /videos/{video_id}/tracks (spec §4.4). Pure
unit tests against build_tracks(); no database involved.
"""
from app.services.tracks import build_tracks


def _row(**overrides) -> dict:
    row = {
        "object_id": "obj-1",
        "t": 64.0,
        "bounding_box_pixels": {"x_min": 5, "y_min": 332, "x_max": 41, "y_max": 434},
        "confidence": 0.79,
        "label": "car",
    }
    row.update(overrides)
    return row


def test_normalizes_corner_box_to_xywh():
    result = build_tracks([_row()])
    assert result[0]["boxes"][0] == {"t": 64.0, "x": 5, "y": 332, "w": 36, "h": 102, "confidence": 0.79}


def test_maps_raw_label_to_coarse_lowercase_label():
    result = build_tracks([_row(label="car")])
    assert result[0]["label"] == "vehicle"

    result = build_tracks([_row(object_id="obj-2", label="person")])
    assert result[0]["label"] == "person"


def test_unmapped_label_falls_back_to_lowercased_raw_value():
    result = build_tracks([_row(label="Backpack")])
    assert result[0]["label"] == "backpack"


def test_groups_boxes_by_object_id():
    rows = [_row(object_id="obj-1", t=1.0), _row(object_id="obj-2", t=1.0)]
    result = build_tracks(rows)
    assert {obj["object_id"] for obj in result} == {"obj-1", "obj-2"}


def test_downsamples_to_at_most_ten_per_second():
    # 30 rows across 1 second (~30fps), same object.
    rows = [_row(t=round(i * (1 / 30), 3)) for i in range(30)]
    result = build_tracks(rows)
    assert len(result[0]["boxes"]) <= 10


def test_boxes_stay_sorted_by_t_after_downsampling():
    rows = [_row(t=round(i * (1 / 30), 3)) for i in range(30)]
    result = build_tracks(rows)
    ts = [b["t"] for b in result[0]["boxes"]]
    assert ts == sorted(ts)


def test_no_geometries_returns_empty_boxes_not_missing_object():
    assert build_tracks([]) == []


def test_malformed_bounding_box_is_skipped_not_raised():
    rows = [_row(bounding_box_pixels={"unexpected": "shape"}), _row(t=2.0)]
    result = build_tracks(rows)
    # the malformed one is dropped, the valid one at t=2.0 survives
    assert len(result[0]["boxes"]) == 1
    assert result[0]["boxes"][0]["t"] == 2.0
