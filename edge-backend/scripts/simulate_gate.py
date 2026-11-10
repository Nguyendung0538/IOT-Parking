#!/usr/bin/env python3
"""Script giả lập thiết bị ESP32 chính (ESP32_PARKING_01).

Cho phép kiểm thử toàn diện luồng MQTT của Edge Backend mà không cần phần cứng thật:
- Kết nối tới Mosquitto Broker với LWT "offline".
- Bắn "online" khi kết nối thành công.
- Lắng nghe và hiển thị Reserved Mask (retained) và Gate Commands từ Edge Server.
- Định kỳ hoặc thủ công bắn State và Gate Request (IN/OUT/Fallback).
"""

import argparse
import json
import sys
import threading
import time
from typing import Dict

import paho.mqtt.client as mqtt

# Trạng thái giả lập của ESP32
sim_state = {
    "uptime": 0,
    "barrierOpen": False,
    "gateState": "IDLE",
    "slots": {
        "A1": False,
        "A2": False,
        "B1": False,
        "B2": False,
    },
    "reserved": {
        "A1": False,
        "A2": False,
        "B1": False,
        "B2": False,
    },
    "seq": 100,
}

state_lock = threading.Lock()
running = True


def compute_free_slots() -> int:
    with state_lock:
        return sum(
            1 for s, occ in sim_state["slots"].items()
            if not occ and not sim_state["reserved"].get(s, False)
        )


def build_state_payload(device_id: str) -> dict:
    with state_lock:
        sim_state["uptime"] += 5
        free = compute_free_slots()
        slots_dict = {}
        for s, occ in sim_state["slots"].items():
            slots_dict[s] = {
                "occupied": occ,
                "reserved": sim_state["reserved"].get(s, False),
            }

        return {
            "deviceId": device_id,
            "uptime": sim_state["uptime"],
            "freeSlots": free,
            "totalSlots": 4,
            "barrierOpen": sim_state["barrierOpen"],
            "gateState": sim_state["gateState"],
            "slots": slots_dict,
        }


