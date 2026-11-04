#lifi_handover_v3.py 대신 reallifi_handover1.py 임.

"""#
LiFi + WiFi 양방향 핸드오버 파일 전송 (새 LiFi PHY 버전)
═════════════════════════════════════════════════════════
- LiFi PHY: OOK + NRZ @ 1 kbps (FPGA 자체 프레임 처리)
- L2 프레임: PREAMBLE 0xAAAA + SFD 0x7E + LEN + DATA + CRC-16 (FPGA)
- 청크 페이로드: [위치(4B)][데이터(0~60B)]  (응용 계층)
- WiFi 보충: HTTP Range
- 시간 측정: 총 다운로드 시간 + 채널별 상태 시간
"""

import serial
import time
import sys
import os
import hashlib
import base64
import struct
import threading
import requests
from enum import Enum
from abc import ABC, abstractmethod


# ═══════════════════════════════════════════════════════════
#  설정
# ═══════════════════════════════════════════════════════════

# 하드웨어
PORT = 'COM4'
BAUD = 115200

# LiFi 채널 (1 kbps - 회로 한계로 인한 보수적 속도)
TX_RATE = 1000                  # 광 비트레이트 (bps)
CHUNK_SIZE = 16                # 청크 데이터 (페이로드 4B 위치 + 60B = 64B = PHY max)

# WiFi 채널
HTTP_SERVER_URL = "http://localhost:8000"

# 핸드오버 정책 (1 kbps에 맞게 조정)
LIFI_TIMEOUT_MS = 2000          # 청크 미수신 → WiFi 트리거
RECOVERY_STABLE_MS = 1500       # LiFi 복구 확인 시간 (Hysteresis 위쪽)
DEGRADE_TTT_MS = 200            # 열화 확정 시간 (Time-to-Trigger)
MEASUREMENT_INTERVAL = 0.05     # 결정 엔진 주기 (20Hz)
WARMUP_TIMEOUT = 15.0           # 첫 LiFi 응답 대기 (1 kbps라 더 길게)

# LiFi PHY 응답 헤더
RESPONSE_HEADER = b'\xFE\xFE'
STATUS_OK = 0x01
STATUS_FAIL = 0x02


# ═══════════════════════════════════════════════════════════
#  LiFi PHY 채널 추상화
# ═══════════════════════════════════════════════════════════

class LiFiChannel(ABC):
    @abstractmethod
    def read_responses(self): pass
    @abstractmethod
    def write_payload(self, data): pass
    @abstractmethod
    def is_open(self): pass
    @abstractmethod
    def close(self): pass


class LiFiPHYChannel(LiFiChannel):
    """
    LiFi PHY 채널 (FPGA가 자체 프레임 처리)
      - write_payload: 페이로드 송신 (FPGA가 PREAMBLE/SFD/CRC 추가)
      - read_responses: [0xFE 0xFE][STATUS][LEN][DATA] 형식 응답 파싱
    """
    
    def __init__(self, port, baud=115200):
        self.port = serial.Serial(port, baud, timeout=1)
        time.sleep(2)
        self.port.reset_input_buffer()
        self.rx_buffer = bytearray()
    
    def write_payload(self, data):
        """페이로드 송신 - FPGA가 LiFi 프레임 자동 구성"""
        self.port.write(data)
        self.port.flush()
    
    def read_responses(self):
        """수신 버퍼에서 모든 응답 파싱 → [(status, data), ...]"""
        if self.port.in_waiting:
            self.rx_buffer.extend(self.port.read(self.port.in_waiting))
        
        results = []
        i = 0
        buf = self.rx_buffer
        
        while i + 4 <= len(buf):
            # HEADER 찾기: 0xFE 0xFE
            if buf[i] != 0xFE or buf[i+1] != 0xFE:
                i += 1
                continue
            
            status = buf[i+2]
            length = buf[i+3]
            
            if status not in (STATUS_OK, STATUS_FAIL):
                i += 1
                continue
            
            # DATA 전체 받았나?
            if i + 4 + length > len(buf):
                break
            
            data = bytes(buf[i+4 : i+4+length])
            results.append((status, data))
            i += 4 + length
        
        self.rx_buffer = self.rx_buffer[i:]
        return results
    
    def is_open(self):
        return self.port.is_open
    
    def close(self):
        try:
            if self.port.is_open:
                self.port.close()
        except:
            pass


