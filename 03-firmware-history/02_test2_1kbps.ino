// =============================================================================
// LiFi 송신부 - ESP32 + LED + MOSFET
// 속도: 1Kbps, UART 8N1, invert=true (대기 중 LED OFF)
// 회로: GPIO 27 → 100Ω → MOSFET Gate (10kΩ 풀다운)
//       5V → 100Ω → LED → MOSFET Drain → GND
// =============================================================================

#define LED_TX_PIN 27
#define BAUD_RATE  1000

void setup() {
    Serial.begin(115200);
    Serial2.begin(BAUD_RATE, SERIAL_8N1, -1, LED_TX_PIN, true);

    Serial.println("======================================");
    Serial.print("  LiFi 송신부 준비 완료 (");
    Serial.print(BAUD_RATE);
    Serial.println(" bps)");
    Serial.println("======================================");
    Serial.println("시리얼 모니터에 보낼 텍스트를 입력하세요:");
}~~

void loop() {
    if (!Serial.available()) return;

    // 입력 읽기 + 정리
    String inputData = Serial.readStringUntil('\n');
    inputData.trim();                       // \r, 공백 제거

    if (inputData.length() == 0) return;    // 빈 입력 무시

    // 입력 정보 출력
    Serial.println("--------------------------------------");
    Serial.print("전송: \"");
    Serial.print(inputData);
    Serial.print("\" (");
    Serial.print(inputData.length());
    Serial.println(" 바이트)");

    // HEX 값 디버깅 출력
    Serial.print("HEX:  ");
    for (size_t i = 0; i < inputData.length(); i++) {
        if ((uint8_t)inputData[i] < 0x10) Serial.print("0");
        Serial.print((uint8_t)inputData[i], HEX);
        Serial.print(" ");
    }
    Serial.println();

    // LED로 전송
    Serial2.print(inputData);
    Serial2.print('\n');                    // 패킷 종료 마커
    Serial2.flush();                        // 모든 비트 전송 완료까지 대기

    // 소요 시간 표시
    unsigned long ms = (inputData.length() + 1) * 10000UL / BAUD_RATE;
    Serial.print("전송 완료 (");
    Serial.print(ms);
    Serial.println(" ms)");
}