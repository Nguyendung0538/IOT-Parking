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

### PHASE S3 — XÂY DỰNG EDGE SERVER: MQTT BRIDGE & ĐỒNG BỘ FIRESTORE [x] (ĐÃ HOÀN THÀNH ✅)

- [x] Tạo `edge-backend/requirements.txt`, `requirements-dev.txt`, `.env.example`, `.env` (Python 3.12, `fastapi`, `uvicorn[standard]`, `paho-mqtt`, `firebase-admin`, `pydantic-settings`, `pytest`).
- [x] Viết `edge-backend/app/config.py` và `edge-backend/app/state_cache.py` (quản lý thread-safe với RLock trạng thái 4 cảm biến, `active_reservations`, `pending_requests`, tính mặt nạ `reservedMask`, tính toán `free_slots_public`).
- [x] Viết `edge-backend/app/mqtt/bridge.py`: kết nối Mosquitto bằng `paho-mqtt` CallbackAPIVersion.VERSION2, nhận `state`, `gate/request`, LWT `status`; cung cấp `publish_gate_command()` và `publish_reserved()` (retained).
- [x] Viết `edge-backend/app/cloud/base.py`, `null_bridge.py` (chế độ giả lập độc lập khi `FIREBASE_ENABLED=false`) và `firestore_bridge.py`:
  - Đồng bộ `sensorOccupied` (chỉ ghi diff) và `deviceOnline` lên Firestore.
  - Mở `on_snapshot` lắng nghe `reservations` (`active`) và `deviceCommands` (`pending`, 60s TTL) từ Repo 2 để đẩy xuống MQTT.
  - Đồng bộ `offline_fallback` events lên Firestore.
- [x] Viết REST API endpoints:
  - `edge-backend/app/api/health.py`: `GET /health` giám sát toàn diện MQTT, Cloud, thiết bị và bãi đỗ.
  - `edge-backend/app/api/debug.py`: `GET /debug/state`, `POST /debug/reservations`, `DELETE /debug/reservations/{id}`, `POST /debug/open-gate`, `POST /debug/simulate-state`.
  - `edge-backend/app/main.py`: Khởi tạo lifespan kết nối các bridge, hỗ trợ test mode.
- [x] Viết công cụ mô phỏng phần cứng và bộ test kiểm thử:
  - `edge-backend/scripts/simulate_gate.py`: CLI mô phỏng ESP32 Gate tương tác hai chiều qua MQTT.
  - Bộ kiểm thử `pytest` 22/22 tests PASSED (`test_models.py`, `test_state_cache.py`, `test_mqtt_bridge.py`, `test_api.py`). Đã kiểm tra thực tế live server Uvicorn.

---

### PHASE S4 — PIPELINE AI NHẬN DIỆN BIỂN SỐ TRÊN GPU NVIDIA [x] (ĐÃ HOÀN THÀNH ✅)

- [x] Cài đặt PyTorch CUDA 12.8 (`cu128`), `torchvision`, `ultralytics`, `easyocr`, `opencv-python-headless` vào `.venv`. Nhận diện đúng GPU **NVIDIA GeForce RTX 5060** (8GB VRAM).
- [x] Viết `edge-backend/app/recognition/plate_utils.py` (chuẩn hóa, sửa ký tự dễ nhầm theo vị trí biển số VN, kiểm tra regex, multi-frame voting) + bộ test `edge-backend/tests/test_plate_utils.py` với `plate_test_cases.json` (27/27 test cases pass 100%).
- [x] Viết `edge-backend/app/recognition/detector.py`: Tải sẵn trọng số YOLOv8 chuyên biển số VN (`models/plate_yolo_s.pt`) đạt độ trễ ~8ms trên RTX 5060 + cơ chế OpenCV Morphological/Contour Fallback.
- [x] Viết `edge-backend/app/recognition/ocr.py` (OpenCV CLAHE + Bilateral Filter + EasyOCR `gpu=True` + thuật toán phân cụm trục Y sắp xếp X ghép chuẩn cả biển 1 dòng và 2 dòng).
- [x] Viết `edge-backend/app/recognition/pipeline.py` (PlateRecognitionPipeline hợp nhất) + tích hợp GPU Warm-up trong lifespan FastAPI + endpoint `POST /debug/recognize-test`.
- [x] Viết `edge-backend/scripts/benchmark_pipeline.py` (đo trực tiếp trên `yolo_plate_dataset`: Latency trung bình **50.4 ms** (< 400ms), Detection Rate **95%**) và `capture_dataset.py`.
- [x] Bộ test `pytest`: **54/54 tests PASSED** (toàn bộ S3 và S4).

