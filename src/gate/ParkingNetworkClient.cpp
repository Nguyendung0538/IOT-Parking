#include "ParkingNetworkClient.h"

ParkingNetworkClient* ParkingNetworkClient::instance = nullptr;

ParkingNetworkClient::ParkingNetworkClient()
    : mqttClient(wifiClient),
      parkingRef(nullptr),
      gateRef(nullptr),
      lastMqttReconnectAttempt(0),
      lastHeartbeatTime(0),
      prevBarrierOpen(false),
      offlineQueueCount(0),
      offlineQueueHead(0),
      offlineQueueTail(0) {
    instance = this;
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        prevOccupied[i] = false;
        prevReserved[i] = false;
    }
}

void ParkingNetworkClient::begin(ParkingMonitor* parkingPtr, GateController* gatePtr) {
    parkingRef = parkingPtr;
    gateRef = gatePtr;

#if ENABLE_WIFI
    Serial.printf("[ParkingNetworkClient] Dang ket noi Wi-Fi: %s ...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    mqttClient.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
    mqttClient.setBufferSize(MQTT_BUFFER_SIZE);
    mqttClient.setCallback(mqttCallback);

    Serial.printf("[ParkingNetworkClient] MQTT Broker cau hinh: %s:%d\n", MQTT_BROKER_HOST, MQTT_BROKER_PORT);
#else
    Serial.println(F("[ParkingNetworkClient] ENABLE_WIFI = false. Chay che do Offline noi bo."));
#endif
}

void ParkingNetworkClient::mqttCallback(char* topic, byte* payload, unsigned int length) {
    if (instance) {
        instance->handleIncomingMessage(topic, payload, length);
    }
}

void ParkingNetworkClient::handleIncomingMessage(char* topic, byte* payload, unsigned int length) {
    Serial.printf("[MQTT RX] Topic: %s (len=%u)\n", topic, length);

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("[MQTT RX] Loi parse JSON: %s\n", err.c_str());
        return;
    }

    // 1. Nhận cập nhật mặt nạ đặt chỗ từ Cloud
    if (strcmp(topic, TOPIC_RESERVED) == 0) {
        bool a1 = doc["A1"] | false;
        bool a2 = doc["A2"] | false;
        bool b1 = doc["B1"] | false;
        bool b2 = doc["B2"] | false;

        Serial.printf("[MQTT RX] Nhan Reserved Mask: A1=%d, A2=%d, B1=%d, B2=%d\n", a1, a2, b1, b2);
        if (parkingRef) {
            bool changed = parkingRef->setReservedMask(a1, a2, b1, b2);
            if (changed && gateRef) {
                publishState(*parkingRef, *gateRef);
            }
        }
        return;
    }

    // 2. Nhận lệnh điều khiển cổng từ Edge Arbiter / Web Admin
    if (strcmp(topic, TOPIC_GATE_COMMAND) == 0) {
        uint32_t seq = doc["seq"] | 0;
        const char* action = doc["action"] | "DENY";
        const char* plate = doc["plate"] | "";
        const char* assignedSlot = doc["assignedSlot"] | "";

        bool allowOpen = (strcmp(action, "OPEN") == 0);
        Serial.printf("[MQTT RX] Nhan Command: seq=%u, action=%s, plate=%s, slot=%s\n",
                      seq, action, plate, assignedSlot);

        if (gateRef) {
            gateRef->handleBackendCommand(seq, allowOpen, plate, assignedSlot);
        }
        return;
    }
}

