# TÀI LIỆU YÊU CẦU DỰ ÁN & THIẾT KẾ FIRMWARE (ESP32 IoT)
**Tên dự án:** Sa hình Mô hình Bãi đỗ xe thông minh (Smart Parking Demo)  
**Phạm vi tài liệu:** Thiết kế hệ thống nhúng, phân bổ chân GPIO, luồng logic điều khiển thiết bị ngoại vi và cấu trúc dữ liệu truyền thông IoT (ESP32 Firmware Context).

---

## 1. TỔNG QUAN DỰ ÁN & MỤC TIÊU
- Xây dựng mô hình sa hình bãi đỗ xe tự động thu nhỏ gồm **1 cổng ra/vào chung** (dùng chung 1 cần gạt Servo barie) và **4 vị trí đỗ xe** chia thành 2 khu vực: **Khu A (Slot A1, A2)** và **Khu B (Slot B1, B2)**.
- **Tự động hóa luồng xe:** Tự động phát hiện xe vào/ra bằng cảm biến tuần tự, nâng/hạ barie an toàn, cảnh báo khi bãi đầy.
- **Chỉ dẫn tại chỗ (Local UI/UX):**
  - **Màn hình LCD 1602 I2C** (tại cổng): Hiển thị trạng thái cổng, tổng số chỗ trống, thông báo chào mừng xe vào / cảm ơn xe ra.
  - **Màn hình OLED 1.3 inch I2C** (ngã rẽ sa hình): Hiển thị mũi tên điều hướng trực quan (trái/phải) tới khu vực còn trống và đếm số chỗ khả dụng của từng khu.
- **Kết nối IoT:** ESP32 kết nối Wi-Fi, sẵn sàng gửi telemetry (trạng thái slot, barie, số lượng xe) lên Backend qua HTTP REST API / MQTT phục vụ ứng dụng Android.

---

## 2. DANH SÁCH LINH KIỆN & PHẦN CỨNG

| STT | Thiết bị / Linh kiện | Số lượng | Giao thức / Kiểu tín hiệu | Chức năng chính |
|:---:|:---|:---:|:---|:---|
| 1 | **NodeMCU ESP32 (CH340)** | 1 | Vi điều khiển chính | Xử lý logic, đọc cảm biến, điều khiển ngoại vi, kết nối Wi-Fi |
| 2 | **Micro Servo SG90 (9g)** | 1 | PWM | Cần gạt Barie kiểm soát cổng ra/vào |
| 3 | **Cảm biến hồng ngoại LM393** | 2 | Digital Input (Active LOW) | Đặt trước & sau barie để nhận diện chiều xe di chuyển (Vào/Ra) |
| 4 | **Cảm biến hồng ngoại LM393** | 4 | Digital Input (Active LOW) | Giám sát tình trạng đỗ xe tại 4 slot: A1, A2, B1, B2 |
| 5 | **LCD 1602 + Module I2C** | 1 | I2C (Mặc định `0x27`) | Hiển thị thông số tổng quan tại cổng ra/vào |
| 6 | **OLED 1.3 inch (SH1106 / SSD1306)** | 1 | I2C (Mặc định `0x3C`) | Hiển thị mũi tên chỉ dẫn rẽ & số lượng slot trống khu A, B |
| 7 | **Nguồn cấp ngoài (5V/2A - 3A)** | 1 | DC Power | Cấp nguồn ổn định cho SG90 và hệ thống cảm biến (tránh sụt áp ESP32) |

---

## 3. SƠ ĐỒ KẾT NỐI CHÂN GPIO (ESP32 PINMAP)

> **Lưu ý thiết kế phần cứng:**  
> - **Chung bus I2C:** LCD 1602 và OLED 1.3 inch dùng chung cặp chân `SDA (GPIO 21)` và `SCL (GPIO 22)`. Hai thiết bị có địa chỉ phần cứng khác nhau (`0x27` và `0x3C`).  
> - **Nguồn điện:** Servo SG90 và các cảm biến LM393 bắt buộc cấp nguồn `5V` (nguồn ngoài chung mass GND với ESP32). Chân Data LM393 trả tín hiệu mức logic Digital tương thích tốt với chân Input của ESP32.

