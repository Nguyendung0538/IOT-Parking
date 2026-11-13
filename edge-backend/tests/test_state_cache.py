"""Unit tests for StateCache (Single source of truth in RAM)."""

from datetime import datetime, timedelta, timezone

from app.models import ReservationItem, SlotState, StatePayload
from app.state_cache import StateCache


def test_initial_cache_state():
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])
    snap = cache.snapshot()

    assert snap["gateOnline"] is False
    assert snap["camOnline"] is False
    assert snap["freeSlotsPublic"] == 4
    assert snap["activeReservationsCount"] == 0
    assert snap["reservedMask"] == {"A1": False, "A2": False, "B1": False, "B2": False}


def test_apply_state_diff_tracking():
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])

    # 1. Bắn state đầu tiên: A1 có xe
    p1 = StatePayload(
        deviceId="ESP32_PARKING_01",
        uptime=10,
        freeSlots=3,
        totalSlots=4,
        barrierOpen=False,
        gateState="IDLE",
        slots={
            "A1": SlotState(occupied=True),
            "A2": SlotState(occupied=False),
            "B1": SlotState(occupied=False),
            "B2": SlotState(occupied=False),
        },
    )
    diffs1 = cache.apply_state(p1)
    assert diffs1 == ["A1"]
    assert cache.get_sensor_occupied()["A1"] is True
    assert cache.free_slots_public() == 3

    # 2. Bắn lại state y hệt -> Không sinh diff nào
    diffs2 = cache.apply_state(p1)
    assert diffs2 == []

    # 3. Xe vào thêm ô B2, ô A1 rời đi
    p2 = StatePayload(
        deviceId="ESP32_PARKING_01",
        uptime=15,
        freeSlots=3,
        totalSlots=4,
        barrierOpen=False,
        gateState="IDLE",
        slots={
            "A1": SlotState(occupied=False),
            "A2": SlotState(occupied=False),
            "B1": SlotState(occupied=False),
            "B2": SlotState(occupied=True),
        },
    )
    diffs3 = cache.apply_state(p2)
    assert set(diffs3) == {"A1", "B2"}
    assert cache.get_sensor_occupied()["A1"] is False
    assert cache.get_sensor_occupied()["B2"] is True


def test_reservations_and_mask_computation():
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])

    now = datetime.now(timezone.utc)
    future = now + timedelta(minutes=30)
    past = now - timedelta(minutes=5)

    # Thêm 1 đặt chỗ hợp lệ cho A1, và 1 đã hết hạn cho B1
    item_valid = ReservationItem(id="r1", slotId="A1", plate="29A-11111", expiresAt=future)
    item_expired = ReservationItem(id="r2", slotId="B1", plate="29A-22222", expiresAt=past)

    cache.upsert_reservation(item_valid)
    cache.upsert_reservation(item_expired)

    # Mặt nạ tính toán chỉ bật True cho các reservation chưa hết hạn
    mask = cache.compute_reserved_mask()
    assert mask == {"A1": True, "A2": False, "B1": False, "B2": False}

    # Free slots public phải trừ ô đã reserved (dù sensor chưa có xe)
    # A1 reserved -> còn 3 ô trống (A2, B1, B2)
    assert cache.free_slots_public() == 3

    # Dọn dẹp hết hạn
    purged = cache.purge_expired_reservations()
    assert purged is True
    assert "r2" not in cache.snapshot()["activeReservations"]
    assert "r1" in cache.snapshot()["activeReservations"]


def test_reserved_mask_changed_and_mark_published():
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])

    # Lần đầu chưa publish -> mask_changed trả về mask hiện tại
    changed_mask = cache.reserved_mask_changed()
    assert changed_mask == {"A1": False, "A2": False, "B1": False, "B2": False}

    # Đánh dấu đã publish
    cache.mark_reserved_mask_published(changed_mask)
    assert cache.reserved_mask_changed() is None

    # Thêm reservation -> mask đổi -> trả về mask mới
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    cache.upsert_reservation(ReservationItem(id="r1", slotId="B2", plate="30E-88888", expiresAt=future))
    new_mask = cache.reserved_mask_changed()
    assert new_mask is not None
    assert new_mask["B2"] is True


def test_device_online_status_lwt():
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])

    # Chuyển Gate sang online
    changed = cache.set_device_online("gate", True)
    assert changed is True
    assert cache.is_gate_online() is True

    # Gọi lại với True -> Không đổi
    assert cache.set_device_online("gate", True) is False

    # Chuyển Gate sang offline
    assert cache.set_device_online("esp32_parking_01", False) is True
    assert cache.is_gate_online() is False

    # Cam online
    assert cache.set_device_online("cam", True) is True
    assert cache.is_cam_online() is True


def test_pending_requests_queue():
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])

    cache.add_pending_request(101, "IN")
    cache.add_pending_request(102, "OUT")

    assert cache.snapshot()["pendingRequestsCount"] == 2

    consumed = cache.consume_pending_request(101)
    assert consumed is not None
    assert consumed.seq == 101
    assert consumed.direction == "IN"

    # Consume lại -> None
    assert cache.consume_pending_request(101) is None
    assert cache.snapshot()["pendingRequestsCount"] == 1

