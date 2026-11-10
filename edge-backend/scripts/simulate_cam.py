"""Simulate ESP32-CAM (Firmware src/cam/main_cam.cpp).

Script này mô phỏng toàn bộ hành vi của ESP32-CAM:
1. Kết nối MQTT Broker và công bố LWT (parking/ESP32_CAM_01/status = online)
2. Lắng nghe topic: parking/ESP32_PARKING_01/gate/request
3. Khi nhận yêu cầu mở cổng từ ESP32 Chính:
   - Lấy 2 ảnh biển số từ dataset (hoặc ảnh test mẫu)
   - Gửi HTTP POST multipart/form-data lên Edge Backend (/recognize) kèm Header X-API-Key
4. Cũng hỗ trợ chạy đơn lẻ 1 lần (--once) để test nhanh API mà không cần MQTT.

Cách sử dụng:
  # Chạy daemon lắng nghe MQTT
  python simulate_cam.py

  # Hoặc test bắn 1 ảnh cụ thể
  python simulate_cam.py --once --image ..\yolo_plate_dataset\images\val\CarLongPlate10.jpg
"""

import argparse
import glob
import io
import json
import logging
import os
from pathlib import Path
import random
import time
from typing import List, Optional

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("simulate_cam")

# Cấu hình mặc định khớp với ESP32-CAM thật
DEFAULT_MQTT_HOST = "127.0.0.1"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_USER = "cam_01"
DEFAULT_MQTT_PASS = "cam_secret_2026"
DEFAULT_EDGE_URL = "http://127.0.0.1:8000/recognize"
DEFAULT_API_KEY = "smart-parking-cam-secret-2026"

TOPIC_GATE_REQUEST = "parking/ESP32_PARKING_01/gate/request"
TOPIC_CAM_STATUS = "parking/ESP32_CAM_01/status"


def find_sample_images(dataset_dir: Optional[str] = None) -> List[str]:
    """Tìm danh sách ảnh mẫu từ dataset."""
    search_dirs = [
        dataset_dir,
        str(Path(__file__).resolve().parent.parent.parent / "yolo_plate_dataset" / "images" / "val"),
        str(Path(__file__).resolve().parent.parent.parent / "yolo_plate_dataset" / "images" / "train"),
    ]
    for d in search_dirs:
        if d and os.path.isdir(d):
            imgs = glob.glob(os.path.join(d, "*.jpg")) + glob.glob(os.path.join(d, "*.png"))
            if imgs:
                return imgs
    return []