| Nhóm chức năng | Tên cảm biến / Module | Chân ESP32 | Chế độ cấu hình Pin | Ghi chú |
|:---|:---|:---:|:---:|:---|
| **I2C Bus chung** | LCD 1602 & OLED 1.3 | **GPIO 21** | I2C SDA | LCD: `0x27`, OLED: `0x3C` |
| | | **GPIO 22** | I2C SCL | Kéo chung bus dữ liệu |
| **Barie Gate** | Servo SG90 Signal | **GPIO 18** | Output (PWM) | Góc mở 90°, góc đóng 0° |
| **Cổng vào/ra** | Cảm biến cổng ngoài ($S_{OUT}$) | **GPIO 34** | Input (GPI only) | Đặt phía ngoài cổng |
| | Cảm biến cổng trong ($S_{IN}$) | **GPIO 35** | Input (GPI only) | Đặt phía trong bãi xe |
| **Khu A (Zone A)**| Slot A1 | **GPIO 25** | Input / Pullup | Báo xe đỗ slot A1 |
| | Slot A2 | **GPIO 26** | Input / Pullup | Báo xe đỗ slot A2 |
| **Khu B (Zone B)**| Slot B1 | **GPIO 27** | Input / Pullup | Báo xe đỗ slot B1 |
| | Slot B2 | **GPIO 14** | Input / Pullup | Báo xe đỗ slot B2 |

*(Lưu ý: GPIO 34, 35 chỉ hỗ trợ Input thuần, không có trở treo nội pullup, phù hợp với module LM393 đã có sẵn mạch so sánh và biến trở).*

---

## 4. LUỒNG XỬ LÝ LOGIC (CORE LOGIC FLOW)

### 4.1. Thuật toán nhận diện chiều xe tại Cổng (State Machine)
Làn xe dùng chung 2 cảm biến LM393 đặt nối tiếp nhau:  
- $S_{OUT}$ (cảm biến phía ngoài bãi)  
- $S_{IN}$ (cảm biến phía trong bãi)

```
        Phía Ngoài                 Barie                 Phía Trong
      [ Cảm biến S_OUT ] ------> [ SERVO ] ------> [ Cảm biến S_IN ]
```

- **Quy trình Xe Vào (Car Entering):**
  1. Trạng thái chờ: Barie đóng (0°), cả $S_{OUT}$ và $S_{IN}$ ở mức HIGH (không có vật cản).
  2. $S_{OUT}$ kích hoạt trước (LOW) $\rightarrow$ Kiểm tra nếu còn chỗ trống:
     - Còn chỗ: Mở Barie (90°), hiển thị LCD: `"Vui long vao!"`. Bật cờ `ENTERING = true`.
     - Hết chỗ (Full): Giữ nguyên barie, hiển thị LCD: `"Bai da day!"`.
  3. Xe đi qua barie và kích hoạt $S_{IN}$ (LOW).
  4. Xe vượt qua hẳn $S_{IN}$ (cả $S_{OUT}$ và $S_{IN}$ đều nhả về HIGH) $\rightarrow$ Đóng barie (0°), hoàn tất quá trình vào.

- **Quy trình Xe Ra (Car Exiting):**
  1. $S_{IN}$ kích hoạt trước (LOW) $\rightarrow$ Mở Barie (90°), hiển thị LCD: `"Hen gap lai!"`. Bật cờ `EXITING = true`.
  2. Xe đi qua barie và kích hoạt $S_{OUT}$ (LOW).
  3. Xe vượt qua hẳn $S_{OUT}$ (cả hai cảm biến nhả về HIGH) $\rightarrow$ Đóng barie (0°), hoàn tất quá trình ra.

- **Cơ chế an toàn (Safety Timeout):** Nếu barie mở nhưng sau khoảng thời gian timeout $T_{timeout} = 10\text{s}$ xe không đi qua hoặc lùi lại, tự động hạ barie và reset trạng thái.

---

