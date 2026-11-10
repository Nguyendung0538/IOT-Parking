"""Debug & Testing API endpoints for Edge Backend.

Cho phép can thiệp, quan sát và kiểm thử hệ thống hoàn toàn mà không cần phần cứng thật
hoặc khi chạy ở chế độ mô phỏng / ngắt kết nối Firebase.
"""

from datetime import datetime, timedelta, timezone
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.models import ReservationItem, SlotState, StatePayload

router = APIRouter(prefix="/debug", tags=["Debug"])


class CreateReservationRequest(BaseModel):
    slotId: str = Field(..., description="Mã ô đỗ (A1, A2, B1, B2)")
    plate: str = Field("29A-12345", description="Biển số xe đặt chỗ")
    ttlSeconds: int = Field(1800, description="Thời gian giữ chỗ tính bằng giây (mặc định 30 phút)")
    resId: Optional[str] = Field(None, description="Tùy chọn ID đặt chỗ, tự sinh nếu để trống")


class OpenGateRequest(BaseModel):
    seq: int = Field(0, description="Sequence number của request (0: lệnh khẩn cấp / thủ công)")
    action: str = Field("OPEN", description="OPEN hoặc DENY")
    plate: str = Field("DEBUG-PLATE", description="Biển số xe")
    assignedSlot: str = Field("", description="Ô được cấp")
    reason: str = Field("debug_manual_gate_command", description="Lý do mở cổng")


class SimulateStateRequest(BaseModel):
    occupied: Dict[str, bool] = Field(
        default_factory=lambda: {"A1": False, "A2": False, "B1": False, "B2": False},
        description="Trạng thái cảm biến các ô",
    )
    barrierOpen: bool = Field(False, description="Trạng thái barie")
    gateState: str = Field("IDLE", description="Trạng thái FSM cổng")
    uptime: int = Field(100, description="Uptime tính bằng giây")


@router.get("/state")
def get_debug_state(request: Request) -> Dict[str, Any]:
    """Lấy snapshot toàn bộ RAM StateCache hiện tại."""
    cache = request.app.state.cache
    return cache.snapshot()


@router.post("/reservations")
def create_reservation(request: Request, body: CreateReservationRequest) -> Dict[str, Any]:
    """Tạo hoặc cập nhật đặt chỗ giả lập, tự động cập nhật và publish mask xuống MQTT."""
    cache = request.app.state.cache
    mqtt = request.app.state.mqtt

    res_id = body.resId or f"debug_{body.slotId}_{int(time.time())}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=body.ttlSeconds)

    item = ReservationItem(
        id=res_id,
        slotId=body.slotId.upper(),
        plate=body.plate,
        expiresAt=expires_at,
    )

    cache.upsert_reservation(item)
    mask = cache.compute_reserved_mask()
    mqtt_published = mqtt.publish_reserved(mask, force=True)

    return {
        "status": "success",
        "reservation": {
            "id": item.id,
            "slotId": item.slotId,
            "plate": item.plate,
            "expiresAt": item.expiresAt.isoformat(),
        },
        "reservedMask": mask,
        "mqttPublished": mqtt_published,
    }


@router.delete("/reservations/{res_or_slot_id}")
def delete_reservation(request: Request, res_or_slot_id: str) -> Dict[str, Any]:
    """Hủy đặt chỗ theo ID hoặc theo mã ô đỗ (slotId), cập nhật mask xuống MQTT."""
    cache = request.app.state.cache
    mqtt = request.app.state.mqtt

    target = res_or_slot_id.strip()
    removed = cache.remove_reservation(target)

    # Nếu không tìm thấy theo res_id, thử quét theo slotId
    if not removed:
        snap = cache.snapshot()
        active = snap.get("activeReservations", {})
        for r_id, r_info in active.items():
            if r_info.get("slotId", "").upper() == target.upper():
                cache.remove_reservation(r_id)
                removed = True
                break

    mask = cache.compute_reserved_mask()
    mqtt_published = mqtt.publish_reserved(mask, force=True)

    return {
        "status": "success" if removed else "not_found",
        "removedTarget": target,
        "reservedMask": mask,
        "mqttPublished": mqtt_published,
    }


@router.post("/open-gate")
def open_gate(request: Request, body: OpenGateRequest) -> Dict[str, Any]:
    """Publish trực tiếp lệnh điều khiển barie xuống topic parking/{DEVICE_ID}/gate/command."""
    mqtt = request.app.state.mqtt
    success = mqtt.publish_gate_command(
        seq=body.seq,
        action=body.action,
        plate=body.plate,
        assigned_slot=body.assignedSlot,
        reason=body.reason,
    )
    if not success:
        raise HTTPException(status_code=503, detail="Không thể publish lệnh mở barie (MQTT chưa kết nối)")

    return {
        "status": "success",
        "command": body.model_dump(),
    }


@router.post("/simulate-state")
def simulate_state(request: Request, body: SimulateStateRequest) -> Dict[str, Any]:
    """Giả lập nhận gói tin trạng thái từ ESP32 để kiểm tra StateCache và đồng bộ Cloud."""
    cache = request.app.state.cache
    cloud = request.app.state.cloud

    slots = {
        s: SlotState(occupied=occ)
        for s, occ in body.occupied.items()
    }
    payload = StatePayload(
        deviceId="ESP32_PARKING_01",
        uptime=body.uptime,
        freeSlots=sum(1 for occ in body.occupied.values() if not occ),
        totalSlots=len(body.occupied),
        barrierOpen=body.barrierOpen,
        gateState=body.gateState,
        slots=slots,
    )

    diffs = cache.apply_state(payload)
    if cloud and diffs:
        current_occ = cache.get_sensor_occupied()
        cloud.enqueue_slots(diffs, current_occ)

    return {
        "status": "success",
        "diffs": diffs,
        "currentState": cache.snapshot(),
    }
