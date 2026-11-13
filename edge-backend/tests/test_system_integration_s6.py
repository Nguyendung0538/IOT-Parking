"""System Integration Tests for Phase S6 (Repo 1 Final Acceptance).

Kiểm thử tự động 8 kịch bản toàn trạm theo đặc tả:
- ST-01: Luồng xe vào chuẩn (End-to-End với AI Pipeline thật trên GPU và ảnh biển số thật)
- ST-02: Ưu tiên Đặt chỗ (Active Reservation được gán đúng slot dù các slot khác trống)
- ST-03: Bảo vệ Ô đã đặt khi bãi đầy (Khách vãng lai bị từ chối DENY khi chỉ còn ô đã đặt)
- ST-04: ESP32-CAM hỏng / tắt nguồn (Edge Server tự động mở cổng sau CAMERA_TIMEOUT_S)
- ST-05: Tắt Edge Server (ESP32 Offline Fallback đồng bộ bù khi kết nối lại)
- ST-06: Khôi phục sau khi Reset cứng ESP32 (Tự động cấp lại retained reservedMask)
- ST-07: Mở cổng khẩn cấp từ Admin Cloud (Xử lý deviceCommands OPEN_GATE & TTL 60s)
- ST-08: Hiệu năng & Stress test 20 lượt nhận diện liên tiếp trên GPU RTX 5060
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from unittest.mock import MagicMock
import pytest

from fastapi.testclient import TestClient

from app.arbiter.gate_arbiter import GateArbiter
from app.cloud.firestore_bridge import FirestoreBridge
from app.cloud.storage_uploader import StorageUploader
from app.config import settings
from app.main import app
from app.models import ReservationItem, StatePayload
from app.mqtt.bridge import MqttBridge
from app.recognition.pipeline import PlateRecognitionPipeline
from app.state_cache import StateCache

SAMPLE_IMAGE_PATH = Path(__file__).resolve().parent.parent.parent / "yolo_plate_dataset" / "CarLongPlate10.jpg"


@pytest.fixture(scope="module")
def real_ai_pipeline():
    """Khởi tạo AI Pipeline thật chạy trên GPU RTX 5060 cho bài kiểm thử tích hợp."""
    p = PlateRecognitionPipeline(
        model_path=settings.YOLO_MODEL_PATH,
        conf_threshold=settings.YOLO_CONF_THRESHOLD,
        padding_ratio=settings.PADDING_RATIO,
        use_gpu=settings.USE_GPU,
    )
    p.warmup()
    return p


@pytest.fixture
def integration_env(real_ai_pipeline, dummy_cloud, mock_mqtt, tmp_path):
    """Môi trường tích hợp toàn phần với AI Pipeline thật và StateCache độc lập."""
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])

    uploader = StorageUploader(cloud_bridge=dummy_cloud, max_workers=1)
    uploader.local_base_dir = tmp_path / "captures"
    uploader.local_base_dir.mkdir(parents=True, exist_ok=True)

    arbiter = GateArbiter(
        cache=cache,
        mqtt_bridge=mock_mqtt,
        cloud_bridge=dummy_cloud,
        storage_uploader=uploader,
    )
    mock_mqtt.reset_mock()

    app.state.test_mode = True
    app.state.cache = cache
    app.state.cloud = dummy_cloud
    app.state.mqtt = mock_mqtt
    app.state.ai_pipeline = real_ai_pipeline
    app.state.arbiter = arbiter
    app.state.storage_uploader = uploader

    with TestClient(app) as client:
        yield {
            "client": client,
            "cache": cache,
            "arbiter": arbiter,
            "cloud": dummy_cloud,
            "mqtt": mock_mqtt,
            "uploader": uploader,
        }

    arbiter.stop()
    uploader.stop()
    app.state.test_mode = False


# ==============================================================================
# ST-01: LUỒNG XE VÀO CHUẨN (END-TO-END VỚI GPU THẬT VÀ ẢNH THẬT)
# ==============================================================================
def test_st01_standard_entry_flow(integration_env):
    """ST-01: Đưa xe biển 61F-222.61 vào khi bãi trống.
    Kiểm tra AI đọc đúng, cấp ô A1, phát MQTT mở barie và lưu ảnh/gateEvents.
    """
    client = integration_env["client"]
    mock_mqtt = integration_env["mqtt"]
    dummy_cloud = integration_env["cloud"]

    assert os.path.exists(SAMPLE_IMAGE_PATH), f"Không tìm thấy ảnh mẫu: {SAMPLE_IMAGE_PATH}"

    with open(SAMPLE_IMAGE_PATH, "rb") as f:
        img_bytes = f.read()

    files = [
        ("images", ("frame_0.jpg", img_bytes, "image/jpeg")),
        ("images", ("frame_1.jpg", img_bytes, "image/jpeg")),
    ]
    data = {"device_id": "ESP32_CAM_01", "seq": "1", "direction": "IN"}
    headers = {"X-API-Key": settings.CAM_SHARED_API_KEY}

    res = client.post("/recognize", data=data, files=files, headers=headers)
    assert res.status_code == 200
    res_data = res.json()

    # 1. Xác nhận AI đọc đúng biển số CarLongPlate10 (61F-222.61)
    assert res_data["ok"] is True
    assert res_data["plateNormalized"] == "61F22261"
    assert res_data["plateDisplay"] == "61F-222.61"
    assert res_data["action"] == "OPEN"
    assert res_data["assignedSlot"] == "A1"
    assert res_data["latencyMs"] < 400.0  # Phải nhanh hơn yêu cầu 400ms

    # 2. Xác nhận lệnh MQTT đã phát xuống ESP32
    mock_mqtt.publish_gate_command.assert_called_once()
    mqtt_args = mock_mqtt.publish_gate_command.call_args[1]
    assert mqtt_args["seq"] == 1
    assert mqtt_args["action"] == "OPEN"
    assert mqtt_args["assigned_slot"] == "A1"
    assert mqtt_args["plate"] == "61F-222.61"

    # Đợi worker thread hoàn thành lưu ảnh & event
    time.sleep(0.1)

    # 3. Xác nhận gateEvents được enqueue lên Cloud
    assert len(dummy_cloud.fallback_events) >= 1
    ev = dummy_cloud.fallback_events[-1]
    assert ev["seq"] == 1
    assert ev["direction"] == "IN"
    assert ev["decision"] == "OPEN"
    assert ev["assigned_slot_id"] == "A1"
    assert ev["status"] == "ok"


# ==============================================================================
# ST-02: ƯU TIÊN ĐẶT CHỖ
# ==============================================================================
def test_st02_reservation_priority(integration_env):
    """ST-02: Ô B1 đang reserved cho biển 61F22261; xe 61F-222.61 vào cổng.
    Arbiter phải gán đúng B1 (dù A1, A2 đang trống) và ghi matchedReservationId.
    """
    client = integration_env["client"]
    cache = integration_env["cache"]
    mock_mqtt = integration_env["mqtt"]
    dummy_cloud = integration_env["cloud"]

    now = datetime.now(timezone.utc)
    cache.upsert_reservation(
        ReservationItem(
            id="res_st02_vip",
            slotId="B1",
            plate="61F22261",
            expiresAt=now + timedelta(minutes=45),
        )
    )

    with open(SAMPLE_IMAGE_PATH, "rb") as f:
        img_bytes = f.read()

    files = [("images", ("frame_0.jpg", img_bytes, "image/jpeg"))]
    data = {"device_id": "ESP32_CAM_01", "seq": "2", "direction": "IN"}
    headers = {"X-API-Key": settings.CAM_SHARED_API_KEY}

    res = client.post("/recognize", data=data, files=files, headers=headers)
    assert res.status_code == 200
    res_data = res.json()

    # Dù A1, A2 trống nhưng có reservation ở B1 -> Phải gán B1
    assert res_data["action"] == "OPEN"
    assert res_data["assignedSlot"] == "B1"
    assert "matched_reservation" in res_data["flags"]

    time.sleep(0.1)
    ev = dummy_cloud.fallback_events[-1]
    assert ev["matched_reservation_id"] == "res_st02_vip"
    assert ev["assigned_slot_id"] == "B1"


# ==============================================================================
# ST-03: BẢO VỆ Ô ĐÃ ĐẶT (BAI DA DAY)
# ==============================================================================
def test_st03_protect_reserved_slot_when_full(integration_env):
    """ST-03: Bãi chỉ còn trống duy nhất ô A1 nhưng A1 đã reserved cho biển khác.
    Khách vãng lai 61F-222.61 vào -> Bị từ chối DENY (FULL).
    """
    client = integration_env["client"]
    cache = integration_env["cache"]
    mock_mqtt = integration_env["mqtt"]

    now = datetime.now(timezone.utc)
    # A2, B1, B2 có xe đỗ vật lý
    cache.apply_state(
        StatePayload(
            slots={
                "A1": {"occupied": False},
                "A2": {"occupied": True},
                "B1": {"occupied": True},
                "B2": {"occupied": True},
            }
        )
    )
    # A1 được đặt trước bởi người khác
    cache.upsert_reservation(
        ReservationItem(
            id="res_st03_other",
            slotId="A1",
            plate="30A99999",
            expiresAt=now + timedelta(minutes=30),
        )
    )

    with open(SAMPLE_IMAGE_PATH, "rb") as f:
        img_bytes = f.read()

    files = [("images", ("frame_0.jpg", img_bytes, "image/jpeg"))]
    data = {"device_id": "ESP32_CAM_01", "seq": "3", "direction": "IN"}
    headers = {"X-API-Key": settings.CAM_SHARED_API_KEY}

    res = client.post("/recognize", data=data, files=files, headers=headers)
    assert res.status_code == 200
    res_data = res.json()

    # Phải từ chối vì không còn chỗ cho khách vãng lai
    assert res_data["action"] == "DENY"
    assert res_data["denyReason"] == "FULL"
    assert res_data["status"] == "denied_full"
    assert res_data["assignedSlot"] == ""

    mqtt_args = mock_mqtt.publish_gate_command.call_args[1]
    assert mqtt_args["action"] == "DENY"
    assert mqtt_args["reason"] == "FULL"


# ==============================================================================
# ST-04: HỎNG / TẮT ESP32-CAM (TIMEOUT 5.0S FAIL-SAFE)
# ==============================================================================
def test_st04_cam_failure_server_timeout(integration_env, monkeypatch):
    """ST-04: Nhận gate/request từ ESP32 nhưng CAM bị tắt (không có HTTP POST).
    Sau timeout, Server tự động mở barie ở chế độ unrecognized kèm cờ camera_timeout.
    """
    arbiter = integration_env["arbiter"]
    mock_mqtt = integration_env["mqtt"]
    dummy_cloud = integration_env["cloud"]

    # Đặt timeout ngắn (0.1s) để bài test chạy tức thì
    monkeypatch.setattr(settings, "CAMERA_TIMEOUT_S", 0.1)

    arbiter.on_gate_request(seq=4, direction="IN")

    # Chờ timer kích hoạt
    time.sleep(0.2)

    mock_mqtt.publish_gate_command.assert_called_once()
    mqtt_args = mock_mqtt.publish_gate_command.call_args[1]
    assert mqtt_args["seq"] == 4
    assert mqtt_args["action"] == "OPEN"
    assert mqtt_args["plate"] == "KHONG RO"
    assert mqtt_args["assigned_slot"] == "A1"

    time.sleep(0.1)
    ev = dummy_cloud.fallback_events[-1]
    assert ev["seq"] == 4
    assert "camera_timeout" in ev["flags"]
    assert "unrecognized" in ev["flags"]


# ==============================================================================
# ST-05: TẮT EDGE SERVER (OFFLINE FALLBACK CỦA ESP32)
# ==============================================================================
def test_st05_edge_offline_fallback(integration_env):
    """ST-05: Khi Edge Server bị tắt, ESP32 tự mở sau 8.0s.
    Khi kết nối lại, ESP32 gửi gói tin gate/request với offlineFallback=True -> Đồng bộ bù.
    """
    cache = integration_env["cache"]
    dummy_cloud = integration_env["cloud"]

    bridge = MqttBridge(cache=cache, cloud=dummy_cloud)

    fallback_payload = json.dumps({
        "deviceId": settings.DEVICE_ID,
        "seq": 5,
        "direction": "IN",
        "offlineFallback": True,
        "clientTimestamp": int(time.time()),
    }).encode("utf-8")

    bridge._handle_gate_request(fallback_payload)

    # Đảm bảo được ghi nhận vào cloud với trạng thái offline_fallback
    assert len(dummy_cloud.fallback_events) >= 1
    ev = dummy_cloud.fallback_events[-1]
    assert ev["seq"] == 5
    assert ev["direction"] == "IN"
    assert ev["decision"] == "OPEN"
    assert ev["status"] == "offline_fallback"
    assert "offline_fallback" in ev["flags"]


# ==============================================================================
# ST-06: KHÔI PHỤC SAU RESET ESP32 (RE-PUBLISH RETAINED RESERVED MASK)
# ==============================================================================
def test_st06_recovery_after_esp32_reset(integration_env):
    """ST-06: Khi ESP32 bị reset cứng và kết nối lại MQTT (báo status=online),
    Edge Server phải lập tức re-publish mặt nạ reserved mới nhất (Retained=True).
    """
    cache = integration_env["cache"]
    mock_mqtt = integration_env["mqtt"]
    dummy_cloud = integration_env["cloud"]

    now = datetime.now(timezone.utc)
    cache.upsert_reservation(
        ReservationItem(
            id="res_b2",
            slotId="B2",
            plate="51F33333",
            expiresAt=now + timedelta(minutes=25),
        )
    )

    bridge = MqttBridge(cache=cache, cloud=dummy_cloud)
    bridge._connected = True
    bridge.client = MagicMock()
    bridge.client.publish.return_value.rc = 0

    # Giả lập ESP32 vừa online trở lại
    bridge._handle_device_status("gate", b"online")

    # Kiểm tra gói retained reserved đã được publish
    bridge.client.publish.assert_called()
    called_topic = bridge.client.publish.call_args[0][0]
    called_payload = json.loads(bridge.client.publish.call_args[0][1])
    called_retain = bridge.client.publish.call_args[1].get("retain")

    assert called_topic == settings.topic_reserved
    assert called_retain is True
    assert called_payload["B2"] is True
    assert called_payload["A1"] is False


# ==============================================================================
# ST-07: MỞ CỔNG TỪ ADMIN CLOUD & KIỂM TRA HẾT HẠN TTL 60S
# ==============================================================================
def test_st07_admin_open_gate_and_ttl_expiration(integration_env):
    """ST-07: Lệnh OPEN_GATE hợp lệ (< 60s) -> Mở cổng qua MQTT.
    Lệnh quá hạn (> 60s) -> Đổi status=failed (EXPIRED).
    """
    cache = integration_env["cache"]
    mock_mqtt = integration_env["mqtt"]

    firestore_bridge = FirestoreBridge(cache=cache, mqtt_bridge=mock_mqtt)

    # 1. Trường hợp lệnh hợp lệ mới tạo
    doc_valid = MagicMock()
    doc_valid.id = "cmd_valid_01"
    doc_valid.to_dict.return_value = {
        "action": "OPEN_GATE",
        "status": "pending",
        "createdAt": datetime.now(timezone.utc),
    }
    change_valid = MagicMock()
    change_valid.type.name = "ADDED"
    change_valid.document = doc_valid

    firestore_bridge._on_commands_snapshot(col_snapshot=[], changes=[change_valid], read_time=None)

    # Xác nhận đổi status=sent và phát MQTT mở cổng
    doc_valid.reference.update.assert_called_with({
        "status": "sent",
        "sentAt": unittest_mock_any(),
    })
    mock_mqtt.publish_gate_command.assert_called_with(seq=0, action="OPEN", plate="ADMIN_OPEN")

    # 2. Trường hợp lệnh đã quá hạn TTL 60s
    mock_mqtt.reset_mock()
    doc_expired = MagicMock()
    doc_expired.id = "cmd_expired_02"
    doc_expired.to_dict.return_value = {
        "action": "OPEN_GATE",
        "status": "pending",
        "createdAt": datetime.now(timezone.utc) - timedelta(seconds=90),
    }
    change_expired = MagicMock()
    change_expired.type.name = "ADDED"
    change_expired.document = doc_expired

    firestore_bridge._on_commands_snapshot(col_snapshot=[], changes=[change_expired], read_time=None)

    # Xác nhận đổi status=failed, failReason=EXPIRED và không mở barie
    doc_expired.reference.update.assert_called_with({
        "status": "failed",
        "failReason": "EXPIRED",
        "processedAt": unittest_mock_any(),
    })
    mock_mqtt.publish_gate_command.assert_not_called()


def unittest_mock_any():
    class AnyMatcher:
        def __eq__(self, other):
            return True
    return AnyMatcher()


# ==============================================================================
# ST-08: HIỆU NĂNG & STRESS TEST 20 LƯỢT NHẬN DIỆN LIÊN TIẾP TRÊN GPU RTX 5060
# ==============================================================================
def test_st08_performance_stress_test_20_iterations(integration_env):
    """ST-08: Gửi liên tiếp 20 lượt nhận diện ảnh thật qua POST /recognize trên GPU.
    Xác nhận 100% thành công, không nghẽn luồng và độ trễ trung bình < 400ms.
    """
    client = integration_env["client"]

    with open(SAMPLE_IMAGE_PATH, "rb") as f:
        img_bytes = f.read()

    latencies = []
    headers = {"X-API-Key": settings.CAM_SHARED_API_KEY}

    for i in range(20):
        seq = 200 + i
        direction = "IN" if i % 2 == 0 else "OUT"
        files = [("images", ("frame_0.jpg", img_bytes, "image/jpeg"))]
        data = {"device_id": "ESP32_CAM_01", "seq": str(seq), "direction": direction}

        t0 = time.perf_counter()
        res = client.post("/recognize", data=data, files=files, headers=headers)
        dur_ms = (time.perf_counter() - t0) * 1000

        assert res.status_code == 200, f"Lượt {i+1} (seq={seq}) thất bại: {res.text}"
        res_json = res.json()
        assert res_json["action"] == "OPEN"
        latencies.append(dur_ms)

    mean_latency = sum(latencies) / len(latencies)
    max_latency = max(latencies)
    min_latency = min(latencies)

    print(f"\n[ST-08 Performance Summary] 20 iterations on RTX 5060:")
    print(f" -> Mean: {mean_latency:.1f} ms (Target: < 400 ms)")
    print(f" -> Min:  {min_latency:.1f} ms")
    print(f" -> Max:  {max_latency:.1f} ms")

    assert mean_latency < 400.0, f"Độ trễ trung bình {mean_latency:.1f}ms vượt quá 400ms!"

