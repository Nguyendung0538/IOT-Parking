#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>
#include "common_network.h"

// ==========================================
// THÔNG TIN THIẾT BỊ
// ==========================================
#define DEVICE_ID DEVICE_ID_GATE
#define SERIAL_BAUD 115200

// ==========================================
// CẤU HÌNH NGOẠI VI & GPIO PINMAP
// ==========================================

// Bus I2C dùng chung cho LCD 1602 và OLED 1.3"
#define PIN_I2C_SDA 21
#define PIN_I2C_SCL 22

// Địa chỉ phần cứng I2C
#define I2C_ADDR_LCD  0x27
#define I2C_ADDR_OLED 0x3C

// Lựa chọn chip điều khiển OLED 1.3" (Mặc định SH1106; bỏ comment SSD1306 nếu màn dùng SSD1306)
#define OLED_USE_SH1106
// #define OLED_USE_SSD1306

// Barie 2 Cổng Độc Lập (2x Micro Servo SG90)
#define PIN_SERVO_GATE_IN  18   // Servo Barie Cổng VÀO
#define PIN_SERVO_GATE_OUT 19   // Servo Barie Cổng RA
#define PIN_SERVO_GATE     PIN_SERVO_GATE_IN // Alias tương thích ngược
#define SERVO_ANGLE_CLOSED 0    // Độ khi barie hạ
#define SERVO_ANGLE_OPEN   90   // Độ khi barie nâng

// Cảm biến hồng ngoại Cổng VÀO (LM393, Active LOW)
// Lưu ý: GPIO 34, 35 chỉ hỗ trợ Input thuần (không có pullup nội)
#define PIN_SENSOR_ENTRY_OUT 34 // Cảm biến ngoài bãi tại Cổng VÀO
#define PIN_SENSOR_ENTRY_IN  35 // Cảm biến trong bãi tại Cổng VÀO
#define PIN_SENSOR_GATE_OUT  PIN_SENSOR_ENTRY_OUT
#define PIN_SENSOR_GATE_IN   PIN_SENSOR_ENTRY_IN

// Cảm biến hồng ngoại Cổng RA (LM393, Active LOW)
#define PIN_SENSOR_EXIT_IN   32 // Cảm biến trong bãi tại Cổng RA
#define PIN_SENSOR_EXIT_OUT  33 // Cảm biến ngoài bãi tại Cổng RA

// Cảm biến hồng ngoại 4 ô đỗ xe (LM393, Active LOW)
#define NUM_SLOTS      4
#define ZONE_A_SLOTS   2
#define ZONE_B_SLOTS   2

#define PIN_SLOT_A1 25
#define PIN_SLOT_A2 26
#define PIN_SLOT_B1 27
#define PIN_SLOT_B2 14

// Mức logic tích cực (LM393 xuất LOW khi phát hiện vật cản)
#define SENSOR_ACTIVE_LEVEL LOW

// ==========================================
// CÁC HẰNG SỐ THỜI GIAN & AN TOÀN (NON-BLOCKING)
// ==========================================
#define SENSOR_DEBOUNCE_MS     250   // Chống rung tín hiệu cảm biến slot (ms)
#define GATE_SAFETY_TIMEOUT_MS 10000 // Tự động hạ barie nếu xe không qua sau 10s
#define GATE_SERVO_DELAY_MS    300   // Thời gian servo hoàn thành chuyển động trước khi chốt trạng thái
#define GATE_AUTH_TIMEOUT_MS   8000  // Fallback mở cổng cục bộ nếu Backend không phản hồi sau 8s
#define DISPLAY_REFRESH_MS     200   // Chu kỳ làm tươi màn hình (ms)
#define LCD_MSG_DURATION_MS    2500  // Thời gian hiển thị lời chào "MOI VAO" / "TAM BIET" (ms)

// ==========================================
// CẤU HÌNH KẾT NỐI IOT (WIFI & BACKEND)
// ==========================================
// Lựa chọn phương thức truyền tải:
// 1 = HTTP POST REST API, 2 = MQTT
#define PROTOCOL_HTTP 1
#define PROTOCOL_MQTT 2
#define IOT_PROTOCOL  PROTOCOL_MQTT

// Cấu hình HTTP Backend cũ (dự phòng)
#define BACKEND_HTTP_URL "http://192.168.1.100:8080/api/parking/telemetry"
#define HTTP_TIMEOUT_MS  3000

// Cấu hình MQTT Broker (kế thừa từ common_network.h)
#define MQTT_BROKER    MQTT_BROKER_HOST
#define MQTT_PORT      MQTT_BROKER_PORT
#define MQTT_TOPIC     TOPIC_STATE
#define MQTT_CLIENT_ID DEVICE_ID_GATE

// Chu kỳ tự động gửi dữ liệu Telemetry lên Backend (ms)
#define TELEMETRY_INTERVAL_MS 5000

#endif // CONFIG_H
