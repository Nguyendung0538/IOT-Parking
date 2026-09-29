# KẾ HOẠCH PHÁT TRIỂN THEO GIAI ĐOẠN
**Dự án:** Sa hình Mô hình Bãi đỗ xe thông minh (Smart Parking Demo)  
**Nền tảng:** ESP32 (Arduino Core / C++)  
**Ngày tạo:** 2026-09-29  
**Tài liệu tham chiếu:** `Project_Overview.md`

---

> [!IMPORTANT]
> Tài liệu này là **context chính** cung cấp cho Agent code. Mỗi giai đoạn (Phase) mô tả đúng thứ tự ưu tiên triển khai, ràng buộc kỹ thuật, và interface rõ ràng giữa các module để Agent có thể sinh code từng phần mà không vi phạm kiến trúc tổng thể.

---

## NGUYÊN TẮC BẤT BIẾN (HARD RULES - KHÔNG ĐƯỢC VI PHẠM)

| # | Quy tắc |
|:--:|:---|
| 1 | **KHÔNG dùng `delay()`** — toàn bộ timing phải dùng `millis()` (non-blocking). |
| 2 | **LCD `0x27`, OLED `0x3C`** — hai thiết bị I2C chung bus GPIO 21/22, khởi tạo đúng địa chỉ. |
| 3 | **Module hóa** — 4 class tách biệt: `GateController`, `ParkingMonitor`, `DisplayManager`, `NetworkClient`. |
| 4 | **Debounce cảm biến** — áp dụng debounce ~200–300ms cho tất cả LM393 (4 slot + 2 cổng). |
| 5 | **Safety Timeout = 10s** — barie tự đóng nếu xe không đi qua sau 10 giây. |
| 6 | **GPIO 34 & 35** — chỉ là Input thuần (GPI only), không dùng INPUT_PULLUP. |

---

## PHASE 1 — KHUNG DỰ ÁN & PHÂN TÍCH PHẦN CỨNG

**Mục tiêu:** Tạo cấu trúc thư mục, định nghĩa hằng số toàn cục, và xác nhận kết nối phần cứng.

### 1.1. Cấu trúc thư mục dự án
```
smart_parking/
├── smart_parking.ino          # Entry point: setup() và loop()
├── config.h                   # Hằng số: GPIO, timeout, địa chỉ I2C, Wi-Fi
├── GateController.h/.cpp      # Module điều khiển cổng + barie
├── ParkingMonitor.h/.cpp      # Module giám sát 4 slot đỗ xe
├── DisplayManager.h/.cpp      # Module quản lý LCD + OLED
└── NetworkClient.h/.cpp       # Module Wi-Fi + HTTP/MQTT payload
```

### 1.2. File `config.h` — Hằng số cần định nghĩa
```cpp
// === GPIO PIN MAP ===
#define PIN_SERVO       18    // PWM — Servo SG90 barie
#define PIN_SENSOR_OUT  34    // GPI only — Cảm biến ngoài cổng (S_OUT)
#define PIN_SENSOR_IN   35    // GPI only — Cảm biến trong cổng (S_IN)
#define PIN_SLOT_A1     25    // Input Pullup — Slot A1
#define PIN_SLOT_A2     26    // Input Pullup — Slot A2
#define PIN_SLOT_B1     27    // Input Pullup — Slot B1
#define PIN_SLOT_B2     14    // Input Pullup — Slot B2

// === I2C ===
#define I2C_SDA         21
#define I2C_SCL         22
#define LCD_I2C_ADDR    0x27
#define OLED_I2C_ADDR   0x3C

// === SERVO ANGLES ===
#define SERVO_OPEN_ANGLE   90   // Độ mở barie
#define SERVO_CLOSE_ANGLE   0   // Độ đóng barie

// === TIMING ===
#define GATE_TIMEOUT_MS     10000   // 10 giây safety timeout
#define DEBOUNCE_MS           250   // Debounce cảm biến slot
#define IOT_PUBLISH_INTERVAL 5000   // Gửi telemetry mỗi 5 giây

// === PARKING ===
#define TOTAL_SLOTS     4
#define ZONE_A_SLOTS    2
#define ZONE_B_SLOTS    2

// === WIFI & BACKEND ===
#define WIFI_SSID       "YOUR_SSID"
#define WIFI_PASSWORD   "YOUR_PASSWORD"
#define BACKEND_URL     "http://your-backend/api/parking/state"
#define DEVICE_ID       "ESP32_PARKING_01"
```

### 1.3. Kiểm tra đầu ra Phase 1
- [ ] Biên dịch thành công với file `config.h` và 4 file `.h` rỗng.
- [ ] Serial Monitor hiển thị đúng GPIO map khi boot.

