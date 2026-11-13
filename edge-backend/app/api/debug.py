"""Debug & Testing API endpoints for Edge Backend.

Cho phép can thiệp, quan sát và kiểm thử hệ thống hoàn toàn mà không cần phần cứng thật
hoặc khi chạy ở chế độ mô phỏng / ngắt kết nối Firebase.
"""

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Dict, List, Optional

import cv2
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
import numpy as np
from pydantic import BaseModel, Field

from app.config import settings
from app.models import ReservationItem, SlotState, StatePayload

router = APIRouter(prefix="/debug", tags=["Debug"])

# Lưu các phiên chụp test đang chờ phản hồi từ ESP32-CAM
_pending_cam_tests: Dict[str, Dict[str, Any]] = {}


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


def _run_yolo_and_visualize(ai_pipeline: Any, frames_bytes: List[bytes]) -> Dict[str, Any]:
    """Chạy YOLO + OCR và trả về kết quả kèm ảnh gốc (có vẽ BBox) & ảnh crop dạng Base64."""
    res = ai_pipeline.recognize_frames(frames_bytes)

    best_idx = res.best_frame_index if 0 <= res.best_frame_index < len(frames_bytes) else 0
    raw_bytes = frames_bytes[best_idx]

    nparr = np.frombuffer(raw_bytes, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    yolo_boxes: List[Dict[str, Any]] = []
    annotated_b64: Optional[str] = None
    crop_b64: Optional[str] = None

    if img_bgr is not None and img_bgr.size > 0:
        annotated = img_bgr.copy()
        detected_plates = ai_pipeline.detector.detect_and_crop(img_bgr)
        for dp in detected_plates:
            x1, y1, x2, y2 = dp.bbox
            color = (0, 255, 100) if dp.source == "yolo" else (0, 165, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = f"{dp.source.upper()} {dp.confidence:.2f}"
            cv2.putText(
                annotated,
                label,
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )
            yolo_boxes.append({
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "confidence": round(float(dp.confidence), 3),
                "source": dp.source,
            })

        _, ann_buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ann_buf is not None:
            annotated_b64 = base64.b64encode(ann_buf.tobytes()).decode("ascii")

        # Ưu tiên ảnh crop từ kết quả voting; nếu OCR không đọc được chữ vẫn lấy ảnh crop của YOLO để kiểm tra
        if res.crop_jpeg_bytes:
            crop_b64 = base64.b64encode(res.crop_jpeg_bytes).decode("ascii")
        elif detected_plates:
            _, crop_buf = cv2.imencode(".jpg", detected_plates[0].crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if crop_buf is not None:
                crop_b64 = base64.b64encode(crop_buf.tobytes()).decode("ascii")
    else:
        annotated_b64 = base64.b64encode(raw_bytes).decode("ascii")

    return {
        "status": "success",
        "plateRaw": res.plate_raw,
        "plateNormalized": res.plate_normalized,
        "plateDisplay": res.plate_display,
        "confidence": res.confidence,
        "isValid": res.is_valid,
        "latencyMs": res.latency_ms,
        "detectorUsed": res.detector_used,
        "totalCandidates": res.total_candidates,
        "bestFrameIndex": res.best_frame_index,
        "hasCropImage": crop_b64 is not None,
        "yoloDetected": len(yolo_boxes) > 0,
        "yoloBoxes": yolo_boxes,
        "annotatedImageBase64": annotated_b64,
        "cropImageBase64": crop_b64,
    }


@router.post("/recognize-test")
async def test_recognition(
    request: Request,
    images: List[UploadFile] = File(..., description="Ảnh JPEG chụp biển số xe (1 hoặc nhiều frame)"),
) -> Dict[str, Any]:
    """Kiểm thử trực tiếp Pipeline AI (YOLO + EasyOCR + Voting) qua upload ảnh."""
    ai_pipeline = getattr(request.app.state, "ai_pipeline", None)
    if not ai_pipeline:
        raise HTTPException(status_code=503, detail="AI Pipeline chưa được khởi tạo trên server")

    frames_bytes: List[bytes] = []
    for img_file in images:
        content = await img_file.read()
        if content:
            frames_bytes.append(content)

    if not frames_bytes:
        raise HTTPException(status_code=400, detail="Không có dữ liệu ảnh hợp lệ")

    return _run_yolo_and_visualize(ai_pipeline, frames_bytes)


class CamTestCaptureRequest(BaseModel):
    useFlash: bool = Field(False, description="Bật đèn Flash LED khi chụp test")
    timeoutSeconds: float = Field(8.0, description="Thời gian tối đa chờ ảnh từ ESP32-CAM")


@router.get("/cam-status")
def get_cam_test_status(request: Request) -> Dict[str, Any]:
    """Lấy trạng thái kết nối ESP32-CAM, MQTT và AI Pipeline cho trang Web Test Cam."""
    cache = request.app.state.cache
    mqtt = getattr(request.app.state, "mqtt", None)
    ai_pipeline = getattr(request.app.state, "ai_pipeline", None)
    snap = cache.snapshot()
    return {
        "camOnline": snap.get("camOnline", False),
        "mqttConnected": bool(mqtt and getattr(mqtt, "_connected", False)),
        "aiReady": ai_pipeline is not None,
    }


@router.post("/cam-test-capture")
async def trigger_cam_test_capture(
    request: Request,
    body: CamTestCaptureRequest = CamTestCaptureRequest(),
) -> Dict[str, Any]:
    """Gửi lệnh MQTT kích hoạt ESP32-CAM chụp ảnh test và đợi kết quả từ YOLO."""
    mqtt = getattr(request.app.state, "mqtt", None)
    ai_pipeline = getattr(request.app.state, "ai_pipeline", None)
    if not ai_pipeline:
        raise HTTPException(status_code=503, detail="AI Pipeline chưa sẵn sàng")
    if not mqtt or not getattr(mqtt, "_connected", False):
        raise HTTPException(status_code=503, detail="MQTT Broker chưa kết nối. Hãy đảm bảo Mosquitto đang chạy.")

    test_id = f"test_{int(time.time() * 1000)}"
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    _pending_cam_tests[test_id] = {
        "event": event,
        "loop": loop,
        "result": None,
    }

    ok = mqtt.publish_cam_test(test_id=test_id, use_flash=body.useFlash)
    if not ok:
        _pending_cam_tests.pop(test_id, None)
        raise HTTPException(status_code=503, detail="Không thể gửi lệnh chụp tới ESP32-CAM qua MQTT")

    try:
        await asyncio.wait_for(event.wait(), timeout=body.timeoutSeconds)
    except asyncio.TimeoutError:
        _pending_cam_tests.pop(test_id, None)
        raise HTTPException(
            status_code=504,
            detail="Hết thời gian chờ ảnh từ ESP32-CAM (kiểm tra ESP32-CAM đã online và nạp firmware test chưa)",
        )

    entry = _pending_cam_tests.pop(test_id, None)
    if not entry or entry.get("result") is None:
        raise HTTPException(status_code=500, detail="Không nhận được dữ liệu ảnh hợp lệ")

    return entry["result"]


@router.post("/cam-test-upload")
async def receive_cam_test_upload(
    request: Request,
    test_id: str = Form(...),
    image: UploadFile = File(...),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Endpoint riêng nhận ảnh chụp test từ ESP32-CAM (không mở barie, không ghi Firestore)."""
    if not x_api_key or x_api_key.strip() != settings.CAM_SHARED_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )

    ai_pipeline = getattr(request.app.state, "ai_pipeline", None)
    if not ai_pipeline:
        raise HTTPException(status_code=503, detail="AI Pipeline chưa khởi tạo")

    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(status_code=400, detail="Empty image")

    result = _run_yolo_and_visualize(ai_pipeline, [img_bytes])

    pending = _pending_cam_tests.get(test_id)
    if pending:
        pending["result"] = result
        loop = pending["loop"]
        event = pending["event"]
        loop.call_soon_threadsafe(event.set)

    return {"ok": True, "testId": test_id}

