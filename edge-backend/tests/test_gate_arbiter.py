"""Unit tests for Fast Gate Arbiter (Phase S5).

Bao phủ 10 kịch bản phân xử theo thiết kế:
- S5-T1: Khách vãng lai vào bãi trống
- S5-T2: Bảo vệ ô đã đặt trước (ST-03)
- S5-T3: Chủ xe có đặt trước đến đúng giờ
- S5-T4: Ô đặt trước bị đỗ đè, chuyển sang ô trống dự phòng (substitution)
- S5-T5: Ô đặt trước bị đỗ đè, bãi đã hết sạch chỗ -> DENY (RESERVED_SLOT_TAKEN)
- S5-T6: Bãi đầy hoàn toàn -> DENY (FULL)
- S5-T7: Không đọc được biển số (unrecognized) khi bãi còn chỗ -> OPEN
- S5-T8: Xe đi ra (direction="OUT") -> Luôn OPEN
- S5-T9: Fail-safe Camera Timeout 5.0s (ST-04)
- S5-T10: Chống trùng seq (Idempotency)
"""

from datetime import datetime, timedelta, timezone
import time
from unittest.mock import MagicMock
import pytest

from app.arbiter.gate_arbiter import ArbiterDecision, GateArbiter
from app.config import settings
from app.models import ReservationItem, StatePayload
from app.state_cache import StateCache


@pytest.fixture
def test_cache():
    return StateCache(slot_ids=["A1", "A2", "B1", "B2"])


@pytest.fixture
def test_mqtt():
    m = MagicMock()
    m.publish_gate_command.return_value = True
    return m


@pytest.fixture
def arbiter(test_cache, test_mqtt):
    a = GateArbiter(cache=test_cache, mqtt_bridge=test_mqtt)
    yield a
    a.stop()


def test_s5_t1_guest_empty_lot(arbiter):
    """S5-T1: Khách vãng lai vào bãi hoàn toàn trống -> Cấp ô A1, OPEN."""
    dec = arbiter.decide(
        seq=1,
        direction="IN",
        plate_raw="61F22261",
        plate_normalized="61F22261",
        plate_display="61F-222.61",
        confidence=0.92,
        is_valid=True,
    )
    assert dec.action == "OPEN"
    assert dec.assigned_slot == "A1"
    assert dec.status == "ok"
    assert dec.matched_reservation_id is None
    assert dec.plate_for_lcd == "61F-222.61"
    assert dec.is_duplicate is False


def test_s5_t2_protect_reserved_slot(arbiter, test_cache):
    """S5-T2: Ô A1 đã được người khác đặt trước. Khách vãng lai vào -> Bỏ qua A1, cấp A2."""
    now = datetime.now(timezone.utc)
    test_cache.upsert_reservation(
        ReservationItem(
            id="res_user_1",
            slotId="A1",
            plate="51A11111",
            expiresAt=now + timedelta(minutes=30),
        )
    )

    dec = arbiter.decide(
        seq=2,
        direction="IN",
        plate_raw="61F22261",
        plate_normalized="61F22261",
        plate_display="61F-222.61",
        confidence=0.90,
        is_valid=True,
    )
    assert dec.action == "OPEN"
    assert dec.assigned_slot == "A2"  # A1 bị khóa bởi reservation
    assert dec.status == "ok"
    assert dec.matched_reservation_id is None


def test_s5_t3_reservation_match(arbiter, test_cache):
    """S5-T3: Xe có đặt trước đến đúng giờ -> Gán đúng ô đã đặt (B1)."""
    now = datetime.now(timezone.utc)
    test_cache.upsert_reservation(
        ReservationItem(
            id="res_vip_88",
            slotId="B1",
            plate="61F22261",
            expiresAt=now + timedelta(minutes=45),
        )
    )

    dec = arbiter.decide(
        seq=3,
        direction="IN",
        plate_raw="61F-222.61",
        plate_normalized="61F22261",
        plate_display="61F-222.61",
        confidence=0.95,
        is_valid=True,
    )
    assert dec.action == "OPEN"
    assert dec.assigned_slot == "B1"
    assert dec.matched_reservation_id == "res_vip_88"
    assert "matched_reservation" in dec.flags