void ParkingNetworkClient::reconnectMqtt() {
#if ENABLE_WIFI
    if (WiFi.status() != WL_CONNECTED) {
        return;
    }

    if (mqttClient.connected()) {
        return;
    }

    unsigned long now = millis();
    if (now - lastMqttReconnectAttempt < MQTT_RECONNECT_MS) {
        return;
    }
    lastMqttReconnectAttempt = now;

    Serial.printf("[ParkingNetworkClient] Dang ket noi lai MQTT Broker (%s:%d)... ", MQTT_BROKER_HOST, MQTT_BROKER_PORT);

    // LWT: Nếu ESP32 Gate ngắt kết nối đột ngột, Broker tự publish "offline" (retained = true)
    bool connected = mqttClient.connect(
        MQTT_CLIENT_ID,
        MQTT_USER_GATE,
        MQTT_PASS_GATE,
        TOPIC_GATE_STATUS,
        1,      // QoS
        true,   // Retained
        "offline"
    );

    if (connected) {
        Serial.println(F("THANH CONG!"));

        // Báo online retained = true
        mqttClient.publish(TOPIC_GATE_STATUS, "online", true);

        // Đăng ký nhận lệnh cổng và mặt nạ đặt chỗ
        mqttClient.subscribe(TOPIC_GATE_COMMAND, 1);
        mqttClient.subscribe(TOPIC_RESERVED, 1);
        Serial.printf("[ParkingNetworkClient] Da subscribe: %s & %s\n", TOPIC_GATE_COMMAND, TOPIC_RESERVED);

        // Gửi ngay trạng thái toàn bãi
        if (parkingRef && gateRef) {
            publishState(*parkingRef, *gateRef);
        }

        // Xả hàng đợi các sự kiện mở offline trong thời gian mất mạng
        flushOfflineQueue();
    } else {
        Serial.printf("THAT BAI, rc=%d. Se thu lai sau 5s.\n", mqttClient.state());
    }
#endif
}

void ParkingNetworkClient::enqueueOfflineEvent(uint32_t seq, bool entering) {
    if (offlineQueueCount >= OFFLINE_QUEUE_CAPACITY) {
        // Hàng đợi đầy: ghi đè phần tử cũ nhất
        offlineQueueHead = (offlineQueueHead + 1) % OFFLINE_QUEUE_CAPACITY;
        offlineQueueCount--;
    }

    offlineQueue[offlineQueueTail].seq = seq;
    offlineQueue[offlineQueueTail].entering = entering;
    offlineQueue[offlineQueueTail].timestamp = millis();

    offlineQueueTail = (offlineQueueTail + 1) % OFFLINE_QUEUE_CAPACITY;
    offlineQueueCount++;

    Serial.printf("[ParkingNetworkClient] Da luu Offline Event vao Queue: seq=%u (Queue size=%u)\n",
                  seq, offlineQueueCount);
}

void ParkingNetworkClient::flushOfflineQueue() {
#if ENABLE_WIFI
    if (!mqttClient.connected() || offlineQueueCount == 0) {
        return;
    }

    Serial.printf("[ParkingNetworkClient] Dang dong bo %u Offline Events len Backend...\n", offlineQueueCount);

    while (offlineQueueCount > 0) {
        OfflineFallbackRecord rec = offlineQueue[offlineQueueHead];
        offlineQueueHead = (offlineQueueHead + 1) % OFFLINE_QUEUE_CAPACITY;
        offlineQueueCount--;

        JsonDocument doc;
        doc["deviceId"] = DEVICE_ID;
        doc["seq"] = rec.seq;
        doc["direction"] = rec.entering ? "IN" : "OUT";
        doc["offlineFallback"] = true;
        doc["clientTimestamp"] = rec.timestamp;

        String payload;
        serializeJson(doc, payload);
        mqttClient.publish(TOPIC_GATE_REQUEST, payload.c_str(), false);
        Serial.printf("[ParkingNetworkClient] Da dong bo offline seq=%u len %s\n", rec.seq, TOPIC_GATE_REQUEST);
    }
#endif
}

