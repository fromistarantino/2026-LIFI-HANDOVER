# 03 · ESP32 펌웨어 개발 이력

광 통신을 처음 성립시키는 과정에서 겪은 문제와 해결을 순서대로 남긴 기록입니다. 최종 코드는 [01](../01-real-lifi/)과 [02](../02-uart-over-light/)에 있습니다.

| # | 파일 | 광 속도 | LED / FPGA_RX 핀 | 이 단계에서 해결한 것 |
|---|---|---|---|---|
| 01 | `01_test1_100bps.ino` | 100 bps | 27 / — | 최초 점등. 배너는 "1Kbps"지만 실제는 100 bps |
| 02 | `02_test2_1kbps.ino` | 1,000 | 27 / — | 회로 주석 기록 (GPIO27 → 100 Ω → 게이트, 10 kΩ 풀다운, LED 직렬 100 Ω) |
| 03 | `03_test3_bidirectional.ino` | 1,000 | 27 / 26 | **첫 양방향 성공.** FPGA → ESP32 9,600 bps |
| 04 | `04_tx_rx_text_10kbps.ino` | 10,000 | 27 / 26 | 한 글자씩 송수신 대조, 200 ms 타임아웃 |
| 05 | `05_lifitx1_wire115200.ino` | 10,000 | 27 / 26 | 와이어 속도를 9,600 → 115,200으로 상향 |
| 06 | `06_lifitx1_1_bigbuffer.ino` | 10,000 | 27 / 26 | 8 KB 버퍼, 비블로킹 송신, 손실 통계 |
| 07 | `07_lifi_transfer2_filemode.ino` | 10,000 | 27 / 26 | `:FILE <크기>` 모드 도입 — 텍스트에서 파일로 |
| 08 | `08_lifi_transfer3_pinswap_drain.ino` | 10,000 | **26 / 27** | 핀 교체 + `TX_DRAIN_MS` 200 ms |
| 09 | `09_lifi_transfer_UART1.ino` | 10,000 | 26 / 27 | 버퍼 1 KB, 타임아웃 300 → 100 ms |
| 10 | `10_lifitx3_magicbyte.ino` | 10,000 | 26 / 27 | **매직 바이트 끊김 감지 50 ms.** UART 계열 최종 |
| 11 | `11_lifi_tx3_bridge.ino` | — | 없음 | **USB-UART 브리지.** Real LiFi 최종 |

`10_lifitx3_magicbyte.ino`는 [02 · UART over Light](../02-uart-over-light/)의 `uartlifi1_esp32.ino`와 동일한 파일입니다.
`11_lifi_tx3_bridge.ino`는 [01 · Real LiFi](../01-real-lifi/)의 `lifi_tx3.ino`와 동일합니다.

---

## 단계별로 해결한 실제 문제

### 마지막 바이트가 사라지는 문제 (07 → 08)

ESP32의 `Serial2.write()`는 FIFO에 넣고 즉시 반환합니다. 10,000 bps에서 한 바이트는 1 ms인데, 프로그램이 곧바로 다음 단계로 넘어가면 **마지막 몇 바이트가 LED로 나가기 전에 상황이 끝나버립니다.**

`Serial2.flush()`로 송신 완료를 기다리고, 추가로 `TX_DRAIN_MS` 200 ms를 더 대기해 FPGA 응답까지 회수하도록 수정했습니다.

### 속도 비대칭으로 인한 수신 유실 (05 → 06)

광 송신은 10,000 bps로 느리고 와이어 수신은 115,200 bps로 빠릅니다. 송신을 블로킹으로 기다리면 그 사이 들어오는 응답이 ESP32의 기본 RX 버퍼(256 B)를 넘겨 유실됩니다.

```cpp
for (size_t i = 0; i < s.length(); i++) {
    while (Serial2.availableForWrite() < 1) {
        readSerial1();      // 송신 대기 중에도 수신 처리
    }
    Serial2.write(s[i]);
    readSerial1();
}
```

송신 대기 틈마다 수신을 처리하도록 바꾸고 `setRxBufferSize(8192)`로 버퍼도 확장했습니다.

---

## ⚠ 주의

- **08번부터 LED 핀과 FPGA 수신 핀이 서로 바뀝니다** (27/26 → 26/27). 배선을 그대로 두고 이전 버전을 플래시하면 ESP32가 FPGA 출력 핀을 구동하고 MOSFET 게이트를 입력으로 듣게 되어 핀 충돌이 발생합니다.
- `02_test2_1kbps.ino`는 `setup()` 닫는 중괄호 뒤에 `~~`가 붙어 있어 **현재 상태로는 컴파일되지 않습니다.** 기록 보존을 위해 원본 그대로 두었습니다.
- 초기 파일들은 배너에 출력하는 속도와 실제 `#define` 값이 어긋나는 경우가 있습니다 (01번: 배너 "1Kbps" / 실제 100 bps, 03번: 배너 "TX 100bps" / 실제 1,000 bps).