def test_s5_t4_reserved_slot_substituted(arbiter, test_cache):
    """S5-T4: Xe có đặt chỗ B1 nhưng B1 đang bị xe khác đỗ đè (sensorOccupied=True).
    Hệ thống tự động chuyển sang ô trống dự phòng (A1).
    """
    now = datetime.now(timezone.utc)
    test_cache.upsert_reservation(
        ReservationItem(
            id="res_vip_88",
            slotId="B1",
            plate="61F22261",
            expiresAt=now + timedelta(minutes=45),
        )
    )
    # Giả lập ô B1 đã bị chiếm
    test_cache.apply_state(StatePayload(slots={"B1": {"occupied": True}}))

    dec = arbiter.decide(
        seq=4,
        direction="IN",
        plate_raw="61F-222.61",
        plate_normalized="61F22261",
        plate_display="61F-222.61",
        confidence=0.95,
        is_valid=True,
    )
    assert dec.action == "OPEN"
    assert dec.assigned_slot == "A1"  # Thay thế sang ô trống đầu tiên
    assert dec.matched_reservation_id == "res_vip_88"
    assert "matched_reservation" in dec.flags
    assert "reserved_slot_substituted" in dec.flags


def test_s5_t5_reserved_slot_taken_and_lot_full(arbiter, test_cache):
    """S5-T5: Xe có đặt chỗ B1 nhưng B1 bị chiếm và cả 3 ô còn lại cũng hết chỗ
    -> DENY với reason = RESERVED_SLOT_TAKEN để Repo 2 hoàn tiền cọc.
    """
    now = datetime.now(timezone.utc)
    test_cache.upsert_reservation(
        ReservationItem(
            id="res_vip_88",
            slotId="B1",
            plate="61F22261",
            expiresAt=now + timedelta(minutes=45),
        )
    )
    # Toàn bộ 4 ô đều bị chiếm hoặc reserved
    test_cache.apply_state(
        StatePayload(
            slots={
                "A1": {"occupied": True},
                "A2": {"occupied": True},
                "B1": {"occupied": True},
                "B2": {"occupied": True},
            }
        )
    )

    dec = arbiter.decide(
        seq=5,
        direction="IN",
        plate_raw="61F-222.61",
        plate_normalized="61F22261",
        plate_display="61F-222.61",
        confidence=0.95,
        is_valid=True,
    )
    assert dec.action == "DENY"
    assert dec.status == "denied_reserved_taken"
    assert dec.deny_reason == "RESERVED_SLOT_TAKEN"
    assert dec.matched_reservation_id == "res_vip_88"


def test_s5_t6_lot_full_for_guest(arbiter, test_cache):
    """S5-T6: Bãi đầy (A1, A2 có xe đỗ, B1, B2 có đặt chỗ) -> Khách vãng lai bị DENY (FULL)."""
    now = datetime.now(timezone.utc)
    test_cache.apply_state(
        StatePayload(
            slots={
                "A1": {"occupied": True},
                "A2": {"occupied": True},
            }
        )
    )
    test_cache.upsert_reservation(
        ReservationItem(id="r1", slotId="B1", plate="30A11111", expiresAt=now + timedelta(minutes=20))
    )
    test_cache.upsert_reservation(
        ReservationItem(id="r2", slotId="B2", plate="30A22222", expiresAt=now + timedelta(minutes=20))
    )

    dec = arbiter.decide(
        seq=6,
        direction="IN",
        plate_raw="51A99999",
        plate_normalized="51A99999",
        plate_display="51A-999.99",
        confidence=0.88,
        is_valid=True,
    )
    assert dec.action == "DENY"
    assert dec.deny_reason == "FULL"
    assert dec.status == "denied_full"
    assert "lot_full" in dec.flags


