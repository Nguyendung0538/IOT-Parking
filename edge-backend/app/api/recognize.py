import logging
from typing import List, Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status

from app.config import settings
from app.models import RecognizeResponse

logger = logging.getLogger("api_recognize")

router = APIRouter(tags=["Recognition"])


@router.post("/recognize", response_model=RecognizeResponse)
async def recognize_plate_and_open_gate(
    request: Request,
    device_id: str = Form(default="ESP32_CAM_01"),
    seq: int = Form(...),
    direction: str = Form(...),
    images: List[UploadFile] = File(...),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Endpoint nhận multipart/form-data từ ESP32-CAM (hoặc simulate_cam.py).
    
    Quy trình đường găng:
    1. Xác thực X-API-Key
    2. Chạy AI Recognition Pipeline trên GPU RTX 5060 (~50ms)
    3. GateArbiter.decide() (< 1ms)
    4. Publish ngay parking/{DEVICE_ID}/gate/command qua MQTT
    5. Đẩy tác vụ lưu ảnh + ghi gateEvents vào background thread
    6. Trả lời HTTP 200 JSON cho ESP32-CAM để giải phóng bộ đệm ảnh
    """
    # 1. Xác thực API Key
    if not x_api_key or x_api_key.strip() != settings.CAM_SHARED_API_KEY:
        logger.warning("[API Recognize] Từ chối truy cập: X-API-Key không đúng (%s)", x_api_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )

    # 2. Chuẩn hóa chiều xe
    dir_norm = direction.upper().strip()
    if dir_norm not in ("IN", "OUT"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid direction: '{direction}'. Must be 'IN' or 'OUT'.",
        )

    # 3. Đọc dữ liệu ảnh nhị phân
    frames_bytes: List[bytes] = []
    for img_file in images:
        content = await img_file.read()
        if content:
            frames_bytes.append(content)

    ai_pipeline = getattr(request.app.state, "ai_pipeline", None)
    arbiter = getattr(request.app.state, "arbiter", None)
    mqtt_bridge = getattr(request.app.state, "mqtt", None)
    uploader = getattr(request.app.state, "storage_uploader", None)

    # 4. Nhận diện biển số (AI Pipeline)
    if ai_pipeline is not None and frames_bytes:
        pipeline_res = ai_pipeline.recognize_frames(frames_bytes)
    else:
        # Trường hợp không có AI Pipeline (hoặc không có frame ảnh)
        from app.recognition.pipeline import PipelineResult
        pipeline_res = PipelineResult(
            plate_raw="",
            plate_normalized="",
            plate_display="",
            confidence=0.0,
            is_valid=False,
            crop_jpeg_bytes=None,
            best_frame_index=0,
            latency_ms=0.0,
            detector_used="none",
            total_candidates=0,
        )

    # 5. Phân xử quyết định cổng (< 1ms)
    if arbiter is not None:
        decision = arbiter.decide(
            seq=seq,
            direction=dir_norm,
            plate_raw=pipeline_res.plate_raw,
            plate_normalized=pipeline_res.plate_normalized,
            plate_display=pipeline_res.plate_display,
            confidence=pipeline_res.confidence,
            is_valid=pipeline_res.is_valid,
        )
    else:
        # Fallback khẩn cấp nếu arbiter chưa khởi tạo
        from app.arbiter.gate_arbiter import ArbiterDecision
        decision = ArbiterDecision(
            seq=seq,
            direction=dir_norm,
            action="OPEN",
            status="ok",
            assigned_slot="A1" if dir_norm == "IN" else "",
            plate_raw=pipeline_res.plate_raw,
            plate_normalized=pipeline_res.plate_normalized,
            plate_display=pipeline_res.plate_display,
            plate_for_lcd=pipeline_res.plate_display[:12] if pipeline_res.is_valid else "KHONG RO",
            confidence=pipeline_res.confidence,
        )

    # 6. Bắn lệnh MQTT Gate Command (nếu không phải duplicate)
    if not decision.is_duplicate and mqtt_bridge is not None:
        mqtt_bridge.publish_gate_command(
            seq=decision.seq,
            action=decision.action,
            plate=decision.plate_for_lcd,
            assigned_slot=decision.assigned_slot,
            reason=decision.deny_reason,
        )

    # 7. Đẩy lưu ảnh và enqueue Firestore vào background uploader
    if not decision.is_duplicate and uploader is not None:
        best_full_jpeg: Optional[bytes] = None
        if frames_bytes and 0 <= pipeline_res.best_frame_index < len(frames_bytes):
            best_full_jpeg = frames_bytes[pipeline_res.best_frame_index]

        uploader.submit_capture_and_event(
            decision=decision,
            full_jpeg_bytes=best_full_jpeg,
            crop_jpeg_bytes=pipeline_res.crop_jpeg_bytes,
            latency_ms=pipeline_res.latency_ms,
        )

    logger.info(
        "[API Recognize] seq=%u (%s) -> Plate='%s' (conf=%.2f) -> %s (slot=%s, status=%s, latency=%.1fms)",
        seq,
        dir_norm,
        decision.plate_display or "KHONG RO",
        decision.confidence,
        decision.action,
        decision.assigned_slot,
        decision.status,
        pipeline_res.latency_ms,
    )

    return RecognizeResponse(
        ok=True,
        seq=decision.seq,
        direction=decision.direction,
        action=decision.action,
        status=decision.status,
        assignedSlot=decision.assigned_slot,
        plateRaw=decision.plate_raw,
        plateNormalized=decision.plate_normalized,
        plateDisplay=decision.plate_display,
        confidence=decision.confidence,
        latencyMs=pipeline_res.latency_ms,
        duplicate=decision.is_duplicate,
        denyReason=decision.deny_reason,
        flags=decision.flags,
    )

