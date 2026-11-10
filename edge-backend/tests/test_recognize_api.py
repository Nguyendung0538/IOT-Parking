"""Integration tests for POST /recognize endpoint (Phase S5)."""

import io
from PIL import Image
import pytest

from app.config import settings


def _create_dummy_jpeg() -> bytes:
    img = Image.new("RGB", (200, 100), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_recognize_missing_api_key(client):
    """Không có header X-API-Key -> 401 Unauthorized."""
    jpeg_data = _create_dummy_jpeg()
    files = [("images", ("frame_0.jpg", jpeg_data, "image/jpeg"))]
    data = {"device_id": "ESP32_CAM_01", "seq": "101", "direction": "IN"}

    res = client.post("/recognize", data=data, files=files)
    assert res.status_code == 401
    assert "Invalid or missing X-API-Key" in res.json()["detail"]


def test_recognize_invalid_api_key(client):
    """X-API-Key sai giá trị -> 401 Unauthorized."""
    jpeg_data = _create_dummy_jpeg()
    files = [("images", ("frame_0.jpg", jpeg_data, "image/jpeg"))]
    data = {"device_id": "ESP32_CAM_01", "seq": "102", "direction": "IN"}
    headers = {"X-API-Key": "wrong-secret-key"}

    res = client.post("/recognize", data=data, files=files, headers=headers)
    assert res.status_code == 401


def test_recognize_invalid_direction(client):
    """Direction không phải IN hoặc OUT -> 422."""
    jpeg_data = _create_dummy_jpeg()
    files = [("images", ("frame_0.jpg", jpeg_data, "image/jpeg"))]
    data = {"device_id": "ESP32_CAM_01", "seq": "103", "direction": "INVALID"}
    headers = {"X-API-Key": settings.CAM_SHARED_API_KEY}

    res = client.post("/recognize", data=data, files=files, headers=headers)
    assert res.status_code == 422


def test_recognize_success_flow(client, mock_mqtt):
    """Luồng nhận diện thành công: Nhận ảnh -> AI Pipeline -> Arbiter -> MQTT -> Background Uploader."""
    jpeg_data = _create_dummy_jpeg()
    files = [
        ("images", ("frame_0.jpg", jpeg_data, "image/jpeg")),
        ("images", ("frame_1.jpg", jpeg_data, "image/jpeg")),
    ]
    data = {"device_id": "ESP32_CAM_01", "seq": "104", "direction": "IN"}
    headers = {"X-API-Key": settings.CAM_SHARED_API_KEY}

    res = client.post("/recognize", data=data, files=files, headers=headers)
    assert res.status_code == 200
    resp_data = res.json()

    assert resp_data["ok"] is True
    assert resp_data["seq"] == 104
    assert resp_data["direction"] == "IN"
    assert resp_data["action"] == "OPEN"
    assert resp_data["assignedSlot"] == "A1"
    assert resp_data["plateNormalized"] == "51A12345"
    assert resp_data["duplicate"] is False

    # Đảm bảo lệnh mở cổng đã được publish qua MQTT
    mock_mqtt.publish_gate_command.assert_called()
    last_call = mock_mqtt.publish_gate_command.call_args[1]
    assert last_call["seq"] == 104
    assert last_call["action"] == "OPEN"
    assert last_call["assigned_slot"] == "A1"


def test_recognize_duplicate_idempotency(client, mock_mqtt):
    """Gửi lại cùng seq 105 -> Lần 2 trả duplicate=True và không publish lại MQTT."""
    jpeg_data = _create_dummy_jpeg()
    headers = {"X-API-Key": settings.CAM_SHARED_API_KEY}

    # Lần 1
    files1 = [("images", ("frame_0.jpg", jpeg_data, "image/jpeg"))]
    data1 = {"device_id": "ESP32_CAM_01", "seq": "105", "direction": "IN"}
    res1 = client.post("/recognize", data=data1, files=files1, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["duplicate"] is False
    initial_mqtt_count = mock_mqtt.publish_gate_command.call_count

    # Lần 2 (trùng seq=105)
    files2 = [("images", ("frame_0.jpg", jpeg_data, "image/jpeg"))]
    data2 = {"device_id": "ESP32_CAM_01", "seq": "105", "direction": "IN"}
    res2 = client.post("/recognize", data=data2, files=files2, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["duplicate"] is True

    # Số lần gọi publish MQTT không được tăng thêm
    assert mock_mqtt.publish_gate_command.call_count == initial_mqtt_count


def test_recognize_direction_out(client, mock_mqtt):
    """Chiều xe ra (OUT) -> Luôn OPEN, assignedSlot rỗng."""
    jpeg_data = _create_dummy_jpeg()
    files = [("images", ("frame_0.jpg", jpeg_data, "image/jpeg"))]
    data = {"device_id": "ESP32_CAM_01", "seq": "106", "direction": "OUT"}
    headers = {"X-API-Key": settings.CAM_SHARED_API_KEY}

    res = client.post("/recognize", data=data, files=files, headers=headers)
    assert res.status_code == 200
    resp_data = res.json()

    assert resp_data["action"] == "OPEN"
    assert resp_data["assignedSlot"] == ""
    assert resp_data["direction"] == "OUT"


def test_health_includes_arbiter(client):
    """Endpoint /health phải trả về trạng thái của cả AI và Arbiter."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()

    assert "arbiter" in data
    assert "processedSeqsCount" in data["arbiter"]
    assert "activeTimersCount" in data["arbiter"]

