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


def test_s5_t1_guest_empty_lot(arbiter, monkeypatch):
    """S5-T1: Khách vãng lai vào bãi hoàn toàn trống -> Cấp ô A1, OPEN."""
    monkeypatch.setattr(settings, "ASSIGN_SLOT_ON_ENTRY", True)
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


def test_s5_t2_protect_reserved_slot(arbiter, test_cache, monkeypatch):
    """S5-T2: Ô A1 đã được người khác đặt trước. Khách vãng lai vào -> Bỏ qua A1, cấp A2."""
    monkeypatch.setattr(settings, "ASSIGN_SLOT_ON_ENTRY", True)
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
    """S5-T7: Không đọc được biển số (unrecognized) nhưng bãi còn chỗ -> WAIT_MANUAL chờ Web Admin nhập biển."""
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
    assert dec.action == "WAIT_MANUAL"
    assert dec.assigned_slot == ""
    assert dec.status == "pending_manual"
    assert dec.deny_reason == "UNRECOGNIZED_PLATE"
    assert "unrecognized" in dec.flags
    assert dec.plate_for_lcd == "KHONG RO"


def test_s5_t8_direction_out(arbiter, test_cache):
    """S5-T8: Chiều xe ra (OUT) -> Có yêu cầu ra từ App thì OPEN; chưa gửi yêu cầu ra thì DENY; không đọc được biển thì WAIT_MANUAL."""
    now = datetime.now(timezone.utc)
    from app.models import ExitRequestItem

    # 1. Ra có biển số rõ nhưng CHƯA gửi yêu cầu ra trên App -> DENY
    dec_denied = arbiter.decide(
        seq=80,
        direction="OUT",
        plate_raw="61F22261",
        plate_normalized="61F22261",
        plate_display="61F-222.61",
        confidence=0.91,
        is_valid=True,
    )
    assert dec_denied.action == "DENY"
    assert dec_denied.status == "denied_no_exit_request"
    assert dec_denied.deny_reason == "NO_EXIT_REQUEST"
    assert "no_exit_request" in dec_denied.flags

    # 2. Sau khi App gửi yêu cầu ra còn hạn -> OPEN
    test_cache.upsert_exit_request(
        ExitRequestItem(
            sessionId="sess_out_1",
            plate="61F22261",
            expiresAt=now + timedelta(minutes=5),
        )
    )
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

    # 3. Ra không đọc được biển -> chờ admin chọn xe trên Web
    dec2 = arbiter.decide(
        seq=9,
        direction="OUT",
        plate_raw="",
        plate_normalized="",
        plate_display="",
        confidence=0.0,
        is_valid=False,
    )
    assert dec2.action == "WAIT_MANUAL"
    assert dec2.status == "pending_manual"
    assert dec2.deny_reason == "UNRECOGNIZED_PLATE"
    assert "exit_manual_reconcile" in dec2.flags


def test_s5_t9_camera_timeout_failsafe(arbiter, test_mqtt, monkeypatch):
    """S5-T9: Khi nhận gate/request nhưng CAM không gọi /recognize -> Sau timeout chuyển WAIT_MANUAL chờ Web duyệt."""
    # Giảm timeout xuống 0.1s để test nhanh không bị chờ 5s
    monkeypatch.setattr(settings, "CAMERA_TIMEOUT_S", 0.1)

    arbiter.on_gate_request(seq=99, direction="IN")
    assert 99 in arbiter._timers

    # Chờ 0.2s để timer kích hoạt
    time.sleep(0.2)

    # Đảm bảo lệnh WAIT_MANUAL đã được publish qua MQTT (giữ barie đóng chờ Admin)
    test_mqtt.publish_gate_command.assert_called_once()
    call_args = test_mqtt.publish_gate_command.call_args[1]
    assert call_args["seq"] == 99
    assert call_args["action"] == "WAIT_MANUAL"
    assert call_args["assigned_slot"] == ""
    assert call_args["plate"] == "KHONG RO"


def test_s5_t10_idempotency_duplicate_seq(arbiter, monkeypatch):
    """S5-T10: Gửi 2 lần cùng seq -> Lần 2 trả duplicate=True và không tạo quyết định mới."""
    monkeypatch.setattr(settings, "ASSIGN_SLOT_ON_ENTRY", True)
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


