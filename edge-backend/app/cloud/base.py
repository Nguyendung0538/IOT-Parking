from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class CloudBridgeBase(ABC):
    """Giao diện trừu tượng cho tầng đồng bộ dữ liệu lên Cloud."""

    @abstractmethod
    def start(self) -> None:
        """Khởi động luồng worker và các listener realtime."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Dừng các listener và xả hàng đợi ghi còn sót lại."""
        pass

    @abstractmethod
    def enqueue_slots(self, slot_ids: List[str], sensor_map: Dict[str, bool]) -> None:
        """Đưa cập nhật trạng thái ô đỗ vào hàng đợi ghi lên Firestore."""
        pass

    @abstractmethod
    def enqueue_lot_summary(self, free_slots: int) -> None:
        """Đưa cập nhật tóm tắt bãi đỗ vào hàng đợi ghi lên Firestore."""
        pass

    @abstractmethod
    def enqueue_device_status(self, device_id: str, online: bool, kind: str) -> None:
        """Đưa cập nhật trạng thái online/offline của thiết bị vào hàng đợi."""
        pass

    @abstractmethod
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
        """Đưa sự kiện cổng vào hàng đợi ghi collection gateEvents."""
        pass

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        """Trả về thông tin chẩn đoán trạng thái phục vụ endpoint /health."""
        pass

