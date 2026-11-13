#include "GateController.h"

#define GATE_SENSOR_DEBOUNCE_MS 80

GateController::GateController()
    : currentState(GATE_STATE_IDLE),
      lastEvent(GATE_EVENT_NONE),
      barrierOpen(false),
      barrierOpenTimestamp(0),
      flagJustEntered(false),
      flagJustExited(false),
      currentSeq(0),
      pendingAuthPublish(false),
      pendingAuthEntering(false),
      authWaitTimestamp(0),
      pendingOfflineFallbackLog(false),
      fallbackSeq(0),
      fallbackEntering(false),
      rawSensorOut(false),
      confirmedSensorOut(false),
      lastDebounceTimeOut(0),
      rawSensorIn(false),
      confirmedSensorIn(false),
      lastDebounceTimeIn(0) {
    activePlate[0] = '\0';
    assignedSlot[0] = '\0';
}

void GateController::begin() {
    // GPIO 34, 35 là GPI only (chỉ đọc input thuần, không pullup nội)
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
            // Tự động giải phóng sự kiện timeout khi đã quay về IDLE
            if (lastEvent == GATE_EVENT_TIMEOUT) {
                lastEvent = GATE_EVENT_NONE;
            }

            if (confirmedSensorOut) {
#if ENABLE_WIFI
                // Có Wi-Fi: Chuyển sang chờ nhận diện biển số & lệnh mở cổng từ Edge Server
                currentSeq++;
                pendingAuthPublish = true;
                pendingAuthEntering = true;
                authWaitTimestamp = now;
                lastEvent = GATE_EVENT_WAITING_AUTH;
                activePlate[0] = '\0';
                assignedSlot[0] = '\0';
                changeState(GATE_STATE_WAIT_AUTH_IN);
                Serial.printf("[GateController] Phat hien xe den cong ngoai -> Cho Edge AI auth (seq=%u)...\n", currentSeq);
#else
                // Chế độ Offline cục bộ thuần túy
                if (parkingFull) {
                    lastEvent = GATE_EVENT_BLOCKED_FULL;
                    changeState(GATE_STATE_BLOCKED_FULL);
                    Serial.println(F("[GateController] Tu choi xe vao: Bai do xe da DAY!"));
                } else {
                    strncpy(activePlate, "LOCAL", sizeof(activePlate));
                    assignedSlot[0] = '\0';
                    openBarrier();
                    lastEvent = GATE_EVENT_CAR_ENTERING;
                    changeState(GATE_STATE_ENTERING_WAIT_IN);
                    Serial.println(F("[GateController] Local Mode: Mo Barie vao (90 deg)."));
                }
#endif
            } else if (confirmedSensorIn) {
#if ENABLE_WIFI
                // Có Wi-Fi: Chuyển sang chờ nhận diện biển số xe ra
                currentSeq++;
                pendingAuthPublish = true;
                pendingAuthEntering = false;
                authWaitTimestamp = now;
                lastEvent = GATE_EVENT_WAITING_AUTH;
                activePlate[0] = '\0';
                assignedSlot[0] = '\0';
                changeState(GATE_STATE_WAIT_AUTH_OUT);
                Serial.printf("[GateController] Phat hien xe muon ra -> Cho Edge AI auth (seq=%u)...\n", currentSeq);
#else
                strncpy(activePlate, "LOCAL", sizeof(activePlate));
                assignedSlot[0] = '\0';
                openBarrier();
                lastEvent = GATE_EVENT_CAR_EXITING;
                changeState(GATE_STATE_EXITING_WAIT_OUT);
                Serial.println(F("[GateController] Local Mode: Mo Barie ra (90 deg)."));
#endif
            }
            break;

        case GATE_STATE_WAIT_AUTH_IN:
            // Xe đã lùi ra khỏi cảm biến ngoài trước khi hoàn tất xác thực
            if (!confirmedSensorOut) {
                pendingAuthPublish = false;
                lastEvent = GATE_EVENT_NONE;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Xe ngoai cong da lui di -> Huy cho auth, ve IDLE."));
                break;
            }

            // Quá thời gian GATE_AUTH_TIMEOUT_MS (8s) -> Kích hoạt Offline Fallback
            if ((now - authWaitTimestamp) >= GATE_AUTH_TIMEOUT_MS) {
                Serial.printf("[GateController] TIMEOUT 8s Backend khong tra loi (seq=%u) -> Kich hoat Offline Fallback!\n", currentSeq);
                pendingOfflineFallbackLog = true;
                fallbackSeq = currentSeq;
                fallbackEntering = true;

                if (parkingFull) {
                    lastEvent = GATE_EVENT_BLOCKED_FULL;
                    changeState(GATE_STATE_BLOCKED_FULL);
                    Serial.println(F("[GateController] Offline Fallback: Bai da day -> Tu choi!"));
                } else {
                    strncpy(activePlate, "OFFLINE", sizeof(activePlate));
                    assignedSlot[0] = '\0';
                    openBarrier();
                    lastEvent = GATE_EVENT_CAR_ENTERING;
                    changeState(GATE_STATE_ENTERING_WAIT_IN);
                    Serial.println(F("[GateController] Offline Fallback: Tu dong mo Barie cho xe vao."));
                }
            }
            break;

        case GATE_STATE_WAIT_AUTH_OUT:
            // Xe đã lùi lại trong bãi trước khi hoàn tất xác thực
            if (!confirmedSensorIn) {
                pendingAuthPublish = false;
                lastEvent = GATE_EVENT_NONE;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Xe trong cong da lui lai -> Huy cho auth, ve IDLE."));
                break;
            }

            // Quá thời gian GATE_AUTH_TIMEOUT_MS (8s) -> Kích hoạt Offline Fallback
            if ((now - authWaitTimestamp) >= GATE_AUTH_TIMEOUT_MS) {
                Serial.printf("[GateController] TIMEOUT 8s Backend khong tra loi (seq=%u) -> Kich hoat Offline Fallback xe ra!\n", currentSeq);
                pendingOfflineFallbackLog = true;
                fallbackSeq = currentSeq;
                fallbackEntering = false;

                strncpy(activePlate, "OFFLINE", sizeof(activePlate));
                assignedSlot[0] = '\0';
                openBarrier();
                lastEvent = GATE_EVENT_CAR_EXITING;
                changeState(GATE_STATE_EXITING_WAIT_OUT);
                Serial.println(F("[GateController] Offline Fallback: Tu dong mo Barie cho xe ra."));
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
                lastEvent = GATE_EVENT_NONE;
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
                lastEvent = GATE_EVENT_NONE;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Xe da ROI BAI HOAN TAT -> Dong barie (0 deg)."));
            }
            break;

        case GATE_STATE_BLOCKED_FULL:
            // Chờ cho đến khi xe ngoài cổng rời đi (S_OUT nhả về HIGH)
            if (!confirmedSensorOut) {
                lastEvent = GATE_EVENT_NONE;
                changeState(GATE_STATE_IDLE);
                Serial.println(F("[GateController] Xe bi chan da roi di -> Tro ve trang thai IDLE."));
            }
            break;
    }
}