---

## PHASE 2 — MODULE `ParkingMonitor` (Giám sát ô đỗ xe)

**Mục tiêu:** Đọc và debounce trạng thái 4 cảm biến LM393 tại các slot A1, A2, B1, B2.

### 2.1. Interface class
```cpp
class ParkingMonitor {
public:
    void begin();
    void update();                    // Gọi trong loop() mỗi tick

    bool isOccupied(uint8_t slotIndex) const;  // 0=A1, 1=A2, 2=B1, 3=B2
    uint8_t getAvailableA() const;    // Số slot trống khu A (0–2)
    uint8_t getAvailableB() const;    // Số slot trống khu B (0–2)
    uint8_t getTotalAvailable() const;
    bool    isFull() const;
};
```

### 2.2. Logic cần triển khai
- **Debounce per-slot:** Mỗi slot có `lastStateTime[]` riêng, chỉ cập nhật `confirmedState[]` khi tín hiệu ổn định > `DEBOUNCE_MS`.
- **Active LOW:** LM393 kéo LOW khi có vật cản → `LOW = Occupied`, `HIGH = Empty`.
- **Tính toán:** `EmptyA = (A1 empty) + (A2 empty)`, tương tự cho B.

### 2.3. Kiểm tra đầu ra Phase 2
- [ ] Serial Monitor in đúng trạng thái slot khi che/bỏ cảm biến.
- [ ] Debounce hoạt động, không có giá trị rung lắc.
- [ ] `isFull()` trả về `true` chính xác khi cả 4 slot bị che.

---

## PHASE 3 — MODULE `GateController` (Điều khiển Cổng & Barie)

**Mục tiêu:** Triển khai state machine phát hiện chiều xe vào/ra, điều khiển Servo SG90.

### 3.1. Interface class
```cpp
class GateController {
public:
    void begin();
    void update(bool parkingFull);    // Gọi trong loop(), cần biết bãi có đầy không

    bool carJustEntered() const;      // Trả true 1 lần khi xe hoàn tất vào
    bool carJustExited()  const;      // Trả true 1 lần khi xe hoàn tất ra
    bool isBarrierOpen()  const;
    GateEvent getLastEvent() const;   // NONE, ENTERING, EXITING, FULL_BLOCKED
};
```

### 3.2. State Machine (enum GateState)
```
IDLE → ENTERING_WAIT_IN → ENTERING_DONE
     → EXITING_WAIT_OUT  → EXITING_DONE
     → FULL_BLOCKED (khi bãi đầy, S_OUT kích hoạt)
```

| State | Điều kiện chuyển | Hành động |
|:---|:---|:---|
| `IDLE` | `S_OUT` LOW & bãi còn chỗ | Mở barie, set `ENTERING_WAIT_IN`, lưu `barrierOpenTime` |
| `IDLE` | `S_OUT` LOW & bãi đầy | Không mở barie, LCD báo `FULL` |
| `IDLE` | `S_IN` LOW | Mở barie, set `EXITING_WAIT_OUT` |
| `ENTERING_WAIT_IN` | `S_OUT` & `S_IN` đều HIGH | Đóng barie, `carEntered = true`, về `IDLE` |
| `ENTERING_WAIT_IN` | Timeout 10s | Đóng barie, về `IDLE` (safety) |
| `EXITING_WAIT_OUT` | `S_OUT` & `S_IN` đều HIGH | Đóng barie, `carExited = true`, về `IDLE` |
| `EXITING_WAIT_OUT` | Timeout 10s | Đóng barie, về `IDLE` (safety) |

### 3.3. Kiểm tra đầu ra Phase 3
- [ ] Servo mở/đóng đúng góc khi mô phỏng thủ công.
- [ ] `carJustEntered()` chỉ bắn 1 lần / lượt xe vào.
- [ ] Safety timeout hoạt động sau 10s.
- [ ] Barie không mở khi `isFull() == true`.

---

## PHASE 4 — MODULE `DisplayManager` (Hiển thị LCD & OLED)

**Mục tiêu:** Render dữ liệu thời gian thực lên LCD 1602 (I2C) và OLED 1.3 inch (U8g2).

### 4.1. Interface class
```cpp
class DisplayManager {
public:
    void begin();
    void update(uint8_t totalAvail, uint8_t availA, uint8_t availB,
                bool barrierOpen, GateEvent lastEvent);
    // GateEvent: NONE, ENTERING, EXITING, FULL_BLOCKED
};
```

### 4.2. Đặc tả LCD 1602 (16×2 ký tự)

