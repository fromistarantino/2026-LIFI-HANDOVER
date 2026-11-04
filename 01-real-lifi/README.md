# 01 · Real LiFi (최종 구현)

FPGA가 자체 L2 프레임을 생성·검증하며 광 채널의 송수신을 모두 담당합니다. ESP32는 광 경로에서 완전히 제거되고 USB-UART 브리지 역할만 합니다.

## 구성

```
PC (Python) ──USB──► ESP32 (브리지) ──UART 115,200──► FPGA ──┬──► LED  ──광──┐
                                                              │              │
                                                              └◄── 74HC14 ◄── PD
```

루프백 구조입니다. PC가 보낸 청크가 FPGA → 광 → FPGA → PC로 왕복하며, 그 성공 여부로 광 채널의 신뢰성을 측정하고 결과를 파일로 재구성합니다. 보드 한 장으로 송수신을 모두 검증하는 방식입니다.

## Verilog 모듈 (703줄)

| 모듈 | 줄 | 역할 |
|---|---|---|
| `lifi_top.v` | 253 | 최상위 통합, 파워온 리셋, PC 명령 수집, 응답 패킷 생성 |
| `lifi_tx_fsm.v` | 117 | 8상태 송신 FSM — 프레임 조립 및 NRZ 비트 송출 |
| `lifi_rx_fsm.v` | 104 | 6상태 수신 FSM — 24비트 시퀀스 검출, 길이 검증, CRC 확인 |
| `uart_rx.v` | 66 | PC 명령 수신 (115,200 bps) |
| `oversampler.v` | 48 | 8배 오버샘플링, 천이 기반 위상 정렬 |
| `uart_tx.v` | 48 | PC 응답 송신 |
| `bit_clock_gen.v` | 42 | 비트 클럭 · 샘플 클럭 분주 |
| `crc16_xmodem.v` | 25 | CRC-16 (다항식 0x1021), 1클럭 8비트 병렬 |

**클럭 도메인**: 125 MHz(시스템) / 8× 샘플 / 1× 비트

비트레이트는 `lifi_top.v`에서 `bit_clock_gen` 인스턴스의 `BIT_RATE` 파라미터로 설정합니다.

## L2 프레임

```
[PREAMBLE 0xAAAA][SFD 0x7E][LEN][PAYLOAD 0~64B][CRC-16][IDLE GAP]
```

- 프리앰블 `0xAAAA`(1010… 교번)로 수신기 위상 정렬
- 수신 FSM이 **24비트 시프트 레지스터로 `0xAAAA7E`를 한 번에 검출**
- 길이 유효성 검사(1 ≤ LEN ≤ 64) — 잡음으로 LEN이 깨졌을 때 수신부가 장시간 잠기는 것을 방지
- CRC-16/XMODEM, 계산 범위는 LEN + PAYLOAD
- 고정 오버헤드 6 B

## FPGA → PC 응답 패킷

```
[0xFE 0xFE][STATUS][LEN][DATA...]
            0x01 = CRC 통과
            0x02 = CRC 실패
```

## Python

| 파일 | 역할 |
|---|---|
| `lifi_handover1.py` | 핸드오버 본체. 5개 스레드(LiFi 송신·LiFi 수신·WiFi 보충·결정 엔진·진행 표시) |
| `http_server.py` | HTTP Range Request(206 Partial Content)를 지원하는 백업 서버 |
| `ber_test_v2.py` | 광 채널 신뢰성 측정. STATUS 바이트를 확인해 CRC 통과/실패를 분리 집계 |
| `lifi_download1.py` | WiFi 없이 LiFi 단독 전송. ARQ 재전송 최대 10회 |

### 응용 계층 청크 (20 B)

```
[POSITION 4B (big-endian)][DATA 16B]
```

PHY가 CRC-16을 처리하므로 응용 계층에는 별도 CRC가 없습니다. 파일 전체 무결성은 MD5로 검증합니다.

### 핸드오버 파라미터

| 상수 | 값 | 의미 |
|---|---|---|
| `LIFI_TIMEOUT_MS` | 2,000 | 청크 미수신 → WiFi 보충 트리거 |
| `RECOVERY_STABLE_MS` | 1,500 | LiFi 복구 확인 시간 |
| `DEGRADE_TTT_MS` | 200 | 열화 확정 시간 (Time-to-Trigger) |
| `MEASUREMENT_INTERVAL` | 0.05 s | 결정 엔진 주기 |

## 실행

```bash
cd python
python http_server.py            # 터미널 1
python lifi_handover1.py FILE    # 터미널 2
```

`lifi_handover1.py` 상단의 `PORT`를 실제 COM 포트로 수정해야 합니다.

## ber_test_v2.py 에 대하여

초기 BER 측정 스크립트는 FPGA의 STATUS 바이트를 무시하고 페이로드만 비교했습니다. 그 결과 CRC가 실패해도 데이터가 우연히 일치하면 성공으로 집계되어 **광 채널 성공률이 100%로 보고**되었습니다. v2에서 STATUS를 확인하도록 수정하면서 송신 FSM의 LSB 처리 결함을 발견했습니다.
