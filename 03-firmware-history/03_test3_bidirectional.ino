#define LED_TX_PIN  27       // ESP32 → MOSFET → LED
#define FPGA_RX_PIN 26       // FPGA → ESP32 (UART 수신)
#define TX_BAUD    1000       // PC → LED 전송 속도
#define RX_BAUD    9600      // FPGA → ESP32 수신 속도


void setup() {
    Serial.begin(115200);                                          // PC
    Serial2.begin(TX_BAUD, SERIAL_8N1, -1, LED_TX_PIN, true);     // LED 송신
    Serial1.begin(RX_BAUD, SERIAL_8N1, FPGA_RX_PIN, -1);          // FPGA 수신

    Serial.println("===========================================");
    Serial.println("  LiFi 양방향 (TX 100bps, RX 9600bps)");
    Serial.println("  보낼 텍스트 입력 후 엔터");
    Serial.println("===========================================");
}

void loop() {
    // PC → ESP32 → LED 송신
    if (Serial.available()) {
        String s = Serial.readStringUntil('\n');
        s.trim();
        if (s.length() > 0) {
            Serial.print("[송신] ");
            Serial.println(s);
            Serial2.print(s);
            Serial2.flush();
        }
    }

    // FPGA → ESP32 → PC 출력
    while (Serial1.available()) {
        char c = Serial1.read();
        Serial.write(c);   // 받은 글자 그대로 출력 (개행 포함)
    }
}