void GateController::handleBackendCommand(uint32_t seq, bool allowOpen, const char* plate, const char* slot) {
    // 1. Trường hợp đặc biệt: Lệnh khẩn cấp từ Web Admin (seq == 0 && allowOpen == true)
    if (seq == 0 && allowOpen) {
        if (plate && plate[0] != '\0') {
            strncpy(activePlate, plate, sizeof(activePlate) - 1);
            activePlate[sizeof(activePlate) - 1] = '\0';
        } else {
            strncpy(activePlate, "ADMIN_OPEN", sizeof(activePlate));
        }
        assignedSlot[0] = '\0';
        openBarrier();
        lastEvent = GATE_EVENT_CAR_ENTERING;
        changeState(GATE_STATE_ENTERING_WAIT_IN);
        Serial.println(F("[GateController] Nhận lệnh KHẨN CẤP/ADMIN: Mở Barie ngay lập tức!"));
        return;
    }

    // 2. Xử lý xe vào: Đang ở GATE_STATE_WAIT_AUTH_IN
    if (currentState == GATE_STATE_WAIT_AUTH_IN && seq == currentSeq) {
        if (plate && plate[0] != '\0') {
            strncpy(activePlate, plate, sizeof(activePlate) - 1);
            activePlate[sizeof(activePlate) - 1] = '\0';
        }
        if (slot && slot[0] != '\0') {
            strncpy(assignedSlot, slot, sizeof(assignedSlot) - 1);
            assignedSlot[sizeof(assignedSlot) - 1] = '\0';
        } else {
            assignedSlot[0] = '\0';
        }

        if (allowOpen) {
            openBarrier();
            lastEvent = GATE_EVENT_CAR_ENTERING;
            changeState(GATE_STATE_ENTERING_WAIT_IN);
            Serial.printf("[GateController] Backend CHO PHEP VAO (seq=%u, Plate=%s, Slot=%s) -> Mo Barie!\n",
                          seq, activePlate, assignedSlot);
        } else {
            lastEvent = GATE_EVENT_BLOCKED_FULL;
            changeState(GATE_STATE_BLOCKED_FULL);
            Serial.printf("[GateController] Backend TU CHOI VAO (seq=%u, Plate=%s) -> Giu Barie dong!\n",
                          seq, activePlate);
        }
        return;
    }

    // 3. Xử lý xe ra: Đang ở GATE_STATE_WAIT_AUTH_OUT
    if (currentState == GATE_STATE_WAIT_AUTH_OUT && seq == currentSeq) {
        if (plate && plate[0] != '\0') {
            strncpy(activePlate, plate, sizeof(activePlate) - 1);
            activePlate[sizeof(activePlate) - 1] = '\0';
        }

        if (allowOpen) {
            openBarrier();
            lastEvent = GATE_EVENT_CAR_EXITING;
            changeState(GATE_STATE_EXITING_WAIT_OUT);
            Serial.printf("[GateController] Backend CHO PHEP RA (seq=%u, Plate=%s) -> Mo Barie!\n",
                          seq, activePlate);
        } else {
            lastEvent = GATE_EVENT_BLOCKED_FULL;
            changeState(GATE_STATE_BLOCKED_FULL);
            Serial.printf("[GateController] Backend TU CHOI RA (seq=%u, Plate=%s) -> Giu Barie dong!\n",
                          seq, activePlate);
        }
        return;
    }

    Serial.printf("[GateController] Bo qua command khong khop: seq=%u (current=%u, state=%s)\n",
                  seq, currentSeq, getStateName());
}

