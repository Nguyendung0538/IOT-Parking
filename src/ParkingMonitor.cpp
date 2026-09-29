#include "ParkingMonitor.h"

ParkingMonitor::ParkingMonitor() {
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        rawState[i] = false;
        confirmedState[i] = false;
        lastDebounceTime[i] = 0;
    }
}

void ParkingMonitor::begin() {
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        // Module LM393 có ngõ ra digital, cấu hình INPUT_PULLUP an toàn
        pinMode(slotPins[i], INPUT_PULLUP);
        
        // Đọc giá trị khởi tạo
        bool initialRead = (digitalRead(slotPins[i]) == SENSOR_ACTIVE_LEVEL);
        rawState[i] = initialRead;
        confirmedState[i] = initialRead;
        lastDebounceTime[i] = millis();
    }
    Serial.println(F("[ParkingMonitor] Initialized 4 parking slots (A1, A2, B1, B2)."));
}

bool ParkingMonitor::update() {
    bool hasChanged = false;
    unsigned long now = millis();

    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        // Đọc tín hiệu thực tế từ cảm biến (Active LOW: LOW = có xe đỗ)
        bool currentReading = (digitalRead(slotPins[i]) == SENSOR_ACTIVE_LEVEL);

        // Nếu tín hiệu raw thay đổi so với lần đọc trước, ghi nhận thời điểm
        if (currentReading != rawState[i]) {
            rawState[i] = currentReading;
            lastDebounceTime[i] = now;
        }

        // Nếu tín hiệu ổn định vượt ngưỡng debounce time
        if ((now - lastDebounceTime[i]) >= SENSOR_DEBOUNCE_MS) {
            if (confirmedState[i] != rawState[i]) {
                confirmedState[i] = rawState[i];
                hasChanged = true;
            }
        }
    }

    return hasChanged;
}

bool ParkingMonitor::isOccupied(uint8_t slotIndex) const {
    if (slotIndex < NUM_SLOTS) {
        return confirmedState[slotIndex];
    }
    return false;
}

const char* ParkingMonitor::getSlotName(uint8_t slotIndex) const {
    if (slotIndex < NUM_SLOTS) {
        return slotNames[slotIndex];
    }
    return "UNKNOWN";
}

uint8_t ParkingMonitor::getAvailableA() const {
    uint8_t count = 0;
    if (!confirmedState[0]) count++; // A1
    if (!confirmedState[1]) count++; // A2
    return count;
}

uint8_t ParkingMonitor::getAvailableB() const {
    uint8_t count = 0;
    if (!confirmedState[2]) count++; // B1
    if (!confirmedState[3]) count++; // B2
    return count;
}

uint8_t ParkingMonitor::getTotalAvailable() const {
    return getAvailableA() + getAvailableB();
}

uint8_t ParkingMonitor::getOccupiedCount() const {
    return NUM_SLOTS - getTotalAvailable();
}

bool ParkingMonitor::isFull() const {
    return getTotalAvailable() == 0;
}

void ParkingMonitor::printStatus() const {
    Serial.println(F("=========================================="));
    Serial.println(F("        TRANG THAI BAI DO XE             "));
    Serial.println(F("=========================================="));
    Serial.printf(" [Khu A] A1: %s | A2: %s  (Trong: %d/%d)\n",
                  confirmedState[0] ? "[X] CO XE " : "[ ] TRONG ",
                  confirmedState[1] ? "[X] CO XE " : "[ ] TRONG ",
                  getAvailableA(), ZONE_A_SLOTS);
    Serial.printf(" [Khu B] B1: %s | B2: %s  (Trong: %d/%d)\n",
                  confirmedState[2] ? "[X] CO XE " : "[ ] TRONG ",
                  confirmedState[3] ? "[X] CO XE " : "[ ] TRONG ",
                  getAvailableB(), ZONE_B_SLOTS);
    Serial.printf(" -> Tong so cho trong: %d/%d | Tinh trang: %s\n",
                  getTotalAvailable(), NUM_SLOTS,
                  isFull() ? "FULL (BAI DA DAY)" : "AVAILABLE (CON CHO)");
    Serial.println(F("=========================================="));
}
