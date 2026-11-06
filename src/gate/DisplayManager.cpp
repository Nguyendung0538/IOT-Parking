#include "DisplayManager.h"

DisplayManager::DisplayManager()
    : lcd(I2C_ADDR_LCD, 16, 2),
      oled(U8G2_R0, /* reset=*/ U8X8_PIN_NONE),
      eventMsgTimestamp(0),
      showingEventMsg(false),
      activeEvent(GATE_EVENT_NONE),
      lastHandledEvent(GATE_EVENT_NONE),
      cachedAvailA(255),
      cachedAvailB(255),
      cachedEnteringGuidance(false),
      lastOledRefreshTime(0) {
    currentLcdLine1[0] = '\0';
    currentLcdLine2[0] = '\0';
    cachedAssignedSlot[0] = '\0';
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        cachedSlotOcc[i] = false;
        cachedSlotRes[i] = false;
    }
}

void DisplayManager::begin() {
    // Khởi tạo LCD 1602 (I2C 0x27)
    lcd.init();
    lcd.backlight();
    showLcdMessage(" SMART PARKING  ", " Khoi dong...   ");

    // Khởi tạo OLED 1.3" SH1106 (I2C 0x3C)
    oled.begin();
    oled.clearBuffer();
    oled.setFont(u8g2_font_6x12_tr);
    oled.drawStr(22, 25, "SMART PARKING");
    oled.drawStr(20, 45, "KHOI DONG HE THONG");
    oled.sendBuffer();

    Serial.println(F("[DisplayManager] LCD 1602 & OLED SH1106 ready."));
}

void DisplayManager::update(const ParkingMonitor& parking, const GateController& gate) {
    // 1. Cập nhật màn hình LCD 1602
    updateLcd(parking, gate);

    // 2. Cập nhật màn hình OLED SH1106 non-blocking
    unsigned long now = millis();

    uint8_t availA = parking.getAvailableA();
    uint8_t availB = parking.getAvailableB();
    const char* assigned = gate.getAssignedSlot();
    bool isEntering = (gate.getLastEvent() == GATE_EVENT_CAR_ENTERING ||
                       gate.getState() == GATE_STATE_ENTERING_WAIT_IN ||
                       gate.getState() == GATE_STATE_ENTERING_PASSING);
    bool hasGuidance = (isEntering && assigned[0] != '\0');

    bool dataChanged = (availA != cachedAvailA ||
                        availB != cachedAvailB ||
                        hasGuidance != cachedEnteringGuidance ||
                        strcmp(assigned, cachedAssignedSlot) != 0);

    if (!dataChanged) {
        for (uint8_t i = 0; i < NUM_SLOTS; i++) {
            if (parking.isOccupied(i) != cachedSlotOcc[i] ||
                parking.isReserved(i) != cachedSlotRes[i]) {
                dataChanged = true;
                break;
            }
        }
    }

    bool timeToRefresh = (now - lastOledRefreshTime >= DISPLAY_REFRESH_MS);

    if (dataChanged || timeToRefresh) {
        lastOledRefreshTime = now;
        cachedAvailA = availA;
        cachedAvailB = availB;
        cachedEnteringGuidance = hasGuidance;
        strncpy(cachedAssignedSlot, assigned, sizeof(cachedAssignedSlot) - 1);
        cachedAssignedSlot[sizeof(cachedAssignedSlot) - 1] = '\0';
        for (uint8_t i = 0; i < NUM_SLOTS; i++) {
            cachedSlotOcc[i] = parking.isOccupied(i);
            cachedSlotRes[i] = parking.isReserved(i);
        }
        updateOled(parking, gate);
    }
}

void DisplayManager::update(uint8_t totalAvail, uint8_t availA, uint8_t availB,
                            bool barrierOpen, GateEvent lastEvent) {
    (void)barrierOpen;
    (void)lastEvent;
    // Tương thích ngược: chỉ cập nhật LCD đơn giản nếu gọi bằng hàm cũ
    char line1[17] = " SMART PARKING  ";
    char line2[17];
    if (totalAvail == 0) {
        strncpy(line2, "Tr:0/4 (HET CHO)", sizeof(line2));
    } else {
        snprintf(line2, sizeof(line2), "Tr:%d/4 (A:%d B:%d)", totalAvail, availA, availB);
    }
    showLcdMessage(line1, line2);
}

void DisplayManager::showLcdMessage(const char* line1, const char* line2) {
    if (strcmp(line1, currentLcdLine1) != 0) {
        lcd.setCursor(0, 0);
        lcd.print(line1);
        strncpy(currentLcdLine1, line1, sizeof(currentLcdLine1) - 1);
        currentLcdLine1[sizeof(currentLcdLine1) - 1] = '\0';
    }
    if (strcmp(line2, currentLcdLine2) != 0) {
        lcd.setCursor(0, 1);
        lcd.print(line2);
        strncpy(currentLcdLine2, line2, sizeof(currentLcdLine2) - 1);
        currentLcdLine2[sizeof(currentLcdLine2) - 1] = '\0';
    }
}

