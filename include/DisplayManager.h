#ifndef DISPLAY_MANAGER_H
#define DISPLAY_MANAGER_H

#include <Arduino.h>
#include <LiquidCrystal_I2C.h>
#include <U8g2lib.h>
#include "config.h"
#include "ParkingMonitor.h"
#include "GateController.h"

class DisplayManager {
public:
    DisplayManager();

    // Khởi tạo LCD 1602 và OLED SH1106
    void begin();

    // Cập nhật giao diện toàn diện từ ParkingMonitor & GateController
    void update(const ParkingMonitor& parking, const GateController& gate);

    // Cập nhật giao diện (hỗ trợ tương thích ngược)
    void update(uint8_t totalAvail, uint8_t availA, uint8_t availB,
                bool barrierOpen, GateEvent lastEvent);

    // Cập nhật thủ công thông điệp LCD
    void showLcdMessage(const char* line1, const char* line2);

private:
    LiquidCrystal_I2C lcd;
    U8G2_SH1106_128X64_NONAME_F_HW_I2C oled;

    // Bộ nhớ đệm chống nhấp nháy LCD
    char currentLcdLine1[17];
    char currentLcdLine2[17];

    // Quản lý thông điệp sự kiện cổng tạm thời trên LCD
    unsigned long eventMsgTimestamp;
    bool showingEventMsg;
    GateEvent activeEvent;
    GateEvent lastHandledEvent;

    // Bộ nhớ đệm OLED để tối ưu tốc độ vẽ (chỉ vẽ lại khi có biến động)
    uint8_t cachedAvailA;
    uint8_t cachedAvailB;
    bool cachedSlotOcc[NUM_SLOTS];
    bool cachedSlotRes[NUM_SLOTS];
    bool cachedEnteringGuidance;
    char cachedAssignedSlot[8];
    unsigned long lastOledRefreshTime;

    // Các hàm nội bộ
    void updateLcd(const ParkingMonitor& parking, const GateController& gate);
    void updateOled(const ParkingMonitor& parking, const GateController& gate);
};

#endif // DISPLAY_MANAGER_H