---

### PHASE S5 — FAST GATE ARBITER, API `/recognize` & UPLOAD STORAGE NỀN [x] (ĐÃ HOÀN THÀNH ✅)

- [x] Viết `edge-backend/app/arbiter/gate_arbiter.py`:
  - Phân xử mở cổng siêu tốc (< 5ms) từ `StateCache` trong RAM:
    - Khi xe vào (`IN`): Ưu tiên khớp `activeReservations` theo biển số. Nếu ô đã đặt bị đỗ đè vật lý (`sensorOccupied == True`) thì tự động chuyển sang ô trống dự phòng đầu tiên (`reserved_slot_substituted`); nếu hết sạch chỗ thì từ chối (`DENY` - `RESERVED_SLOT_TAKEN`). Nếu là khách vãng lai hoặc `unrecognized`, tìm ô đầu tiên thỏa mãn `!sensorOccupied && !isReserved`. Nếu hết chỗ trả `DENY` (`FULL`).
    - Khi xe ra (`OUT`): Luôn `OPEN` (gắn cờ `exit_manual_reconcile` nếu không rõ biển).
    - Định dạng biển số hiển thị tối đa 12 ký tự cho LCD 1602 của ESP32 (`plate_for_lcd`).
  - Hẹn giờ Fail-safe `5.0s` (`CAMERA_TIMEOUT_S = 5.0`): Khi `MqttBridge` nhận `gate/request`, khởi tạo timer 5.0s. Nếu ESP32-CAM bị treo hoặc quá 5s chưa gửi ảnh, tự động mở cổng ở chế độ `unrecognized` (`camera_timeout`) trước mốc fallback 8s của ESP32.
  - Chống trùng lặp `seq` (Idempotency): Duy trì LRU 128 `seq` gần nhất, nếu nhận trùng thì trả về quyết định cũ với `is_duplicate = True` và không phát lại lệnh MQTT.
- [x] Mở rộng `CloudBridgeBase`, `NullCloudBridge` và `FirestoreBridge`: Nhận đầy đủ schema chuẩn của Repo 2 cho `gateEvents` (`plateDisplay`, `confidence`, `assignedSlotId`, `matchedReservationId`, `denyReason`, `imageUrl`, `cropImageUrl`, `latencyMs`).
- [x] Viết `edge-backend/app/cloud/storage_uploader.py`: ThreadPoolExecutor nền lưu ảnh chụp (`full.jpg`, `crop.jpg`) vào `data/captures/{yyyy-mm-dd}/` (mount static `/captures`) và upload Firebase Storage khi kích hoạt, sau đó đẩy sự kiện vào `CloudBridge`.
- [x] Viết `edge-backend/app/api/recognize.py` (`POST /recognize`): Xác thực header `X-API-Key`, nhận `multipart/form-data` (1 hoặc 2 ảnh), chạy AI GPU RTX 5060, gọi `GateArbiter`, bắn ngay lệnh MQTT `gate/command`, đẩy background uploader và trả lời HTTP 200 JSON cho ESP32-CAM.
- [x] Viết `edge-backend/scripts/simulate_cam.py`: Giả lập ESP32-CAM kết nối MQTT, subscribe `gate/request`, tự động bốc ảnh từ dataset gửi `POST /recognize` lên Edge Server.
- [x] Bộ kiểm thử `pytest`: **71/71 tests PASSED** (thêm 10 test cases `test_gate_arbiter.py` S5-T1 → S5-T10 và 7 test cases `test_recognize_api.py`).

---

### PHASE S6 — KIỂM THỬ TÍCH HỢP TOÀN TRẠM (`ST-01` → `ST-08`)

- [ ] Kiểm thử biên dịch cả 2 môi trường PlatformIO (`nodemcu-32s` và `esp32cam`).
- [ ] Chạy `pytest` cho toàn bộ logic `plate_utils` và `gate_arbiter` trên `edge-backend`.
- [ ] Thực hiện 8 kịch bản kiểm thử tích hợp `ST-01` → `ST-08` trong `docs/Plan_Repo1_IoT_And_Edge_Backend.md`.