| Trạng thái | Dòng 1 | Dòng 2 |
|:---|:---|:---|
| Chờ bình thường | `SMART PARKING  ` | `Trong: 3/4 [OK]` |
| Bãi đầy | `SMART PARKING  ` | `BAI DA DAY!    ` |
| Xe đang vào | `SMART PARKING  ` | `MOI VAO...     ` |
| Xe đang ra | `SMART PARKING  ` | `TAM BIET!      ` |

> **Lưu ý:** Dùng thư viện `LiquidCrystal_I2C`. Gọi `lcd.clear()` chỉ khi nội dung thực sự thay đổi để tránh nhấp nháy — so sánh chuỗi trước khi ghi.

### 4.3. Đặc tả OLED 1.3 inch — U8g2 (128×64 px)

| Vùng | Pixel | Nội dung |
|:---|:---|:---|
| Trên | 128×36 | Mũi tên ← (khu A), → (khu B), hoặc ký hiệu **STOP/X** |
| Dưới | 128×28 | `KhuA: [1/2]  KhuB: [2/2]` |

**Logic mũi tên:**
```
if (availA > 0)              → Vẽ mũi tên TRÁI  (ưu tiên khu A)
else if (availB > 0)         → Vẽ mũi tên PHẢI
else (total == 0)            → Vẽ STOP / [FULL]
```

> **Driver OLED:** Ưu tiên `U8G2_SH1106_128X64_NONAME_F_HW_I2C` nếu màn hình là SH1106. Fallback sang `U8G2_SSD1306_128X64_NONAME_F_HW_I2C` nếu là SSD1306. Kiểm tra nhãn chip trên mặt sau màn hình.

### 4.4. Kiểm tra đầu ra Phase 4
- [ ] LCD hiển thị đúng nội dung theo từng trạng thái.
- [ ] OLED hiển thị mũi tên đúng chiều theo logic điều hướng.
- [ ] Không có xung đột I2C — hai màn hình hoạt động đồng thời.
- [ ] Không nhấp nháy màn hình (tránh `clear()` không cần thiết).

---

## PHASE 5 — TÍCH HỢP TOÀN BỘ (Integration Loop)

**Mục tiêu:** Kết nối tất cả module trong `setup()` và `loop()`, đảm bảo luồng dữ liệu nhất quán.

### 5.1. Cấu trúc `smart_parking.ino`
```cpp
#include "config.h"
#include "GateController.h"
#include "ParkingMonitor.h"
#include "DisplayManager.h"
#include "NetworkClient.h"

GateController  gate;
ParkingMonitor  parking;
DisplayManager  display;
NetworkClient   network;

void setup() {
    Serial.begin(115200);
    parking.begin();
    gate.begin();
    display.begin();
    network.begin();
}

void loop() {
    parking.update();
    gate.update(parking.isFull());
    display.update(
        parking.getTotalAvailable(),
        parking.getAvailableA(),
        parking.getAvailableB(),
        gate.isBarrierOpen(),
        gate.getLastEvent()   // NONE / ENTERING / EXITING / FULL_BLOCKED
    );
    network.update(parking, gate);   // Publish nếu đến interval
}
```

### 5.2. Luồng dữ liệu giữa các module
```
ParkingMonitor ──(slot states)──► GateController (isFull?)
ParkingMonitor ──(availA/B)──────► DisplayManager (mũi tên OLED)
GateController ──(event, open)───► DisplayManager (LCD message)
ParkingMonitor ┐
GateController ┘ ────────────────► NetworkClient (JSON payload)
```

### 5.3. Kiểm tra đầu ra Phase 5
- [ ] Kịch bản xe vào: cảm biến → barie mở → LCD "MOI VAO" → xe qua → barie đóng → slot count giảm → OLED cập nhật.
- [ ] Kịch bản xe ra: cảm biến trong → barie mở → xe qua → barie đóng → slot count tăng.
- [ ] Kịch bản bãi đầy: barie không mở khi S_OUT kích hoạt, LCD báo "BAI DA DAY!".
- [ ] Safety timeout: barie tự đóng sau 10s.
- [ ] Toàn bộ chạy non-blocking, Serial Monitor in đều đặn.

---

## PHASE 6 — MODULE `NetworkClient` (IoT Connectivity)

**Mục tiêu:** Kết nối Wi-Fi và gửi JSON payload định kỳ lên Backend.

### 6.1. Interface class
```cpp
class NetworkClient {
public:
    void begin();
    void update(const ParkingMonitor& parking, const GateController& gate);
    bool isConnected() const;
};
```

