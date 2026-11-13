#ifndef PARKING_MONITOR_H
#define PARKING_MONITOR_H

#include <Arduino.h>
#include "config.h"

class ParkingMonitor {
public:
    ParkingMonitor();

    // Khởi tạo các chân GPIO cho 4 ô đỗ xe
    void begin();

    // Quét cảm biến & cập nhật debounce non-blocking. Trả về true nếu có ô thay đổi trạng thái
    bool update();

    // Cập nhật mặt nạ đặt chỗ từ Cloud/MQTT (A1, A2, B1, B2).
    // Trả về true nếu trạng thái reserved có thay đổi
    bool setReservedMask(bool a1, bool a2, bool b1, bool b2);

    // Kiểm tra trạng thái từng slot (0: A1, 1: A2, 2: B1, 3: B2)
    // Trả về true: Đã có xe vật lý đỗ (Occupied)
    bool isOccupied(uint8_t slotIndex) const;

    // Kiểm tra slot có đang được đặt trước trên Cloud không
    bool isReserved(uint8_t slotIndex) const;

    // Kiểm tra slot có khả dụng cho xe vãng lai không (Không có xe vật lý & Không bị đặt trước)
    bool isSlotAvailable(uint8_t slotIndex) const;

    // Trả về tên slot theo chỉ số
    const char* getSlotName(uint8_t slotIndex) const;

    // Lấy số lượng ô trống khả dụng thực tế theo khu vực & toàn bãi
    uint8_t getAvailableA() const;
    uint8_t getAvailableB() const;
    uint8_t getTotalAvailable() const;
    uint8_t getOccupiedCount() const;
    uint8_t getReservedCount() const;

    // Bãi đỗ xe đã đầy hoàn toàn (không còn ô khả dụng)
    bool isFull() const;

    // In trạng thái chi tiết 4 slot ra Serial
    void printStatus() const;

private:
    const uint8_t slotPins[NUM_SLOTS] = {
        PIN_SLOT_A1,
        PIN_SLOT_A2,
        PIN_SLOT_B1,
        PIN_SLOT_B2
    };

    const char* slotNames[NUM_SLOTS] = {
        "A1", "A2", "B1", "B2"
    };

    bool rawState[NUM_SLOTS];
    bool confirmedState[NUM_SLOTS];
    bool reservedState[NUM_SLOTS];
    unsigned long lastDebounceTime[NUM_SLOTS];
};

#endif // PARKING_MONITOR_H
