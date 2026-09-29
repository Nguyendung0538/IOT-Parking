#ifndef DISPLAY_MANAGER_H
#define DISPLAY_MANAGER_H

#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <U8g2lib.h>
#include "config.h"
#include "GateController.h"

class DisplayManager {
public:
    DisplayManager();

    // Khởi tạo cả LCD 1602 và OLED SH1106 trên bus I2C chung
    void begin();

    // Cập nhật giao diện màn hình non-blocking
    void update(uint8_t totalAvail, uint8_t availA, uint8_t availB,
                bool barrierOpen, GateEvent lastEvent);

    // Cập nhật thủ công LCD
    void showLcdMessage(const char* line1, const char* line2);

private:
    LiquidCrystal_I2C lcd;
    U8G2_SH1106_128X64_NONAME_F_HW_I2C oled;

    unsigned long lastRefreshTime;
};

#endif // DISPLAY_MANAGER_H