void DisplayManager::updateLcd(const ParkingMonitor& parking, const GateController& gate) {
    char targetLine1[17] = " SMART PARKING  ";
    char targetLine2[17];

    unsigned long now = millis();
    GateEvent currentGateEvent = gate.getLastEvent();
    GateState currentGateState = gate.getState();

    // 1. Nếu đang ở trạng thái chờ Edge Server xác thực (WAIT_AUTH)
    if (currentGateState == GATE_STATE_WAIT_AUTH_IN ||
        currentGateState == GATE_STATE_WAIT_AUTH_OUT ||
        currentGateEvent == GATE_EVENT_WAITING_AUTH) {
        showLcdMessage("DANG NHAN DIEN..", "VUI LONG CHO... ");
        return;
    }

    // 2. Bắt sự kiện mới từ cổng (Edge-Trigger: chỉ ghi nhận khi sự kiện mới xuất hiện)
    if (currentGateEvent != GATE_EVENT_NONE && currentGateEvent != lastHandledEvent) {
        showingEventMsg = true;
        activeEvent = currentGateEvent;
        eventMsgTimestamp = now;
        lastHandledEvent = currentGateEvent;
    }

    // 3. Hết thời gian hiển thị thông điệp tạm thời
    if (showingEventMsg && (now - eventMsgTimestamp >= LCD_MSG_DURATION_MS)) {
        // Riêng trường hợp xe bị chặn vì bãi đầy, giữ thông báo cho tới khi xe lùi đi
        if (currentGateEvent != GATE_EVENT_BLOCKED_FULL) {
            showingEventMsg = false;
            activeEvent = GATE_EVENT_NONE;
        }
    }

    // Reset cờ nhận diện sự kiện khi cổng đã rảnh và thông điệp tạm thời đã kết thúc
    if (!showingEventMsg && currentGateEvent == GATE_EVENT_NONE) {
        lastHandledEvent = GATE_EVENT_NONE;
    }

    uint8_t totalAvail = parking.getTotalAvailable();
    uint8_t availA = parking.getAvailableA();
    uint8_t availB = parking.getAvailableB();
    const char* plate = gate.getActivePlate();
    const char* slot = gate.getAssignedSlot();

    // 4. Xử lý hiển thị thông điệp sự kiện
    if (showingEventMsg) {
        switch (activeEvent) {
            case GATE_EVENT_CAR_ENTERING:
                if (slot[0] != '\0') {
                    snprintf(targetLine1, sizeof(targetLine1), "MOI VAO -> O %-3s", slot);
                } else {
                    strncpy(targetLine1, "MOI VAO BAI >>  ", sizeof(targetLine1));
                }

                if (plate[0] != '\0' && strcmp(plate, "LOCAL") != 0 && strcmp(plate, "ADMIN_OPEN") != 0) {
                    snprintf(targetLine2, sizeof(targetLine2), "BS: %-12s", plate);
                } else {
                    snprintf(targetLine2, sizeof(targetLine2), "Tr:%d/4 (A:%d B:%d)", totalAvail, availA, availB);
                }
                break;

            case GATE_EVENT_CAR_EXITING:
                strncpy(targetLine1, "TAM BIET <<     ", sizeof(targetLine1));
                if (plate[0] != '\0' && strcmp(plate, "LOCAL") != 0) {
                    snprintf(targetLine2, sizeof(targetLine2), "BS: %-12s", plate);
                } else {
                    snprintf(targetLine2, sizeof(targetLine2), "Tr:%d/4 (A:%d B:%d)", totalAvail, availA, availB);
                }
                break;

            case GATE_EVENT_BLOCKED_FULL:
                strncpy(targetLine1, "BAI XE DA DAY!  ", sizeof(targetLine1));
                strncpy(targetLine2, "VUI LONG QUAY XE", sizeof(targetLine2));
                break;

            case GATE_EVENT_TIMEOUT:
                strncpy(targetLine1, "DONG BARIE...   ", sizeof(targetLine1));
                snprintf(targetLine2, sizeof(targetLine2), "Tr:%d/4 (A:%d B:%d)", totalAvail, availA, availB);
                break;

            default:
                strncpy(targetLine1, " SMART PARKING  ", sizeof(targetLine1));
                snprintf(targetLine2, sizeof(targetLine2), "Tr:%d/4 (A:%d B:%d)", totalAvail, availA, availB);
                break;
        }
    } else {
        strncpy(targetLine1, " SMART PARKING  ", sizeof(targetLine1));
        if (totalAvail == 0) {
            strncpy(targetLine2, "Tr:0/4 (HET CHO)", sizeof(targetLine2));
        } else {
            snprintf(targetLine2, sizeof(targetLine2), "Tr:%d/4 (A:%d B:%d)", totalAvail, availA, availB);
        }
    }

    targetLine1[sizeof(targetLine1) - 1] = '\0';
    targetLine2[sizeof(targetLine2) - 1] = '\0';

    // Cập nhật lên LCD (chống nhấp nháy nhờ bộ đệm so sánh)
    showLcdMessage(targetLine1, targetLine2);
}

