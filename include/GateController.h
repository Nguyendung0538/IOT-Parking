#ifndef GATE_CONTROLLER_H
#define GATE_CONTROLLER_H

#include <Arduino.h>
#include <ESP32Servo.h>
#include "config.h"

// Các sự kiện tại cổng vào/ra để thông báo cho Display & IoT
enum GateEvent {
    GATE_EVENT_NONE,
    GATE_EVENT_WAITING_AUTH,   // Đang chờ camera chụp & Edge AI nhận diện biển số
    GATE_EVENT_CAR_ENTERING,   // Xe được phép vào bãi (đã mở barrier)
    GATE_EVENT_CAR_EXITING,    // Xe được phép ra bãi (đã mở barrier)
    GATE_EVENT_BLOCKED_FULL,   // Từ chối vào: bãi đầy hoặc backend từ chối
    GATE_EVENT_TIMEOUT         // Quá thời gian an toàn 10s tự động đóng barrier
};

// Các trạng thái máy trạng thái hữu hạn (FSM)
enum GateState {
    GATE_STATE_IDLE,             // Trạng thái nghỉ: Barie đóng, chờ xe đến
    GATE_STATE_WAIT_AUTH_IN,     // Xe chạm S_OUT ngoài cổng, chờ Edge AI xác thực vào
    GATE_STATE_WAIT_AUTH_OUT,    // Xe chạm S_IN trong bãi, chờ Edge AI xác thực ra
    GATE_STATE_ENTERING_WAIT_IN, // Cổng mở cho vào, xe đang di chuyển tới S_IN
    GATE_STATE_ENTERING_PASSING, // Xe đang đi qua giữa barie (đang che S_IN)
    GATE_STATE_EXITING_WAIT_OUT, // Cổng mở cho ra, xe đang di chuyển tới S_OUT
    GATE_STATE_EXITING_PASSING,  // Xe đang đi qua giữa barie (đang che S_OUT)
    GATE_STATE_BLOCKED_FULL      // Xe đến khi bãi đầy/bị từ chối, chờ xe lùi đi
};

class GateController {
public:
    GateController();

    // Khởi tạo chân Servo và các chân cảm biến S_OUT, S_IN
    void begin();

    // Cập nhật State Machine điều khiển cổng (gọi liên tục trong loop)
    void update(bool parkingFull);

    // Xử lý lệnh mở/từ chối cổng nhận từ Edge Backend (qua MQTT parking/gate/command)
    void handleBackendCommand(uint32_t seq, bool allowOpen, const char* plate, const char* slot);

    // Tiêu thụ yêu cầu xác thực mới để gửi lên MQTT (trả về true khi có request mới)
    bool consumePendingAuthRequest(uint32_t& outSeq, const char*& outDirection);

    // Tiêu thụ sự kiện tự mở do timeout 8s để xếp vào hàng đợi đồng bộ bù
    bool consumeOfflineFallbackEvent(uint32_t& outSeq, bool& outEntering);

    // Kiểm tra barie đang mở hay đóng
    bool isBarrierOpen() const;

    // Lấy sự kiện cổng gần nhất (dùng cho Display & Telemetry)
    GateEvent getLastEvent() const;

    // Lấy trạng thái FSM hiện tại
    GateState getState() const;

    // Reset sự kiện sau khi đã được xử lý hiển thị
    void clearLastEvent();

    // Cờ báo hiệu xe vừa hoàn tất quá trình vào bãi (tự reset sau khi đọc)
    bool carJustEntered();

    // Cờ báo hiệu xe vừa hoàn tất quá trình ra bãi (tự reset sau khi đọc)
    bool carJustExited();

    // Tên trạng thái dạng chuỗi phục vụ in log
    const char* getStateName() const;

    // Biển số xe hiện tại vừa được nhận diện
    const char* getActivePlate() const;

    // Ô đỗ được gợi ý từ Backend
    const char* getAssignedSlot() const;

    // Sequence ID hiện tại
    uint32_t getCurrentSeq() const;

private:
    Servo gateServo;
    GateState currentState;
    GateEvent lastEvent;
    bool barrierOpen;
    unsigned long barrierOpenTimestamp;

    // Cờ sự kiện hoàn tất 1 lượt xe
    bool flagJustEntered;
    bool flagJustExited;

    // Xác thực biển số qua Edge Server
    uint32_t currentSeq;
    bool pendingAuthPublish;
    bool pendingAuthEntering;
    unsigned long authWaitTimestamp;
    char activePlate[16];
    char assignedSlot[8];

    // Ghi nhận Offline Fallback (8s timeout)
    bool pendingOfflineFallbackLog;
    uint32_t fallbackSeq;
    bool fallbackEntering;

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
