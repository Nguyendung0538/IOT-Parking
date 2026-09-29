#include "GateController.h"

#define GATE_SENSOR_DEBOUNCE_MS 80

GateController::GateController()
    : currentState(GATE_STATE_IDLE),
      lastEvent(GATE_EVENT_NONE),
      barrierOpen(false),
      barrierOpenTimestamp(0),
      flagJustEntered(false),
      flagJustExited(false),
      rawSensorOut(false),
      confirmedSensorOut(false),
      lastDebounceTimeOut(0),
      rawSensorIn(false),
      confirmedSensorIn(false),
      lastDebounceTimeIn(0) {
}

void GateController::begin() {
    // GPIO 34, 35 là GPI only (chỉ đọc input thuần, không pullup)
    pinMode(PIN_SENSOR_GATE_OUT, INPUT);
    pinMode(PIN_SENSOR_GATE_IN, INPUT);

    // Chuẩn bị servo SG90
    ESP32PWM::allocateTimer(0);
    gateServo.setPeriodHertz(50); // Chuẩn servo 50Hz
    gateServo.attach(PIN_SERVO_GATE, 500, 2400);

    // Mặc định hạ barie
    closeBarrier();

    // Khởi tạo đọc trạng thái ban đầu của cảm biến
    rawSensorOut = (digitalRead(PIN_SENSOR_GATE_OUT) == SENSOR_ACTIVE_LEVEL);
    confirmedSensorOut = rawSensorOut;
    rawSensorIn = (digitalRead(PIN_SENSOR_GATE_IN) == SENSOR_ACTIVE_LEVEL);
    confirmedSensorIn = rawSensorIn;

    Serial.println(F("[GateController] Initialized gate sensors & SG90 servo (Active-LOW, FSM ready)."));
}

void GateController::updateSensors() {
    unsigned long now = millis();

    // Debounce cảm biến cổng ngoài (S_OUT - GPIO 34)
    bool readingOut = (digitalRead(PIN_SENSOR_GATE_OUT) == SENSOR_ACTIVE_LEVEL);
    if (readingOut != rawSensorOut) {
        rawSensorOut = readingOut;
        lastDebounceTimeOut = now;
    }
    if ((now - lastDebounceTimeOut) >= GATE_SENSOR_DEBOUNCE_MS) {
        confirmedSensorOut = rawSensorOut;
    }

    // Debounce cảm biến cổng trong (S_IN - GPIO 35)
    bool readingIn = (digitalRead(PIN_SENSOR_GATE_IN) == SENSOR_ACTIVE_LEVEL);
    if (readingIn != rawSensorIn) {
        rawSensorIn = readingIn;
        lastDebounceTimeIn = now;
    }
    if ((now - lastDebounceTimeIn) >= GATE_SENSOR_DEBOUNCE_MS) {
        confirmedSensorIn = rawSensorIn;
    }
}

