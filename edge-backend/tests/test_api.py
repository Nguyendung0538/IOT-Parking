"""Integration tests for Edge Backend HTTP APIs (/health, /debug)."""


def test_get_root(client):
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["service"] == "iot-parking-edge-backend"
    assert data["phase"] == "S4"


def test_get_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()

    assert "status" in data
    assert "mqtt" in data
    assert "cloud" in data
    assert "devices" in data
    assert "lot" in data
    assert data["mqtt"]["connected"] is True


def test_debug_state(client):
    res = client.get("/debug/state")
    assert res.status_code == 200
    data = res.json()
    assert "sensorOccupied" in data
    assert "reservedMask" in data
    assert "freeSlotsPublic" in data


def test_debug_reservations_crud(client):
    # 1. Tạo đặt chỗ cho ô A1
    body = {
        "slotId": "A1",
        "plate": "29B-55555",
        "ttlSeconds": 600,
        "resId": "test_res_a1",
    }
    post_res = client.post("/debug/reservations", json=body)
    assert post_res.status_code == 200
    post_data = post_res.json()
    assert post_data["status"] == "success"
    assert post_data["reservedMask"]["A1"] is True

    # Kiểm tra state cache đã cập nhật
    state_res = client.get("/debug/state")
    assert state_res.json()["reservedMask"]["A1"] is True

    # 2. Xóa đặt chỗ ô A1
    del_res = client.delete("/debug/reservations/test_res_a1")
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["status"] == "success"
    assert del_data["reservedMask"]["A1"] is False

    # Xóa lại -> trả về not_found nhưng không crash
    del_again = client.delete("/debug/reservations/test_res_a1")
    assert del_again.status_code == 200
    assert del_again.json()["status"] == "not_found"


def test_debug_open_gate(client, mock_mqtt):
    body = {
        "seq": 0,
        "action": "OPEN",
        "plate": "99A-12345",
        "assignedSlot": "B1",
        "reason": "manual_override",
    }
    res = client.post("/debug/open-gate", json=body)
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    mock_mqtt.publish_gate_command.assert_called_once()


def test_debug_simulate_state(client, dummy_cloud):
    body = {
        "occupied": {"A1": True, "A2": False, "B1": False, "B2": False},
        "barrierOpen": False,
        "gateState": "IDLE",
        "uptime": 50,
    }
    res = client.post("/debug/simulate-state", json=body)
    assert res.status_code == 200
    data = res.json()
    assert "A1" in data["diffs"]
    assert len(dummy_cloud.slot_updates) >= 1
    assert dummy_cloud.slot_updates[-1]["slotId"] == "A1"
    assert dummy_cloud.slot_updates[-1]["occupied"] is True


def test_debug_recognize_test(client):
    fake_img = b"\xff\xd8\xff\xe0\x00\x10JFIF"  # Header JPEG giả lập
    files = [("images", ("frame_0.jpg", fake_img, "image/jpeg"))]
    res = client.post("/debug/recognize-test", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["plateNormalized"] == "51A12345"
    assert data["plateDisplay"] == "51A-123.45"
    assert data["isValid"] is True

