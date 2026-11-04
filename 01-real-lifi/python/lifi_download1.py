"""
LiFi 단독 다운로드 v2 (위치 헤더 분리 버그 수정)
═══════════════════════════════════════════════════════════
v1 버그:
  - received 전체(위치 4B + 데이터)를 buffer에 저장
  - 위치 헤더가 데이터를 덮어써서 Base64 디코딩 실패

v2 수정:
  - received[4:]만 데이터로 저장 (위치 헤더 분리)
  - 위치 검증 추가
"""

import serial
import time
import sys
import os
import hashlib
import base64
import struct


PORT = 'COM4'
BAUD = 115200
CHUNK_SIZE = 16
TIMEOUT = 5.0
MAX_ARQ = 10

STATUS_OK = 0x01
STATUS_FAIL = 0x02


def send_chunk_and_wait(ser, payload, timeout=5.0):
    ser.reset_input_buffer()
    ser.write(payload)
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
                    status = recv[j+2]
                    length = recv[j+3]
                    if status not in (STATUS_OK, STATUS_FAIL):
                        continue
                    if len(recv) >= j + 4 + length:
                        received_payload = bytes(recv[j+4 : j+4+length])
                        return status, received_payload
    
    return None, None


def main():
    if len(sys.argv) < 2:
        print("사용법: python lifi_download_v2.py <파일경로>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    if not os.path.exists(input_file):
        print(f"파일 없음: {input_file}")
        sys.exit(1)
    
    with open(input_file, 'rb') as f:
        original_data = f.read()
    original_size = len(original_data)
    original_md5 = hashlib.md5(original_data).hexdigest()
    basename = os.path.basename(input_file)
    
    encoded_data = base64.b64encode(original_data)
    encoded_size = len(encoded_data)
    num_chunks = (encoded_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    
    print("=" * 70)
    print(f"파일: {input_file}")
    print(f"  원본:    {original_size:,} 바이트")
    print(f"  Base64:  {encoded_size:,} 바이트")
    print(f"  MD5:     {original_md5}")
    print(f"\nLiFi 다운로드 (1 kbps)")
    print(f"  청크:    {num_chunks}개")
    print(f"  페이로드: 4B(위치) + {CHUNK_SIZE}B(데이터) = {4+CHUNK_SIZE}B")
    print(f"\n예상 시간: {num_chunks * 0.4:.0f}초")
    print("=" * 70)
    
    try:
        ser = serial.Serial(PORT, BAUD, timeout=2)
        time.sleep(2)
        ser.reset_input_buffer()
    except Exception as e:
        print(f"포트 열기 실패: {e}")
        sys.exit(1)
    
    received_buffer = bytearray(encoded_size)
    received_mask = bytearray(encoded_size)
    total_arq = 0
    total_crc_fail = 0
    
    start_time = time.time()
    print(f"\n다운로드 시작...\n")
    
    for chunk_idx in range(num_chunks):
        position = chunk_idx * CHUNK_SIZE
        chunk_data = encoded_data[position:position + CHUNK_SIZE]
        payload = struct.pack(">I", position) + chunk_data
        
        success = False
        last_attempt = 0
        
        for attempt in range(1, MAX_ARQ + 1):
            last_attempt = attempt
            status, received = send_chunk_and_wait(ser, payload, timeout=TIMEOUT)
            
            if status == STATUS_OK and received is not None and len(received) >= 4:
                # 위치 4B 분리
                resp_position = struct.unpack(">I", received[:4])[0]
                actual_data = received[4:]
                
                if resp_position != position:
                    print(f"\n위치 불일치: 예상 {position}, 수신 {resp_position}")
                    continue
                
                # 실제 데이터만 저장
                for j, byte in enumerate(actual_data):
                    pos = position + j
                    if pos < encoded_size:
                        received_buffer[pos] = byte
                        received_mask[pos] = 1
                
                success = True
                break
            elif status == STATUS_FAIL:
                total_crc_fail += 1
                total_arq += 1
                continue
            else:
                print(f"\n청크 {chunk_idx} (pos={position}) TIMEOUT")
                if attempt >= 3:
                    break
        
        if not success:
            print(f"\n청크 {chunk_idx} 다운로드 실패")
            ser.close()
            sys.exit(1)
        
        progress = (chunk_idx + 1) * 100 // num_chunks
        elapsed = time.time() - start_time
        bar_len = 30
        filled = (bar_len * (chunk_idx + 1)) // num_chunks
        bar = '#' * filled + '.' * (bar_len - filled)
        
        if last_attempt > 1:
            print(f"  [{bar}] {progress:3d}% ({chunk_idx+1}/{num_chunks}) {elapsed:5.1f}s (ARQ {last_attempt-1}회)")
        else:
            print(f"\r  [{bar}] {progress:3d}% ({chunk_idx+1}/{num_chunks}) {elapsed:5.1f}s", end='', flush=True)
    
    elapsed = time.time() - start_time
    ser.close()
    
    print(f"\n\n{'='*70}")
    print(f"LiFi 다운로드 완료")
    print(f"\n총 다운로드 시간: {elapsed:.2f}초")
    print(f"평균 속도:        {encoded_size/elapsed:.0f} B/s ({encoded_size*8/elapsed:.0f} bps)")
    
    print(f"\nLiFi 통계:")
    print(f"  총 청크:        {num_chunks}")
    print(f"  ARQ 재전송:     {total_arq}회 (CRC FAIL {total_crc_fail}회 자동 복구)")
    arq_rate = total_arq * 100 / num_chunks if num_chunks > 0 else 0
    print(f"  ARQ 비율:       {arq_rate:.1f}%")
    
    missing = sum(1 for m in received_mask if m == 0)
    if missing > 0:
        print(f"\n누락된 바이트: {missing}/{encoded_size}")
    
    encoded_recv = bytes(received_buffer[:encoded_size])
    
    print(f"\n데이터 검증:")
    print(f"  Base64 첫 32B (원본): {encoded_data[:32].decode('ascii', errors='replace')}")
    print(f"  Base64 첫 32B (수신): {encoded_recv[:32].decode('ascii', errors='replace')}")
    print(f"  일치: {'OK' if encoded_recv == encoded_data else 'MISMATCH'}")
    
    if encoded_recv != encoded_data:
        diff_positions = [i for i in range(min(len(encoded_recv), len(encoded_data))) 
                         if encoded_recv[i] != encoded_data[i]]
        print(f"\n  차이 위치 총 {len(diff_positions)}개")
        if diff_positions:
            print(f"  처음 10개: {diff_positions[:10]}")
            for pos in diff_positions[:5]:
                ob = encoded_data[pos]
                rb = encoded_recv[pos]
                oc = chr(ob) if 32 <= ob < 127 else '.'
                rc = chr(rb) if 32 <= rb < 127 else '.'
                print(f"    [{pos}] 원본=0x{ob:02X}('{oc}') 수신=0x{rb:02X}('{rc}')")
    
    try:
        decoded = base64.b64decode(encoded_recv)
    except Exception as e:
        print(f"\nBase64 디코딩 실패: {e}")
        sys.exit(1)
    
    recv_md5 = hashlib.md5(decoded).hexdigest()
    print(f"\n무결성 검증:")
    print(f"  원본 MD5: {original_md5}")
    print(f"  수신 MD5: {recv_md5}")
    
    if recv_md5 == original_md5:
        output = "received_" + basename
        with open(output, 'wb') as f:
            f.write(decoded)
        print(f"\n  MD5 일치! ▶ {output}")
        
        try:
            preview = decoded[:200].decode('utf-8', errors='replace')
            print(f"\n  미리보기:")
            print(f"  ─────────────────────────────────────────")
            for line in preview.split('\n')[:8]:
                print(f"  {line}")
            print(f"  ─────────────────────────────────────────")
        except:
            pass
    else:
        print(f"\n  MD5 불일치")
    
    print("=" * 70)


if __name__ == "__main__":
    main()