### 6.2. JSON Payload (HTTP POST)
```json
{
  "device_id": "ESP32_PARKING_01",
  "timestamp": 1727421316,
  "gate": {
    "barrier_open": false,
    "last_action": "ENTER"
  },
  "slots": {
    "A1": true,
    "A2": false,
    "B1": false,
    "B2": false
  },
  "summary": {
    "total_slots": 4,
    "total_occupied": 1,
    "total_available": 3,
    "zone_A_available": 1,
    "zone_B_available": 2
  }
}
```
*(Quy ước: `true` = occupied / có xe, `false` = available / trống)*

### 6.3. Chiến lược kết nối
- Dùng `WiFi.begin()` trong `setup()`, retry tự động.
- Dùng `HTTPClient` (Arduino ESP32) để POST JSON mỗi `IOT_PUBLISH_INTERVAL` ms.
- **Reconnect logic non-blocking:** Nếu `WiFi.status() != WL_CONNECTED`, thực hiện reconnect mà không block `loop()`.
- **Thư viện:** `WiFi.h`, `HTTPClient.h`, `ArduinoJson.h` (v6+).

### 6.4. Kiểm tra đầu ra Phase 6
- [ ] Serial Monitor xác nhận kết nối Wi-Fi thành công.
- [ ] Backend nhận đúng JSON payload mỗi 5 giây.
- [ ] Hệ thống không bị đứng khi mất Wi-Fi (non-blocking reconnect).
- [ ] `slots` trong JSON khớp hoàn toàn với trạng thái thực tế.

---

## PHASE 7 — KIỂM THỬ HỆ THỐNG & TỐI ƯU

**Mục tiêu:** Kiểm tra toàn bộ kịch bản thực tế, phát hiện edge case và tối ưu hiệu năng.

### 7.1. Bộ test cases bắt buộc

| TC | Kịch bản | Kết quả mong đợi |
|:--:|:---|:---|
| TC-01 | Xe vào khi còn chỗ | Barie mở, LCD "MOI VAO", slot đúng khu giảm, OLED cập nhật mũi tên |
| TC-02 | Xe ra khi bãi không đầy | Barie mở, LCD "TAM BIET", slot tăng, OLED cập nhật |
| TC-03 | Xe vào khi bãi đầy | Barie không mở, LCD "BAI DA DAY", OLED hiện STOP |
| TC-04 | Xe dừng giữa chừng (timeout) | Sau 10s barie tự đóng, về trạng thái IDLE |
| TC-05 | Mất Wi-Fi giữa chừng | Hệ thống không treo, tự reconnect khi có lại Wi-Fi |
| TC-06 | Hai xe vào liên tiếp nhanh | State machine xử lý đúng, không nhảy trạng thái |
| TC-07 | Bãi vừa hết chỗ cuối cùng | OLED chuyển sang STOP ngay lập tức |
| TC-08 | Slot trống luân phiên A, B | OLED mũi tên đổi chiều đúng theo ưu tiên khu A |

### 7.2. Tối ưu hiệu năng
- Đo thời gian mỗi vòng `loop()` qua `Serial`, đảm bảo < 5ms / tick.
- Giảm tần suất gọi `u8g2.sendBuffer()` — chỉ khi dữ liệu OLED thực sự thay đổi.
- Cân nhắc dùng **FreeRTOS Task** tách `NetworkClient` nếu HTTP POST gây jitter cho vòng lặp chính.

---

## TỔNG HỢP GIAI ĐOẠN

```
Phase 1 (Config) → Phase 2 (ParkingMonitor) → Phase 3 (GateController)
                                                        ↓
Phase 4 (DisplayManager) ──────────────────→ Phase 5 (Integration)
                                                        ↓
Phase 6 (NetworkClient) ───────────────────→ Phase 7 (Test & Optimize)
```

| Phase | Module / Mục tiêu | Dependency |
|:---:|:---|:---|
| 1 | Khung dự án + `config.h` | — |
| 2 | `ParkingMonitor` | Phase 1 |
| 3 | `GateController` | Phase 1, 2 |
| 4 | `DisplayManager` | Phase 1 |
| 5 | Tích hợp `loop()` | Phase 2, 3, 4 |
| 6 | `NetworkClient` | Phase 1, 2, 3 |
| 7 | Kiểm thử & tối ưu | Phase 5, 6 |

---

> **Hướng dẫn cho Agent code:** Triển khai tuần tự từng Phase. Sau mỗi Phase, chạy bộ kiểm tra đầu ra trước khi chuyển sang Phase tiếp theo. Tham chiếu `Project_Overview.md` để tra cứu thông số phần cứng chi tiết khi cần.
