# KẾ HOẠCH TRIỂN KHAI CHI TIẾT REPO 1 (`iot-parking-station`)
**Tài liệu:** Kế hoạch thực thi từng bước (Step-by-step Execution Plan) cho các phần còn lại của Repo 1  
**Tham chiếu kiến trúc:** `docs/Plan_Repo1_IoT_And_Edge_Backend.md`  
**Ngày tạo:** 2026-11-06

---

## 1. TỔNG QUAN PHẦN ĐÃ HOÀN THÀNH VS PHẦN CẦN TRIỂN KHAI

### 1.1. Phần đã hoàn thành (100% — Giữ nguyên logic cốt lõi)
- [x] **Phase F1 — Cấu hình phần cứng (`include/config.h`):** Pinmap I2C (`21, 22`), Servo (`18`), Cảm biến cổng (`34, 35`), 4 cảm biến ô đỗ (`25, 26, 27, 14`), hằng số debounce `250ms` và safety timeout `10000ms`.
- [x] **Phase F2 — Giám sát ô đỗ cục bộ (`ParkingMonitor.h/.cpp`):** Đọc và chống nhiễu 4 cảm biến hồng ngoại Active-LOW.
- [x] **Phase F3 — Điều khiển cổng cục bộ (`GateController.h/.cpp`):** FSM phát hiện xe vào/ra tuần tự qua `S_OUT` & `S_IN`, đóng/mở Servo SG90, tự đóng sau 10s.
- [x] **Phase F4 — Hiển thị màn hình (`DisplayManager.h/.cpp`):** Hiển thị trạng thái trên LCD 1602 (`0x27`) và vẽ mũi tên điều hướng Khu A/B trên OLED 1.3" SH1106 (`0x3C`).
- [x] **Phase F5 — Vòng lặp tích hợp (`src/main.cpp`):** Chạy tuần tự non-blocking với `millis()`.

---

## 2. CHI TIẾT CÁC BƯỚC CẦN LÀM TIẾP THEO (PHASES S1 → S6)

### PHASE S1 — TÁI CẤU TRÚC PLATFORMIO MULTI-ENV & KHỞI TẠO MOSQUITTO BROKER (ĐÃ HOÀN THÀNH ✅)

#### Bước S1.1: Tách cấu trúc thư mục Firmware (`gate` và `cam`)
- [x] Tạo thư mục `src/gate/` và di chuyển 5 file `.cpp` hiện tại (`main.cpp`, `ParkingMonitor.cpp`, `GateController.cpp`, `DisplayManager.cpp`, `ParkingNetworkClient.cpp`) vào `src/gate/`.
- [x] Tạo thư mục `src/cam/` và file khung `src/cam/main_cam.cpp`.
- [x] Cập nhật `platformio.ini`:
  - Thêm `build_src_filter = +<gate/*> -<cam/*>` cho `[env:nodemcu-32s]`.
  - Thêm môi trường `[env:esp32cam]` (`board = esp32cam`, `board_build.partitions = huge_app.csv`, `build_src_filter = +<cam/*> -<gate/*>`).

#### Bước S1.2: Tạo Header hợp đồng mạng dùng chung (`include/common_network.h`)
- [x] Định nghĩa thông tin Wi-Fi, địa chỉ IP máy trạm Edge Server (`EDGE_SERVER_IP`), cổng MQTT (`1883`), cổng FastAPI (`8000`).
- [x] Định nghĩa tài khoản MQTT (`esp32_gate_01`, `esp32_cam_01`) và 6 hằng số Topic chuẩn:
  - `TOPIC_STATE`: `"parking/ESP32_PARKING_01/state"`
  - `TOPIC_GATE_REQUEST`: `"parking/ESP32_PARKING_01/gate/request"`
  - `TOPIC_GATE_COMMAND`: `"parking/ESP32_PARKING_01/gate/command"`
  - `TOPIC_RESERVED`: `"parking/ESP32_PARKING_01/reserved"`
  - `TOPIC_GATE_STATUS`: `"parking/ESP32_PARKING_01/status"`
  - `TOPIC_CAM_STATUS`: `"parking/ESP32_CAM_01/status"`

#### Bước S1.3: Khởi tạo cấu hình Mosquitto Broker (`edge-backend/mosquitto/`)
- [x] Tạo `edge-backend/mosquitto/mosquitto.conf` (`listener 1883 0.0.0.0`, `allow_anonymous false`, trỏ tới `passwd` và `acl`).
- [x] Tạo `edge-backend/mosquitto/acl` phân quyền đọc/ghi theo từng client.
- [x] Tạo `edge-backend/docker-compose.yml` để khởi chạy container `eclipse-mosquitto:2`.

---

