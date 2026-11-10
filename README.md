# LiFi 하드웨어 및 LiFi-WiFi 핸드오버 시스템

> 충북대학교 반도체공학부 종합설계 II (2026)
> 이진양 · 임우성 | 지도교수 노정훈

가시광(LiFi) 채널과 WiFi 채널을 동시에 운용하면서, 광 경로가 차단되면 WiFi가 결손 구간을 자동으로 보충하고 광 경로가 복구되면 다시 LiFi로 되돌아오는 **이중 채널 파일 전송 시스템**입니다. 상용 LiFi 모듈 없이 아날로그 광 송수신 회로, FPGA RTL(PHY·데이터링크), PC 응용 계층을 모두 직접 구현했습니다.

---

## 두 가지 구현

이 저장소에는 **서로 다른 두 가지 구현**이 들어 있습니다. 설정이 완전히 다르므로 섞어서 사용할 수 없습니다.

| | [01 · Real LiFi](01-real-lifi/) | [02 · UART over Light](02-uart-over-light/) |
|---|---|---|
| 개념 | FPGA가 자체 프레임을 만들어 광 송수신 | ESP32 UART 신호를 LED로 그대로 송출 |
| 광 송신 주체 | **FPGA** (Pmod JA2) | **ESP32** (GPIO26) |
| 광 수신 주체 | FPGA (Pmod JA1) | FPGA (Pmod JA1) |
| 광 방향 | 양방향 (루프백) | 단방향 (상행은 전선) |
| 광 비트레이트 | 1,000 bps (`BIT_RATE` 파라미터) | 10,000 bps (UART 8N1) |
| 프레임 구조 | PREAMBLE + SFD + LEN + PAYLOAD + CRC-16 | 없음 (START/STOP 비트) |
| PHY 오류 검출 | **CRC-16/XMODEM** (FPGA 하드웨어) | 없음 |
| FPGA 코드 | 8개 모듈 (703줄) | 단일 모듈 (171줄) |
| ESP32 역할 | USB-UART 브리지만 | 광 송신 + 끊김 감지 |
| 끊김 감지 | Python 타임아웃 2,000 ms | **ESP32 매직 바이트 50 ms** |
| 응용 청크 | 20 B `[POS 4B][DATA 16B]` | 37 B `[SYNC][POS][LEN][DATA 30B][CRC-8]` |
| 표준 참조 | IEEE 802.15.7 PHY-I | — |

### 왜 두 가지인가

초기 구현(02)은 ESP32의 UART 출력을 LED 게이트에 그대로 연결한 방식이었습니다. 동작은 했지만 **프레임 경계와 오류 검출 기구가 없어** 수신부가 데이터의 시작 위치와 길이를 판단할 수 없었고, 사실상 시리얼 케이블을 빛으로 대체한 것에 지나지 않았습니다.

이를 IEEE 802.15.7 PHY-I을 참조한 자체 데이터링크 계층으로 재설계한 것이 01입니다. 프리앰블로 비트 동기를 잡고, SFD로 프레임 시작을 확정하며, 길이 필드와 CRC-16으로 무결성을 PHY에서 보장합니다. **대신 전송 속도는 낮아졌습니다** — 표준성과 신뢰성을 얻고 처리량을 내준 트레이드오프입니다.

두 방식을 동일한 아날로그 하드웨어에서 모두 완성했기 때문에, 수신단 비트 동기 방식의 차이가 성능에 미치는 영향을 직접 비교할 수 있습니다.

---

## 저장소 구성

```
├── 01-real-lifi/            ★ 최종 구현
│   ├── verilog/             Verilog 8개 모듈 + XDC 제약
│   ├── esp32/               USB-UART 브리지 펌웨어
│   └── python/              핸드오버 · HTTP 서버 · 검증 도구
├── 02-uart-over-light/      초기 구현
│   ├── verilog/             단일 모듈 수신기 + XDC
│   ├── esp32/               광 송신 + 매직 바이트 감지
│   └── python/              핸드오버 · HTTP 서버
├── 03-firmware-history/     ESP32 펌웨어 개발 이력 11단계
├── 04-dataset/              핸드오버 예측 모델 학습 데이터셋 (587,000행)
│   ├── 01_raw_frame_log.csv       원시 수신 로그 136,500행
│   ├── 02_training_dataset.csv    특징 11개 + 정답 587,000행
│   ├── 03_label_summary.csv       차광 이벤트 5,434회
│   ├── 04_trace_conditions.csv    시나리오 500개 생성 조건
│   └── export_full.py             데이터 생성 스크립트
└── docs/
    └── hardware-wiring.md   아날로그 회로 · 핀맵 · 부품표
```