### 4.2. Giám sát ô đỗ & Giải thuật điều hướng (Routing Navigation)
- **Chu kỳ quét:** Đọc trạng thái 4 cảm biến LM393 tại các slot (A1, A2, B1, B2) có áp dụng chống nhiễu (Debounce ~200-300ms).
- **Tính toán chỗ trống:**
  - $Empty_A = \text{slot A1 trống} + \text{slot A2 trống}$
  - $Empty_B = \text{slot B1 trống} + \text{slot B2 trống}$
  - $Total_{Empty} = Empty_A + Empty_B$
- **Quy tắc điều hướng mũi tên trên màn hình OLED 1.3 inch:**
  - **Ưu tiên Khu A trước:** Nếu $Empty_A > 0 \rightarrow$ Hiển thị **Mũi tên rẽ TRÁI** (Hướng về Khu A).
  - **Nếu Khu A đầy, còn Khu B:** Nếu $Empty_A == 0$ và $Empty_B > 0 \rightarrow$ Hiển thị **Mũi tên rẽ PHẢI** (Hướng về Khu B).
  - **Bãi đầy hoàn toàn:** Nếu $Total_{Empty} == 0 \rightarrow$ Hiển thị **Biểu tượng STOP / [X]** và chữ `"FULL"`.

---

## 5. ĐẶC TẢ GIAO DIỆN HIỂN THỊ (DISPLAY UI/UX)

### 5.1. Màn hình LCD 1602 (Tại cổng)
*Bố cục 2 dòng $\times$ 16 ký tự:*
```
Dòng 1: SMART PARKING
Dòng 2: Trong: 3/4 [OK]  (hoặc "BAI DA DAY!" nếu 0/4)
```
*Khi có xe qua cổng:*
- Xe vào: `Dòng 2: MOI VAO...`
- Xe ra: `Dòng 2: TAM BIET!`

### 5.2. Màn hình OLED 1.3 inch (Tại ngã rẽ sa hình)
*Độ phân giải 128x64 pixel (SH1106 / SSD1306):*
- **Vùng trên (128x36):** Đồ họa mũi tên chỉ hướng (Mũi tên Trái $\leftarrow$, Mũi tên Phải $\rightarrow$, hoặc Biểu tượng STOP).
- **Vùng dưới (128x28):** Thông số chi tiết 2 khu vực:
  ```
  Khu A: [ 1 / 2 ] | Khu B: [ 2 / 2 ]
  ```

---

## 6. MÔ HÌNH DỮ LIỆU ĐỒNG BỘ IOT (PAYLOAD SPECIFICATION)

ESP32 định kỳ đóng gói dữ liệu dạng **JSON Payload** (gửi qua HTTP POST webhook hoặc MQTT topic `parking/state`):

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
*(Ghi chú: `true` = đã có xe đỗ / occupied, `false` = đang trống / available).*

---

## 7. YÊU CẦU KIẾN TRÚC CODE FIRMWARE CHO AGENT
Khi Agent lập trình sinh mã nguồn (C++/Arduino Core cho ESP32), cần tuân thủ các nguyên tắc sau:
1. **Non-blocking Code:** Tuyệt đối không dùng hàm `delay()` trong vòng lặp chính `loop()`. Toàn bộ tác vụ quét cảm biến, điều khiển Servo, cập nhật màn hình OLED/LCD và kết nối mạng phải quản lý theo `millis()`.
2. **Quản lý Bus I2C:** Tránh xung đột xung nhịp giữa LCD 1602 và OLED 1.3 inch; khởi tạo đúng địa chỉ bus `0x27` và `0x3C`.
3. **Module hóa source code:** Tách file hoặc class rõ ràng:
   - `GateController`: Xử lý Servo & cặp cảm biến vào/ra tuần tự.
   - `ParkingMonitor`: Đọc trạng thái và debounce 4 slot đỗ xe.
   - `DisplayManager`: Render dữ liệu lên LCD & OLED (dùng thư viện nhẹ như `U8g2` cho OLED).
   - `NetworkClient`: Quản lý kết nối Wi-Fi & publish JSON payload lên Backend.