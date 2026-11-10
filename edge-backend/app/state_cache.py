import time
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

from app.models import (
    PendingRequestItem,
    ReservationItem,
    StatePayload,
)


class StateCache:
    """Thread-safe single-source-of-truth trong RAM của Edge Server.
    
    Quản lý trạng thái vật lý của các ô đỗ xe, trạng thái cổng,
    kết nối thiết bị, danh sách đặt chỗ và yêu cầu đang chờ xử lý.
    """

    def __init__(self, slot_ids: Optional[List[str]] = None):
        self._lock = RLock()
        self.slot_ids = list(slot_ids) if slot_ids else ["A1", "A2", "B1", "B2"]

        # Trạng thái cảm biến vật lý (true: có xe, false: trống)
        self._sensor_occupied: Dict[str, bool] = {s: False for s in self.slot_ids}
        self._barrier_open: bool = False
        self._gate_state: str = "IDLE"
        self._uptime: int = 0
        self._last_state_at: float = 0.0

        # Trạng thái kết nối phần cứng (từ MQTT LWT)
        self._gate_online: bool = False
        self._cam_online: bool = False
        self._last_gate_status_at: float = 0.0
        self._last_cam_status_at: float = 0.0

        # Đặt chỗ (từ Firebase Firestore on_snapshot hoặc Debug API)
        # res_id -> ReservationItem
        self._active_reservations: Dict[str, ReservationItem] = {}
        self._last_published_reserved_mask: Optional[Dict[str, bool]] = None

        # Hàng đợi request đang chờ ảnh (tối đa 32 mục, phục vụ Phase S5)
        # seq -> PendingRequestItem
        self._pending_requests: Dict[int, PendingRequestItem] = {}

    # ==========================================
    # CẬP NHẬT TRẠNG THÁI CẢM BIẾN (TỪ ESP32)
    # ==========================================
    def apply_state(self, payload: StatePayload) -> List[str]:
        """Cập nhật dữ liệu từ gói tin state của ESP32 chính.
        
        Trả về danh sách các slot ID có trạng thái vật lý thực sự thay đổi (diff)
        để chỉ ghi các slot này lên Firestore, tránh tốn quota.
        """
        changed_slots: List[str] = []
        with self._lock:
            self._uptime = payload.uptime
            self._barrier_open = payload.barrierOpen
            self._gate_state = payload.gateState
            self._last_state_at = time.time()

            for slot_id in self.slot_ids:
                if slot_id in payload.slots:
                    new_occ = payload.slots[slot_id].occupied
                    old_occ = self._sensor_occupied.get(slot_id, False)
                    if new_occ != old_occ:
                        self._sensor_occupied[slot_id] = new_occ
                        changed_slots.append(slot_id)

        return changed_slots

    def get_sensor_occupied(self) -> Dict[str, bool]:
        with self._lock:
            return dict(self._sensor_occupied)

    # ==========================================
    # TRẠNG THÁI THIẾT BỊ ONLINE / OFFLINE (LWT)
    # ==========================================
    def set_device_online(self, kind: str, online: bool) -> bool:
        """Cập nhật trạng thái online/offline của Gate hoặc Cam.
        
        Trả về True nếu trạng thái thực sự thay đổi.
        """
        now = time.time()
        with self._lock:
            if kind.lower() in ("gate", "esp32_parking_01"):
                if self._gate_online != online:
                    self._gate_online = online
                    self._last_gate_status_at = now
                    return True
                self._last_gate_status_at = now
            elif kind.lower() in ("cam", "esp32_cam_01"):
                if self._cam_online != online:
                    self._cam_online = online
                    self._last_cam_status_at = now
                    return True
                self._last_cam_status_at = now
        return False

    def is_gate_online(self) -> bool:
        with self._lock:
            return self._gate_online

    def is_cam_online(self) -> bool:
        with self._lock:
            return self._cam_online

    # ==========================================
    # QUẢN LÝ ĐẶT CHỖ (RESERVATIONS)
    # ==========================================
    def replace_reservations(self, items: List[ReservationItem]) -> None:
        """Thay thế toàn bộ danh sách reservation (dùng khi on_snapshot bắn danh sách)."""
        with self._lock:
            self._active_reservations = {item.id: item for item in items}

    def upsert_reservation(self, item: ReservationItem) -> None:
        with self._lock:
            self._active_reservations[item.id] = item

    def remove_reservation(self, res_id: str) -> bool:
        with self._lock:
            return self._active_reservations.pop(res_id, None) is not None

    def purge_expired_reservations(self) -> bool:
        """Tự động loại bỏ các reservation đã hết hạn (expiresAt <= now).
        
        Trả về True nếu có ít nhất 1 reservation bị loại bỏ.
        """
        now = datetime.now(timezone.utc)
        removed = False
        with self._lock:
            expired_ids = [
                r_id for r_id, item in self._active_reservations.items()
                if item.expiresAt <= now
            ]
            for r_id in expired_ids:
                del self._active_reservations[r_id]
                removed = True
        return removed

    def compute_reserved_mask(self) -> Dict[str, bool]:
        """Tính mặt nạ đặt chỗ 4 ô: ô X reserved <=> có active reservation chưa hết hạn."""
        now = datetime.now(timezone.utc)
        mask = {s: False for s in self.slot_ids}
        with self._lock:
            for item in self._active_reservations.values():
                if item.slotId in mask and item.expiresAt > now:
                    mask[item.slotId] = True
        return mask

    def reserved_mask_changed(self) -> Optional[Dict[str, bool]]:
        """Kiểm tra mặt nạ mới có khác mặt nạ đã publish gần nhất không.
        
        Nếu khác, trả về mặt nạ mới. Nếu không đổi, trả về None.
        """
        current_mask = self.compute_reserved_mask()
        with self._lock:
            if self._last_published_reserved_mask != current_mask:
                return current_mask
        return None

    def mark_reserved_mask_published(self, mask: Dict[str, bool]) -> None:
        with self._lock:
            self._last_published_reserved_mask = dict(mask)

    def get_last_published_reserved_mask(self) -> Optional[Dict[str, bool]]:
        with self._lock:
            if self._last_published_reserved_mask is not None:
                return dict(self._last_published_reserved_mask)
            return None

    # ==========================================
    # TÍNH TOÁN CHỖ TRỐNG KHẢ DỤNG THỰC TẾ
    # ==========================================
    def free_slots_public(self) -> int:
        """Số ô trống khả dụng cho khách vãng lai (!occupied && !reserved).
        Khớp chuẩn xác công thức với firmware ESP32 chính.
        """
        mask = self.compute_reserved_mask()
        with self._lock:
            count = 0
            for s in self.slot_ids:
                if not self._sensor_occupied.get(s, False) and not mask.get(s, False):
                    count += 1
            return count

    # ==========================================
    # HÀNG ĐỢI REQUEST (PHỤC VỤ PHASE S5)
    # ==========================================
    def add_pending_request(self, seq: int, direction: str) -> None:
        with self._lock:
            # Giữ tối đa 32 mục, dọn mục cũ nhất nếu vượt quá
            if len(self._pending_requests) >= 32:
                oldest_seq = min(
                    self._pending_requests.keys(),
                    key=lambda k: self._pending_requests[k].receivedAt
                )
                del self._pending_requests[oldest_seq]

            self._pending_requests[seq] = PendingRequestItem(
                seq=seq,
                direction=direction,
                receivedAt=datetime.now(timezone.utc)
            )

    def consume_pending_request(self, seq: int) -> Optional[PendingRequestItem]:
        with self._lock:
            return self._pending_requests.pop(seq, None)

    # ==========================================
    # SNAPSHOT TOÀN BỘ CACHE CHO API / HEALTH
    # ==========================================
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            mask = self.compute_reserved_mask()
            now = time.time()
            return {
                "sensorOccupied": dict(self._sensor_occupied),
                "barrierOpen": self._barrier_open,
                "gateState": self._gate_state,
                "uptime": self._uptime,
                "lastStateAgoS": round(now - self._last_state_at, 1) if self._last_state_at > 0 else None,
                "gateOnline": self._gate_online,
                "camOnline": self._cam_online,
                "lastGateStatusAgoS": round(now - self._last_gate_status_at, 1) if self._last_gate_status_at > 0 else None,
                "lastCamStatusAgoS": round(now - self._last_cam_status_at, 1) if self._last_cam_status_at > 0 else None,
                "activeReservationsCount": len(self._active_reservations),
                "activeReservations": {
                    r_id: {
                        "slotId": item.slotId,
                        "plate": item.plate,
                        "expiresAt": item.expiresAt.isoformat(),
                    }
                    for r_id, item in self._active_reservations.items()
                },
                "reservedMask": mask,
                "freeSlotsPublic": self.free_slots_public(),
                "pendingRequestsCount": len(self._pending_requests),
            }