void ParkingNetworkClient::update(ParkingMonitor& parking, GateController& gate) {
#if ENABLE_WIFI
    // 1. Kiểm tra & duy trì kết nối Wi-Fi / MQTT
    if (WiFi.status() == WL_CONNECTED) {
        if (!mqttClient.connected()) {
            reconnectMqtt();
        } else {
            mqttClient.loop();
        }
    }

    // 2. Xử lý yêu cầu xác thực cổng mới từ GateController
    uint32_t reqSeq = 0;
    const char* reqDir = nullptr;
    if (gate.consumePendingAuthRequest(reqSeq, reqDir)) {
        publishGateRequest(reqSeq, reqDir, parking.getTotalAvailable());
    }

    // 3. Xử lý sự kiện Offline Fallback nếu có
    uint32_t fbSeq = 0;
    bool fbEntering = false;
    if (gate.consumeOfflineFallbackEvent(fbSeq, fbEntering)) {
        enqueueOfflineEvent(fbSeq, fbEntering);
        if (mqttClient.connected()) {
            flushOfflineQueue();
        }
    }

    // 4. Kiểm tra biến động trạng thái (cảm biến xe, đặt chỗ, barrier) hoặc heartbeat 30s
    unsigned long now = millis();
    bool stateChanged = false;

    if (gate.isBarrierOpen() != prevBarrierOpen) {
        stateChanged = true;
        prevBarrierOpen = gate.isBarrierOpen();
    }

    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        if (parking.isOccupied(i) != prevOccupied[i] ||
            parking.isReserved(i) != prevReserved[i]) {
            stateChanged = true;
            prevOccupied[i] = parking.isOccupied(i);
            prevReserved[i] = parking.isReserved(i);
        }
    }

    bool heartbeatDue = (now - lastHeartbeatTime >= 30000);

    if ((stateChanged || heartbeatDue) && mqttClient.connected()) {
        publishState(parking, gate);
    }
#else
    (void)parking;
    (void)gate;
#endif
}

bool ParkingNetworkClient::isConnected() {
#if ENABLE_WIFI
    return (WiFi.status() == WL_CONNECTED && mqttClient.connected());
#else
    return false;
#endif
}

void ParkingNetworkClient::publishState(const ParkingMonitor& parking, const GateController& gate) {
#if ENABLE_WIFI
    if (!mqttClient.connected()) {
        return;
    }

    lastHeartbeatTime = millis();

    JsonDocument doc;
    doc["deviceId"] = DEVICE_ID;
    doc["uptime"] = millis() / 1000;
    doc["freeSlots"] = parking.getTotalAvailable();
    doc["totalSlots"] = NUM_SLOTS;
    doc["barrierOpen"] = gate.isBarrierOpen();
    doc["gateState"] = gate.getStateName();

    JsonObject slotsObj = doc["slots"].to<JsonObject>();
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        JsonObject s = slotsObj[parking.getSlotName(i)].to<JsonObject>();
        s["occupied"] = parking.isOccupied(i);
        s["reserved"] = parking.isReserved(i);
    }

    JsonObject sumObj = doc["summary"].to<JsonObject>();
    sumObj["zone_A_available"] = parking.getAvailableA();
    sumObj["zone_B_available"] = parking.getAvailableB();
    sumObj["total_available"] = parking.getTotalAvailable();
    sumObj["total_occupied"] = parking.getOccupiedCount();
    sumObj["total_reserved"] = parking.getReservedCount();

    String payload;
    serializeJson(doc, payload);

    // Gửi retained = true để Edge Server / Web Admin vừa mở lên là có ngay trạng thái mới nhất
    bool ok = mqttClient.publish(TOPIC_STATE, payload.c_str(), true);
    if (ok) {
        Serial.printf("[ParkingNetworkClient] Da publish TOPIC_STATE (len=%u)\n", payload.length());
    } else {
        Serial.println(F("[ParkingNetworkClient] Loi publish TOPIC_STATE!"));
    }
#else
    (void)parking;
    (void)gate;
#endif
}

void ParkingNetworkClient::publishGateRequest(uint32_t seq, const char* direction, uint8_t freeSlots) {
#if ENABLE_WIFI
    if (!mqttClient.connected()) {
        Serial.printf("[ParkingNetworkClient] Khong the gui Gate Request (seq=%u): MQTT chua ket noi!\n", seq);
        return;
    }

    JsonDocument doc;
    doc["deviceId"] = DEVICE_ID;
    doc["seq"] = seq;
    doc["direction"] = direction;
    doc["freeSlots"] = freeSlots;
    doc["timestamp"] = millis();

    String payload;
    serializeJson(doc, payload);

    bool ok = mqttClient.publish(TOPIC_GATE_REQUEST, payload.c_str(), false);
    if (ok) {
        Serial.printf("[ParkingNetworkClient] >> PUBLISH GATE REQUEST: %s\n", payload.c_str());
    } else {
        Serial.printf("[ParkingNetworkClient] Loi publish TOPIC_GATE_REQUEST (seq=%u)!\n", seq);
    }
#else
    (void)seq;
    (void)direction;
    (void)freeSlots;
#endif
}
