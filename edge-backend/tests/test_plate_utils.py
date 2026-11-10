"""Unit tests for plate_utils using plate_test_cases.json."""

import json
from pathlib import Path
import pytest

from app.recognition.plate_utils import (
    PlateCandidate,
    clean_raw_text,
    correct_ocr_confusions,
    format_plate_display,
    is_valid_vn_plate,
    normalize_plate,
    vote_best_candidate,
)

CASES_PATH = Path(__file__).parent / "plate_test_cases.json"


def load_test_cases():
    with open(CASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("case", load_test_cases(), ids=lambda c: c["description"])
def test_plate_test_cases(case):
    raw = case["raw"]
    expected_norm = case["expectedNormalized"]
    expected_disp = case["expectedDisplay"]
    expected_valid = case["expectedValid"]

    res = normalize_plate(raw)

    assert res.plate_normalized == expected_norm, f"Failed on raw: {raw}"
    assert res.is_valid == expected_valid, f"Failed validity on: {raw}"
    assert res.plate_display == expected_disp, f"Failed display on: {raw}"


def test_voting_mechanism():
    # Frame 1: đọc sai/thiếu, không hợp lệ
    c1 = PlateCandidate(
        plate_raw="51A123",
        plate_normalized="51A123",
        plate_display="51A123",
        confidence=0.85,
        is_valid=False,
        frame_index=0,
    )
    # Frame 2: đọc đủ và hợp lệ nhưng conf thấp hơn
    c2 = PlateCandidate(
        plate_raw="51A-123.45",
        plate_normalized="51A12345",
        plate_display="51A-123.45",
        confidence=0.75,
        is_valid=True,
        frame_index=1,
    )

    # Hệ thống phải ưu tiên chọn c2 vì c2 hợp lệ!
    best = vote_best_candidate([c1, c2])
    assert best is not None
    assert best.plate_normalized == "51A12345"
    assert best.is_valid is True


def test_voting_empty_list():
    assert vote_best_candidate([]) is None