def test_exit_plate_not_in_lot_triggers_wait_manual(arbiter, test_cache):
    """Khi danh sách phiên đỗ đã đồng bộ mà biển xe ra không có trong bãi -> WAIT_MANUAL (PLATE_NOT_IN_LOT)."""
    from app.models import ExitRequestItem

    now = datetime.now(timezone.utc)
    test_cache.replace_active_sessions({"sess_1": "30A12345"})

    # 1. Biển quét được không nằm trong active sessions -> giữ barie và chờ duyệt Web Admin
    dec_unknown = arbiter.decide(
        seq=201,
        direction="OUT",
        plate_raw="30A99999",
        plate_normalized="30A99999",
        plate_display="30A-999.99",
        confidence=0.88,
        is_valid=True,
    )
    assert dec_unknown.action == "WAIT_MANUAL"
    assert dec_unknown.status == "pending_manual"
    assert dec_unknown.deny_reason == "PLATE_NOT_IN_LOT"
    assert "plate_not_in_lot" in dec_unknown.flags
    assert "exit_manual_reconcile" in dec_unknown.flags

    # 2. Biển có trong bãi nhưng chưa có yêu cầu ra -> DENY (NO_EXIT_REQUEST)
    dec_no_req = arbiter.decide(
        seq=202,
        direction="OUT",
        plate_raw="30A12345",
        plate_normalized="30A12345",
        plate_display="30A-123.45",
        confidence=0.95,
        is_valid=True,
    )
    assert dec_no_req.action == "DENY"
    assert dec_no_req.deny_reason == "NO_EXIT_REQUEST"

    # 3. Biển có trong bãi VÀ có yêu cầu ra -> OPEN
    test_cache.upsert_exit_request(
        ExitRequestItem(
            sessionId="sess_1",
            plate="30A12345",
            expiresAt=now + timedelta(minutes=5),
        )
    )
    dec_ok = arbiter.decide(
        seq=203,
        direction="OUT",
        plate_raw="30A12345",
        plate_normalized="30A12345",
        plate_display="30A-123.45",
        confidence=0.95,
        is_valid=True,
    )
    assert dec_ok.action == "OPEN"
    assert dec_ok.status == "ok"


def test_gate_counter_blocks_walkin_when_lot_full_by_gate(arbiter, test_cache):
    """4 xe đã qua cổng (carsInLot=4) dù cảm biến ô đỗ vẫn trống -> từ chối khách vãng lai."""
    test_cache.set_cars_in_lot(4)

    dec = arbiter.decide(
        seq=301,
        direction="IN",
        plate_raw="51A99999",
        plate_normalized="51A99999",
        plate_display="51A-999.99",
        confidence=0.92,
        is_valid=True,
    )
    assert dec.action == "DENY"
    assert dec.status == "denied_full"
    assert dec.deny_reason == "FULL"
    assert "lot_full" in dec.flags


def test_gate_counter_with_reserved_deduction(arbiter, test_cache):
    """3 xe trong bãi + 1 ô đặt trước (cảm biến ô đỗ vẫn trống):
    - Khách vãng lai bị chặn (3 + 1 >= 4)
    - Chủ xe đặt trước vẫn được vào ô đã đặt
    - Khi carsInLot=4 (full vật lý), ngay cả chủ xe đặt trước cũng bị từ chối.
    """
    now = datetime.now(timezone.utc)
    test_cache.upsert_reservation(
        ReservationItem(
            id="res_hold_1",
            slotId="B2",
            plate="30A77777",
            expiresAt=now + timedelta(minutes=30),
        )
    )
    test_cache.set_cars_in_lot(3)

    # 1. Khách vãng lai bị chặn vì 3 xe trong bãi + 1 ô đặt trước = 4/4
    dec_walkin = arbiter.decide(
        seq=302,
        direction="IN",
        plate_raw="51A88888",
        plate_normalized="51A88888",
        plate_display="51A-888.88",
        confidence=0.91,
        is_valid=True,
    )
    assert dec_walkin.action == "DENY"
    assert dec_walkin.deny_reason == "FULL"
    assert dec_walkin.status == "denied_full"

    # 2. Chủ xe có reservation vẫn được vào đúng ô B2
    dec_owner = arbiter.decide(
        seq=303,
        direction="IN",
        plate_raw="30A77777",
        plate_normalized="30A77777",
        plate_display="30A-777.77",
        confidence=0.95,
        is_valid=True,
    )
    assert dec_owner.action == "OPEN"
    assert dec_owner.assigned_slot == "B2"
    assert dec_owner.matched_reservation_id == "res_hold_1"

    # 3. Nếu carsInLot=4 (4 xe đã vào vật lý), chủ xe đặt chỗ khác cũng bị chặn
    test_cache.set_cars_in_lot(4)
    dec_owner_full = arbiter.decide(
        seq=304,
        direction="IN",
        plate_raw="30A77777",
        plate_normalized="30A77777",
        plate_display="30A-777.77",
        confidence=0.95,
        is_valid=True,
    )
    assert dec_owner_full.action == "DENY"
    assert dec_owner_full.status == "denied_reserved_taken"
    assert dec_owner_full.deny_reason == "RESERVED_SLOT_TAKEN"


