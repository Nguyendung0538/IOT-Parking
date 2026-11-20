#ifndef GATE_CONTROLLER_H
#define GATE_CONTROLLER_H

#include <Arduino.h>
#include <ESP32Servo.h>
#include "config.h"

// Các sự kiện tại cổng vào/ra để thông báo cho Display & IoT
enum GateEvent {
    GATE_EVENT_NONE,
    GATE_EVENT_WAITING_AUTH,   // Đang chờ camera chụp & Edge AI nhận diện biển số
    GATE_EVENT_CAR_ENTERING,   // Xe được phép vào bãi (đã mở barrier cổng vào)
    GATE_EVENT_CAR_EXITING,    // Xe được phép ra bãi (đã mở barrier cổng ra)
    GATE_EVENT_BLOCKED_FULL,   // Từ chối vào: bãi đầy hoặc backend từ chối
    GATE_EVENT_NO_EXIT_REQUEST,// Từ chối ra: xe chưa bấm gửi yêu cầu ra trên App Android
    GATE_EVENT_MANUAL_REQUIRED,// Không đọc được biển: giữ barie đóng, chờ Admin xử lý trên Web
    GATE_EVENT_TIMEOUT         // Quá thời gian an toàn 10s tự động đóng barrier
};

// Trạng thái tương thích chung (tổng hợp / hiển thị)
enum GateState {
    GATE_STATE_IDLE,
    GATE_STATE_WAIT_AUTH_IN,
    GATE_STATE_WAIT_AUTH_OUT,
    GATE_STATE_ENTERING_WAIT_IN,
    GATE_STATE_ENTERING_PASSING,
    GATE_STATE_EXITING_WAIT_OUT,
    GATE_STATE_EXITING_PASSING,
    GATE_STATE_BLOCKED_FULL
};

// FSM độc lập cho Cổng VÀO (1 chiều: S_ENTRY_OUT -> Servo IN -> S_ENTRY_IN)
enum GateInState {
    GATE_IN_IDLE,
    GATE_IN_WAIT_AUTH,
    GATE_IN_WAIT_MANUAL,
    GATE_IN_OPEN_WAIT,
    GATE_IN_PASSING,
    GATE_IN_BLOCKED_FULL
};

// FSM độc lập cho Cổng RA (1 chiều: S_EXIT_IN -> Servo OUT -> S_EXIT_OUT)
enum GateOutState {
    GATE_OUT_IDLE,
    GATE_OUT_WAIT_AUTH,
    GATE_OUT_WAIT_MANUAL,
    GATE_OUT_OPEN_WAIT,
    GATE_OUT_PASSING,
    GATE_OUT_BLOCKED
};

class GateController {
public:
    GateController();

    // Khởi tạo 2 Servo và 4 cảm biến cổng (VÀO: 34/35 + 18, RA: 32/33 + 19)
    void begin();

    // Cập nhật song song cả 2 FSM Cổng VÀO và Cổng RA (gọi liên tục trong loop)
    void update(bool parkingFull);

    // Xử lý lệnh mở/đóng/chờ web/từ chối nhận từ Edge Backend / Admin
    void handleBackendCommand(uint32_t seq, const char* action, const char* direction, const char* plate, const char* slot);
    void handleBackendCommand(uint32_t seq, bool allowOpen, const char* direction, const char* plate, const char* slot);
    void handleBackendCommand(uint32_t seq, bool allowOpen, const char* plate, const char* slot);

    // Tiêu thụ yêu cầu xác thực mới để gửi lên MQTT (trả về true khi có request mới từ 1 trong 2 cổng)
    bool consumePendingAuthRequest(uint32_t& outSeq, const char*& outDirection);

    // Tiêu thụ sự kiện tự mở do timeout 8s để xếp vào hàng đợi đồng bộ bù
    bool consumeOfflineFallbackEvent(uint32_t& outSeq, bool& outEntering);

    // Kiểm tra trạng thái barie
    bool isBarrierOpen() const;
    bool isBarrierInOpen() const;
    bool isBarrierOutOpen() const;

    // Lấy sự kiện cổng gần nhất (dùng cho Display & Telemetry)
    GateEvent getLastEvent() const;

    // Trạng thái tổng hợp / từng cổng
    GateState getState() const;
    GateInState getEntryState() const;
    GateOutState getExitState() const;

    // Reset sự kiện sau khi đã được xử lý hiển thị
    void clearLastEvent();

    // Cờ báo hiệu xe vừa hoàn tất qua cổng (tự reset sau khi đọc)
    bool carJustEntered();
    bool carJustExited();

    // Tên trạng thái dạng chuỗi phục vụ Telemetry & log
    const char* getStateName() const;
    const char* getEntryStateName() const;
    const char* getExitStateName() const;

    // Biển số & ô đỗ gần nhất
    const char* getActivePlate() const;
    const char* getAssignedSlot() const;

    // Sequence ID gần nhất
    uint32_t getCurrentSeq() const;

private:
    Servo gateServoIn;
    Servo gateServoOut;

    GateInState entryState;
    GateOutState exitState;
    GateEvent lastEvent;

    bool barrierInOpen;
    bool barrierOutOpen;
    unsigned long barrierInOpenTimestamp;
    unsigned long barrierOutOpenTimestamp;

    // Cờ sự kiện hoàn tất 1 lượt xe
    bool flagJustEntered;
    bool flagJustExited;

    // Sequence ID dùng chung tăng dần để không bao giờ trùng giữa Cổng VÀO và Cổng RA
    uint32_t nextSeq;
    uint32_t seqIn;
    uint32_t seqOut;

    // Hàng đợi xác thực & Fallback cho Cổng VÀO
    bool pendingInAuthPublish;
    unsigned long inAuthWaitTimestamp;
    bool pendingInOfflineFallback;
    uint32_t fallbackInSeq;

    // Hàng đợi xác thực & Fallback cho Cổng RA
    bool pendingOutAuthPublish;
    unsigned long outAuthWaitTimestamp;
    bool pendingOutOfflineFallback;
    uint32_t fallbackOutSeq;

    char activePlate[16];
    char assignedSlot[8];

    // Biến debounce cho 4 cảm biến 2 cổng (LM393)
    bool rawEntryOut;
    bool confirmedEntryOut;
    unsigned long lastDebounceEntryOut;

    bool rawEntryIn;
    bool confirmedEntryIn;
    unsigned long lastDebounceEntryIn;

    bool rawExitIn;
    bool confirmedExitIn;
    unsigned long lastDebounceExitIn;

    bool rawExitOut;
    bool confirmedExitOut;
    unsigned long lastDebounceExitOut;

    void updateSensors();
    void updateEntryGate(bool parkingFull, unsigned long now);
    void updateExitGate(unsigned long now);

    void openEntryBarrier();
    void closeEntryBarrier();
    void openExitBarrier();
    void closeExitBarrier();
};

#endif // GATE_CONTROLLER_H
