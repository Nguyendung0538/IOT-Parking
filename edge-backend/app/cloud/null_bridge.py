import logging
from typing import Any, Dict, List

from app.cloud.base import CloudBridgeBase

logger = logging.getLogger("null_cloud_bridge")


class NullCloudBridge(CloudBridgeBase):
    """Bridge giả lập dùng khi FIREBASE_ENABLED = false.
    
    Ghi log ra console và không thực hiện bất kỳ kết nối mạng ra ngoài nào.
    Cho phép test 100% các luồng phần cứng và MQTT mà không phụ thuộc Firebase.
    """

    def __init__(self, cache: Any = None, mqtt_bridge: Any = None):
        self.cache = cache
        self.mqtt = mqtt_bridge
        self._running = False

    def start(self) -> None:
        self._running = True
        logger.info("[NullCloud] Khởi động chế độ Offline (FIREBASE_ENABLED=false).")

    def stop(self) -> None:
        self._running = False
        logger.info("[NullCloud] Dừng NullCloudBridge.")

    def enqueue_slots(self, slot_ids: List[str], sensor_map: Dict[str, bool]) -> None:
        logger.info(
            "[NullCloud] [Bỏ qua ghi Firestore] Cảm biến slot đổi: %s (Trạng thái: %s)",
            slot_ids,
            {s: sensor_map.get(s) for s in slot_ids},
        )

    def enqueue_lot_summary(self, free_slots: int) -> None:
        logger.info("[NullCloud] [Bỏ qua ghi Firestore] Chỗ trống bãi đổi: %d", free_slots)

    def enqueue_device_status(self, device_id: str, online: bool, kind: str) -> None:
        logger.info("[NullCloud] [Bỏ qua ghi Firestore] Thiết bị %s (%s) online=%s", kind, device_id, online)

    def enqueue_gate_event(
        self,
        seq: int,
        direction: str,
        decision: str,
        status: str,
        flags: List[str],
        plate_raw: str = "",
        plate_norm: str = "",
    ) -> None:
        logger.info(
            "[NullCloud] [Bỏ qua ghi Firestore] GateEvent: seq=%u, dir=%s, decision=%s, status=%s, flags=%s",
            seq,
            direction,
            decision,
            status,
            flags,
        )

    def status(self) -> Dict[str, Any]:
        return {
            "mode": "null",
            "connected": True,
            "queueSize": 0,
            "listeners": {
                "reservations": "disabled",
                "deviceCommands": "disabled",
            },
        }