def test_s5_t7_unrecognized_plate_with_free_slots(arbiter, test_cache):
    """S5-T7: Không đọc được biển số (unrecognized) nhưng bãi còn chỗ -> OPEN kèm cờ unrecognized."""
    # Ô A1, A2 bị chiếm, B1 còn trống
    test_cache.apply_state(
        StatePayload(
            slots={
                "A1": {"occupied": True},
                "A2": {"occupied": True},
            }
        )
    )

    dec = arbiter.decide(
        seq=7,
        direction="IN",
        plate_raw="",
        plate_normalized="",
        plate_display="",
        confidence=0.0,
        is_valid=False,
    )
    assert dec.action == "OPEN"
    assert dec.assigned_slot == "B1"
    assert dec.status == "unrecognized"
    assert "unrecognized" in dec.flags
    assert dec.plate_for_lcd == "KHONG RO"


def test_s5_t8_direction_out(arbiter):
    """S5-T8: Chiều xe ra (OUT) -> Luôn OPEN."""
    # Ra có biển số rõ
    dec1 = arbiter.decide(
        seq=8,
        direction="OUT",
        plate_raw="61F22261",
        plate_normalized="61F22261",
        plate_display="61F-222.61",
        confidence=0.91,
        is_valid=True,
    )
    assert dec1.action == "OPEN"
    assert dec1.assigned_slot == ""
    assert dec1.status == "ok"

    # Ra không đọc được biển
    dec2 = arbiter.decide(
        seq=9,
        direction="OUT",
        plate_raw="",
        plate_normalized="",
        plate_display="",
        confidence=0.0,
        is_valid=False,
    )
    assert dec2.action == "OPEN"
    assert dec2.status == "unrecognized"
    assert "exit_manual_reconcile" in dec2.flags


def test_s5_t9_camera_timeout_failsafe(arbiter, test_mqtt, monkeypatch):
    """S5-T9: Khi nhận gate/request nhưng CAM không gọi /recognize -> Sau timeout tự động mở cổng."""
    # Giảm timeout xuống 0.1s để test nhanh không bị chờ 5s
    monkeypatch.setattr(settings, "CAMERA_TIMEOUT_S", 0.1)

    arbiter.on_gate_request(seq=99, direction="IN")
    assert 99 in arbiter._timers

    # Chờ 0.2s để timer kích hoạt
    time.sleep(0.2)

    # Đảm bảo lệnh mở cổng đã được publish qua MQTT
    test_mqtt.publish_gate_command.assert_called_once()
    call_args = test_mqtt.publish_gate_command.call_args[1]
    assert call_args["seq"] == 99
    assert call_args["action"] == "OPEN"
    assert call_args["assigned_slot"] == "A1"
    assert call_args["plate"] == "KHONG RO"


def test_s5_t10_idempotency_duplicate_seq(arbiter):
    """S5-T10: Gửi 2 lần cùng seq -> Lần 2 trả duplicate=True và không tạo quyết định mới."""
    dec1 = arbiter.decide(
        seq=50,
        direction="IN",
        plate_raw="61F22261",
        plate_normalized="61F22261",
        plate_display="61F-222.61",
        confidence=0.92,
        is_valid=True,
    )
    assert dec1.is_duplicate is False
    assert dec1.assigned_slot == "A1"

    # Gọi lại với cùng seq 50 (dù đổi plate)
    dec2 = arbiter.decide(
        seq=50,
        direction="IN",
        plate_raw="51A99999",
        plate_normalized="51A99999",
        plate_display="51A-999.99",
        confidence=0.85,
        is_valid=True,
    )
    assert dec2.is_duplicate is True
    assert dec2.status == "duplicate_ignored"
    # Giữ nguyên gán slot ban đầu
    assert dec2.assigned_slot == "A1"
    assert dec2.plate_display == "61F-222.61"