def create_dummy_jpeg() -> bytes:
    """Tạo 1 ảnh JPEG trắng đơn giản nếu không tìm thấy dataset."""
    from PIL import Image
    img = Image.new("RGB", (320, 240), color=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def send_recognize_request(
    edge_url: str,
    api_key: str,
    seq: int,
    direction: str,
    image_paths: List[str],
) -> Optional[dict]:
    """Gửi HTTP POST multipart/form-data lên Edge Server giống hệt main_cam.cpp."""
    files = []
    opened_files = []

    try:
        if image_paths:
            for idx, p in enumerate(image_paths[:2]):
                f = open(p, "rb")
                opened_files.append(f)
                files.append(("images", (f"frame_{idx}.jpg", f, "image/jpeg")))
        else:
            dummy_bytes = create_dummy_jpeg()
            files.append(("images", ("frame_0.jpg", dummy_bytes, "image/jpeg")))

        data = {
            "device_id": "ESP32_CAM_01",
            "seq": str(seq),
            "direction": direction,
        }
        headers = {
            "X-API-Key": api_key,
        }

        t0 = time.perf_counter()
        resp = requests.post(edge_url, data=data, files=files, headers=headers, timeout=5.0)
        dur_ms = (time.perf_counter() - t0) * 1000

        if resp.status_code == 200:
            res_json = resp.json()
            logger.info(
                "[CAM -> HTTP 200] seq=%d, dir=%s -> Action=%s, Slot=%s, Plate='%s' (Latency: %.1fms)",
                seq,
                direction,
                res_json.get("action"),
                res_json.get("assignedSlot"),
                res_json.get("plateDisplay"),
                dur_ms,
            )
            return res_json
        else:
            logger.error("[CAM -> HTTP %d] Lỗi từ Edge Server: %s", resp.status_code, resp.text)
            return None

    except Exception as e:
        logger.error("[CAM -> HTTP ERROR] Không thể gửi ảnh lên %s: %s", edge_url, e)
        return None
    finally:
        for f in opened_files:
            try:
                f.close()
            except Exception:
                pass


class CamSimulator:
    def __init__(
        self,
        mqtt_host: str = DEFAULT_MQTT_HOST,
        mqtt_port: int = DEFAULT_MQTT_PORT,
        edge_url: str = DEFAULT_EDGE_URL,
        api_key: str = DEFAULT_API_KEY,
        image_override: Optional[str] = None,
    ):
        self.edge_url = edge_url
        self.api_key = api_key
        self.image_override = image_override
        self.sample_images = find_sample_images()
        logger.info("Tìm thấy %d ảnh mẫu trong dataset.", len(self.sample_images))

        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id="ESP32_CAM_SIM",
            clean_session=True,
        )
        self.client.username_pw_set(DEFAULT_MQTT_USER, DEFAULT_MQTT_PASS)
        # Thiết lập LWT offline
        self.client.will_set(TOPIC_CAM_STATUS, "offline", qos=1, retain=False)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port

    def _on_connect(self, client, userdata, flags, rc, properties):
        if rc == 0:
            logger.info("Đã kết nối MQTT Broker %s:%d thành công!", self.mqtt_host, self.mqtt_port)
            # Công bố trạng thái online
            client.publish(TOPIC_CAM_STATUS, "online", qos=1, retain=False)
            logger.info("Đã công bố LWT: %s = online", TOPIC_CAM_STATUS)
            # Subscribe lắng nghe yêu cầu chụp ảnh từ ESP32 Chính
            client.subscribe(TOPIC_GATE_REQUEST, qos=1)
            logger.info("Đang lắng nghe topic: %s", TOPIC_GATE_REQUEST)
        else:
            logger.error("Kết nối MQTT thất bại rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            seq = payload.get("seq", 0)
            direction = payload.get("direction", "IN")
            offline_fallback = payload.get("offlineFallback", False)

            if offline_fallback:
                logger.info("[CAM] Bỏ qua gate/request offlineFallback (seq=%d)", seq)
                return

            logger.info(
                "=================================================="
            )
            logger.info("[CAM RX] Nhận gate/request từ ESP32 Chính! seq=%u, direction=%s", seq, direction)

            # Giả lập thời gian chụp 2 ảnh của ESP32-CAM (~150ms)
            time.sleep(0.15)

            # Chọn ảnh để gửi
            if self.image_override and os.path.exists(self.image_override):
                imgs = [self.image_override]
            elif self.sample_images:
                imgs = random.sample(self.sample_images, min(2, len(self.sample_images)))
                logger.info("Chọn ngẫu nhiên ảnh từ dataset: %s", [os.path.basename(p) for p in imgs])
            else:
                imgs = []

            # Gửi HTTP POST lên Edge Server
            send_recognize_request(
                edge_url=self.edge_url,
                api_key=self.api_key,
                seq=seq,
                direction=direction,
                image_paths=imgs,
            )

        except Exception as e:
            logger.error("[CAM] Lỗi khi xử lý message: %s", e)

    def start(self):
        logger.info("Đang kết nối tới MQTT Broker %s:%d...", self.mqtt_host, self.mqtt_port)
        self.client.connect(self.mqtt_host, self.mqtt_port, 60)
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            logger.info("Dừng giả lập ESP32-CAM.")
            self.client.publish(TOPIC_CAM_STATUS, "offline", qos=1, retain=False)
            self.client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="ESP32-CAM Simulator for Phase S5")
    parser.add_argument("--host", default=DEFAULT_MQTT_HOST, help="MQTT Broker Host")
    parser.add_argument("--port", type=int, default=DEFAULT_MQTT_PORT, help="MQTT Broker Port")
    parser.add_argument("--edge-url", default=DEFAULT_EDGE_URL, help="Edge Server /recognize URL")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="CAM_SHARED_API_KEY")
    parser.add_argument("--image", default=None, help="Đường dẫn file ảnh cụ thể để test")
    parser.add_argument("--once", action="store_true", help="Chỉ gửi 1 request test rồi thoát")
    parser.add_argument("--seq", type=int, default=1, help="Seq dùng cho chế độ --once")
    parser.add_argument("--dir", default="IN", choices=["IN", "OUT"], help="Direction cho chế độ --once")

    args = parser.parse_args()

    if args.once:
        logger.info("Chạy chế độ gửi test 1 lần: seq=%d, dir=%s, image=%s", args.seq, args.dir, args.image)
        imgs = [args.image] if args.image and os.path.exists(args.image) else find_sample_images()
        send_recognize_request(
            edge_url=args.edge_url,
            api_key=args.api_key,
            seq=args.seq,
            direction=args.dir,
            image_paths=imgs[:2] if imgs else [],
        )
    else:
        sim = CamSimulator(
            mqtt_host=args.host,
            mqtt_port=args.port,
            edge_url=args.edge_url,
            api_key=args.api_key,
            image_override=args.image,
        )
        sim.start()


if __name__ == "__main__":
    main()

