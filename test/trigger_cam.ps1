# Script ban lenh chup anh toi ESP32-CAM qua Mosquitto Docker
param (
    [int]$Seq = 1,
    [string]$Direction = "IN"
)

$payload = "{`"deviceId`":`"ESP32_PARKING_01`",`"seq`":$Seq,`"direction`":`"$Direction`",`"freeSlots`":3}"

Write-Host "[TRIGGER] Dang ban lenh chup toi ESP32-CAM qua Mosquitto..." -ForegroundColor Cyan
Write-Host " -> Topic: parking/ESP32_PARKING_01/gate/request"
Write-Host " -> Payload: $payload"

docker exec parking_mosquitto mosquitto_pub -u esp32_gate_01 -P gate_secret_2026 -t "parking/ESP32_PARKING_01/gate/request" -m "$payload"

if ($LASTEXITCODE -eq 0) {
    Write-Host "[TRIGGER] >> BAN LENH THANH CONG! Quan sat den Flash LED tren ESP32-CAM." -ForegroundColor Green
} else {
    Write-Host "[TRIGGER] >> That bai! Kiem tra container parking_mosquitto co dang chay khong." -ForegroundColor Red
}
