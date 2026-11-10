"""Pytest configuration and shared fixtures for Edge Backend tests."""

import pytest
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.cloud.base import CloudBridgeBase
from app.config import settings
from app.main import app
from app.models import ReservationItem
from app.state_cache import StateCache


class DummyCloudBridge(CloudBridgeBase):
    """Bridge giả lập để kiểm thử không gọi Firebase thật."""

    def __init__(self):
        super().__init__()
        self.slot_updates: List[Dict[str, Any]] = []
        self.device_statuses: List[Dict[str, Any]] = []
        self.fallback_events: List[Dict[str, Any]] = []
        self.lot_summaries: List[int] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def enqueue_slots(self, slot_ids: List[str], sensor_map: Dict[str, bool]) -> None:
        for sid in slot_ids:
            self.slot_updates.append({"slotId": sid, "occupied": sensor_map.get(sid, False)})

    def enqueue_lot_summary(self, free_slots: int) -> None:
        self.lot_summaries.append(free_slots)

    def enqueue_device_status(self, device_id: str, online: bool, kind: str) -> None:
        self.device_statuses.append({"deviceId": device_id, "online": online, "kind": kind})

    def enqueue_gate_event(
        self,
        seq: int,
        direction: str,
        decision: str,
        status: str,
        flags: List[str],
        plate_raw: str = "",
        plate_norm: str = "",
    ) -> None:
        self.fallback_events.append({
            "seq": seq,
            "direction": direction,
            "decision": decision,
            "status": status,
            "flags": flags or [],
            "plate_raw": plate_raw,
            "plate_norm": plate_norm,
        })

    def status(self) -> Dict[str, Any]:
        return {
            "mode": "dummy",
            "connected": True,
            "started": self.started,
            "queuedUpdates": len(self.slot_updates),
        }


@pytest.fixture
def cache():
    return StateCache(slot_ids=["A1", "A2", "B1", "B2"])


@pytest.fixture
def dummy_cloud():
    return DummyCloudBridge()


@pytest.fixture
def mock_mqtt():
    m = MagicMock()
    m.is_connected = True
    m.last_message_ago_s = 2.0
    m.message_count = 10
    m.parse_errors = 0
    m.publish_reserved.return_value = True
    m.publish_gate_command.return_value = True
    return m


@pytest.fixture
def mock_ai_pipeline():
    m = MagicMock()
    m.status = {
        "gpuAvailable": True,
        "device": "cuda:0",
        "yoloLoaded": True,
        "warmupDone": True,
        "lastLatencyMs": 45.0,
    }
    mock_res = MagicMock()
    mock_res.plate_raw = "51A12345"
    mock_res.plate_normalized = "51A12345"
    mock_res.plate_display = "51A-123.45"
    mock_res.confidence = 0.95
    mock_res.is_valid = True
    mock_res.latency_ms = 42.0
    mock_res.detector_used = "yolo"
    mock_res.total_candidates = 1
    mock_res.best_frame_index = 0
    mock_res.crop_jpeg_bytes = b"fake_jpeg"
    m.recognize_frames.return_value = mock_res
    return m


@pytest.fixture
def client(cache, dummy_cloud, mock_mqtt, mock_ai_pipeline):
    app.state.test_mode = True
    app.state.cache = cache
    app.state.cloud = dummy_cloud
    app.state.mqtt = mock_mqtt
    app.state.ai_pipeline = mock_ai_pipeline
    with TestClient(app) as c:
        yield c
    app.state.test_mode = False
