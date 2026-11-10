from collections import OrderedDict
from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.state_cache import StateCache

logger = logging.getLogger("gate_arbiter")


@dataclass
class ArbiterDecision:
    """Kết quả phân xử quyết định cổng của GateArbiter."""
    seq: int
    direction: str                        # "IN" | "OUT"
    action: str                           # "OPEN" | "DENY"
    status: str                           # "ok" | "unrecognized" | "denied_full" | "denied_reserved_taken" | "duplicate_ignored"
    assigned_slot: str = ""               # "A1" | "A2" | "B1" | "B2" | ""
    matched_reservation_id: Optional[str] = None
    deny_reason: Optional[str] = None     # "FULL" | "RESERVED_SLOT_TAKEN" | None
    flags: List[str] = field(default_factory=list)
    plate_raw: str = ""
    plate_normalized: str = ""
    plate_display: str = ""
    plate_for_lcd: str = ""               # Tối đa 12 ký tự hiển thị trên LCD 1602 của ESP32
    confidence: float = 0.0
    is_duplicate: bool = False


class GateArbiter:
    """Bộ phân xử cổng siêu tốc (< 5ms) từ StateCache trong RAM.
    
    Nhiệm vụ:
    - Quyết định mở/từ chối barie và chọn ô đỗ cho xe vào
    - Bảo vệ tuyệt đối các ô đã được đặt trước (active reservations)
    - Xử lý bù trừ ô đỗ (substitution) khi ô đặt trước bị xe khác đỗ đè
    - Quản lý Timer fail-safe 5.0s (Camera Timeout)
    - Đảm bảo tính Idempotency (chống trùng lặp seq)
    """

    def __init__(
        self,
        cache: StateCache,
        mqtt_bridge: Optional[Any] = None,
        cloud_bridge: Optional[Any] = None,
        storage_uploader: Optional[Any] = None,
    ):
        self.cache = cache
        self.mqtt = mqtt_bridge
        self.cloud = cloud_bridge
        self.storage_uploader = storage_uploader

        self._lock = threading.RLock()
        self._timers: Dict[int, threading.Timer] = {}
        # Lưu tối đa 128 seq đã xử lý gần nhất để chống xử lý trùng (Idempotency)
        self._processed_seqs: OrderedDict[int, ArbiterDecision] = OrderedDict()

    # ==========================================
    # TIMER QUẢN LÝ FAIL-SAFE 5.0 GIÂY
    # ==========================================
    def on_gate_request(self, seq: int, direction: str) -> None:
        """Được gọi khi MQTT Bridge nhận gói tin gate/request từ ESP32 Chính."""
        with self._lock:
            # 1. Hủy timer cũ nếu cùng seq
            self._cancel_timer_locked(seq)

            # 2. Nếu seq này đã được xử lý xong từ trước (hiếm gặp, vd mạng chập chờn)
            if seq > 0 and seq in self._processed_seqs:
                logger.info("[Arbiter] Nhận gate/request seq=%d nhưng đã xử lý xong trước đó -> Bỏ qua", seq)
                return

            # 3. Đăng ký Timer 5.0s
            timer = threading.Timer(
                interval=settings.CAMERA_TIMEOUT_S,
                function=self._on_camera_timeout,
                args=(seq, direction),
            )
            timer.daemon = True
            self._timers[seq] = timer
            timer.start()

            logger.info(
                "[Arbiter] Bắt đầu hẹn giờ Fail-safe %.1fs cho seq=%u (dir=%s)",
                settings.CAMERA_TIMEOUT_S,
                seq,
                direction,
            )

    def cancel_timer(self, seq: int) -> None:
        """Hủy Timer của seq khi nhận được ảnh qua HTTP /recognize."""
        with self._lock:
            self._cancel_timer_locked(seq)

    def _cancel_timer_locked(self, seq: int) -> None:
        timer = self._timers.pop(seq, None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass

    def _on_camera_timeout(self, seq: int, direction: str) -> None:
        """Kích hoạt khi hết 5.0 giây mà CAM chưa gửi ảnh lên server."""
        with self._lock:
            # Xóa khỏi danh sách timer
            self._timers.pop(seq, None)

            # Kiểm tra nếu đã được xử lý (tránh race condition với /recognize)
            if seq > 0 and seq in self._processed_seqs:
                return

            logger.warning(
                "[Arbiter Timeout] Hết %.1fs chờ ảnh cho seq=%u (dir=%s)! Tự động phân xử chế độ UNRECOGNIZED.",
                settings.CAMERA_TIMEOUT_S,
                seq,
                direction,
            )

            # Ra quyết định mở cổng ở chế độ unrecognized kèm cờ camera_timeout
            decision = self._decide_locked(
                seq=seq,
                direction=direction,
                plate_raw="",
                plate_normalized="",
                plate_display="",
                confidence=0.0,
                is_valid=False,
                extra_flags=["unrecognized", "camera_timeout"],
            )

        # Ngoài lock: Publish MQTT và gửi sự kiện lên cloud
        if self.mqtt:
            self.mqtt.publish_gate_command(
                seq=decision.seq,
                action=decision.action,
                plate=decision.plate_for_lcd,
                assigned_slot=decision.assigned_slot,
                reason=decision.deny_reason,
            )

        # Lưu bản ghi gateEvents (không có ảnh vì camera timeout)
        if self.storage_uploader:
            self.storage_uploader.submit_capture_and_event(
                decision=decision,
                full_jpeg_bytes=None,
                crop_jpeg_bytes=None,
                latency_ms=settings.CAMERA_TIMEOUT_S * 1000,
            )
        elif self.cloud:
            self.cloud.enqueue_gate_event(
                seq=decision.seq,
                direction=decision.direction,
                decision=decision.action,
                status=decision.status,
                flags=decision.flags,
                plate_raw=decision.plate_raw,
                plate_norm=decision.plate_normalized,
                plate_display=decision.plate_display,
                confidence=decision.confidence,
                assigned_slot_id=decision.assigned_slot if decision.assigned_slot else None,
                matched_reservation_id=decision.matched_reservation_id,
                deny_reason=decision.deny_reason,
                latency_ms=settings.CAMERA_TIMEOUT_S * 1000,
            )

    # ==========================================
    # LOGIC PHÂN XỬ QUYẾT ĐỊNH CỔNG
    # ==========================================
    def decide(
        self,
        seq: int,
        direction: str,
        plate_raw: str = "",
        plate_normalized: str = "",
        plate_display: str = "",
        confidence: float = 0.0,
        is_valid: bool = False,
        extra_flags: Optional[List[str]] = None,
    ) -> ArbiterDecision:
        """Phân xử quyết định cổng và chống trùng lặp theo seq."""
        with self._lock:
            # 1. Hủy Timer của seq này (nếu có)
            self._cancel_timer_locked(seq)
            # 2. Xóa khỏi pending requests trong StateCache
            self.cache.remove_pending_request(seq)

            return self._decide_locked(
                seq=seq,
                direction=direction,
                plate_raw=plate_raw,
                plate_normalized=plate_normalized,
                plate_display=plate_display,
                confidence=confidence,
                is_valid=is_valid,
                extra_flags=extra_flags,
            )

    def _decide_locked(
        self,
        seq: int,
        direction: str,
        plate_raw: str = "",
        plate_normalized: str = "",
        plate_display: str = "",
        confidence: float = 0.0,
        is_valid: bool = False,
        extra_flags: Optional[List[str]] = None,
    ) -> ArbiterDecision:
        direction = direction.upper().strip()

        # 1. Chống trùng lặp seq (Idempotency)
        if seq > 0 and seq in self._processed_seqs:
            logger.info("[Arbiter] Phát hiện trùng lặp seq=%u -> Trả lại quyết định cũ", seq)
            cached_decision = self._processed_seqs[seq]
            return ArbiterDecision(
                seq=cached_decision.seq,
                direction=cached_decision.direction,
                action=cached_decision.action,
                status="duplicate_ignored",
                assigned_slot=cached_decision.assigned_slot,
                matched_reservation_id=cached_decision.matched_reservation_id,
                deny_reason=cached_decision.deny_reason,
                flags=list(cached_decision.flags),
                plate_raw=cached_decision.plate_raw,
                plate_normalized=cached_decision.plate_normalized,
                plate_display=cached_decision.plate_display,
                plate_for_lcd=cached_decision.plate_for_lcd,
                confidence=cached_decision.confidence,
                is_duplicate=True,
            )

        flags: List[str] = list(extra_flags) if extra_flags else []

        # Chuẩn bị chuỗi hiển thị tối đa 12 ký tự trên LCD 1602 của ESP32
        if is_valid and plate_display:
            plate_for_lcd = plate_display[:12]
        elif is_valid and plate_normalized:
            plate_for_lcd = plate_normalized[:12]
        else:
            plate_for_lcd = "KHONG RO"

        # 2. XỬ LÝ CHIỀU XE RA (OUT)
        if direction == "OUT":
            if is_valid:
                status = "ok"
            else:
                status = "unrecognized"
                if "unrecognized" not in flags:
                    flags.append("unrecognized")
                if "exit_manual_reconcile" not in flags:
                    flags.append("exit_manual_reconcile")

            decision = ArbiterDecision(
                seq=seq,
                direction="OUT",
                action="OPEN",
                status=status,
                assigned_slot="",
                matched_reservation_id=None,
                deny_reason=None,
                flags=flags,
                plate_raw=plate_raw,
                plate_normalized=plate_normalized,
                plate_display=plate_display,
                plate_for_lcd=plate_for_lcd,
                confidence=confidence,
                is_duplicate=False,
            )
            self._save_decision_locked(seq, decision)
            return decision

        # 3. XỬ LÝ CHIỀU XE VÀO (IN)
        # 3.1. Dọn dẹp các reservation đã hết hạn
        self.cache.purge_expired_reservations()

        sensor_occ = self.cache.get_sensor_occupied()
        reserved_mask = self.cache.compute_reserved_mask()

        # Kiểm tra xem biển số có khớp reservation active nào không
        matched_res = None
        if is_valid and plate_normalized:
            matched_res = self.cache.get_active_reservation_for_plate(plate_normalized)

        # ----------------------------------------------------
        # NHÁNH 3.A: BIỂN SỐ KHỚP ACTIVE RESERVATION
        # ----------------------------------------------------
        if matched_res is not None:
            res_slot = matched_res.slotId
            is_slot_occupied = sensor_occ.get(res_slot, False)

            if not is_slot_occupied:
                # Ô đã đặt đang trống -> Gán đúng ô đã đặt
                flags.append("matched_reservation")
                decision = ArbiterDecision(
                    seq=seq,
                    direction="IN",
                    action="OPEN",
                    status="ok",
                    assigned_slot=res_slot,
                    matched_reservation_id=matched_res.id,
                    deny_reason=None,
                    flags=flags,
                    plate_raw=plate_raw,
                    plate_normalized=plate_normalized,
                    plate_display=plate_display,
                    plate_for_lcd=plate_for_lcd,
                    confidence=confidence,
                    is_duplicate=False,
                )
            else:
                # Ô đã đặt bị xe khác đỗ đè -> Tìm ô dự phòng trống chưa bị ai đặt
                alt_slot = None
                for s in self.cache.slot_ids:
                    if not sensor_occ.get(s, False) and not reserved_mask.get(s, False):
                        alt_slot = s
                        break

                if alt_slot is not None:
                    flags.extend(["matched_reservation", "reserved_slot_substituted"])
                    decision = ArbiterDecision(
                        seq=seq,
                        direction="IN",
                        action="OPEN",
                        status="ok",
                        assigned_slot=alt_slot,
                        matched_reservation_id=matched_res.id,
                        deny_reason=None,
                        flags=flags,
                        plate_raw=plate_raw,
                        plate_normalized=plate_normalized,
                        plate_display=plate_display,
                        plate_for_lcd=plate_for_lcd,
                        confidence=confidence,
                        is_duplicate=False,
                    )
                else:
                    # Bãi đã hết sạch ô trống
                    flags.extend(["reserved_slot_occupied_and_lot_full"])
                    decision = ArbiterDecision(
                        seq=seq,
                        direction="IN",
                        action="DENY",
                        status="denied_reserved_taken",
                        assigned_slot="",
                        matched_reservation_id=matched_res.id,
                        deny_reason="RESERVED_SLOT_TAKEN",
                        flags=flags,
                        plate_raw=plate_raw,
                        plate_normalized=plate_normalized,
                        plate_display=plate_display,
                        plate_for_lcd=plate_for_lcd,
                        confidence=confidence,
                        is_duplicate=False,
                    )

            self._save_decision_locked(seq, decision)
            return decision

        # ----------------------------------------------------
        # NHÁNH 3.B: KHÁCH VÃNG LAI HOẶC KHÔNG RÕ BIỂN SỐ
        # ----------------------------------------------------
        # Tìm ô trống khả dụng đầu tiên: !sensorOccupied && !isReserved
        free_slot = None
        for s in self.cache.slot_ids:
            if not sensor_occ.get(s, False) and not reserved_mask.get(s, False):
                free_slot = s
                break

        if free_slot is not None:
            # Còn ô trống -> Mở cổng
            if is_valid:
                status = "ok"
            else:
                status = "unrecognized"
                if "unrecognized" not in flags:
                    flags.append("unrecognized")

            decision = ArbiterDecision(
                seq=seq,
                direction="IN",
                action="OPEN",
                status=status,
                assigned_slot=free_slot,
                matched_reservation_id=None,
                deny_reason=None,
                flags=flags,
                plate_raw=plate_raw,
                plate_normalized=plate_normalized,
                plate_display=plate_display,
                plate_for_lcd=plate_for_lcd,
                confidence=confidence,
                is_duplicate=False,
            )
        else:
            # Hết ô trống -> Từ chối
            flags.append("lot_full")
            if not is_valid and "unrecognized" not in flags:
                flags.append("unrecognized")

            decision = ArbiterDecision(
                seq=seq,
                direction="IN",
                action="DENY",
                status="denied_full",
                assigned_slot="",
                matched_reservation_id=None,
                deny_reason="FULL",
                flags=flags,
                plate_raw=plate_raw,
                plate_normalized=plate_normalized,
                plate_display=plate_display,
                plate_for_lcd=plate_for_lcd,
                confidence=confidence,
                is_duplicate=False,
            )

        self._save_decision_locked(seq, decision)
        return decision

    def _save_decision_locked(self, seq: int, decision: ArbiterDecision) -> None:
        if seq > 0:
            self._processed_seqs[seq] = decision
            if len(self._processed_seqs) > 128:
                self._processed_seqs.popitem(last=False)

    def stop(self) -> None:
        """Hủy toàn bộ timer khi tắt server."""
        with self._lock:
            for timer in self._timers.values():
                try:
                    timer.cancel()
                except Exception:
                    pass
            self._timers.clear()

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                "activeTimersCount": len(self._timers),
                "processedSeqsCount": len(self._processed_seqs),
            }

