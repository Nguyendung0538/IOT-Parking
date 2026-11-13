from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import logging
import os
from pathlib import Path
from typing import Any, Optional

from app.arbiter.gate_arbiter import ArbiterDecision
from app.cloud.base import CloudBridgeBase
from app.config import settings

logger = logging.getLogger("storage_uploader")


class StorageUploader:
    """Worker bất đồng bộ xử lý lưu ảnh chụp biển số và đẩy gateEvents lên Cloud.
    
    Chạy trong ThreadPoolExecutor riêng biệt, hoàn toàn tách rời khỏi đường găng
    mở barie (< 5ms).
    """

    def __init__(self, cloud_bridge: CloudBridgeBase, max_workers: int = 2):
        self.cloud = cloud_bridge
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="storage_uploader",
        )
        self._running = True

        # Đảm bảo thư mục lưu ảnh cục bộ tồn tại
        self.local_base_dir = Path(settings.LOCAL_CAPTURE_DIR)
        if not self.local_base_dir.is_absolute():
            backend_root = Path(__file__).resolve().parent.parent.parent
            self.local_base_dir = backend_root / settings.LOCAL_CAPTURE_DIR
        self.local_base_dir.mkdir(parents=True, exist_ok=True)

    def submit_capture_and_event(
        self,
        decision: ArbiterDecision,
        full_jpeg_bytes: Optional[bytes] = None,
        crop_jpeg_bytes: Optional[bytes] = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Đẩy tác vụ lưu ảnh và ghi sự kiện vào hàng đợi thread pool."""
        if not self._running:
            logger.warning("[StorageUploader] Executor đã dừng, bỏ qua lưu ảnh seq=%d", decision.seq)
            return

        self.executor.submit(
            self._process_upload_and_enqueue,
            decision,
            full_jpeg_bytes,
            crop_jpeg_bytes,
            latency_ms,
        )

    def _process_upload_and_enqueue(
        self,
        decision: ArbiterDecision,
        full_jpeg_bytes: Optional[bytes],
        crop_jpeg_bytes: Optional[bytes],
        latency_ms: float,
    ) -> None:
        try:
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            prefix = f"seq{decision.seq}_{decision.direction}_{int(now.timestamp())}"

            image_url: Optional[str] = None
            crop_image_url: Optional[str] = None

            # 1. Lưu ảnh ra đĩa cục bộ (Local Storage)
            day_dir = self.local_base_dir / date_str
            day_dir.mkdir(parents=True, exist_ok=True)

            base_http_url = f"http://127.0.0.1:{settings.API_PORT}"
            if full_jpeg_bytes:
                full_path = day_dir / f"{prefix}_full.jpg"
                with open(full_path, "wb") as f:
                    f.write(full_jpeg_bytes)
                image_url = f"{base_http_url}/captures/{date_str}/{prefix}_full.jpg"

            if crop_jpeg_bytes:
                crop_path = day_dir / f"{prefix}_crop.jpg"
                with open(crop_path, "wb") as f:
                    f.write(crop_jpeg_bytes)
                crop_image_url = f"{base_http_url}/captures/{date_str}/{prefix}_crop.jpg"

            # 2. Upload lên Firebase Cloud Storage (nếu được kích hoạt và có Firebase App)
            import firebase_admin
            if settings.FIREBASE_ENABLED and settings.FIREBASE_STORAGE_BUCKET and firebase_admin._apps:
                try:
                    from firebase_admin import storage
                    bucket = storage.bucket(settings.FIREBASE_STORAGE_BUCKET)

                    if full_jpeg_bytes:
                        blob_name = f"gate/{date_str}/{prefix}_full.jpg"
                        blob = bucket.blob(blob_name)
                        blob.upload_from_string(full_jpeg_bytes, content_type="image/jpeg")
                        image_url = blob.public_url

                    if crop_jpeg_bytes:
                        blob_name = f"gate/{date_str}/{prefix}_crop.jpg"
                        blob = bucket.blob(blob_name)
                        blob.upload_from_string(crop_jpeg_bytes, content_type="image/jpeg")
                        crop_image_url = blob.public_url

                    logger.debug("[Storage] Đã upload ảnh lên Firebase Storage bucket: %s", settings.FIREBASE_STORAGE_BUCKET)
                except Exception as fb_err:
                    logger.warning("[Storage] Lỗi khi upload Firebase Storage (giữ local URL): %s", fb_err)

            # 3. Ghi sự kiện gateEvents vào CloudBridge
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
                image_url=image_url,
                crop_image_url=crop_image_url,
                latency_ms=latency_ms,
            )
            logger.debug(
                "[StorageUploader] Hoàn tất lưu ảnh & enqueue gateEvent seq=%u (dir=%s, decision=%s)",
                decision.seq,
                decision.direction,
                decision.action,
            )

        except Exception as e:
            logger.error("[StorageUploader] Lỗi khi xử lý lưu ảnh / gateEvent seq=%u: %s", decision.seq, e, exc_info=True)

    def stop(self) -> None:
        """Dọn dẹp thread pool khi tắt server."""
        self._running = False
        self.executor.shutdown(wait=False, cancel_futures=True)