void GateController::update(bool parkingFull) {
    updateSensors();
    unsigned long now = millis();

    switch (currentState) {
        case GATE_STATE_IDLE:
            if (confirmedSensorOut) {
                // Có xe tiếp cận từ phía ngoài bãi
                if (parkingFull) {
                    lastEvent = GATE_EVENT_BLOCKED_FULL;
                    changeState(GATE_STATE_BLOCKED_FULL);
                    Serial.println(F("[GateController] Tu choi xe vao: Bai do xe da DAY!"));
                } else {
                    openBarrier();
                    lastEvent = GATE_EVENT_CAR_ENTERING;
                    changeState(GATE_STATE_ENTERING_WAIT_IN);
                    Serial.println(F("[GateController] Phat hien xe den cong ngoai -> Mo Barie (90 deg)."));
                }
            } else if (confirmedSensorIn) {
                // Có xe tiếp cận từ phía trong bãi muốn ra
                openBarrier();
                lastEvent = GATE_EVENT_CAR_EXITING;
                changeState(GATE_STATE_EXITING_WAIT_OUT);
                Serial.println(F("[GateController] Phat hien xe muon ra -> Mo Barie (90 deg)."));
            }
            break;

        case GATE_STATE_ENTERING_WAIT_IN:
            // Kiểm tra an toàn Timeout 10s nếu xe không đi tiếp
            if ((now - barrierOpenTimestamp) >= GATE_SAFETY_TIMEOUT_MS) {
                closeBarrier();
                lastEvent = GATE_EVENT_TIMEOUT;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Timeout 10s: Xe vao khong qua cong -> Dong barie!"));
                break;
            }

            // Xe tiến vào kích hoạt cảm biến trong
            if (confirmedSensorIn) {
                changeState(GATE_STATE_ENTERING_PASSING);
                Serial.println(F("[GateController] Xe dang di qua cong (cham S_IN)..."));
            }
            break;

        case GATE_STATE_ENTERING_PASSING:
            // Kiểm tra an toàn Timeout 10s
            if ((now - barrierOpenTimestamp) >= GATE_SAFETY_TIMEOUT_MS) {
                closeBarrier();
                lastEvent = GATE_EVENT_TIMEOUT;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Timeout 10s khi xe qua cong -> Dong barie an toan!"));
                break;
            }

            // Xe đã vượt qua hẳn cả 2 cảm biến (cả 2 đều nhả về HIGH)
            if (!confirmedSensorOut && !confirmedSensorIn) {
                closeBarrier();
                flagJustEntered = true;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Xe da VAO BAI HOAN TAT -> Dong barie (0 deg)."));
            }
            break;

        case GATE_STATE_EXITING_WAIT_OUT:
            // Kiểm tra an toàn Timeout 10s nếu xe không ra
            if ((now - barrierOpenTimestamp) >= GATE_SAFETY_TIMEOUT_MS) {
                closeBarrier();
                lastEvent = GATE_EVENT_TIMEOUT;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Timeout 10s: Xe ra khong qua cong -> Dong barie!"));
                break;
            }

            // Xe tiến ra kích hoạt cảm biến ngoài
            if (confirmedSensorOut) {
                changeState(GATE_STATE_EXITING_PASSING);
                Serial.println(F("[GateController] Xe dang di qua cong (cham S_OUT)..."));
            }
            break;

        case GATE_STATE_EXITING_PASSING:
            // Kiểm tra an toàn Timeout 10s
            if ((now - barrierOpenTimestamp) >= GATE_SAFETY_TIMEOUT_MS) {
                closeBarrier();
                lastEvent = GATE_EVENT_TIMEOUT;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Timeout 10s khi xe ra cong -> Dong barie an toan!"));
                break;
            }

            // Xe đã vượt qua hẳn cả 2 cảm biến ra ngoài (cả 2 đều nhả về HIGH)
            if (!confirmedSensorOut && !confirmedSensorIn) {
                closeBarrier();
                flagJustExited = true;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Xe da ROI BAI HOAN TAT -> Dong barie (0 deg)."));
            }
            break;

        case GATE_STATE_BLOCKED_FULL:
            // Chờ cho đến khi xe ngoài cổng rời đi (S_OUT nhả về HIGH)
            if (!confirmedSensorOut) {
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Xe bi chan da roi di -> Tro ve trang thai IDLE."));
            }
            break;
    }
}

bool GateController::isBarrierOpen() const {
    return barrierOpen;
}

GateEvent GateController::getLastEvent() const {
    return lastEvent;
}

void GateController::clearLastEvent() {
    lastEvent = GATE_EVENT_NONE;
}

bool GateController::carJustEntered() {
    bool res = flagJustEntered;
    flagJustEntered = false;
    return res;
}

bool GateController::carJustExited() {
    bool res = flagJustExited;
    flagJustExited = false;
    return res;
}

const char* GateController::getStateName() const {
    switch (currentState) {
        case GATE_STATE_IDLE:             return "IDLE";
        case GATE_STATE_ENTERING_WAIT_IN: return "ENTERING_WAIT_IN";
        case GATE_STATE_ENTERING_PASSING: return "ENTERING_PASSING";
        case GATE_STATE_EXITING_WAIT_OUT: return "EXITING_WAIT_OUT";
        case GATE_STATE_EXITING_PASSING:  return "EXITING_PASSING";
        case GATE_STATE_BLOCKED_FULL:     return "BLOCKED_FULL";
        default:                          return "UNKNOWN";
    }
}

void GateController::openBarrier() {
    gateServo.write(SERVO_ANGLE_OPEN);
    barrierOpen = true;
    barrierOpenTimestamp = millis();
}

void GateController::closeBarrier() {
    gateServo.write(SERVO_ANGLE_CLOSED);
    barrierOpen = false;
}

void GateController::changeState(GateState newState) {
    currentState = newState;
}
