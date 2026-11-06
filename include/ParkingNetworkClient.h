#ifndef PARKING_NETWORK_CLIENT_H
#define PARKING_NETWORK_CLIENT_H

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "config.h"
#include "ParkingMonitor.h"
#include "GateController.h"

struct OfflineFallbackRecord {
    uint32_t seq;
    bool entering;
    unsigned long timestamp;
};

class ParkingNetworkClient {
public:
    ParkingNetworkClient();

    // Khởi tạo Wi-Fi và MQTT Client
    void begin(ParkingMonitor* parkingPtr, GateController* gatePtr);

    // Cập nhật kết nối, xử lý MQTT loop và gửi dữ liệu non-blocking
    void update(ParkingMonitor& parking, GateController& gate);

    // Kiểm tra trạng thái kết nối MQTT
    bool isConnected() const;

    // Gửi bản tin trạng thái toàn bãi (Retained = true)
    void publishState(const ParkingMonitor& parking, const GateController& gate);

    // Gửi yêu cầu xác thực xe vào/ra lên Edge Server
    void publishGateRequest(uint32_t seq, const char* direction, uint8_t freeSlots);

private:
    WiFiClient wifiClient;
    PubSubClient mqttClient;

    ParkingMonitor* parkingRef;
    GateController* gateRef;

    unsigned long lastMqttReconnectAttempt;
    unsigned long lastHeartbeatTime;

    // Bộ đệm lưu trạng thái trước đó để phát hiện thay đổi
    bool prevOccupied[NUM_SLOTS];
    bool prevReserved[NUM_SLOTS];
    bool prevBarrierOpen;

    // Hàng đợi lưu các sự kiện Offline Fallback khi mất mạng
    static const uint8_t OFFLINE_QUEUE_CAPACITY = 8;
    OfflineFallbackRecord offlineQueue[OFFLINE_QUEUE_CAPACITY];
    uint8_t offlineQueueCount;
    uint8_t offlineQueueHead;
    uint8_t offlineQueueTail;

    void reconnectMqtt();
    void flushOfflineQueue();
    void enqueueOfflineEvent(uint32_t seq, bool entering);
    static void mqttCallback(char* topic, byte* payload, unsigned int length);
    void handleIncomingMessage(char* topic, byte* payload, unsigned int length);

    static ParkingNetworkClient* instance;
};

#endif // PARKING_NETWORK_CLIENT_H
