"""
LiFi 광 채널 BER 측정 (★ STATUS 검증 포함 - 진짜 BER)

기존 ber_test.py의 문제:
  - STATUS 바이트 무시하고 payload만 비교
  - CRC FAIL이어도 데이터 일치하면 OK로 카운트
  - LSB 버그가 있어도 100%로 표시됨

수정:
  - STATUS=0x01 (OK)일 때만 진짜 OK
  - STATUS=0x02 (CRC FAIL)이면 명확히 FAIL로 표시
  - PHY의 진짜 신뢰성 측정

사용법:
  python ber_test_v2.py COM4
"""
import serial
import time
import sys


def send_once(ser, data, timeout=5.0):
    """
    응답 반환:
      'OK'      : STATUS=0x01, payload 일치
      'CRC_FAIL': STATUS=0x02 (CRC 불일치, 데이터는 받음)
      'DATA_FAIL': STATUS=0x01인데 payload 불일치
      'TIMEOUT' : 응답 없음
    """
    ser.reset_input_buffer()
    ser.write(data)
    ser.flush()
    
    start = time.time()
    recv = bytearray()
    while time.time() - start < timeout:
        if ser.in_waiting:
            recv.extend(ser.read(ser.in_waiting))
        time.sleep(0.02)
        
        if len(recv) >= 4:
            for j in range(len(recv) - 3):
                if recv[j] == 0xFE and recv[j+1] == 0xFE:
                    status = recv[j+2]                   # ★ STATUS 확인
                    length = recv[j+3]
                    if status not in (0x01, 0x02):
                        continue
                    if len(recv) >= j + 4 + length:
                        payload = bytes(recv[j+4 : j+4+length])
                        if status == 0x01:
                            return 'OK' if payload == data else 'DATA_FAIL'
                        else:
                            return 'CRC_FAIL'
    return 'TIMEOUT'


def main():
    if len(sys.argv) < 2:
        print("사용법: python ber_test_v2.py <COM 포트>")
        sys.exit(1)
    
    port = sys.argv[1]
    
    try:
        ser = serial.Serial(port, 115200, timeout=2)
        time.sleep(2)
        ser.reset_input_buffer()
    except Exception as e:
        print(f"포트 열기 실패: {e}")
        sys.exit(1)
    
    print(f"\n{'='*70}")
    print(f"LiFi BER 측정 (★ STATUS 검증 포함)")
    print(f"포트: {port}")
    print(f"{'='*70}")
    print(f"\n구분:")
    print(f"  OK       : STATUS=0x01, 데이터 일치 (진짜 성공)")
    print(f"  CRC_FAIL : STATUS=0x02 (CRC 검증 실패)")
    print(f"  TIMEOUT  : 응답 없음")
    print()
    
    test_data = {
        "1B  b'A'":              b"A",
        "1B  b'\\x00'":           b"\x00",
        "1B  b'\\xFF'":           b"\xFF",
        "1B  b'\\xAA'":           b"\xAA",
        "1B  b'\\x7E'":           b"\x7E",
        "2B  b'AB'":             b"AB",
        "5B  b'HELLO'":          b"HELLO",
        "5B  b'\\x00'*5":         b"\x00" * 5,
        "5B  b'\\xFF'*5":         b"\xFF" * 5,
        "5B  b'\\xAA'*5":         b"\xAA" * 5,
        "5B  b'AAAAA'":          b"AAAAA",
        "5B  b'\\xFE'*5":         b"\xFE" * 5,
        "13B 'LiFi Test 123'":   b"LiFi Test 123",
        "16B 0x00~0x0F":         bytes(range(16)),
        "24B long msg":          b"This is a longer message",
    }
    
    trials = 10
    
    print(f"각 데이터 {trials}회 송신:\n")
    print(f"{'-'*70}")
    print(f"{'데이터':30s} {'OK':>4s} {'CRC':>4s} {'TIM':>4s} {'결과':>15s}")
    print(f"{'-'*70}")
    
    total_ok = 0
    total_crc = 0
    total_timeout = 0
    
    for name, data in test_data.items():
        ok = crc_fail = data_fail = timeout = 0
        
        for i in range(trials):
            result = send_once(ser, data)
            if result == 'OK':
                ok += 1
            elif result == 'CRC_FAIL':
                crc_fail += 1
            elif result == 'DATA_FAIL':
                data_fail += 1
            else:
                timeout += 1
            time.sleep(0.15)
        
        total_ok += ok
        total_crc += crc_fail
        total_timeout += timeout
        
        # 결과 표시
        ok_rate = ok * 100 // trials
        if ok_rate == 100:
            status = "✓ 완벽"
        elif crc_fail >= 5:
            status = "✗ CRC 손상"
        elif ok_rate >= 70:
            status = "△ 불안정"
        else:
            status = "✗ 실패"
        
        print(f"{name:30s} {ok:>4d} {crc_fail:>4d} {timeout:>4d} {status:>15s}")
    
    print(f"{'-'*70}")
    
    total = trials * len(test_data)
    print(f"\n총 결과:")
    print(f"  OK:        {total_ok}/{total} ({total_ok*100/total:.1f}%)")
    print(f"  CRC FAIL:  {total_crc}/{total} ({total_crc*100/total:.1f}%)")
    print(f"  TIMEOUT:   {total_timeout}/{total} ({total_timeout*100/total:.1f}%)")
    
    if total_crc > 0:
        print(f"\n⚠ CRC FAIL이 발생 → PHY에 LSB 버그 가능성")
        print(f"   lifi_tx_fsm.v의 S_IDLE_GAP 확인 필요")
    else:
        print(f"\n✓ PHY 완전 정상")
    
    ser.close()


if __name__ == "__main__":
    main()
