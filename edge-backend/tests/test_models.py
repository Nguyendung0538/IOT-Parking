"""Unit tests for Pydantic Models and JSON serialization."""

import json
from app.models import (
    GateCommandPayload,
    GateRequestPayload,
    ReservedMaskPayload,
    SlotState,
    StatePayload,
)


def test_state_payload_deserialization():
    raw_json = """
    {
        "deviceId": "ESP32_PARKING_01",
        "uptime": 125,
        "freeSlots": 3,
        "totalSlots": 4,
        "barrierOpen": false,
        "gateState": "IDLE",
        "slots": {
            "A1": {"occupied": true, "reserved": false},
            "A2": {"occupied": false, "reserved": false},
            "B1": {"occupied": false, "reserved": true},
            "B2": {"occupied": false, "reserved": false}
        },
        "summary": {
            "total_available": 3,
            "total_occupied": 1
        }
    }
    """
    payload = StatePayload.model_validate_json(raw_json)
    assert payload.deviceId == "ESP32_PARKING_01"
    assert payload.uptime == 125
    assert payload.freeSlots == 3
    assert payload.barrierOpen is False
    assert payload.gateState == "IDLE"
    assert len(payload.slots) == 4
    assert payload.slots["A1"].occupied is True
    assert payload.slots["A2"].occupied is False
    assert payload.slots["B1"].reserved is True


def test_gate_request_payload_deserialization():
    raw_json = """
    {
        "deviceId": "ESP32_PARKING_01",
        "seq": 42,
        "direction": "IN",
        "freeSlots": 2,
        "timestamp": 1731234567,
        "offlineFallback": true
    }
    """
    payload = GateRequestPayload.model_validate_json(raw_json)
    assert payload.seq == 42
    assert payload.direction == "IN"
    assert payload.offlineFallback is True


def test_gate_command_payload_serialization():
    cmd = GateCommandPayload(
        seq=10,
        action="OPEN",
        plate="29A-99999",
        assignedSlot="A2",
        reason="authorized_auto",
    )
    json_str = cmd.model_dump_json(exclude_none=True)
    parsed = json.loads(json_str)

    assert parsed["seq"] == 10
    assert parsed["action"] == "OPEN"
    assert parsed["plate"] == "29A-99999"
    assert parsed["assignedSlot"] == "A2"
    assert parsed["reason"] == "authorized_auto"


def test_reserved_mask_payload():
    mask = ReservedMaskPayload(A1=True, A2=False, B1=True, B2=False)
    dumped = mask.model_dump()
    assert dumped == {"A1": True, "A2": False, "B1": True, "B2": False}