### PHASE S2 — NÂNG CẤP FIRMWARE ESP32 CHÍNH & VIẾT FIRMWARE ESP32-CAM (ĐÃ HOÀN THÀNH ✅)

#### Bước S2.1: Bổ sung mặt nạ Đặt chỗ trong `ParkingMonitor`
- [x] Thêm mảng `bool reservedState[NUM_SLOTS]` vào `ParkingMonitor.h`.
- [x] Thêm các phương thức:
  ```cpp
  bool setReservedMask(bool a1, bool a2, bool b1, bool b2);
  bool isReserved(uint8_t slotIndex) const;
  bool isSlotAvailable(uint8_t slotIndex) const; // !isOccupied(i) && !isReserved(i)
  ```
- [x] Cập nhật `getAvailableA()`, `getAvailableB()`, `getTotalAvailable()` và `isFull()` dựa trên `isSlotAvailable(i)`.

#### Bước S2.2: Bổ sung trạng thái `WAIT_AUTH` & Fallback 8s trong `GateController`
- [x] Mở rộng `enum GateState` thêm `GATE_STATE_WAIT_AUTH_IN` và `GATE_STATE_WAIT_AUTH_OUT`.
- [x] Mở rộng `enum GateEvent` thêm `GATE_EVENT_WAITING_AUTH`.
- [x] Thêm hằng số `#define GATE_AUTH_TIMEOUT_MS 8000` trong `include/config.h`.
- [x] Thêm các phương thức giao tiếp với `ParkingNetworkClient`:
  ```cpp
  bool consumePendingAuthRequest(uint32_t& outSeq, const char*& outDirection);
  bool consumeOfflineFallbackEvent(uint32_t& outSeq, bool& outEntering);
  void handleBackendCommand(uint32_t seq, bool allowOpen, const char* plate, const char* assignedSlot);
  const char* getActivePlate() const;
  const char* getAssignedSlot() const;
  ```
- [x] Logic Fallback: Nếu đang ở `WAIT_AUTH_IN` / `WAIT_AUTH_OUT` mà quá `8000ms` (hoặc khi `ENABLE_WIFI == false`), tự động duyệt cục bộ (`!parkingFull` đối với chiều vào, luôn mở đối với chiều ra).

#### Bước S2.3: Nâng cấp `DisplayManager` hiển thị Biển số & Ô được gán
- [x] Cập nhật `DisplayManager::update(...)` nhận thêm `const ParkingMonitor& parking` và `const GateController& gate`.
- [x] **LCD 1602:**
  - Khi `GATE_EVENT_WAITING_AUTH`: `DANG NHAN DIEN..` / `VUI LONG CHO...`
  - Khi `GATE_EVENT_CAR_ENTERING` có biển số: `MOI VAO -> O A1` / `BS: 51A-123.45`
  - Khi `GATE_EVENT_CAR_EXITING` có biển số: `TAM BIET <<     ` / `BS: 51A-123.45`
- [x] **OLED 1.3":**
  - Khi xe vừa được duyệt vào và có `assignedSlot` (vd `"A2"`): vẽ thanh điều hướng hướng về khu của `assignedSlot` kèm nhãn `RE PHAI O A2 ->` hoặc `<- RE TRAI O B1`.
  - Hiển thị trạng thái chi tiết từng ô: `B1:%c B2:%c` và `A1:%c A2:%c` (`X` = có xe, `R` = đã đặt, `_` = trống).

#### Bước S2.4: Chuyển `ParkingNetworkClient` sang MQTT (`PubSubClient`)
- [x] Thay `HTTPClient` bằng `WiFiClient` + `PubSubClient` (`setBufferSize(1024)`).
- [x] Kết nối non-blocking (thử lại mỗi `5000ms`), đăng ký LWT `TOPIC_GATE_STATUS = "offline"`, khi kết nối thành công thì publish `"online"` (retained) và subscribe `TOPIC_GATE_COMMAND`, `TOPIC_RESERVED`.
- [x] Xử lý callback MQTT:
  - Topic `TOPIC_RESERVED` → parse JSON `{"A1":..., "A2":..., "B1":..., "B2":...}` cập nhật `parking.setReservedMask(...)`.
  - Topic `TOPIC_GATE_COMMAND` → parse JSON `{"seq":..., "action":..., "plate":..., "assignedSlot":...}` gọi `gate.handleBackendCommand(...)`.
- [x] Publish `TOPIC_GATE_REQUEST` ngay khi `gate.consumePendingAuthRequest(...)` trả về `true`.
- [x] Publish `TOPIC_STATE` (retained) ngay khi trạng thái slot/barie thay đổi hoặc định kỳ mỗi `30000ms`.
- [x] Hàng đợi lưu Offline Fallback Events và tự động xả đồng bộ bù khi MQTT kết nối lại.