---

## 하드웨어

| 구분 | 부품 |
|---|---|
| FPGA 보드 | Digilent Arty Z7-10 (Xilinx Zynq-7010), 125 MHz |
| MCU | ESP32-WROOM-32 |
| 광 송신 | 로직레벨 N-ch MOSFET + LED (직렬 150 Ω) |
| 광 수신 | 포토다이오드 → MCP6022 TIA (R_f 1 MΩ) → 74HC14 슈미트 트리거 |
| 전송 거리 | 5 ~ 30 mm (차광 환경) |

전체 결선도와 핀맵은 **[docs/hardware-wiring.md](docs/hardware-wiring.md)** 를 참조하십시오.

---

## 실행 방법

### 공통 준비

```bash
pip install pyserial requests
```

### 01 · Real LiFi

1. Vivado에서 `01-real-lifi/verilog/` 전체와 `arty_z7.xdc`로 프로젝트 생성 → 비트스트림 생성 → Program Device
2. Arduino IDE에서 `01-real-lifi/esp32/lifi_tx3/lifi_tx3.ino` 업로드
3. 백업 HTTP 서버 실행
   ```bash
   cd 01-real-lifi/python && python http_server.py
   ```
4. 핸드오버 전송 실행 (별도 터미널)
   ```bash
   python lifi_handover1.py <파일경로>
   ```
   `lifi_handover1.py` 상단의 `PORT` 를 실제 COM 포트로 수정해야 합니다.
5. 전송 중 LED와 포토다이오드 사이를 가렸다 풀면 핸드오버가 시연됩니다.

**검증 도구**
- `ber_test_v2.py <COM포트>` — 광 채널 신뢰성 측정. FPGA가 보내는 STATUS 바이트를 확인하여 CRC 통과/실패를 구분 집계합니다.
- `lifi_download1.py <파일경로>` — WiFi 없이 LiFi 단독 전송. ARQ 재전송 포함.

### 02 · UART over Light

1. Vivado에서 `02-uart-over-light/verilog/lifirx4.v` + `lifirx.xdc` 로 비트스트림 생성
2. `02-uart-over-light/esp32/uartlifi1_esp32/uartlifi1_esp32.ino` 업로드
3. `http_server.py` 실행
4. `python handover1.py <파일경로>`

> ⚠ 두 방식은 **MOSFET 게이트 연결 위치가 다릅니다.** 01은 FPGA Pmod JA2, 02는 ESP32 GPIO26입니다. 전환할 때 점퍼선을 옮겨야 합니다.

---

## 핸드오버 알고리즘

| 버전 | 핵심 기법 | 표준 근거 | 감소 대상 |
|---|---|---|---|
| V1 | 고정 300 ms Timeout, 2 KB 단위 요청 | — | 기준 |
| V2 | Adaptive Timeout (EMA, 100~600 ms) | 3GPP TR 36.839 | 판단 지연 |
| V3 | Aggressive Bursting (2→8 KB, Keep-Alive) | TCP BBR | 요청 지연 |
| V4 | Predictive Pre-handover (Make-before-Break) | 3GPP TS 38.331 CHO | 연결 준비 지연 |

상태 머신은 `WARMUP → LIFI_PRIMARY ⇄ DUAL_ACTIVE → COMPLETE` 의 4상태로 구성되며, 수신 위치 마스크(`received_mask`)를 통해 두 채널이 같은 구간을 동시에 전달해도 파일에는 한 번만 기록됩니다.

---

## 참고 문헌

1. IEEE Std 802.11bb-2023, *Wireless LAN MAC and PHY Specifications Amendment 6: Light Communications*, Nov. 2023.
2. IEEE Std 802.15.7-2018, *Short-Range Optical Wireless Communications*, Apr. 2019.
3. 3GPP TS 38.331, *5G NR RRC Protocol Specification*, Rel. 16, V16.4.1, Mar. 2021.
4. 3GPP TR 36.839, *LTE Mobility Enhancements in Heterogeneous Networks*, V11.1.0, Dec. 2012.
5. N. Cardwell et al., *BBR: Congestion-Based Congestion Control*, ACM Queue, vol. 14, no. 5, pp. 20-53, Oct. 2016.
6. R. Fielding and J. Reschke, *HTTP/1.1: Range Requests*, IETF RFC 7233, Jun. 2014.

---

본 작품은 교육부 및 한국산업기술진흥원의 반도체특성화대학 재정지원(P0025682) 사업비를 지원받아 수행하였습니다.
