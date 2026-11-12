import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import firebase_admin
from firebase_admin import credentials, firestore

from app.cloud.base import CloudBridgeBase
from app.config import settings
from app.models import ReservationItem
from app.state_cache import StateCache

logger = logging.getLogger("firestore_bridge")


class FirestoreBridge(CloudBridgeBase):
    """Bridge đồng bộ 2 chiều giữa Edge Server và Google Cloud Firestore (Repo 2).
    
    Chiều lên:
    - Trạng thái cảm biến ô đỗ -> lots/{LOT_ID}/slots/{slotId}
    - Trạng thái thiết bị -> devices/{DEVICE_ID}
    - Sự kiện cổng -> gateEvents/{autoId}
    - Heartbeat định kỳ -> lots/{LOT_ID}.edgeHeartbeatAt

    Chiều xuống (Realtime on_snapshot):
    - reservations -> Cập nhật StateCache -> Publish mặt nạ reserved (Retained)
    - deviceCommands (OPEN_GATE) -> Publish lệnh mở cổng tức thì qua MQTT
    """

    def __init__(self, cache: StateCache, mqtt_bridge: Any = None, settings: Any = None):
        self.cache = cache
        self.mqtt = mqtt_bridge

        self.db = None
        self._write_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

        self._res_watch = None
        self._cmd_watch = None

        self._connected = False
        self._res_listener_status = "uninitialized"
        self._cmd_listener_status = "uninitialized"

    def start(self) -> None:
        logger.info("Đang khởi tạo Firebase Admin SDK...")
        cred_path = settings.GOOGLE_APPLICATION_CREDENTIALS
        if not os.path.isabs(cred_path):
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            candidate = os.path.join(base_dir, cred_path)
            if os.path.exists(candidate):
                cred_path = candidate

        emulator_host = settings.FIRESTORE_EMULATOR_HOST or os.environ.get("FIRESTORE_EMULATOR_HOST", "")
        if emulator_host:
            os.environ["FIRESTORE_EMULATOR_HOST"] = emulator_host
            os.environ["GCLOUD_PROJECT"] = settings.FIREBASE_PROJECT_ID
        if settings.FIREBASE_STORAGE_EMULATOR_HOST:
            os.environ["STORAGE_EMULATOR_HOST"] = f"http://{settings.FIREBASE_STORAGE_EMULATOR_HOST}"

        try:
            if emulator_host and not os.path.exists(cred_path):
                from google.auth.credentials import AnonymousCredentials
                from google.cloud import firestore as gc_firestore

                self.db = gc_firestore.Client(
                    project=settings.FIREBASE_PROJECT_ID,
                    credentials=AnonymousCredentials(),
                )
                logger.info(
                    "Kết nối Firestore Emulator tại %s (project=%s)",
                    emulator_host,
                    settings.FIREBASE_PROJECT_ID,
                )
            else:
                if not firebase_admin._apps:
                    if os.path.exists(cred_path):
                        cred = credentials.Certificate(cred_path)
                        firebase_admin.initialize_app(cred, {
                            "projectId": settings.FIREBASE_PROJECT_ID,
                            "storageBucket": settings.FIREBASE_STORAGE_BUCKET or None,
                        })
                        logger.info("Firebase Admin khởi tạo thành công từ certificate: %s", cred_path)
                    else:
                        firebase_admin.initialize_app(options={
                            "projectId": settings.FIREBASE_PROJECT_ID,
                        })
                        logger.info("Firebase Admin khởi tạo từ Application Default Credentials.")
                self.db = firestore.client()
            self._connected = True
        except Exception as e:
            logger.error("Lỗi khi kết nối Firebase Firestore: %s", e)
            self._connected = False
            return

        self._running = True

        # 1. Khởi động Worker Thread xử lý ghi batch lên Firestore
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="firestore_writer",
            daemon=True,
        )
        self._worker_thread.start()

        # 2. Khởi động Heartbeat & Expire Check Thread
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="firestore_heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

        # 3. Đồng bộ toàn bộ trạng thái ban đầu từ StateCache lên Firestore
        self._initial_sync()

        # 4. Đăng ký Realtime Listeners
        self._start_listeners()

    def stop(self) -> None:
        logger.info("Dừng FirestoreBridge...")
        self._running = False

        if self._res_watch:
            try:
                self._res_watch.unsubscribe()
            except Exception:
                pass
            self._res_watch = None

        if self._cmd_watch:
            try:
                self._cmd_watch.unsubscribe()
            except Exception:
                pass
            self._cmd_watch = None

        # Đánh thức worker thread nếu đang chờ queue.get()
        self._write_queue.put(None)

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    # ==========================================
    # ENQUEUE APIS (GỌI TỪ MQTT BRIDGE / API)
    # ==========================================
    def enqueue_slots(self, slot_ids: List[str], sensor_map: Dict[str, bool]) -> None:
        for s in slot_ids:
            occ = sensor_map.get(s, False)
            self._write_queue.put({
                "type": "slot",
                "slot_id": s,
                "sensor_occupied": occ,
            })

    def enqueue_lot_summary(self, free_slots: int) -> None:
        self._write_queue.put({
            "type": "lot_summary",
            "free_slots": free_slots,
        })

    def enqueue_device_status(self, device_id: str, online: bool, kind: str) -> None:
        self._write_queue.put({
            "type": "device_status",
            "device_id": device_id,
            "online": online,
            "kind": kind,
        })

    def enqueue_gate_event(
        self,
        seq: int,
        direction: str,
        decision: str,
        status: str,
        flags: List[str],
        plate_raw: str = "",
        plate_norm: str = "",
        plate_display: str = "",
        confidence: float = 0.0,
        assigned_slot_id: Optional[str] = None,
        matched_reservation_id: Optional[str] = None,
        deny_reason: Optional[str] = None,
        image_url: Optional[str] = None,
        crop_image_url: Optional[str] = None,
        latency_ms: float = 0.0,
    ) -> None:
        self._write_queue.put({
            "type": "gate_event",
            "seq": seq,
            "direction": direction,
            "decision": decision,
            "status": status,
            "flags": flags,
            "plate_raw": plate_raw,
            "plate_norm": plate_norm,
            "plate_display": plate_display,
            "confidence": confidence,
            "assigned_slot_id": assigned_slot_id,
            "matched_reservation_id": matched_reservation_id,
            "deny_reason": deny_reason,
            "image_url": image_url,
            "crop_image_url": crop_image_url,
            "latency_ms": latency_ms,
        })

    # ==========================================
    # INITIAL SYNC
    # ==========================================
    def _initial_sync(self) -> None:
        """Đẩy toàn bộ trạng thái hiện có trong RAM lên Firestore khi khởi động."""
        if not self.db:
            return
        logger.info("Đang thực hiện Full Initial Sync lên Firestore...")
        sensor_map = self.cache.get_sensor_occupied()
        for slot_id, occ in sensor_map.items():
            self.enqueue_slots([slot_id], sensor_map)
        self.enqueue_device_status(settings.DEVICE_ID, self.cache.is_gate_online(), "gate")
        self.enqueue_device_status(settings.CAM_DEVICE_ID, self.cache.is_cam_online(), "cam")

    # ==========================================
    # WORKER LOOP & BATCH WRITING
    # ==========================================
    def _worker_loop(self) -> None:
        while self._running:
            try:
                item = self._write_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if item is None:
                break

            # Thu thập các job dồn ứ trong khoảng 50ms để ghi batch
            batch_items = [item]
            drain_deadline = time.time() + 0.05
            while time.time() < drain_deadline and len(batch_items) < 100:
                try:
                    next_item = self._write_queue.get_nowait()
                    if next_item is None:
                        break
                    batch_items.append(next_item)
                except queue.Empty:
                    break

            self._commit_batch(batch_items)

    def _commit_batch(self, items: List[Dict[str, Any]]) -> None:
        if not self.db:
            return

        # Coalesce / gộp các cập nhật của cùng 1 document
        slots_to_write: Dict[str, bool] = {}
        devices_to_write: Dict[str, Dict[str, Any]] = {}
        gate_events_to_write: List[Dict[str, Any]] = []
        update_lot_summary: Optional[int] = None

        for item in items:
            t = item["type"]
            if t == "slot":
                slots_to_write[item["slot_id"]] = item["sensor_occupied"]
            elif t == "device_status":
                devices_to_write[item["device_id"]] = {
                    "online": item["online"],
                    "kind": item["kind"],
                }
            elif t == "gate_event":
                gate_events_to_write.append(item)
            elif t == "lot_summary":
                update_lot_summary = item["free_slots"]

        try:
            batch = self.db.batch()

            # 1. Ghi slots: lots/{LOT_ID}/slots/{slotId}
            for slot_id, occ in slots_to_write.items():
                doc_ref = (
                    self.db.collection("lots")
                    .document(settings.LOT_ID)
                    .collection("slots")
                    .document(slot_id)
                )
                batch.set(
                    doc_ref,
                    {
                        "sensorOccupied": occ,
                        "updatedAt": firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )

            # 2. Ghi devices: devices/{id}
            for dev_id, info in devices_to_write.items():
                doc_ref = self.db.collection("devices").document(dev_id)
                batch.set(
                    doc_ref,
                    {
                        "online": info["online"],
                        "lastSeen": firestore.SERVER_TIMESTAMP,
                        "lotId": settings.LOT_ID,
                        "type": info["kind"],
                    },
                    merge=True,
                )

                # Đồng bộ trạng thái online vào lots/{LOT_ID}
                lot_ref = self.db.collection("lots").document(settings.LOT_ID)
                if info["kind"] == "gate":
                    batch.set(lot_ref, {"deviceOnline": info["online"]}, merge=True)
                elif info["kind"] == "cam":
                    batch.set(lot_ref, {"camOnline": info["online"]}, merge=True)

            # 3. Ghi gateEvents: gateEvents/{autoId}
            for ev in gate_events_to_write:
                event_ref = self.db.collection("gateEvents").document()
                batch.set(event_ref, {
                    "deviceId": settings.DEVICE_ID,
                    "lotId": settings.LOT_ID,
                    "seq": ev["seq"],
                    "direction": ev["direction"],
                    "plateRaw": ev.get("plate_raw", ""),
                    "plateNormalized": ev.get("plate_norm", ""),
                    "plateDisplay": ev.get("plate_display", ""),
                    "confidence": ev.get("confidence", 0.0),
                    "decision": ev["decision"],
                    "denyReason": ev.get("deny_reason"),
                    "assignedSlotId": ev.get("assigned_slot_id"),
                    "matchedReservationId": ev.get("matched_reservation_id"),
                    "flags": ev["flags"],
                    "status": ev["status"],
                    "imageUrl": ev.get("image_url"),
                    "cropImageUrl": ev.get("crop_image_url"),
                    "latencyMs": ev.get("latency_ms", 0.0),
                    "source": "edge",
                    "serverTs": firestore.SERVER_TIMESTAMP,
                })

            batch.commit()
            logger.debug(
                "[Firestore Commit] Batch thành công: %d slots, %d devices, %d gateEvents",
                len(slots_to_write),
                len(devices_to_write),
                len(gate_events_to_write),
            )
        except Exception as e:
            logger.error("[Firestore Error] Lỗi khi commit batch: %s", e)

    # ==========================================
    # REALTIME LISTENERS
    # ==========================================
    def _start_listeners(self) -> None:
        if not self.db:
            return

        try:
            # 1. Listener reservations active
            res_query = (
                self.db.collection("reservations")
                .where("lotId", "==", settings.LOT_ID)
                .where("status", "==", "active")
            )
            self._res_watch = res_query.on_snapshot(self._on_reservations_snapshot)
            self._res_listener_status = "active"
            logger.info("Đã đăng ký Firestore on_snapshot: reservations (lotId=%s, status=active)", settings.LOT_ID)
        except Exception as e:
            self._res_listener_status = f"error: {e}"
            logger.error("Lỗi khi đăng ký listener reservations: %s", e)

        try:
            # 2. Listener deviceCommands pending
            cmd_query = (
                self.db.collection("deviceCommands")
                .where("deviceId", "==", settings.DEVICE_ID)
                .where("status", "==", "pending")
            )
            self._cmd_watch = cmd_query.on_snapshot(self._on_commands_snapshot)
            self._cmd_listener_status = "active"
            logger.info("Đã đăng ký Firestore on_snapshot: deviceCommands (deviceId=%s, status=pending)", settings.DEVICE_ID)
        except Exception as e:
            self._cmd_listener_status = f"error: {e}"
            logger.error("Lỗi khi đăng ký listener deviceCommands: %s", e)

    def _on_reservations_snapshot(self, col_snapshot: Any, changes: Any, read_time: Any) -> None:
        """Xử lý sự kiện khi có đặt chỗ mới, hủy hoặc hoàn tất."""
        logger.debug("[Firestore Snapshot] Nhận cập nhật reservations (docs=%d)", len(col_snapshot))
        res_items: List[ReservationItem] = []

        for doc in col_snapshot:
            data = doc.to_dict() or {}
            slot_id = data.get("slotId")
            expires_at = data.get("expiresAt")

            if not slot_id or not expires_at:
                continue

            # Xử lý datetime từ Firestore Timestamp
            if hasattr(expires_at, "to_datetime"):
                exp_dt = expires_at.to_datetime()
            elif isinstance(expires_at, datetime):
                exp_dt = expires_at
            else:
                continue

            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)

            res_items.append(
                ReservationItem(
                    id=doc.id,
                    slotId=slot_id,
                    plate=data.get("plate", ""),
                    expiresAt=exp_dt,
                )
            )

        self.cache.replace_reservations(res_items)
        changed_mask = self.cache.reserved_mask_changed()

        if changed_mask is not None:
            logger.info("[Firestore -> Cache] Mặt nạ đặt chỗ thay đổi: %s", changed_mask)
            if self.mqtt:
                self.mqtt.publish_reserved(changed_mask)

    def _on_commands_snapshot(self, col_snapshot: Any, changes: Any, read_time: Any) -> None:
        """Xử lý lệnh điều khiển thiết bị (VD: Admin mở cổng khẩn cấp)."""
        now_dt = datetime.now(timezone.utc)

        for change in changes:
            if change.type.name not in ("ADDED", "MODIFIED"):
                continue

            doc = change.document
            data = doc.to_dict() or {}
            if data.get("status") != "pending":
                continue

            action = data.get("action", "")
            created_at = data.get("createdAt")
            cmd_id = doc.id

            # Kiểm tra thời gian hết hạn của lệnh (TTL 60s)
            created_dt = None
            if hasattr(created_at, "to_datetime"):
                created_dt = created_at.to_datetime()
            elif isinstance(created_at, datetime):
                created_dt = created_at

            if created_dt and created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)

            age_s = (now_dt - created_dt).total_seconds() if created_dt else 0.0

            if age_s > settings.DEVICE_COMMAND_TTL_S:
                logger.warning(
                    "[Firestore Commands] Lệnh %s (action=%s) đã quá hạn TTL (%.1fs > %ds) -> Đổi status=failed",
                    cmd_id,
                    action,
                    age_s,
                    settings.DEVICE_COMMAND_TTL_S,
                )
                doc.reference.update({
                    "status": "failed",
                    "failReason": "EXPIRED",
                    "processedAt": firestore.SERVER_TIMESTAMP,
                })
                continue

            # Đổi status = "sent" trước khi phát lệnh để chống gửi lặp
            logger.info("[Firestore Commands] Nhận lệnh mới: %s (action=%s, age=%.1fs) -> Chuyển status=sent", cmd_id, action, age_s)
            doc.reference.update({
                "status": "sent",
                "sentAt": firestore.SERVER_TIMESTAMP,
            })

            if action == "OPEN_GATE":
                if self.mqtt:
                    self.mqtt.publish_gate_command(seq=0, action="OPEN", plate="ADMIN_OPEN")
            else:
                logger.warning("[Firestore Commands] Action chưa hỗ trợ: %s", action)

    # ==========================================
    # HEARTBEAT & PERIODIC PURGE
    # ==========================================
    def _heartbeat_loop(self) -> None:
        while self._running:
            time.sleep(10)
            if not self._running:
                break

            # 1. Tự động kiểm tra và gỡ các reservation đã hết hạn khỏi mask
            purged = self.cache.purge_expired_reservations()
            if purged:
                changed_mask = self.cache.reserved_mask_changed()
                if changed_mask is not None:
                    logger.info("[Heartbeat] Reservation hết hạn -> Cập nhật lại mặt nạ: %s", changed_mask)
                    if self.mqtt:
                        self.mqtt.publish_reserved(changed_mask)

            # 2. Ghi heartbeat định kỳ lên lots/{LOT_ID} mỗi 30s
            if self.db:
                try:
                    lot_ref = self.db.collection("lots").document(settings.LOT_ID)
                    lot_ref.set({"edgeHeartbeatAt": firestore.SERVER_TIMESTAMP}, merge=True)
                except Exception as e:
                    logger.debug("Lỗi heartbeat Firestore: %s", e)

    # ==========================================
    # STATUS CHO HEALTH CHECK
    # ==========================================
    def status(self) -> Dict[str, Any]:
        return {
            "mode": "firestore",
            "connected": self._connected,
            "queueSize": self._write_queue.qsize(),
            "listeners": {
                "reservations": self._res_listener_status,
                "deviceCommands": self._cmd_listener_status,
            },
        }

