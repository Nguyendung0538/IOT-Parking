#include "ParkingMonitor.h"

ParkingMonitor::ParkingMonitor() {
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        rawState[i] = false;
        confirmedState[i] = false;
        reservedState[i] = false;
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
        reservedState[i] = false;
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

bool ParkingMonitor::setReservedMask(bool a1, bool a2, bool b1, bool b2) {
    bool changed = false;
    bool newMask[NUM_SLOTS] = { a1, a2, b1, b2 };

    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        if (reservedState[i] != newMask[i]) {
            reservedState[i] = newMask[i];
            changed = true;
        }
    }

    if (changed) {
        Serial.printf("[ParkingMonitor] Updated Reserved Mask: A1=%d, A2=%d, B1=%d, B2=%d\n",
                      reservedState[0], reservedState[1], reservedState[2], reservedState[3]);
    }
    return changed;
}

bool ParkingMonitor::isOccupied(uint8_t slotIndex) const {
    if (slotIndex < NUM_SLOTS) {
        return confirmedState[slotIndex];
    }
    return false;
}

bool ParkingMonitor::isReserved(uint8_t slotIndex) const {
    if (slotIndex < NUM_SLOTS) {
        return reservedState[slotIndex];
    }
    return false;
}

bool ParkingMonitor::isSlotAvailable(uint8_t slotIndex) const {
    if (slotIndex < NUM_SLOTS) {
        // Ô chỉ khả dụng khi KHÔNG có xe vật lý VÀ KHÔNG bị đặt trước
        return (!confirmedState[slotIndex] && !reservedState[slotIndex]);
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
    if (isSlotAvailable(0)) count++; // A1
    if (isSlotAvailable(1)) count++; // A2
    return count;
}

uint8_t ParkingMonitor::getAvailableB() const {
    uint8_t count = 0;
    if (isSlotAvailable(2)) count++; // B1
    if (isSlotAvailable(3)) count++; // B2
    return count;
}

uint8_t ParkingMonitor::getTotalAvailable() const {
    return getAvailableA() + getAvailableB();
}

uint8_t ParkingMonitor::getOccupiedCount() const {
    uint8_t count = 0;
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        if (confirmedState[i]) count++;
    }
    return count;
}

uint8_t ParkingMonitor::getReservedCount() const {
    uint8_t count = 0;
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        // Đếm số ô được đặt trước mà chưa có xe vào đỗ
        if (reservedState[i] && !confirmedState[i]) {
            count++;
        }
    }
    return count;
}

bool ParkingMonitor::isFull() const {
    return getTotalAvailable() == 0;
}

static const char* formatSlotStatus(bool occupied, bool reserved) {
    if (occupied) return "[X] CO XE ";
    if (reserved) return "[R] DA DAT";
    return "[ ] TRONG ";
}

void ParkingMonitor::printStatus() const {
    Serial.println(F("=========================================="));
    Serial.println(F("        TRANG THAI BAI DO XE             "));
    Serial.println(F("=========================================="));
    Serial.printf(" [Khu A] A1: %s | A2: %s  (Trong: %d/%d)\n",
                  formatSlotStatus(confirmedState[0], reservedState[0]),
                  formatSlotStatus(confirmedState[1], reservedState[1]),
                  getAvailableA(), ZONE_A_SLOTS);
    Serial.printf(" [Khu B] B1: %s | B2: %s  (Trong: %d/%d)\n",
                  formatSlotStatus(confirmedState[2], reservedState[2]),
                  formatSlotStatus(confirmedState[3], reservedState[3]),
                  getAvailableB(), ZONE_B_SLOTS);
    Serial.printf(" -> Tong so cho trong: %d/%d (Da dat: %d, Co xe: %d) | Tinh trang: %s\n",
                  getTotalAvailable(), NUM_SLOTS,
                  getReservedCount(), getOccupiedCount(),
                  isFull() ? "FULL (BAI DA DAY)" : "AVAILABLE (CON CHO)");
    Serial.println(F("=========================================="));
}
