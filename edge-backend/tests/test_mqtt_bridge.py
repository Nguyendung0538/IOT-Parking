"""Unit tests for MqttBridge message handling and publishing."""

import json
from unittest.mock import MagicMock

from app.config import settings
from app.mqtt.bridge import MqttBridge
from app.state_cache import StateCache


def test_mqtt_bridge_handle_state(dummy_cloud):
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])
    bridge = MqttBridge(cache=cache, settings=settings, cloud=dummy_cloud)

    state_json = json.dumps({
        "deviceId": "ESP32_PARKING_01",
        "uptime": 200,
        "freeSlots": 3,
        "totalSlots": 4,
        "barrierOpen": False,
        "gateState": "IDLE",
        "slots": {
            "A1": {"occupied": True},
            "A2": {"occupied": False},
            "B1": {"occupied": False},
            "B2": {"occupied": False},
        },
    }).encode("utf-8")

    bridge._handle_state(state_json)

    # 1. StateCache đã ghi nhận A1 occupied
    assert cache.get_sensor_occupied()["A1"] is True

    # 2. DummyCloud đã nhận diff cập nhật của A1
    assert len(dummy_cloud.slot_updates) == 1
    assert dummy_cloud.slot_updates[0] == {"slotId": "A1", "occupied": True}


def test_mqtt_bridge_handle_gate_request_normal(dummy_cloud):
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])
    bridge = MqttBridge(cache=cache, settings=settings, cloud=dummy_cloud)

    req_json = json.dumps({
        "deviceId": "ESP32_PARKING_01",
        "seq": 105,
        "direction": "IN",
        "freeSlots": 2,
        "offlineFallback": False,
    }).encode("utf-8")

    bridge._handle_gate_request(req_json)

    # Request bình thường -> Lưu vào pending queue
    snap = cache.snapshot()
    assert snap["pendingRequestsCount"] == 1
    consumed = cache.consume_pending_request(105)
    assert consumed is not None
    assert consumed.direction == "IN"


def test_mqtt_bridge_handle_gate_request_fallback(dummy_cloud):
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])
    bridge = MqttBridge(cache=cache, settings=settings, cloud=dummy_cloud)

    req_json = json.dumps({
        "deviceId": "ESP32_PARKING_01",
        "seq": 106,
        "direction": "IN",
        "freeSlots": 1,
        "offlineFallback": True,
    }).encode("utf-8")

    bridge._handle_gate_request(req_json)

    # Fallback event -> Gửi ngay lên Cloud
    assert len(dummy_cloud.fallback_events) == 1
    assert dummy_cloud.fallback_events[0]["status"] == "offline_fallback"
    assert dummy_cloud.fallback_events[0]["direction"] == "IN"
    assert "offline_fallback" in dummy_cloud.fallback_events[0]["flags"]


def test_mqtt_bridge_handle_device_status(dummy_cloud):
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])
    bridge = MqttBridge(cache=cache, settings=settings, cloud=dummy_cloud)

    # Báo gate online
    bridge._handle_device_status("gate", b"online")
    assert cache.is_gate_online() is True
    assert len(dummy_cloud.device_statuses) == 1
    assert dummy_cloud.device_statuses[0]["online"] is True
    assert dummy_cloud.device_statuses[0]["deviceId"] == settings.DEVICE_ID

    # Báo gate offline
    bridge._handle_device_status("gate", b"offline")
    assert cache.is_gate_online() is False
    assert len(dummy_cloud.device_statuses) == 2
    assert dummy_cloud.device_statuses[1]["online"] is False


def test_mqtt_bridge_publish_reserved():
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])
    bridge = MqttBridge(cache=cache, settings=settings)

    # Giả lập client paho publish thành công
    mock_client = MagicMock()
    mock_info = MagicMock()
    mock_info.rc = 0  # MQTT_ERR_SUCCESS
    mock_client.publish.return_value = mock_info
    bridge.client = mock_client
    bridge._connected = True

    mask = {"A1": True, "A2": False, "B1": False, "B2": False}

    # Lần 1: publish thành công
    ok = bridge.publish_reserved(mask)
    assert ok is True
    mock_client.publish.assert_called_once()
    args, kwargs = mock_client.publish.call_args
    assert args[0] == settings.topic_reserved
    assert kwargs.get("retain") is True

    # Lần 2: cùng mask đó và không force -> không publish lại
    mock_client.reset_mock()
    ok2 = bridge.publish_reserved(mask, force=False)
    assert ok2 is False
    mock_client.publish.assert_not_called()

    # Lần 3: force=True -> publish lại
    ok3 = bridge.publish_reserved(mask, force=True)
    assert ok3 is True
    mock_client.publish.assert_called_once()


def test_mqtt_bridge_publish_gate_command():
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])
    bridge = MqttBridge(cache=cache, settings=settings)

    mock_client = MagicMock()
    mock_info = MagicMock()
    mock_info.rc = 0
    mock_client.publish.return_value = mock_info
    bridge.client = mock_client
    bridge._connected = True

    ok = bridge.publish_gate_command(
        seq=5,
        action="OPEN",
        plate="30G-12345",
        assigned_slot="B2",
        reason="test_command",
    )
    assert ok is True
    mock_client.publish.assert_called_once()
    args, kwargs = mock_client.publish.call_args
    assert args[0] == settings.topic_gate_command
    assert kwargs.get("retain") is False

    payload = json.loads(args[1])
    assert payload["seq"] == 5
    assert payload["action"] == "OPEN"
    assert payload["plate"] == "30G-12345"
    assert payload["assignedSlot"] == "B2"