static char getSlotChar(const ParkingMonitor& parking, uint8_t idx) {
    if (parking.isOccupied(idx)) return 'X';
    if (parking.isReserved(idx)) return 'R';
    return '_';
}

void DisplayManager::updateOled(const ParkingMonitor& parking, const GateController& gate) {
    oled.clearBuffer();

    uint8_t availA = parking.getAvailableA();
    uint8_t availB = parking.getAvailableB();
    const char* assigned = gate.getAssignedSlot();
    bool isEntering = (gate.getLastEvent() == GATE_EVENT_CAR_ENTERING ||
                       gate.getState() == GATE_STATE_ENTERING_WAIT_IN ||
                       gate.getState() == GATE_STATE_ENTERING_PASSING);
    bool hasGuidance = (isEntering && assigned[0] != '\0');

    // 1. Đường phân cách giao diện
    oled.drawVLine(63, 0, hasGuidance ? 46 : 64);
    oled.drawHLine(0, 15, 128);
    oled.drawHLine(0, 31, 128);
    oled.drawHLine(0, 47, 128);

    // 2. Tiêu đề: TRÁI LÀ KHU B, PHẢI LÀ KHU A
    oled.setFont(u8g2_font_7x14B_tr);
    int wB = oled.getStrWidth("KHU B");
    oled.drawStr((63 - wB) / 2, 13, "KHU B");

    int wA = oled.getStrWidth("KHU A");
    oled.drawStr(64 + (63 - wA) / 2, 13, "KHU A");

    // 3. Số lượng chỗ trống khả dụng [ X / 2 ] ở giữa
    char strB[12];
    snprintf(strB, sizeof(strB), "[ %d/2 ]", availB);
    int wSlotB = oled.getStrWidth(strB);
    oled.drawStr((63 - wSlotB) / 2, 28, strB);

    char strA[12];
    snprintf(strA, sizeof(strA), "[ %d/2 ]", availA);
    int wSlotA = oled.getStrWidth(strA);
    oled.drawStr(64 + (63 - wSlotA) / 2, 28, strA);

    // 4. Chi tiết từng ô đỗ xe: B1/B2 và A1/A2 (X: Xe đỗ, R: Đã đặt, _: Trống)
    oled.setFont(u8g2_font_6x12_tr);
    char detB[16];
    snprintf(detB, sizeof(detB), "B1:%c B2:%c", getSlotChar(parking, 2), getSlotChar(parking, 3));
    int wDetB = oled.getStrWidth(detB);
    oled.drawStr((63 - wDetB) / 2, 43, detB);

    char detA[16];
    snprintf(detA, sizeof(detA), "A1:%c A2:%c", getSlotChar(parking, 0), getSlotChar(parking, 1));
    int wDetA = oled.getStrWidth(detA);
    oled.drawStr(64 + (63 - wDetA) / 2, 43, detA);

    // 5. Khu vực đáy: Điều hướng ô đỗ khi xe vào HOẶC trạng thái CÒN CHỖ / HẾT CHỖ
    if (hasGuidance) {
        // Vẽ banner điều hướng nổi bật ở đáy màn hình
        oled.drawBox(0, 48, 128, 16);
        oled.setDrawColor(0);
        char guideMsg[24];
        if (assigned[0] == 'B') {
            snprintf(guideMsg, sizeof(guideMsg), "<- RE TRAI O %s", assigned);
        } else {
            snprintf(guideMsg, sizeof(guideMsg), "RE PHAI O %s ->", assigned);
        }
        int wGuide = oled.getStrWidth(guideMsg);
        oled.drawStr((128 - wGuide) / 2, 60, guideMsg);
        oled.setDrawColor(1);
    } else {
        // Khu B (Bên trái)
        const char* statB = (availB > 0) ? "CON CHO" : "HET CHO";
        int wStatB = oled.getStrWidth(statB);
        int xStatB = (63 - wStatB) / 2;
        if (availB == 0) {
            oled.drawBox(2, 49, 59, 14);
            oled.setDrawColor(0);
            oled.drawStr(xStatB, 60, statB);
            oled.setDrawColor(1);
        } else {
            oled.drawStr(xStatB, 60, statB);
        }

        // Khu A (Bên phải)
        const char* statA = (availA > 0) ? "CON CHO" : "HET CHO";
        int wStatA = oled.getStrWidth(statA);
        int xStatA = 64 + (63 - wStatA) / 2;
        if (availA == 0) {
            oled.drawBox(66, 49, 59, 14);
            oled.setDrawColor(0);
            oled.drawStr(xStatA, 60, statA);
            oled.setDrawColor(1);
        } else {
            oled.drawStr(xStatA, 60, statA);
        }
    }

    // Đẩy buffer ra màn hình OLED
    oled.sendBuffer();
}