def on_connect(client: mqtt.Client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print(f"[ESP32 SIM] Kết nối thất bại: {reason_code}")
        return

    print(f"[ESP32 SIM] Đã kết nối MQTT Broker thành công! (rc={reason_code})")
    device_id = userdata["device_id"]

    # 1. Báo cáo trạng thái online (Retained=True)
    status_topic = f"parking/{device_id}/status"
    client.publish(status_topic, "online", qos=1, retain=True)
    print(f"[ESP32 SIM] -> Published status: 'online' lên {status_topic}")

    # 2. Lắng nghe reserved mask và gate command
    reserved_topic = f"parking/{device_id}/reserved"
    cmd_topic = f"parking/{device_id}/gate/command"
    client.subscribe([(reserved_topic, 1), (cmd_topic, 1)])
    print(f"[ESP32 SIM] -> Đã Subscribed: {reserved_topic} và {cmd_topic}")


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
    topic = msg.topic
    payload_str = msg.payload.decode("utf-8", errors="ignore")
    device_id = userdata["device_id"]

    if topic == f"parking/{device_id}/reserved":
        try:
            mask = json.loads(payload_str)
            with state_lock:
                for k, v in mask.items():
                    if k in sim_state["reserved"]:
                        sim_state["reserved"][k] = bool(v)
            print(f"\n[ESP32 SIM RX] <<< CẬP NHẬT RESERVED MASK: {mask} (Trống thực tế: {compute_free_slots()})")
        except Exception as e:
            print(f"[ESP32 SIM RX] Lỗi giải mã reserved mask: {e}")

    elif topic == f"parking/{device_id}/gate/command":
        try:
            cmd = json.loads(payload_str)
            action = cmd.get("action", "")
            seq = cmd.get("seq", 0)
            plate = cmd.get("plate", "")
            assigned = cmd.get("assignedSlot", "")
            print(f"\n[ESP32 SIM RX] <<< NHẬN LỆNH CỔNG: action={action}, seq={seq}, plate='{plate}', slot='{assigned}'")

            if action == "OPEN":
                with state_lock:
                    sim_state["barrierOpen"] = True
                    sim_state["gateState"] = "BARRIER_OPEN"
                print("[ESP32 SIM] => Barie ĐANG MỞ! (Sẽ tự đóng sau 3 giây)")

                def auto_close():
                    time.sleep(3)
                    with state_lock:
                        sim_state["barrierOpen"] = False
                        sim_state["gateState"] = "IDLE"
                    print("\n[ESP32 SIM] => Barie ĐÃ ĐÓNG về IDLE.")
                    # Bắn state cập nhật
                    payload = build_state_payload(device_id)
                    client.publish(f"parking/{device_id}/state", json.dumps(payload), qos=1)

                threading.Thread(target=auto_close, daemon=True).start()

        except Exception as e:
            print(f"[ESP32 SIM RX] Lỗi giải mã gate command: {e}")


def auto_publish_loop(client: mqtt.Client, device_id: str, interval: int):
    while running:
        time.sleep(interval)
        try:
            payload = build_state_payload(device_id)
            client.publish(f"parking/{device_id}/state", json.dumps(payload), qos=1)
            # print(f"[ESP32 SIM TX] State heartbeat (free={payload['freeSlots']})")
        except Exception as e:
            print(f"[ESP32 SIM] Lỗi auto publish: {e}")


def main():
    parser = argparse.ArgumentParser(description="Mô phỏng phần cứng ESP32 Gate")
    parser.add_argument("--host", default="127.0.0.1", help="Địa chỉ MQTT Broker")
    parser.add_argument("--port", type=int, default=1883, help="Cổng MQTT Broker")
    parser.add_argument("--user", default="esp32_gate_01", help="MQTT Username")
    parser.add_argument("--password", default="gate_secret_2026", help="MQTT Password")
    parser.add_argument("--device", default="ESP32_PARKING_01", help="Device ID")
    parser.add_argument("--auto-interval", type=int, default=5, help="Chu kỳ tự động gửi state (s, 0 để tắt)")
    args = parser.parse_args()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="sim_esp32_gate_worker",
        userdata={"device_id": args.device},
    )

    if args.user and args.password:
        client.username_pw_set(args.user, args.password)

    # Đăng ký LWT
    lwt_topic = f"parking/{args.device}/status"
    client.will_set(lwt_topic, "offline", qos=1, retain=True)

    client.on_connect = on_connect
    client.on_message = on_message

    print(f"[ESP32 SIM] Đang kết nối tới {args.host}:{args.port} dưới tư cách {args.device}...")
    try:
        client.connect(args.host, args.port, keepalive=30)
    except Exception as e:
        print(f"[ESP32 SIM] Kết nối thất bại: {e}")
        return

    client.loop_start()

    # Khởi động luồng auto publish state
    if args.auto_interval > 0:
        pub_thread = threading.Thread(
            target=auto_publish_loop,
            args=(client, args.device, args.auto_interval),
            daemon=True,
        )
        pub_thread.start()

    print("\n--- HƯỚNG DẪN LỆNH MÔ PHỎNG CLI ---")
    print("  occupy <A1|A2|B1|B2> : Xe đỗ vào ô")
    print("  free <A1|A2|B1|B2>   : Xe rời ô")
    print("  state                : Bắn gói tin state ngay lập tức")
    print("  req in               : Bắn yêu cầu xe VÀO (IN)")
    print("  req out              : Bắn yêu cầu xe RA (OUT)")
    print("  req fallback         : Bắn gate/request với offlineFallback=true")
    print("  status               : Xem trạng thái hiện tại trong RAM")
    print("  quit                 : Thoát mô phỏng")
    print("------------------------------------\n")

    global running
    try:
        while True:
            cmd_line = input("sim> ").strip()
            if not cmd_line:
                continue

            parts = cmd_line.split()
            cmd = parts[0].lower()

            if cmd == "quit" or cmd == "exit":
                break
            elif cmd == "status":
                with state_lock:
                    print(f"State: {json.dumps(sim_state, indent=2)}")
                    print(f"Free slots public: {compute_free_slots()}")
            elif cmd == "occupy" and len(parts) > 1:
                slot = parts[1].upper()
                if slot in sim_state["slots"]:
                    with state_lock:
                        sim_state["slots"][slot] = True
                    payload = build_state_payload(args.device)
                    client.publish(f"parking/{args.device}/state", json.dumps(payload), qos=1)
                    print(f"[ESP32 SIM] Ô {slot} ĐÃ CÓ XE (Occupied = True) -> Bắn state!")
                else:
                    print(f"Ô không hợp lệ: {slot}")
            elif cmd == "free" and len(parts) > 1:
                slot = parts[1].upper()
                if slot in sim_state["slots"]:
                    with state_lock:
                        sim_state["slots"][slot] = False
                    payload = build_state_payload(args.device)
                    client.publish(f"parking/{args.device}/state", json.dumps(payload), qos=1)
                    print(f"[ESP32 SIM] Ô {slot} ĐÃ TRỐNG (Occupied = False) -> Bắn state!")
                else:
                    print(f"Ô không hợp lệ: {slot}")
            elif cmd == "state":
                payload = build_state_payload(args.device)
                client.publish(f"parking/{args.device}/state", json.dumps(payload), qos=1)
                print(f"[ESP32 SIM] Đã bắn state: {json.dumps(payload)}")
            elif cmd == "req" and len(parts) > 1:
                sub = parts[1].lower()
                with state_lock:
                    sim_state["seq"] += 1
                    cur_seq = sim_state["seq"]

                req_payload = {
                    "deviceId": args.device,
                    "seq": cur_seq,
                    "direction": "IN" if sub in ("in", "fallback") else "OUT",
                    "freeSlots": compute_free_slots(),
                    "timestamp": int(time.time()),
                    "offlineFallback": (sub == "fallback"),
                }
                client.publish(f"parking/{args.device}/gate/request", json.dumps(req_payload), qos=1)
                print(f"[ESP32 SIM] Đã bắn gate/request (seq={cur_seq}, direction={req_payload['direction']}, fallback={req_payload['offlineFallback']})")
            else:
                print(f"Lệnh không nhận dạng: {cmd_line}")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        print("\n[ESP32 SIM] Đang ngắt kết nối...")
        running = False
        client.publish(lwt_topic, "offline", qos=1, retain=True)
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        print("[ESP32 SIM] Đã dừng mô phỏng.")


if __name__ == "__main__":
    main()

