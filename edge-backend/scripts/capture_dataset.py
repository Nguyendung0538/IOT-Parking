"""Tool thu thập dataset ảnh biển số xe từ ESP32-CAM phục vụ kiểm thử và huấn luyện."""

import argparse
import datetime
import json
import logging
from pathlib import Path
import time
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("capture_dataset")

DATASET_RAW_DIR = Path(__file__).resolve().parent.parent / "dataset" / "raw"


def main():
    parser = argparse.ArgumentParser(description="Kích hoạt ESP32-CAM chụp ảnh và lưu dataset")
    parser.add_argument("--mqtt-host", default="127.0.0.1", help="Địa chỉ MQTT Broker")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="Cổng MQTT Broker")
    parser.add_argument("--mqtt-user", default="edge_server", help="MQTT User")
    parser.add_argument("--mqtt-pass", default="edge_secret_2026", help="MQTT Password")
    parser.add_argument("--device", default="ESP32_PARKING_01", help="Device ID của ESP32 Gate")
    parser.add_argument("--interval", type=int, default=0, help="Chu kỳ tự động bắn lệnh chụp (giây, 0 = thủ công)")
    args = parser.parse_args()

    DATASET_RAW_DIR.mkdir(parents=True, exist_ok=True)
    topic_request = f"parking/{args.device}/gate/request"

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="capture_dataset_worker",
    )
    if args.mqtt_user and args.mqtt_pass:
        client.username_pw_set(args.mqtt_user, args.mqtt_pass)

    logger.info("Đang kết nối MQTT tới %s:%d ...", args.mqtt_host, args.mqtt_port)
    try:
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
        client.loop_start()
        logger.info("Đã kết nối MQTT Broker thành công!")
    except Exception as e:
        logger.error("Kết nối MQTT thất bại: %s", e)
        return

    seq = 1000

    print("\n" + "=" * 60)
    print("📸 CÔNG CỤ KÍCH HOẠT CHỤP ẢNH DATASET TỪ ESP32-CAM")
    print("=" * 60)
    print("  • Nhấn [Enter] để bắn lệnh kích hoạt ESP32-CAM chụp ảnh")
    print("  • Gõ 'auto <giây>' để tự động chụp theo chu kỳ (vd: auto 3)")
    print("  • Gõ 'q' hoặc 'quit' để thoát")
    print("=" * 60 + "\n")

    try:
        while True:
            cmd = input("capture> ").strip()
            if cmd.lower() in ("q", "quit", "exit"):
                break

            if cmd.lower().startswith("auto"):
                parts = cmd.split()
                interval = int(parts[1]) if len(parts) > 1 else 3
                print(f"Bắt đầu tự động chụp mỗi {interval} giây. Nhấn Ctrl+C để dừng...")
                try:
                    while True:
                        seq += 1
                        payload = json.dumps({
                            "deviceId": args.device,
                            "seq": seq,
                            "direction": "IN",
                            "freeSlots": 3,
                            "timestamp": int(time.time()),
                        })
                        client.publish(topic_request, payload, qos=1)
                        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Đã gửi lệnh chụp seq={seq}")
                        time.sleep(interval)
                except KeyboardInterrupt:
                    print("\nĐã dừng chế độ tự động.")
                    continue

            # Mặc định: bấm Enter để chụp 1 lần
            seq += 1
            payload = json.dumps({
                "deviceId": args.device,
                "seq": seq,
                "direction": "IN",
                "freeSlots": 3,
                "timestamp": int(time.time()),
            })
            client.publish(topic_request, payload, qos=1)
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Đã gửi lệnh chụp seq={seq} -> CAM đang nháy đèn chụp ảnh...")

    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        logger.info("Đã đóng công cụ capture.")


if __name__ == "__main__":
    main()