#### Bước S2.5: Viết Firmware ESP32-CAM (`include/cam_config.h` & `src/cam/main_cam.cpp`)
- [x] Khai báo chân camera AI-Thinker OV2640 trong `include/cam_config.h`.
- [x] Trong `src/cam/main_cam.cpp`:
  - Khởi tạo `esp_camera_init` (SVGA `800x600`, JPEG quality `12`, `fb_count = 2`, tự động hạ cấp nếu không có PSRAM).
  - Kết nối Wi-Fi & MQTT (`esp32_cam_01`, LWT `TOPIC_CAM_STATUS = "offline"`), subscribe `TOPIC_GATE_REQUEST`.
  - Khi nhận `gate/request`: bật Flash LED (`GPIO 4`), xả 1 frame cũ, chụp 2 frame JPEG cách nhau `150ms`, tắt Flash LED, stream HTTP POST multipart/form-data trực tiếp lên `http://<EDGE_IP>:8000/recognize` kèm `X-API-Key`.

---

### PHASE S3 — XÂY DỰNG EDGE SERVER: MQTT BRIDGE & ĐỒNG BỘ FIRESTORE

- [ ] Tạo `edge-backend/requirements.txt` (`fastapi`, `uvicorn[standard]`, `python-multipart`, `paho-mqtt`, `firebase-admin`, `pydantic-settings`, `opencv-python-headless`, `ultralytics`, `easyocr`, `pytest`, `httpx`).
- [ ] Viết `edge-backend/app/config.py` và `edge-backend/app/state_cache.py` (quản lý thread-safe trạng thái 4 cảm biến, `active_reservations`, `pending_requests`).
- [ ] Viết `edge-backend/app/mqtt/bridge.py`: kết nối Mosquitto, nhận `state`, `gate/request`, `status`; cung cấp hàm `publish_gate_command()` và `publish_reserved_mask()`.
- [ ] Viết `edge-backend/app/cloud/firestore_bridge.py`:
  - Đồng bộ `sensorOccupied` và `deviceOnline` lên Firestore.
  - Mở `on_snapshot` lắng nghe `reservations` (`active`) và `deviceCommands` (`pending`) từ Repo 2 để đẩy xuống MQTT.

---

### PHASE S4 — PIPELINE AI NHẬN DIỆN BIỂN SỐ TRÊN GPU NVIDIA

- [ ] Viết `edge-backend/app/recognition/plate_utils.py` (chuẩn hóa, sửa ký tự dễ nhầm theo vị trí biển số VN, kiểm tra regex) + bộ test `edge-backend/tests/test_plate_utils.py` đọc `plate_test_cases.json`.
- [ ] Viết `edge-backend/app/recognition/detector.py` (YOLOv8s/11s CUDA inference + crop biển số padding 8%).
- [ ] Viết `edge-backend/app/recognition/ocr.py` (OpenCV CLAHE + Bilateral Filter + EasyOCR `gpu=True` + ghép biển 1/2 dòng + voting đa khung).
- [ ] Viết `edge-backend/scripts/capture_dataset.py` và `benchmark_pipeline.py`.

---

### PHASE S5 — FAST GATE ARBITER, API `/recognize` & UPLOAD STORAGE NỀN

- [ ] Viết `edge-backend/app/arbiter/gate_arbiter.py`:
  - Chọn slot tức thì (< 5ms) từ `state_cache`: ưu tiên khớp `reservation` → tiếp theo là ô trống `!sensorOccupied && !isReserved` theo thứ tự `A1 → A2 → B1 → B2`.
  - Hẹn giờ `5.0s` khi nhận `gate/request`: nếu quá 5s không nhận được ảnh từ CAM thì tự động mở cổng (nếu còn chỗ) với cờ `unrecognized`.
- [ ] Viết `edge-backend/app/api/recognize.py` (`POST /recognize`) và `edge-backend/app/cloud/storage_uploader.py` (upload ảnh nền sau khi đã bắn MQTT mở barie, ghi `gateEvents` lên Firestore).

---

### PHASE S6 — KIỂM THỬ TÍCH HỢP TOÀN TRẠM (`ST-01` → `ST-08`)

- [ ] Kiểm thử biên dịch cả 2 môi trường PlatformIO (`nodemcu-32s` và `esp32cam`).
- [ ] Chạy `pytest` cho toàn bộ logic `plate_utils` và `gate_arbiter` trên `edge-backend`.
- [ ] Thực hiện 8 kịch bản kiểm thử tích hợp `ST-01` → `ST-08` trong `docs/Plan_Repo1_IoT_And_Edge_Backend.md`.