# ═══════════════════════════════════════════════════════════
#  청크 페이로드 인코딩 (응용 계층)
#  구조: [P3 P2 P1 P0][DATA(0~60B)]
#    - LiFi PHY가 자체 CRC-16 처리 → 응용 CRC 불필요
# ═══════════════════════════════════════════════════════════

def build_chunk_payload(position, data):
    """청크 페이로드 = 4B 위치(big-endian) + 데이터"""
    return struct.pack(">I", position) + data


def parse_chunk_payload(data):
    """페이로드 → (position, chunk_data) | None"""
    if len(data) < 4:
        return None
    position = struct.unpack(">I", data[:4])[0]
    return position, data[4:]


# ═══════════════════════════════════════════════════════════
#  핸드오버 상태
# ═══════════════════════════════════════════════════════════

class HandoverState(Enum):
    WARMUP = "워밍업"
    LIFI_PRIMARY = "LiFi 사용 중"
    DUAL_ACTIVE = "이중 활성 (WiFi 보조)"
    COMPLETE = "완료"


# ═══════════════════════════════════════════════════════════
#  핸드오버 매니저
#
#  5개 스레드 협력:
#    1. Sender   : LiFi 청크 송신 + 점프
#    2. Receiver : LiFi 응답 수신 + 청크 파싱
#    3. WiFi     : Timeout 청크만 HTTP Range로 보충
#    4. Decision : Overdue 통계 → 상태 전이
#    5. Progress : 진행률/속도/ETA + 스피너 애니메이션
# ═══════════════════════════════════════════════════════════

