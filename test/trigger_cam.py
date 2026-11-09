import subprocess
import sys
import json
import time

def trigger(seq=1, direction="IN"):
    payload = json.dumps({
        "deviceId": "ESP32_PARKING_01",
        "seq": seq,
        "direction": direction,
        "freeSlots": 3
    })
    
    cmd = [
        "docker", "exec", "parking_mosquitto",
        "mosquitto_pub",
        "-u", "esp32_gate_01",
        "-P", "gate_secret_2026",
        "-t", "parking/ESP32_PARKING_01/gate/request",
        "-m", payload
    ]
    
    print(f"[TRIGGER] Dang ban lenh chup toi ESP32-CAM qua Mosquitto...")
    print(f" -> Topic: parking/ESP32_PARKING_01/gate/request")
    print(f" -> Payload: {payload}")
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print("[TRIGGER] >> BAN LENH THANH CONG! Quan sat den Flash LED tren ESP32-CAM.")
        else:
            print(f"[TRIGGER] Loi khi gui: {res.stderr}")
    except Exception as e:
        print(f"[TRIGGER] Loi: {e}")

if __name__ == "__main__":
    seq_num = int(sys.argv[1]) if len(sys.argv) > 1 else int(time.time()) % 1000
    trigger(seq=seq_num, direction="IN")
