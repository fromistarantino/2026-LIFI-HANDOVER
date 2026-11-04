/*
 * ESP32 USB-UART Bridge
 * PC ↔ ESP32 ↔ FPGA UART (115200 8N1)
 * 
 * GPIO33 (TX) → FPGA Pmod JB Pin 1 (W14)
 * GPIO32 (RX) ← FPGA Pmod JB Pin 2 (Y14)
 * GND        ↔ FPGA GND
 */

#define UART_RX_PIN 32
#define UART_TX_PIN 33

void setup() {
    Serial.begin(115200);
    Serial2.begin(115200, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
}

void loop() {
    while (Serial.available()) Serial2.write(Serial.read());
    while (Serial2.available()) Serial.write(Serial2.read());
}