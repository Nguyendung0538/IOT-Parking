# Sổ Tay Vận Hành & Đấu Nối Trạm Bãi Đỗ Xe Thông Minh (Repo 1: `iot-parking-station`)

> **Dành cho:** Người vận hành hệ thống, Kỹ sư phần cứng, và Nhà phát triển tích hợp Repo 2 (Android App & Cloud Functions).  
> **Phiên bản:** v1.0 — Đóng gói hoàn chỉnh sau khi hoàn thành Phase S6.

---

## 1. Sơ Đồ Kiến Trúc Hệ Thống

```mermaid
graph TD
    subgraph Station["Trạm Vật Lý / Mô Hình Sa Hình"]
        ESP32["ESP32 Chính (Gate Controller)<br/>4x Cảm biến ô đỗ (A1, A2, B1, B2)<br/>2x Cảm biến cổng (S_IN, S_OUT)<br/>1x Servo SG90 Barie<br/>1x LCD 1602 + 1x OLED 1.3 SH1106"]
        CAM["ESP32-CAM AI-Thinker<br/>Camera OV2640 + Flash LED GPIO 4"]
    end

    subgraph Edge["Máy Chủ Trạm (Edge Server / Laptop RTX 5060)"]
        Broker["Mosquitto MQTT Broker (:1883)<br/>User/Pass & ACL"]
        FastAPI["FastAPI Edge Backend (:8000)<br/>Fast Gate Arbiter & REST APIs"]
        GPU["NVIDIA GPU AI Pipeline<br/>YOLOv8 + EasyOCR (CUDA 12.8)"]
        Storage["Async Storage Uploader<br/>data/captures/ & Firebase Storage"]
        Cache["StateCache (In-RAM Single Truth)"]
    end

    subgraph Cloud["Google Cloud Platform (Repo 2)"]
        Firestore["Cloud Firestore (Realtime DB)<br/>lots, slots, reservations, gateEvents"]
        Functions["Firebase Cloud Functions<br/>onGateEventCreated, onReservationCreated"]
    end

    ESP32 <-->|MQTT Telemetry & Commands| Broker
    CAM -->|MQTT gate/request| Broker
    CAM -->|HTTP POST /recognize (Multipart)| FastAPI
    Broker <--> FastAPI
    FastAPI <--> GPU
    FastAPI <--> Cache
    FastAPI --> Storage
    Storage --> Cloud
    FastAPI <-->|Realtime on_snapshot| Firestore
```

---

## 2. Hướng Dẫn Đấu Nối Phần Cứng (ESP32 & Cảm Biến)

### 2.1. Bảng phân chân ESP32 Chính (`NodeMCU-32S`)

| Chân ESP32 | Thiết bị ngoại vi | Ghi chú & Cấu hình |
| :---: | :--- | :--- |
| **GPIO 21** | SDA (I2C Bus) | Nối chung SDA của LCD 1602 (`0x27`) và OLED SH1106 (`0x3C`) |
| **GPIO 22** | SCL (I2C Bus) | Nối chung SCL của LCD 1602 và OLED SH1106 |
| **GPIO 18** | Chân tín hiệu Servo SG90 | Điều khiển cần barie (Góc đóng: `0°`, Góc mở: `90°`) |
| **GPIO 34** | Cảm biến cổng ngoài `S_OUT` | Cảm biến hồng ngoại phát hiện xe đến cổng vào (Active-LOW) |
| **GPIO 35** | Cảm biến cổng trong `S_IN` | Cảm biến hồng ngoại phát hiện xe đã qua cổng (Active-LOW) |
| **GPIO 25** | Cảm biến ô đỗ `A1` | Cảm biến hồng ngoại (Active-LOW, có xe = 0, trống = 1) |
| **GPIO 26** | Cảm biến ô đỗ `A2` | Cảm biến hồng ngoại (Active-LOW) |
| **GPIO 27** | Cảm biến ô đỗ `B1` | Cảm biến hồng ngoại (Active-LOW) |
| **GPIO 14** | Cảm biến ô đỗ `B2` | Cảm biến hồng ngoại (Active-LOW) |
| **VIN (5V) & GND** | Nguồn cấp | Cấp nguồn 5V/2A ổn định cho Servo và module LCD/OLED |

### 2.2. ESP32-CAM AI-Thinker
- Sử dụng module **AI-Thinker ESP32-CAM** (Camera OV2640).
- Flash LED tích hợp nối chân `GPIO 4`.
- Nguồn cấp: Nối chân **5V & GND** với adapter 5V/2A (tránh dùng nguồn USB máy tính yếu dễ bị sụt áp khi bật Flash và Wi-Fi).

---

## 3. Quy Trình Khởi Động Trạm (Step-by-Step)

### Bước 1: Khởi động Mosquitto MQTT Broker
Trên máy Edge Server (hoặc Laptop):
```powershell
cd f:\CodePJ\IOT-Parking\edge-backend
docker-compose up -d
```
Kiểm tra broker đang lắng nghe tại cổng `1883`:
```powershell
netstat -ano | findstr 1883
```

