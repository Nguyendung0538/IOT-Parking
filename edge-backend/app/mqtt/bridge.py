import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.request

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from app.config import settings
from app.models import GateCommandPayload, GateRequestPayload, StatePayload
from app.state_cache import StateCache

logger = logging.getLogger("mqtt_bridge")


def fetch_phone_cam_frames(
    url: str,
    num_frames: int = 2,
    interval_ms: int = 120,
    timeout_s: float = 2.5,
) -> List[bytes]:
    """Kéo ảnh JPEG tức thời từ app IP Webcam trên điện thoại qua HTTP GET."""
    frames: List[bytes] = []
    if not url:
        return frames

    for idx in range(max(1, num_frames)):
        try:
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if data:
                        frames.append(data)
        except Exception as e:
            logger.warning("[PhoneCam] Lỗi kéo frame #%d từ %s: %s", idx + 1, url, e)
            break

        if idx < num_frames - 1 and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)

    return frames


class MqttBridge:
    """MQTT Bridge kết nối giữa Mosquitto Broker và Edge Server.
    
    Chịu trách nhiệm:
    - Nhận telemetry `parking/{DEVICE_ID}/state` và cập nhật vào StateCache
    - Bắt sự kiện LWT `online/offline` của Gate và Cam
    - Nhận yêu cầu xác thực `parking/{DEVICE_ID}/gate/request`
    - Phát lệnh mở cổng `parking/{DEVICE_ID}/gate/command` (Admin / Arbiter)
    - Phát mặt nạ đặt chỗ `parking/{DEVICE_ID}/reserved` (Retained)
    """

    def __init__(
        self,
        cache: StateCache,
        cloud_bridge: Any = None,
        cloud: Any = None,
        arbiter: Any = None,
        ai_pipeline: Any = None,
        storage_uploader: Any = None,
        **kwargs: Any,
    ):
        self.cache = cache
        self.cloud = cloud if cloud is not None else cloud_bridge
        self.arbiter = arbiter
        self.ai_pipeline = ai_pipeline
        self.storage_uploader = storage_uploader

        self._connected = False
        self._last_message_at: float = 0.0
        self._message_count: int = 0
        self._parse_errors: int = 0
        self._last_barrier_open: Optional[bool] = None

        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=settings.MQTT_CLIENT_ID,
            clean_session=True,
        )
        self.client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        # Đăng ký callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # ==========================================
    # LIFECYCLE
    # ==========================================
    def start(self) -> None:
        logger.info(
            "Đang kết nối tới MQTT Broker %s:%d (User: %s)...",
            settings.MQTT_HOST,
            settings.MQTT_PORT,
            settings.MQTT_USER,
        )
        try:
            self.client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.error("Lỗi khi kết nối MQTT connect_async: %s", e)

    def stop(self) -> None:
        logger.info("Dừng MQTT Bridge...")
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as e:
            logger.warning("Lỗi khi ngắt kết nối MQTT: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_message_ago_s(self) -> Optional[float]:
        if self._last_message_at > 0:
            return round(time.time() - self._last_message_at, 1)
        return None

    @property
    def message_count(self) -> int:
        return self._message_count

    @property
    def parse_errors(self) -> int:
        return self._parse_errors

    # ==========================================
    # PAHO CALLBACKS
    # ==========================================
    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code.is_failure:
            logger.error("Kết nối MQTT thất bại: %s", reason_code)
            self._connected = False
            return

        self._connected = True
        logger.info("Kết nối MQTT Broker THÀNH CÔNG (rc=%s)", reason_code)

        # Đăng ký các topic cần lắng nghe
        subscriptions = [
            (settings.topic_state, 1),
            (settings.topic_gate_request, 1),
            (settings.topic_gate_status, 1),
            (settings.topic_cam_status, 1),
        ]
        client.subscribe(subscriptions)
        for topic, qos in subscriptions:
            logger.info(" -> Subscribed topic: %s (QoS %d)", topic, qos)

        # Ngay khi kết nối, publish lại mặt nạ reserved hiện tại (retained)
        # để đảm bảo broker luôn có trạng thái mới nhất cho ESP32
        current_mask = self.cache.compute_reserved_mask()
        self.publish_reserved(current_mask, force=True)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
        self._connected = False
        logger.warning("Mất kết nối MQTT Broker (rc=%s). Đang tự động kết nối lại...", reason_code)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        topic = message.topic
        payload_bytes = message.payload
        self._last_message_at = time.time()
        self._message_count += 1

        try:
            if topic == settings.topic_state:
                self._handle_state(payload_bytes)
            elif topic == settings.topic_gate_request:
                self._handle_gate_request(payload_bytes)
            elif topic == settings.topic_gate_status:
                self._handle_device_status("gate", payload_bytes)
            elif topic == settings.topic_cam_status:
                self._handle_device_status("cam", payload_bytes)
            else:
                logger.debug("Bỏ qua topic không quan tâm: %s", topic)
        except Exception as e:
            self._parse_errors += 1
            logger.error("Lỗi khi xử lý message từ topic %s: %s", topic, e, exc_info=True)

    # ==========================================
    # HANDLERS
    # ==========================================
    def _handle_state(self, payload_bytes: bytes) -> None:
        try:
            data = json.loads(payload_bytes.decode("utf-8"))
            payload = StatePayload.model_validate(data)
        except Exception as e:
            self._parse_errors += 1
            logger.warning("Gói tin state không đúng định dạng: %s", e)
            return

        diff_slots = self.cache.apply_state(payload)
        logger.debug(
            "[MQTT RX] State: barrier=%s, gateState=%s, free=%d, diff_slots=%s",
            payload.barrierOpen,
            payload.gateState,
            payload.freeSlots,
            diff_slots,
        )

        if self.cloud and diff_slots:
            sensor_map = self.cache.get_sensor_occupied()
            self.cloud.enqueue_slots(diff_slots, sensor_map)
            self.cloud.enqueue_lot_summary(self.cache.free_slots_public())

        if self.cloud and payload.barrierOpen != self._last_barrier_open:
            self._last_barrier_open = payload.barrierOpen
            if hasattr(self.cloud, "enqueue_barrier_state"):
                self.cloud.enqueue_barrier_state(payload.barrierOpen)

    def _handle_gate_request(self, payload_bytes: bytes) -> None:
        try:
            data = json.loads(payload_bytes.decode("utf-8"))
            payload = GateRequestPayload.model_validate(data)
        except Exception as e:
            self._parse_errors += 1
            logger.warning("Gói tin gate/request không đúng định dạng: %s", e)
            return

        if payload.offlineFallback:
            logger.warning(
                "[MQTT RX] Nhận báo cáo xe qua cổng ở chế độ OFFLINE FALLBACK: seq=%u, dir=%s",
                payload.seq,
                payload.direction,
            )
            if self.cloud:
                self.cloud.enqueue_gate_event(
                    seq=payload.seq,
                    direction=payload.direction,
                    decision="OPEN",
                    status="offline_fallback",
                    flags=["offline_fallback"],
                )
        else:
            self.cache.add_pending_request(payload.seq, payload.direction)
            if self.arbiter:
                self.arbiter.on_gate_request(payload.seq, payload.direction)
            logger.info(
                "[MQTT RX] Nhận gate/request (seq=%u, direction=%s) -> Khởi tạo GateArbiter Timer 5.0s",
                payload.seq,
                payload.direction,
            )

            # Nếu đang bật chế độ Camera Điện Thoại, tự động kéo ảnh và phân xử ngay lập tức
            if settings.CAM_SOURCE == "phone":
                if settings.PHONE_CAM_URL and self.ai_pipeline and self.arbiter:
                    t = threading.Thread(
                        target=self._process_phone_cam_gate_request,
                        args=(payload.seq, payload.direction),
                        daemon=True,
                    )
                    t.start()
                elif not settings.PHONE_CAM_URL:
                    logger.warning(
                        "[PhoneCam] CAM_SOURCE='phone' nhưng chưa cấu hình PHONE_CAM_URL! "
                        "Hãy nhập IP điện thoại tại trang /cam-test."
                    )

    def _process_phone_cam_gate_request(self, seq: int, direction: str) -> None:
        """Luồng nền tự động chụp từ Camera Điện thoại -> YOLO -> (Retry nếu OCR lỗi) -> GateArbiter -> Mở cổng."""
        t0 = time.perf_counter()
        max_attempts = max(1, settings.PHONE_CAM_MAX_ATTEMPTS)

        best_frames: List[bytes] = []
        best_res = None

        for attempt in range(1, max_attempts + 1):
            # Dừng retry nếu gần chạm ngưỡng Fail-safe Timer (chừa lại ~0.8s an toàn)
            if attempt > 1 and (time.perf_counter() - t0) >= max(1.0, settings.CAMERA_TIMEOUT_S - 0.8):
                logger.warning("[PhoneCam] Gần hết thời gian Fail-safe cho seq=%u -> Dừng retry.", seq)
                break

            frames_bytes = fetch_phone_cam_frames(
                url=settings.PHONE_CAM_URL,
                num_frames=settings.PHONE_CAM_FRAMES,
                interval_ms=settings.PHONE_CAM_INTERVAL_MS,
            )
            if not frames_bytes:
                logger.warning(
                    "[PhoneCam] Lần %d/%d không lấy được ảnh từ %s (seq=%u).",
                    attempt,
                    max_attempts,
                    settings.PHONE_CAM_URL,
                    seq,
                )
                if attempt < max_attempts:
                    time.sleep(settings.PHONE_CAM_RETRY_DELAY_MS / 1000.0)
                continue

            res = self.ai_pipeline.recognize_frames(frames_bytes)
            best_frames = frames_bytes
            best_res = res

            if res.is_valid and res.plate_display:
                if attempt > 1:
                    logger.info(
                        "[PhoneCam] Chụp lại lần %d/%d THÀNH CÔNG cho seq=%u -> '%s'",
                        attempt,
                        max_attempts,
                        seq,
                        res.plate_display,
                    )
                break

            logger.info(
                "[PhoneCam] Lần %d/%d chưa đọc được biển hợp lệ (raw='%s', seq=%u) -> %s",
                attempt,
                max_attempts,
                res.plate_raw,
                seq,
                "Chụp lại..." if attempt < max_attempts else "Hết lượt thử.",
            )
            if attempt < max_attempts:
                time.sleep(settings.PHONE_CAM_RETRY_DELAY_MS / 1000.0)

        if not best_frames or best_res is None:
            logger.warning(
                "[PhoneCam] Không lấy được ảnh nào từ %s cho seq=%u. Chờ Fail-safe Timer.",
                settings.PHONE_CAM_URL,
                seq,
            )
            return

        decision = self.arbiter.decide(
            seq=seq,
            direction=direction.upper().strip(),
            plate_raw=best_res.plate_raw,
            plate_normalized=best_res.plate_normalized,
            plate_display=best_res.plate_display,
            confidence=best_res.confidence,
            is_valid=best_res.is_valid,
        )

        if not decision.is_duplicate:
            self.publish_gate_command(
                seq=decision.seq,
                action=decision.action,
                plate=decision.plate_for_lcd,
                assigned_slot=decision.assigned_slot,
                reason=decision.deny_reason,
            )

            if self.storage_uploader:
                best_full_jpeg: Optional[bytes] = None
                if 0 <= best_res.best_frame_index < len(best_frames):
                    best_full_jpeg = best_frames[best_res.best_frame_index]
                self.storage_uploader.submit_capture_and_event(
                    decision=decision,
                    full_jpeg_bytes=best_full_jpeg,
                    crop_jpeg_bytes=best_res.crop_jpeg_bytes,
                    latency_ms=best_res.latency_ms,
                )

        total_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "[PhoneCam Gate] seq=%u (%s) -> Plate='%s' (conf=%.2f) -> %s (slot=%s, total=%.1fms)",
            seq,
            direction,
            decision.plate_display or "KHONG RO",
            decision.confidence,
            decision.action,
            decision.assigned_slot,
            total_ms,
        )

    def _handle_device_status(self, kind: str, payload_bytes: bytes) -> None:
        status_str = payload_bytes.decode("utf-8", errors="ignore").strip().lower()
        online = (status_str == "online")

        device_id = settings.DEVICE_ID if kind == "gate" else settings.CAM_DEVICE_ID
        logger.info("[MQTT RX] Thiết bị %s (%s) chuyển trạng thái: %s", kind.upper(), device_id, status_str)

        changed = self.cache.set_device_online(kind, online)

        if self.cloud and changed:
            self.cloud.enqueue_device_status(device_id, online, kind)

        # Nếu Gate vừa online trở lại, đẩy ngay mặt nạ reserved mới nhất xuống
        if kind == "gate" and online:
            current_mask = self.cache.compute_reserved_mask()
            self.publish_reserved(current_mask, force=True)

    # ==========================================
    # OUTBOUND PUBLISH API
    # ==========================================
    def publish_reserved(self, mask: Dict[str, bool], force: bool = False) -> bool:
        """Publish mặt nạ đặt chỗ 4 ô xuống topic parking/{DEVICE_ID}/reserved (Retained = True)."""
        if not self._connected:
            logger.debug("Không thể publish_reserved: Chưa kết nối MQTT")
            return False

        if not force and self.cache.get_last_published_reserved_mask() == mask:
            return False

        payload_str = json.dumps(mask)
        info = self.client.publish(settings.topic_reserved, payload_str, qos=1, retain=True)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            self.cache.mark_reserved_mask_published(mask)
            logger.info("[MQTT TX] >> PUBLISH RESERVED MASK (retained=True): %s", payload_str)
            return True
        else:
            logger.error("[MQTT TX] Lỗi publish_reserved rc=%d", info.rc)
            return False

    def publish_gate_command(
        self,
        seq: int,
        action: str,
        plate: str = "",
        assigned_slot: str = "",
        reason: Optional[str] = None,
    ) -> bool:
        """Publish lệnh mở/từ chối barie xuống topic parking/{DEVICE_ID}/gate/command (QoS 1, retain=False)."""
        if not self._connected:
            logger.error("Không thể publish_gate_command: Chưa kết nối MQTT")
            return False

        cmd = GateCommandPayload(
            seq=seq,
            action=action.upper(),
            plate=plate,
            assignedSlot=assigned_slot,
            reason=reason,
        )
        payload_str = cmd.model_dump_json(exclude_none=True)
        info = self.client.publish(settings.topic_gate_command, payload_str, qos=1, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info("[MQTT TX] >> PUBLISH GATE COMMAND: %s", payload_str)
            return True
        else:
            logger.error("[MQTT TX] Lỗi publish_gate_command rc=%d", info.rc)
            return False

    def publish_cam_test(self, test_id: str, use_flash: bool = False) -> bool:
        """Publish lệnh chụp ảnh kiểm thử xuống topic parking/{CAM_DEVICE_ID}/test."""
        if not self._connected:
            logger.error("Không thể publish_cam_test: Chưa kết nối MQTT")
            return False

        topic = f"parking/{settings.CAM_DEVICE_ID}/test"
        payload_str = json.dumps({"testId": test_id, "useFlash": use_flash})
        info = self.client.publish(topic, payload_str, qos=1, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info("[MQTT TX] >> PUBLISH CAM TEST (%s): %s", topic, payload_str)
            return True
        else:
            logger.error("[MQTT TX] Lỗi publish_cam_test rc=%d", info.rc)
            return False
