"""Pytest configuration and shared fixtures for Edge Backend tests."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
import pytest

from fastapi.testclient import TestClient

from app.arbiter.gate_arbiter import GateArbiter
from app.cloud.base import CloudBridgeBase
from app.cloud.storage_uploader import StorageUploader
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
        plate_display: str = "",
        confidence: float = 0.0,
        assigned_slot_id: Optional[str] = None,
        matched_reservation_id: Optional[str] = None,
        deny_reason: Optional[str] = None,
        image_url: Optional[str] = None,
        crop_image_url: Optional[str] = None,
        latency_ms: float = 0.0,
    ) -> None:
        self.fallback_events.append({
            "seq": seq,
            "direction": direction,
            "decision": decision,
            "status": status,
            "flags": flags or [],
            "plate_raw": plate_raw,
            "plate_norm": plate_norm,
            "plate_display": plate_display,
            "confidence": confidence,
            "assigned_slot_id": assigned_slot_id,
            "matched_reservation_id": matched_reservation_id,
            "deny_reason": deny_reason,
            "image_url": image_url,
            "crop_image_url": crop_image_url,
            "latency_ms": latency_ms,
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
def dummy_uploader(dummy_cloud, tmp_path):
    # Dùng thư mục tạm tmp_path để tránh ghi đè data thật trong test
    uploader = StorageUploader(cloud_bridge=dummy_cloud, max_workers=1)
    uploader.local_base_dir = tmp_path / "captures"
    uploader.local_base_dir.mkdir(parents=True, exist_ok=True)
    yield uploader
    uploader.stop()


@pytest.fixture
def arbiter(cache, mock_mqtt, dummy_cloud, dummy_uploader):
    a = GateArbiter(
        cache=cache,
        mqtt_bridge=mock_mqtt,
        cloud_bridge=dummy_cloud,
        storage_uploader=dummy_uploader,
    )
    yield a
    a.stop()


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
def client(cache, dummy_cloud, mock_mqtt, mock_ai_pipeline, arbiter, dummy_uploader):
    app.state.test_mode = True
    app.state.cache = cache
    app.state.cloud = dummy_cloud
    app.state.mqtt = mock_mqtt
    app.state.ai_pipeline = mock_ai_pipeline
    app.state.arbiter = arbiter
    app.state.storage_uploader = dummy_uploader
    with TestClient(app) as c:
        yield c
    app.state.test_mode = False
