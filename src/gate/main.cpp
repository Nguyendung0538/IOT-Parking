#include <Arduino.h>
#include <Wire.h>
#include "config.h"
#include "ParkingMonitor.h"
#include "GateController.h"
#include "DisplayManager.h"
#include "ParkingNetworkClient.h"

// Khởi tạo các đối tượng điều khiển hệ thống
ParkingMonitor parking;
GateController gate;
DisplayManager display;
ParkingNetworkClient network;

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1000); // Đợi Serial ổn định để không mất log đầu tiên

    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F("    BAI DO XE THONG MINH - ESP32 Firmware v1.0   "));
    Serial.println(F("=================================================="));
    Serial.flush();

    // Khởi tạo I2C bus trước tất cả các module để tránh lock bus
    Serial.print(F("[Init] Khoi tao I2C bus (SDA=21, SCL=22)..."));
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Serial.println(F(" OK"));
    Serial.flush();

    // Khởi động ParkingMonitor (GPIO input, không có I2C)
    Serial.print(F("[Init] ParkingMonitor..."));
    parking.begin();
    Serial.println(F(" OK"));
    Serial.flush();

    // Khởi động GateController (Servo + GPIO input)
    Serial.print(F("[Init] GateController..."));
    gate.begin();
    Serial.println(F(" OK"));
    Serial.flush();

    // Khởi động DisplayManager (LCD + OLED qua I2C)
    Serial.print(F("[Init] DisplayManager..."));
    display.begin();
    Serial.println(F(" OK"));
    Serial.flush();

    // Khởi động ParkingNetworkClient (MQTT hai chiều)
    Serial.print(F("[Init] ParkingNetworkClient..."));
    network.begin(&parking, &gate);
    Serial.println(F(" OK"));
    Serial.flush();

    Serial.println(F("[System] Khoi dong hoan tat!"));
    parking.printStatus();
}

void loop() {
    // 1. Quét cảm biến 4 slot đỗ xe (debounce 250ms non-blocking)
    bool slotStateChanged = parking.update();

    if (slotStateChanged) {
        Serial.println(F("\n[EVENT] Thay doi trang thai o do xe!"));
        parking.printStatus();
    }

    // 2. Cập nhật máy trạng thái Barie & Cảm biến cổng vào/ra (FSM + Fallback 8s)
    gate.update(parking.isFull());

    if (gate.carJustEntered()) {
        Serial.println(F("\n[GATE NOTIFY] >> 1 XE VAO BAI THANH CONG! <<"));
    }

    if (gate.carJustExited()) {
        Serial.println(F("\n[GATE NOTIFY] >> 1 XE ROI BAI THANH CONG! <<"));
    }

    // 3. Cập nhật giao diện màn hình LCD & OLED (Hiển thị nhận diện, điều hướng ô đỗ)
    display.update(parking, gate);

    // 4. Đồng bộ Telemetry, nhận diện biển số & xử lý lệnh MQTT
    network.update(parking, gate);
}
