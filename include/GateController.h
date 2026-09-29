#ifndef GATE_CONTROLLER_H
#define GATE_CONTROLLER_H

#include <Arduino.h>
#include <ESP32Servo.h>
#include "config.h"

// Các sự kiện tại cổng vào/ra để thông báo cho Display & IoT
enum GateEvent {
    GATE_EVENT_NONE,
    GATE_EVENT_CAR_ENTERING,
    GATE_EVENT_CAR_EXITING,
    GATE_EVENT_BLOCKED_FULL,
    GATE_EVENT_TIMEOUT
};

// Các trạng thái máy trạng thái hữu hạn (FSM)
enum GateState {
    GATE_STATE_IDLE,             // Trạng thái nghỉ: Barie đóng, chờ xe đến
    GATE_STATE_ENTERING_WAIT_IN, // Xe đã kích hoạt S_OUT ngoài cổng, chờ chạm S_IN
    GATE_STATE_ENTERING_PASSING, // Xe đang đi qua giữa barie (đã chạm S_IN)
    GATE_STATE_EXITING_WAIT_OUT, // Xe đã kích hoạt S_IN trong bãi, chờ chạm S_OUT
    GATE_STATE_EXITING_PASSING,  // Xe đang đi qua giữa barie (đã chạm S_OUT)
    GATE_STATE_BLOCKED_FULL      // Xe đến khi bãi đầy, từ chối mở barie
};

class GateController {
public:
    GateController();

    // Khởi tạo chân Servo và các chân cảm biến S_OUT, S_IN
    void begin();

    // Cập nhật State Machine điều khiển cổng (gọi liên tục trong loop)
    void update(bool parkingFull);

    // Kiểm tra barie đang mở hay đóng
    bool isBarrierOpen() const;

    // Lấy sự kiện cổng gần nhất (dùng cho Display & Telemetry)
    GateEvent getLastEvent() const;

    // Reset sự kiện sau khi đã được xử lý hiển thị
    void clearLastEvent();

    // Cờ báo hiệu xe vừa hoàn tất quá trình vào bãi (tự reset sau khi đọc)
    bool carJustEntered();

    // Cờ báo hiệu xe vừa hoàn tất quá trình ra bãi (tự reset sau khi đọc)
    bool carJustExited();

    // Tên trạng thái dạng chuỗi phục vụ in log
    const char* getStateName() const;

private:
    Servo gateServo;
    GateState currentState;
    GateEvent lastEvent;
    bool barrierOpen;
    unsigned long barrierOpenTimestamp;

    // Cờ sự kiện hoàn tất 1 lượt xe
    bool flagJustEntered;
    bool flagJustExited;

    // Biến debounce cho 2 cảm biến cổng (LM393)
    bool rawSensorOut;
    bool confirmedSensorOut;
    unsigned long lastDebounceTimeOut;

    bool rawSensorIn;
    bool confirmedSensorIn;
    unsigned long lastDebounceTimeIn;

    void updateSensors();
    void openBarrier();
    void closeBarrier();
    void changeState(GateState newState);
};

#endif // GATE_CONTROLLER_H