bool GateController::consumePendingAuthRequest(uint32_t& outSeq, const char*& outDirection) {
    if (pendingAuthPublish) {
        pendingAuthPublish = false;
        outSeq = currentSeq;
        outDirection = pendingAuthEntering ? "IN" : "OUT";
        return true;
    }
    return false;
}

bool GateController::consumeOfflineFallbackEvent(uint32_t& outSeq, bool& outEntering) {
    if (pendingOfflineFallbackLog) {
        pendingOfflineFallbackLog = false;
        outSeq = fallbackSeq;
        outEntering = fallbackEntering;
        return true;
    }
    return false;
}

bool GateController::isBarrierOpen() const {
    return barrierOpen;
}

GateEvent GateController::getLastEvent() const {
    return lastEvent;
}

GateState GateController::getState() const {
    return currentState;
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
        case GATE_STATE_WAIT_AUTH_IN:     return "WAIT_AUTH_IN";
        case GATE_STATE_WAIT_AUTH_OUT:    return "WAIT_AUTH_OUT";
        case GATE_STATE_ENTERING_WAIT_IN: return "ENTERING_WAIT_IN";
        case GATE_STATE_ENTERING_PASSING: return "ENTERING_PASSING";
        case GATE_STATE_EXITING_WAIT_OUT: return "EXITING_WAIT_OUT";
        case GATE_STATE_EXITING_PASSING:  return "EXITING_PASSING";
        case GATE_STATE_BLOCKED_FULL:     return "BLOCKED_FULL";
        default:                          return "UNKNOWN";
    }
}

const char* GateController::getActivePlate() const {
    return activePlate;
}

const char* GateController::getAssignedSlot() const {
    return assignedSlot;
}

uint32_t GateController::getCurrentSeq() const {
    return currentSeq;
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
