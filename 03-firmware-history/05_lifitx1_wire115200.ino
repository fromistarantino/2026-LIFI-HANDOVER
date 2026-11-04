#define LED_TX_PIN  27       // ESP32 → MOSFET → LED
#define FPGA_RX_PIN 26       // FPGA → ESP32 (와이어 UART 수신)
#define TX_BAUD    10000     // LiFi 광채널 (FPGA RX_BAUD와 일치)
#define RX_BAUD    115200      // FPGA→ESP32 와이어 (FPGA TX_BAUD와 일치)

void setup() {
    Serial.begin(115200);
    Serial2.begin(TX_BAUD, SERIAL_8N1, -1, LED_TX_PIN, true);
    Serial1.begin(RX_BAUD, SERIAL_8N1, FPGA_RX_PIN, -1);

    Serial.println("===========================================");
    Serial.println("  LiFi 송수신");
    Serial.print("  광채널: ");
    Serial.print(TX_BAUD);
    Serial.println(" bps");
    Serial.print("  와이어: ");
    Serial.print(RX_BAUD);
    Serial.println(" bps");
    Serial.println("===========================================");
    Serial.println("보낼 텍스트 입력 후 엔터:");
}

void loop() {
    // PC → ESP32 → LED 송신
    if (Serial.available()) {
        String s = Serial.readStringUntil('\n');
        s.trim();
        if (s.length() > 0) {
            Serial.print("\n[송신] ");
            Serial.println(s);
            Serial.print("[수신] ");
            Serial2.print(s);
        }
    }

    // FPGA → ESP32 → PC 출력 (실시간 한 글자씩)
    while (Serial1.available()) {
        char c = Serial1.read();
        Serial.write(c);
    }
}