### Bước 2: Khởi động Edge Backend Server
Kích hoạt môi trường ảo Python 3.12 (đã cài PyTorch CUDA + Ultralytics + EasyOCR):
```powershell
cd f:\CodePJ\IOT-Parking\edge-backend
.\.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Khi khởi động thành công, màn hình sẽ hiển thị:
```text
[INFO] [plate_detector]: PlateDetector: Khởi tạo trên GPU (NVIDIA GeForce RTX 5060)
[INFO] [plate_ocr]: PlateOCR: Khởi tạo EasyOCR thành công (GPU=True)
[INFO] [plate_pipeline]: GPU Warm-up THÀNH CÔNG trong 650 ms! Pipeline sẵn sàng.
[INFO] [mqtt_bridge]: Kết nối MQTT Broker THÀNH CÔNG (rc=0)
[INFO] Edge Backend Server đã sẵn sàng phục vụ!
```
- Kiểm tra trạng thái hệ thống: Mở trình duyệt truy cập `http://localhost:8000/health`.

### Bước 3: Nạp Firmware lên ESP32
Dùng cáp micro-USB kết nối mạch vào máy tính:
1. **Nạp ESP32 Chính:**
   ```powershell
   pio run -e nodemcu-32s -t upload
   ```
2. **Nạp ESP32-CAM (nối GPIO 0 xuống GND khi nạp):**
   ```powershell
   pio run -e esp32cam -t upload
   ```

---

## 4. Chế Độ Chạy Giả Lập Toàn Phần (Software-in-the-Loop Simulation)

Nếu chưa cắm mạch phần cứng, bạn có thể chạy **100% mô phỏng tương tác thời gian thực** bằng 2 script CLI tích hợp sẵn:

### Cửa sổ 1: Giả lập ESP32-CAM
Lắng nghe MQTT `gate/request` và tự động bốc ảnh biển số xe thật gửi lên Edge Server:
```powershell
cd f:\CodePJ\IOT-Parking\edge-backend
.\.venv\Scripts\python.exe scripts/simulate_cam.py
```

### Cửa sổ 2: Giả lập ESP32 Chính (Barie + 4 Ô đỗ)
```powershell
cd f:\CodePJ\IOT-Parking\edge-backend
.\.venv\Scripts\python.exe scripts/simulate_gate.py
```
- Nhấn phím `1` để kích hoạt xe đến cổng vào $\rightarrow$ ESP32 gửi `gate/request` $\rightarrow$ CAM Simulator bắt được $\rightarrow$ gửi ảnh $\rightarrow$ AI đọc biển số trong ~50ms $\rightarrow$ Barie mở $\rightarrow$ Nhấn phím `2` để xe vào bãi $\rightarrow$ Barie tự đóng lại!
- Nhấn các phím `A1`, `A2`, `B1`, `B2` để đảo trạng thái cảm biến ô đỗ.

---

## 5. Chạy Lại Bộ Kiểm Thử Nghiệm Thu Toàn Trạm (S6)

Bất kỳ khi nào thay đổi mã nguồn hoặc trọng số mô hình AI, bạn có thể chạy lại bộ test nghiệm thu tự động chỉ bằng một lệnh:

```powershell
# Chạy script nghiệm thu có bảng tổng kết trực quan
python edge-backend/scripts/run_s6_verification.py

# Hoặc chạy toàn bộ 79 tests (S3, S4, S5, S6)
pytest -v edge-backend/tests
```

---

## 6. Xử Lý Sự Cố Thường Gặp (Troubleshooting)

| Hiện tượng | Nguyên nhân | Cách khắc phục |
| :--- | :--- | :--- |
| **Barie mở chậm sau đúng 5.0 giây, LCD hiện `KHONG RO`** | ESP32-CAM mất Wi-Fi, sai IP Edge Server, hoặc rút nguồn. | Đây là cơ chế bảo vệ an toàn `CAMERA_TIMEOUT_S = 5.0` của Phase S5 để xe không bị kẹt. Kiểm tra nguồn 5V của CAM và cấu hình Wi-Fi trong `include/common_network.h`. |
| **Barie mở chậm sau đúng 8.0 giây** | Edge Server bị tắt hoặc mất kết nối Mosquitto. | Đây là cơ chế Fallback cứng trên ESP32 (`GATE_TIMEOUT_FALLBACK_MS = 8000`). Bật lại FastAPI Server trên laptop. |
| **HTTP 401 Unauthorized khi gửi `/recognize`** | Header `X-API-Key` của CAM không khớp với server. | Kiểm tra `CAM_SHARED_API_KEY` trong `edge-backend/.env` và `include/common_network.h` (mặc định: `smart-parking-cam-secret-2026`). |
| **Chữ trên LCD 1602 bị vuông đen hoặc OLED không sáng** | Sai địa chỉ I2C hoặc lỏng dây SDA/SCL. | Kiểm tra dây nối GPIO 21 (SDA), GPIO 22 (SCL). Địa chỉ chuẩn: LCD là `0x27`, OLED là `0x3C`. |