class HandoverManager:
    def __init__(self, input_file, lifi_channel):
        self.input_file = input_file
        self.lifi = lifi_channel
        
        self._prepare_file()
        self._init_buffers()
        self._init_state()
        self._init_wifi_session()
        self._init_stats()
        
        self.running = True
        self.start_time = 0
    
    # ─── 초기화 헬퍼 ─────────────────────────────────────
    
    def _prepare_file(self):
        with open(self.input_file, 'rb') as f:
            self.original_data = f.read()
        self.original_size = len(self.original_data)
        self.original_md5 = hashlib.md5(self.original_data).hexdigest()
        self.basename = os.path.basename(self.input_file)
        
        self.encoded_data = base64.b64encode(self.original_data)
        self.encoded_size = len(self.encoded_data)
        self.encoded_filename = self.basename + '.b64'
        
        with open(self.encoded_filename, 'wb') as f:
            f.write(self.encoded_data)
    
    def _init_buffers(self):
        self.buffer = bytearray(self.encoded_size)
        self.mask = bytearray(self.encoded_size)
        self.received_count = 0
        self.lock = threading.Lock()
        
        self.send_pos = 0
        self.send_complete = False
        
        self.pending_chunks = []
        self.pending_lock = threading.Lock()
        
        self.last_lifi_byte_time = time.time()
    
    def _init_state(self):
        self.state = HandoverState.WARMUP
        self.state_lock = threading.Lock()
        self.state_changed_at = time.time()
        self.transition_history = []
        self.warmup_done = False
        self.overdue_since = 0
        self.no_overdue_since = 0
    
    def _init_wifi_session(self):
        self.wifi_url = f"{HTTP_SERVER_URL}/{self.encoded_filename}"
        self.wifi_session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=2, pool_maxsize=4, max_retries=0
        )
        self.wifi_session.mount('http://', adapter)
        try:
            self.wifi_session.head(self.wifi_url, timeout=2)
        except:
            pass
    
    def _init_stats(self):
        self.lifi_bytes = 0
        self.wifi_bytes = 0
        self.handover_count = 0
        self.recovery_count = 0
        self.crc_fail_count = 0
    
    # ─── 상태 머신 ───────────────────────────────────────
    
    def transition_state(self, new_state, reason=""):
        with self.state_lock:
            if self.state == new_state:
                return
            
            now = time.time()
            old_state = self.state
            self.state = new_state
            self.state_changed_at = now
            self.transition_history.append((now, old_state.value, new_state.value, reason))
            
            if new_state == HandoverState.DUAL_ACTIVE:
                self.handover_count += 1
            elif new_state == HandoverState.LIFI_PRIMARY and old_state == HandoverState.DUAL_ACTIVE:
                self.recovery_count += 1
    
    # ─── 버퍼 조작 ───────────────────────────────────────
    
    def _write_data(self, position, data, source):
        with self.lock:
            new_bytes = 0
            for j, byte in enumerate(data):
                pos = position + j
                if pos < self.encoded_size and not self.mask[pos]:
                    self.buffer[pos] = byte
                    self.mask[pos] = 1
                    self.received_count += 1
                    new_bytes += 1
            
            if source == "LiFi":
                self.lifi_bytes += new_bytes
            else:
                self.wifi_bytes += new_bytes
    
    def _find_next_gap(self):
        with self.lock:
            for i in range(self.encoded_size):
                if not self.mask[i]:
                    gap_end = i
                    for j in range(i, min(i + 8192, self.encoded_size)):
                        if self.mask[j]:
                            break
                        gap_end = j
                    return i, gap_end
            return None, None
    
    # ═══════════════════════════════════════════════════════
    #  스레드 1: LiFi 송신 (Sender 점프)
    # ═══════════════════════════════════════════════════════
    
    def lifi_sender_thread(self):
        """LiFi 청크 송신. WiFi가 채운 영역은 자동 스킵."""
        # 1 kbps에서 청크당 시간:
        #   프레임 = PREAMBLE(16) + SFD(8) + LEN(8) + DATA*8 + CRC(16) + IDLE(8) bits
        #   1 kbps + EOF 100ms + 여유 50ms
        chunk_delay = ((CHUNK_SIZE + 4) * 8 + 56) / TX_RATE + 0.30
        
        while self.running and self.send_pos < self.encoded_size:
            with self.lock:
                while self.send_pos < self.encoded_size and self.mask[self.send_pos]:
                    self.send_pos += 1
            
            if self.send_pos >= self.encoded_size:
                break
            
            chunk_data = self.encoded_data[self.send_pos:self.send_pos + CHUNK_SIZE]
            position = self.send_pos
            payload = build_chunk_payload(position, chunk_data)
            
            try:
                self.lifi.write_payload(payload)
            except Exception as e:
                if self.running:
                    print(f"\n송신 오류: {e}")
                break
            
            with self.pending_lock:
                self.pending_chunks.append((position, time.time()))
            
            self.send_pos += len(chunk_data)
            time.sleep(chunk_delay)
        
        self.send_complete = True
    
    # ═══════════════════════════════════════════════════════
    #  스레드 2: LiFi 수신 (응답 파싱)
    # ═══════════════════════════════════════════════════════
    
    def lifi_receiver_thread(self):
        """LiFi PHY 응답 수신 → STATUS 확인 → 위치 파싱 → 버퍼 쓰기"""
        while self.running and self.received_count < self.encoded_size:
            try:
                for status, payload in self.lifi.read_responses():
                    self.last_lifi_byte_time = time.time()
                    
                    if status == STATUS_OK:
                        result = parse_chunk_payload(payload)
                        if result is not None:
                            position, chunk_data = result
                            if chunk_data:
                                self._write_data(position, chunk_data, "LiFi")
                    elif status == STATUS_FAIL:
                        # CRC FAIL → 무시 (WiFi가 timeout으로 보충)
                        self.crc_fail_count += 1
                
                time.sleep(0.005)
            except Exception as e:
                if self.running:
                    print(f"\n수신 오류: {e}")
                time.sleep(0.1)
    
    # ═══════════════════════════════════════════════════════
    #  스레드 3: WiFi 보충 (Timeout 기반)
    # ═══════════════════════════════════════════════════════
    
    def wifi_data_flow_thread(self):
        while self.running and self.received_count < self.encoded_size:
            now = time.time()
            target_pos = None
            
            with self.pending_lock:
                self.pending_chunks = [
                    (p, t) for p, t in self.pending_chunks
                    if p < self.encoded_size and not self.mask[p]
                ]
                for pos, t in self.pending_chunks:
                    if now - t > LIFI_TIMEOUT_MS / 1000.0:
                        target_pos = pos
                        break
            
            if target_pos is None:
                if self.send_complete and self.received_count < self.encoded_size:
                    target_pos, _ = self._find_next_gap()
                    if target_pos is None:
                        break
                else:
                    time.sleep(0.05)
                    continue
            
            with self.lock:
                gap_start = target_pos
                gap_end = gap_start
                for j in range(gap_start, min(gap_start + 2048, self.encoded_size)):
                    if self.mask[j]:
                        break
                    gap_end = j
            
            if gap_end < gap_start:
                continue
            
            try:
                response = self.wifi_session.get(
                    self.wifi_url,
                    headers={'Range': f'bytes={gap_start}-{gap_end}'},
                    stream=True, timeout=3
                )
                if response.status_code in [200, 206]:
                    pos = gap_start
                    for chunk in response.iter_content(chunk_size=512):
                        if not self.running:
                            response.close()
                            return
                        if chunk:
                            self._write_data(pos, chunk, "WiFi")
                            pos += len(chunk)
                response.close()
            except requests.exceptions.Timeout:
                pass
            except Exception:
                time.sleep(0.1)
    
    # ═══════════════════════════════════════════════════════
    #  스레드 4: 핸드오버 결정 엔진
    # ═══════════════════════════════════════════════════════
    
    def handover_decision_engine(self):
        warmup_start = time.time()
        while self.running and self.lifi_bytes == 0:
            self.last_lifi_byte_time = time.time()
            if time.time() - warmup_start > WARMUP_TIMEOUT:
                break
            time.sleep(0.05)
        
        self.warmup_done = True
        self.transition_state(HandoverState.LIFI_PRIMARY, "워밍업 완료")
        time.sleep(0.2)
        
        while self.running and self.received_count < self.encoded_size:
            now = time.time()
            
            with self.pending_lock:
                overdue_count = sum(
                    1 for p, t in self.pending_chunks
                    if p < self.encoded_size and not self.mask[p]
                    and now - t > LIFI_TIMEOUT_MS / 1000.0
                )
            
            if self.state == HandoverState.LIFI_PRIMARY:
                if overdue_count > 0:
                    if self.overdue_since == 0:
                        self.overdue_since = now
                    elif now - self.overdue_since > DEGRADE_TTT_MS / 1000.0:
                        self.transition_state(
                            HandoverState.DUAL_ACTIVE,
                            f"LiFi {overdue_count}개 청크 미수신 (>{LIFI_TIMEOUT_MS}ms)"
                        )
                        self.no_overdue_since = 0
                else:
                    self.overdue_since = 0
            
            elif self.state == HandoverState.DUAL_ACTIVE:
                if overdue_count == 0:
                    if self.no_overdue_since == 0:
                        self.no_overdue_since = now
                    elif now - self.no_overdue_since > RECOVERY_STABLE_MS / 1000.0:
                        self.transition_state(
                            HandoverState.LIFI_PRIMARY,
                            f"LiFi 안정화 ({RECOVERY_STABLE_MS}ms 정상)"
                        )
                        self.overdue_since = 0
                else:
                    self.no_overdue_since = 0
            
            time.sleep(MEASUREMENT_INTERVAL)
        
        self.transition_state(HandoverState.COMPLETE, "수신 완료")
    
    # ═══════════════════════════════════════════════════════
    #  스레드 5: 진행률 + 스피너
    # ═══════════════════════════════════════════════════════
    
    def progress_thread(self):
        UPDATE_INTERVAL = 0.05
        spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        partial_chars = ['▏', '▎', '▍', '▌', '▋', '▊', '▉']
        
        last_count = 0
        last_time = self.start_time
        recent_rate = 0
        anim_idx = 0
        
        while self.running and self.received_count < self.encoded_size:
            now = time.time()
            
            dt = now - last_time
            if dt >= 1.0:
                recent_rate = (self.received_count - last_count) / dt
                last_count = self.received_count
                last_time = now
            
            remaining = self.encoded_size - self.received_count
            if recent_rate > 1:
                eta = remaining / recent_rate
                eta_str = f"{int(eta):3d}s" if eta < 999 else "---s"
            else:
                eta_str = "---s"
            
            bar_len = 18
            eighths = (bar_len * 8 * self.received_count) // self.encoded_size
            full_blocks = eighths // 8
            partial = eighths % 8
            
            if full_blocks >= bar_len:
                bar = '█' * bar_len
            elif partial == 0:
                bar = '█' * full_blocks + '░' * (bar_len - full_blocks)
            else:
                bar = ('█' * full_blocks
                       + partial_chars[partial - 1]
                       + '░' * (bar_len - full_blocks - 1))
            
            progress = self.received_count * 100 // self.encoded_size
            spinner = spinner_chars[anim_idx % len(spinner_chars)]
            anim_idx += 1
            
            channel_label = {
                HandoverState.WARMUP:        "⏳ 워밍업",
                HandoverState.LIFI_PRIMARY:  "💡 LiFi ",
                HandoverState.DUAL_ACTIVE:   "📶 WiFi ",
                HandoverState.COMPLETE:      "✓  완료 ",
            }.get(self.state, "?       ")
            
            with self.pending_lock:
                pending = len(self.pending_chunks)
            
            rate_str = (f"{recent_rate/1024:5.2f}KB/s"
                       if recent_rate >= 1024
                       else f"{recent_rate:5.0f} B/s")
            
            elapsed = now - self.start_time
            
            print(f"\r{spinner} {channel_label} [{bar}] {progress:3d}% "
                  f"L:{self.lifi_bytes//1024:2d}KB W:{self.wifi_bytes//1024:2d}KB "
                  f"P:{pending:3d} CRC:{self.crc_fail_count:2d} "
                  f"{rate_str} ETA:{eta_str} "
                  f"H/O:{self.handover_count}R:{self.recovery_count} "
                  f"T:{elapsed:5.1f}s   ",
                  end='', flush=True)
            time.sleep(UPDATE_INTERVAL)
        
        if self.received_count >= self.encoded_size:
            bar = '█' * 18
            elapsed = time.time() - self.start_time
            print(f"\r✓ ✓  완료  [{bar}] 100% "
                  f"L:{self.lifi_bytes//1024:2d}KB W:{self.wifi_bytes//1024:2d}KB "
                  f"P:  0 CRC:{self.crc_fail_count:2d} "
                  f"          ETA:  0s "
                  f"H/O:{self.handover_count}R:{self.recovery_count} "
                  f"T:{elapsed:5.1f}s   ",
                  end='', flush=True)
    
    # ═══════════════════════════════════════════════════════
    #  상태별 누적 시간 계산
    # ═══════════════════════════════════════════════════════
    
    def _calculate_state_durations(self):
        """각 상태별 누적 시간 (초)"""
        durations = {state.value: 0.0 for state in HandoverState}
        
        if not self.transition_history:
            durations[HandoverState.WARMUP.value] = time.time() - self.start_time
            return durations
        
        prev_time = self.start_time
        
        for trans_time, old_state, new_state, reason in self.transition_history:
            durations[old_state] += trans_time - prev_time
            prev_time = trans_time
        
        last_state = self.transition_history[-1][2]
        durations[last_state] += time.time() - prev_time
        
        return durations
    
    # ═══════════════════════════════════════════════════════
    #  메인 실행
    # ═══════════════════════════════════════════════════════
    
    def run(self):
        self._print_header()
        if not self._check_wifi():
            return
        if not self._start_lifi_session():
            return
        self._run_threads()
        self._finalize()
    
    def _print_header(self):
        print("=" * 76)
        print(f"📁 파일: {self.input_file}")
        print(f"   원본:    {self.original_size:,} 바이트")
        print(f"   Base64:  {self.encoded_size:,} 바이트")
        print(f"   MD5:     {self.original_md5}")
        print(f"💡 LiFi PHY: OOK + NRZ @ {TX_RATE} bps")
        print(f"   L2:      PREAMBLE 0xAAAA + SFD 0x7E + LEN + DATA + CRC-16 (FPGA 처리)")
        print(f"   응용:    [위치(4B)][데이터({CHUNK_SIZE}B)] = {4+CHUNK_SIZE}B/청크")
        print(f"📶 WiFi:     {self.wifi_url} (HTTP Keep-Alive)")
        print(f"⚙ 핸드오버:")
        print(f"   LiFi 타임아웃:  {LIFI_TIMEOUT_MS}ms (이후 WiFi 보충)")
        print(f"   복구 확인:      {RECOVERY_STABLE_MS}ms (LiFi 안정화 후)")
        print("=" * 76)
    
    def _check_wifi(self):
        try:
            r = self.wifi_session.head(self.wifi_url, timeout=2)
            if r.status_code != 200:
                print(f"⚠ WiFi 서버 응답: {r.status_code}")
                return False
            print("✓ WiFi 채널 사전 연결 완료")
            return True
        except Exception as e:
            print(f"⚠ WiFi 서버 없음: {e}")
            print("   먼저 다른 터미널에서 실행: python -m http.server 8000")
            return False
    
    def _start_lifi_session(self):
        num_chunks = (self.encoded_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        print(f"\n전송 계획: {num_chunks}개 청크")
        
        # 예상 시간 계산
        chunk_time = ((CHUNK_SIZE + 4) * 8 + 56) / TX_RATE + 0.15
        est_lifi_only = num_chunks * chunk_time
        print(f"   LiFi 단독 시 예상 시간: {est_lifi_only:.0f}초 (약 {est_lifi_only/60:.1f}분)")
        
        self.last_lifi_byte_time = time.time()
        print("\n전송 시작 (LED 가렸다 풀어서 핸드오버 시연, Ctrl+C로 중단)\n")
        self.start_time = time.time()
        return True
    
    def _run_threads(self):
        threads = [
            threading.Thread(target=self.lifi_sender_thread, daemon=True, name="Sender"),
            threading.Thread(target=self.lifi_receiver_thread, daemon=True, name="Receiver"),
            threading.Thread(target=self.wifi_data_flow_thread, daemon=True, name="WiFi"),
            threading.Thread(target=self.handover_decision_engine, daemon=True, name="Decision"),
            threading.Thread(target=self.progress_thread, daemon=True, name="Progress"),
        ]
        for t in threads:
            t.start()
        
        try:
            while self.received_count < self.encoded_size:
                time.sleep(0.2)
        except KeyboardInterrupt:
            self._handle_interrupt()
            sys.exit(0)
        
        self.running = False
        time.sleep(0.5)
    
    def _handle_interrupt(self):
        print("\n\n⚠ 사용자 중단 (Ctrl+C)")
        progress = self.received_count * 100 // self.encoded_size
        print(f"   진행: {progress}% ({self.received_count:,}/{self.encoded_size:,} B)")
        print(f"   LiFi: {self.lifi_bytes:,} B, WiFi: {self.wifi_bytes:,} B")
        print(f"   핸드오버: {self.handover_count}회, 복구: {self.recovery_count}회")
        self.running = False
        time.sleep(0.3)
        self.lifi.close()
    
    def _finalize(self):
        """완료 통계 + MD5 검증"""
        elapsed = time.time() - self.start_time
        self.lifi.close()
        
        print(f"\n\n{'='*76}")
        print(f"✓ 수신 완료")
        print(f"\n⏱ 시간 측정:")
        print(f"   ★ 총 다운로드 시간: {elapsed:.2f}초  ({elapsed/60:.2f}분)")
        
        durations = self._calculate_state_durations()
        lifi_time = durations[HandoverState.LIFI_PRIMARY.value]
        wifi_time = durations[HandoverState.DUAL_ACTIVE.value]
        warmup_time = durations[HandoverState.WARMUP.value]
        
        print(f"\n   상태별 시간:")
        if warmup_time > 0.01:
            print(f"     워밍업:        {warmup_time:5.2f}초")
        if lifi_time > 0.01:
            pct = lifi_time*100/elapsed
            print(f"     LiFi 단독:     {lifi_time:5.2f}초 ({pct:.0f}%)")
        if wifi_time > 0.01:
            pct = wifi_time*100/elapsed
            print(f"     WiFi 보조:     {wifi_time:5.2f}초 ({pct:.0f}%)")
        
        print(f"\n📊 데이터 통계:")
        print(f"   평균 속도:     {self.encoded_size/elapsed:.0f} B/s ({self.encoded_size*8/elapsed:.0f} bps)")
        print(f"   LiFi 분담:     {self.lifi_bytes:,} B ({self.lifi_bytes*100//max(self.encoded_size,1)}%)")
        print(f"   WiFi 분담:     {self.wifi_bytes:,} B ({self.wifi_bytes*100//max(self.encoded_size,1)}%)")
        
        num_chunks = (self.encoded_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        if num_chunks > 0:
            print(f"   CRC 실패 청크: {self.crc_fail_count}회 / {num_chunks} 청크")
        print(f"   핸드오버:      {self.handover_count}회 → 복구 {self.recovery_count}회")
        
        if self.transition_history:
            print(f"\n📊 상태 전이 이력:")
            for t, old, new, reason in self.transition_history:
                rel_t = t - self.start_time
                print(f"   [{rel_t:6.2f}s] {old:25s} → {new:25s}")
                print(f"            ({reason})")
        
        self._verify_and_save()
        print("=" * 76)
    
    def _verify_and_save(self):
        """MD5 확인 → 파일 저장"""
        encoded = bytes(self.buffer)
        while len(encoded) % 4 != 0:
            encoded += b'='
        
        try:
            decoded = base64.b64decode(encoded, validate=False)
        except:
            decoded = b''
        
        recv_md5 = hashlib.md5(decoded).hexdigest() if decoded else "(none)"
        print(f"\n📋 무결성 검증:")
        print(f"   원본 MD5: {self.original_md5}")
        print(f"   수신 MD5: {recv_md5}")
        
        if recv_md5 == self.original_md5:
            output = "received_" + self.basename
            with open(output, 'wb') as f:
                f.write(decoded)
            print(f"\n   ✅ MD5 일치! ▶ {output}")
            return
        
        print(f"\n   ⚠ MD5 불일치 → WiFi 전체 재다운로드 시도")
        try:
            r = self.wifi_session.get(self.wifi_url, timeout=30)
            if r.status_code == 200:
                full = r.content
                while len(full) % 4 != 0:
                    full += b'='
                full_decoded = base64.b64decode(full)
                if hashlib.md5(full_decoded).hexdigest() == self.original_md5:
                    output = "received_" + self.basename
                    with open(output, 'wb') as f:
                        f.write(full_decoded)
                    print(f"   ✅ 자동 복구 성공 ▶ {output}")
                else:
                    print(f"   ❌ 재다운로드도 불일치")
        except Exception as e:
            print(f"   ❌ 복구 실패: {e}")


# ═══════════════════════════════════════════════════════════
#  진입점
# ═══════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("사용법: python lifi_handover_new.py <파일경로>")
        print("예시:  python lifi_handover_new.py myfile.txt")
        print()
        print("준비:")
        print("  1. Vivado: lifi_top.v BIT_RATE = 1_000 확인 후 Program Device")
        print("  2. 다른 터미널: python -m http.server 8000")
        print("  3. 같은 폴더에 myfile.txt 있어야 함")
        sys.exit(1)
    
    input_file = sys.argv[1]
    if not os.path.exists(input_file):
        print(f"❌ 파일 없음: {input_file}")
        sys.exit(1)
    
    try:
        lifi = LiFiPHYChannel(PORT, BAUD)
    except serial.SerialException as e:
        print(f"❌ 포트 열기 실패: {e}")
        print(f"   확인: 다른 프로그램이 {PORT}를 사용 중이거나 ESP32 미연결")
        sys.exit(1)
    
    try:
        HandoverManager(input_file, lifi).run()
    except KeyboardInterrupt:
        try:
            lifi.close()
        except:
            pass


if __name__ == "__main__":
    main()
