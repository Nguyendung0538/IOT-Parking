#include "DisplayManager.h"

DisplayManager::DisplayManager()
    : lcd(I2C_ADDR_LCD, 16, 2),
      oled(U8G2_R0, /* reset=*/ U8X8_PIN_NONE),
      lastRefreshTime(0) {
}

void DisplayManager::begin() {
    // Wire.begin() được gọi tập trung trong main.cpp::setup() để tránh conflict

    // Khởi tạo LCD 1602
    lcd.init();
    lcd.backlight();
    lcd.setCursor(0, 0);
    lcd.print("SMART PARKING");
    lcd.setCursor(0, 1);
    lcd.print("System Starting");

    // Khởi tạo OLED 1.3" (SH1106)
    oled.begin();
    oled.clearBuffer();
    oled.setFont(u8g2_font_ncenB08_tr);
    oled.drawStr(10, 25, "SMART PARKING");
    oled.drawStr(25, 45, "INITIALIZING");
    oled.sendBuffer();

    Serial.println(F("[DisplayManager] LCD 1602 (0x27) + OLED SH1106 (0x3C) ready."));
}

void DisplayManager::update(uint8_t totalAvail, uint8_t availA, uint8_t availB,
                            bool barrierOpen, GateEvent lastEvent) {
    // Tránh warning unused parameters trong Phase stub
    (void)totalAvail;
    (void)availA;
    (void)availB;
    (void)barrierOpen;
    (void)lastEvent;
}

void DisplayManager::showLcdMessage(const char* line1, const char* line2) {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print(line1);
    lcd.setCursor(0, 1);
    lcd.print(line2);
}
