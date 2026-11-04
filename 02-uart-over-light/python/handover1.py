"""
LiFi + WiFi 양방향 핸드오버 파일 전송
══════════════════════════════════════
- Soft Handover (Make-before-Break)
- Hysteresis + Time-to-Trigger
- Timeout-based ARQ
- CRC-8 청크 무결성 + MD5 파일 무결성
- PHY 독립적 추상화
- ESP32 Magic Byte 즉시 핸드오버
- DUAL_ACTIVE에서 pending 비워둠 (ping-pong 완전 차단)
- LIFI_PRIMARY 직후 1초 백업 비활성 (안정화)
- LiFi 청크 수신만으로 즉시 LIFI_PRIMARY 전환
- 양쪽 채널 동시 표시
"""

import serial
import time
import sys
import os
import hashlib
import base64
import threading
import requests
from enum import Enum
from abc import ABC, abstractmethod


# ═══════════════════════════════════════════════════════════
#  설정
# ═══════════════════════════════════════════════════════════

PORT = 'COM7'
BAUD = 115200

TX_RATE = 10000
CHUNK_SIZE = 30
SYNC_BYTE = 0xAA
HEADER_XOR = 0xA5

HTTP_SERVER_URL = "http://localhost:8000"

LIFI_TIMEOUT_MS = 300
RECOVERY_STABLE_MS = 200      # ★ 400 → 200
DEGRADE_TTT_MS = 50
MEASUREMENT_INTERVAL = 0.02
WARMUP_TIMEOUT = 10.0

# LIFI_LOST cooldown만 적용 (LIFI_BACK은 즉시)
HANDOVER_COOLDOWN_MS = 200

# ★ LIFI_PRIMARY 전환 직후 백업 비활성 시간 (핵심!)
LIFI_PRIMARY_STABILIZE_MS = 1000

LIFI_LOST_MAGIC = b'\xFF\xFE\xFD'
LIFI_BACK_MAGIC = b'\xFF\xFE\xFC'


# ═══════════════════════════════════════════════════════════
#  CRC-8
# ═══════════════════════════════════════════════════════════

def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


# ═══════════════════════════════════════════════════════════
#  청크 프로토콜
# ═══════════════════════════════════════════════════════════

def build_chunk(position: int, data: bytes) -> bytes:
    length = len(data)
    header = bytes([
        ((position >> 24) & 0xFF) ^ HEADER_XOR,
        ((position >> 16) & 0xFF) ^ HEADER_XOR,
        ((position >> 8)  & 0xFF) ^ HEADER_XOR,
        ( position        & 0xFF) ^ HEADER_XOR,
        length ^ HEADER_XOR,
    ])
    crc_xored = crc8(header + data) ^ HEADER_XOR
    return bytes([SYNC_BYTE]) + header + data + bytes([crc_xored])


# ═══════════════════════════════════════════════════════════
#  LiFi 물리 계층
# ═══════════════════════════════════════════════════════════

class LiFiChannel(ABC):
    @abstractmethod
    def read_bytes(self) -> bytes: ...
    @abstractmethod
    def write_bytes(self, data: bytes) -> None: ...
    @abstractmethod
    def is_open(self) -> bool: ...
    @abstractmethod
    def close(self) -> None: ...
    
    def init_session(self, total_bytes: int) -> bool:
        return True


class UARTLiFiChannel(LiFiChannel):
    def __init__(self, port: str, baud: int):
        self.port = serial.Serial(port, baud, timeout=1)
        time.sleep(2)
        self.port.reset_input_buffer()
    
    def read_bytes(self) -> bytes:
        return bytes(self.port.read(self.port.in_waiting)) if self.port.in_waiting else b''
    
    def write_bytes(self, data: bytes) -> None:
        self.port.write(data)
    
    def is_open(self) -> bool:
        return self.port.is_open
    
    def close(self) -> None:
        try:
            if self.port.is_open:
                self.port.close()
        except:
            pass
    
    def init_session(self, total_bytes: int) -> bool:
        self.port.write(f":FILE {total_bytes}\n".encode())
        start = time.time()
        while time.time() - start < 5:
            line = self.port.readline().decode(errors='replace').strip()
            if line and "FILE_START" in line:
                print(f"[ESP32] {line}")
                self.port.reset_input_buffer()
                return True
        return False


# ═══════════════════════════════════════════════════════════
#  핸드오버 상태
# ═══════════════════════════════════════════════════════════

class HandoverState(Enum):
    WARMUP = "준비"
    LIFI_PRIMARY = "LiFi 사용 중"
    DUAL_ACTIVE = "핸드 오버(WiFi 사용)"
    COMPLETE = "완료"


# ═══════════════════════════════════════════════════════════
#  핸드오버 매니저
# ═══════════════════════════════════════════════════════════

class HandoverManager:
    def __init__(self, input_file: str, lifi_channel: LiFiChannel):
        self.input_file = input_file
        self.lifi = lifi_channel
        
        self._prepare_file()
        self._init_buffers()
        self._init_state()
        self._init_wifi_session()
        self._init_stats()
        
        self.running = True
        self.start_time = 0
    
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
        
        self.sync_buffer = bytearray()
        self.last_lifi_byte_time = time.time()
        
        self.magic_buffer = bytearray()
    
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
    
    def transition_state(self, new_state: HandoverState, reason: str = ""):
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
    
    # ═══════════════════════════════════════════════════════
    #  Magic Byte 처리
    # ═══════════════════════════════════════════════════════
    
    def _process_magic_bytes(self, data: bytes) -> bytes:
        self.magic_buffer.extend(data)
        buf = self.magic_buffer
        cleaned = bytearray()
        i = 0
        
        while i < len(buf):
            if buf[i] == 0xFF:
                if i + 2 >= len(buf):
                    break
                
                if buf[i+1] == 0xFE:
                    if buf[i+2] == 0xFD:
                        self._on_lifi_lost()
                        i += 3
                        continue
                    elif buf[i+2] == 0xFC:
                        self._on_lifi_back()
                        i += 3
                        continue
            
            cleaned.append(buf[i])
            i += 1
        
        self.magic_buffer = buf[i:]
        return bytes(cleaned)
    
    def _on_lifi_lost(self):
        """LiFi 끊김 → 즉시 WiFi 활성화 (cooldown 적용)"""
        if time.time() - self.state_changed_at < HANDOVER_COOLDOWN_MS / 1000.0:
            return
        
        if self.state == HandoverState.LIFI_PRIMARY:
            with self.pending_lock:
                old_time = time.time() - 10.0
                self.pending_chunks = [(p, old_time) for p, t in self.pending_chunks]
            
            self.transition_state(
                HandoverState.DUAL_ACTIVE,
                "ESP32 LIFI_LOST 즉시 감지"
            )
            self.overdue_since = 0
            self.no_overdue_since = 0
    
    def _on_lifi_back(self):
        """
        LiFi 복구 → 즉시 LIFI_PRIMARY 전환.
        
        ★ Cooldown 없음 (빠른 복귀)
        ★ pending_chunks 완전 비우기
        ★ send_pos를 가장 앞 mask=0 위치로 재설정
        """
        if self.state != HandoverState.DUAL_ACTIVE:
            return
        
        # ★ pending 완전 비움
        with self.pending_lock:
            self.pending_chunks.clear()
        
        # ★ send_pos 재설정
        with self.lock:
            new_send_pos = self.encoded_size
            for i in range(self.encoded_size):
                if not self.mask[i]:
                    new_send_pos = i
                    break
            self.send_pos = new_send_pos
        
        self.transition_state(
            HandoverState.LIFI_PRIMARY,
            "ESP32 LIFI_BACK 즉시 감지"
        )
        self.overdue_since = 0
        self.no_overdue_since = 0
    
    def _write_data(self, position: int, data: bytes, source: str):
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
    
    def _parse_chunks(self):
        results = []
        i = 0
        buf = self.sync_buffer
        
        while i < len(buf):
            if buf[i] != SYNC_BYTE:
                i += 1
                continue
            
            if i + 6 > len(buf):
                break
            
            try:
                p3 = buf[i+1] ^ HEADER_XOR
                p2 = buf[i+2] ^ HEADER_XOR
                p1 = buf[i+3] ^ HEADER_XOR
                p0 = buf[i+4] ^ HEADER_XOR
                length = buf[i+5] ^ HEADER_XOR
                position = (p3 << 24) | (p2 << 16) | (p1 << 8) | p0
            except IndexError:
                i += 1
                continue
            
            if length == 0 or length > 250 or position >= self.encoded_size:
                i += 1
                continue
            
            actual_length = min(length, self.encoded_size - position)
            
            if i + 6 + actual_length + 1 > len(buf):
                break
            
            data = bytes(buf[i+6:i+6+actual_length])
            crc_received = buf[i+6+actual_length] ^ HEADER_XOR
            crc_computed = crc8(bytes(buf[i+1:i+6]) + data)
            
            if crc_computed != crc_received:
                self.crc_fail_count += 1
                i += 1
                continue
            
            results.append((position, data))
            i += 7 + length
        
        self.sync_buffer = self.sync_buffer[i:]
        return results
    
    # ═══════════════════════════════════════════════════════
    #  스레드 1: LiFi 송신
    # ═══════════════════════════════════════════════════════
    
    def lifi_sender_thread(self):
        """
        ★ DUAL_ACTIVE 상태에서는 pending에 추가 안 함
           → WiFi 사용 중 pending이 쌓이지 않음
           → LIFI_BACK 후 ping-pong 완전 방지
        """
        chunk_delay = (7 + CHUNK_SIZE) * 10.0 / TX_RATE * 1.3
        
        while self.running and self.send_pos < self.encoded_size:
            with self.lock:
                while self.send_pos < self.encoded_size and self.mask[self.send_pos]:
                    self.send_pos += 1
            
            if self.send_pos >= self.encoded_size:
                break
            
            chunk_data = self.encoded_data[self.send_pos:self.send_pos + CHUNK_SIZE]
            chunk_bytes = build_chunk(self.send_pos, chunk_data)
            chunk_position = self.send_pos
            
            try:
                self.lifi.write_bytes(chunk_bytes)
            except Exception as e:
                if self.running:
                    print(f"\n송신 오류: {e}")
                break
            
            # ★ DUAL_ACTIVE에서는 pending에 추가 안 함
            if self.state != HandoverState.DUAL_ACTIVE:
                with self.pending_lock:
                    self.pending_chunks.append((chunk_position, time.time()))
            
            self.send_pos += len(chunk_data)
            time.sleep(chunk_delay)
        
        self.send_complete = True
    
    # ═══════════════════════════════════════════════════════
    #  스레드 2: LiFi 수신 (즉시 복귀)
    # ═══════════════════════════════════════════════════════
    
    def lifi_receiver_thread(self):
        """
        ★ DUAL_ACTIVE 상태에서 청크 수신 시 즉시 LIFI_PRIMARY 전환
           → ESP32 BACK magic 안 와도 빠른 복귀
        """
        while self.running and self.received_count < self.encoded_size:
            try:
                data = self.lifi.read_bytes()
                if data:
                    self.last_lifi_byte_time = time.time()
                    cleaned_data = self._process_magic_bytes(data)
                    
                    if cleaned_data:
                        # ★ 청크 데이터 = LiFi 복구 신호
                        if self.state == HandoverState.DUAL_ACTIVE:
                            self._on_lifi_back()
                        
                        self.sync_buffer.extend(cleaned_data)
                        for position, chunk_data in self._parse_chunks():
                            self._write_data(position, chunk_data, "LiFi")
                time.sleep(0.001)
            except Exception as e:
                if self.running:
                    print(f"\n수신 오류: {e}")
                time.sleep(0.1)
    
    # ═══════════════════════════════════════════════════════
    #  스레드 3: WiFi 보충
    # ═══════════════════════════════════════════════════════
    
    def wifi_data_flow_thread(self):
        """
        ★ DUAL_ACTIVE 상태에서 pending 비어도 _find_next_gap으로 동작
        """
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
                # ★ DUAL_ACTIVE에서도 _find_next_gap 사용
                if (self.state == HandoverState.DUAL_ACTIVE 
                    or self.send_complete) and self.received_count < self.encoded_size:
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
    #  스레드 4: 핸드오버 결정 엔진 (백업)
    # ═══════════════════════════════════════════════════════
    
    def handover_decision_engine(self):
        """
        ★ LIFI_PRIMARY 전환 직후 1초 동안 백업 동작 안 함 (안정화)
        """
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
                # ★ LIFI_PRIMARY 전환 직후 1초는 백업 안 함 (ping-pong 핵심 방지)
                if now - self.state_changed_at < LIFI_PRIMARY_STABILIZE_MS / 1000.0:
                    self.overdue_since = 0
                elif overdue_count > 0:
                    if self.overdue_since == 0:
                        self.overdue_since = now
                    elif now - self.overdue_since > DEGRADE_TTT_MS / 1000.0:
                        self.transition_state(
                            HandoverState.DUAL_ACTIVE,
                            f"Timeout 백업: {overdue_count}개 청크 미수신"
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
                            f"Timeout 백업: LiFi 안정화"
                        )
                        self.overdue_since = 0
                else:
                    self.no_overdue_since = 0
            
            time.sleep(MEASUREMENT_INTERVAL)
        
        self.transition_state(HandoverState.COMPLETE, "수신 완료")
    
    # ═══════════════════════════════════════════════════════
    #  스레드 5: 진행률 + 채널 표시
    # ═══════════════════════════════════════════════════════
    
    def progress_thread(self):
        last_count = 0
        last_time = self.start_time
        recent_rate = 0
        
        spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        spinner_idx = 0
        
        while self.running and self.received_count < self.encoded_size:
            now = time.time()
            
            dt = now - last_time
            if dt >= 1.0:
                recent_rate = (self.received_count - last_count) / dt
                last_count = self.received_count
                last_time = now
            
            remaining = self.encoded_size - self.received_count
            if recent_rate > 10:
                eta = remaining / recent_rate
                eta_str = f"{int(eta):3d}s" if eta < 999 else "---s"
            else:
                eta_str = "---s"
            
            progress = self.received_count * 100 // self.encoded_size
            bar_len = 18
            filled = bar_len * self.received_count // self.encoded_size
            bar = '█' * filled + '░' * (bar_len - filled)
            
            spinner = spinner_chars[spinner_idx % len(spinner_chars)]
            spinner_idx += 1
            
            if self.state == HandoverState.WARMUP:
                channel_label = " ⏳ 준비중                              "
            elif self.state == HandoverState.LIFI_PRIMARY:
                channel_label = " [⭕]LiFi / WiFi  (LiFi 연결됨)              "
            elif self.state == HandoverState.DUAL_ACTIVE:
                channel_label = "  LiFi /[⭕]WiFi (LiFi 연결끊김, WiFi 사용중)"
            elif self.state == HandoverState.COMPLETE:
                channel_label = " ✓  다운로드 완료                        "
                spinner = "✓"
            else:
                channel_label = " ?                                       "
            
            with self.pending_lock:
                pending = len(self.pending_chunks)
            
            recent_rate_bps = recent_rate * 8
            if recent_rate_bps >= 1000:
                rate_str = f"{recent_rate_bps/1000:5.1f}kbps"
            else:
                rate_str = f"{recent_rate_bps:5.0f} bps"
            
            print(f"\r{spinner} {channel_label} [{bar}]{progress:3d}% "
                  f"LiFi:{self.lifi_bytes//1024:2d}KB WiFi:{self.wifi_bytes//1024:2d}KB "
                  f"P:{pending:3d} CRC:{self.crc_fail_count:2d} "
                  f"{rate_str} ETA:{eta_str} "
                  f"H/O:{self.handover_count} R:{self.recovery_count}"
                  f"\033[K",
                  end='', flush=True)
            time.sleep(0.05)  # ★ 0.1 → 0.05 (빠른 update)
    
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
        print(f"📁 [파일 정보]")
        print(f"   이름:   {self.basename}")
        print(f"   원본 크기:   {self.original_size:,} 바이트,   인코딩: {self.encoded_size:,} 바이트")
        print(f"   MD5:    {self.original_md5}")
        print()
        print(f"🌐 [서버 정보]")
        print(f"   LiFi 서버:    {self.lifi.__class__.__name__}")
        print(f"   WiFi 서버:    {self.wifi_url} (HTTP Keep-Alive)")
        print()
        print(f"🔁 [핸드오버 정보]")
        print(f"    송신 실패 알림:  50ms ")
        print(f"    Timeout 백업:    {LIFI_TIMEOUT_MS}ms")
        print(f"    복구 확인:       {RECOVERY_STABLE_MS}ms")
        print(f"    LIFI 안정화:     {LIFI_PRIMARY_STABILIZE_MS}ms")
        print(f"    핸드오버 cooldown: {HANDOVER_COOLDOWN_MS}ms")
        print(f"    청크 구조:       6B 헤더 + {CHUNK_SIZE}B 데이터 + 1B CRC = {7+CHUNK_SIZE}B")
        print(f"    CRC:             CRC-8 (다항식 0x07)")
        print("=" * 76)
    
    def _check_wifi(self) -> bool:
        try:
            r = self.wifi_session.head(self.wifi_url, timeout=2)
            if r.status_code != 200:
                print(f"⚠ WiFi 서버 응답: {r.status_code}")
                return False
            print("✓ WiFi 채널 연결 가능 확인됨")
            return True
        except Exception as e:
            print(f"⚠ WiFi 서버 없음: {e}")
            return False
    
    def _start_lifi_session(self) -> bool:
        num_chunks = (self.encoded_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        total_send_bytes = sum(
            7 + min(CHUNK_SIZE, self.encoded_size - i * CHUNK_SIZE)
            for i in range(num_chunks)
        )
        print(f"\n파일 모드: {total_send_bytes} 바이트 송신 예정 ({num_chunks} 청크)")
        
        if not self.lifi.init_session(total_send_bytes):
            print("❌ LiFi 세션 시작 실패")
            return False
        
        self.last_lifi_byte_time = time.time()
        print("\n전송 시작 (Ctrl+C로 중단)\n")
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
        elapsed = time.time() - self.start_time
        self.lifi.close()
        
        print(f"\n\n{'='*76}")
        print(f"✓ 수신 완료 ({elapsed:.1f}초)")
        print(f"\n📊 통신 통계:")
        print(f"   전송 시간:     {elapsed:.1f}초")
        print(f"   평균 속도:     {self.encoded_size*8/elapsed:.0f} bps")
        print(f"   LiFi 통신:     {self.lifi_bytes:,} B "
              f"({self.lifi_bytes*100//self.encoded_size}%)")
        print(f"   WiFi 통신:     {self.wifi_bytes:,} B "
              f"({self.wifi_bytes*100//self.encoded_size}%)")
        
        num_chunks = (self.encoded_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        if num_chunks > 0:
            print(f"   CRC 실패율:    {self.crc_fail_count*100/num_chunks:.1f}% "
                  f"({self.crc_fail_count}/{num_chunks} 청크)")
        print(f"   핸드오버:     실행 {self.handover_count}회 → 복구 {self.recovery_count}회")
        
        if self.transition_history:
            print(f"\n📊 상태 전이 이력:")
            for t, old, new, reason in self.transition_history:
                print(f"   {old:15s} → {new:15s}  [{reason}]")
        
        self._verify_and_save()
        print("=" * 76)
    
    def _verify_and_save(self):
        encoded = bytes(self.buffer)
        while len(encoded) % 4 != 0:
            encoded += b'='
        
        try:
            decoded = base64.b64decode(encoded, validate=False)
        except:
            decoded = b''
        
        recv_md5 = hashlib.md5(decoded).hexdigest() if decoded else "(none)"
        print(f"\n원본 MD5: {self.original_md5}")
        print(f"수신 MD5: {recv_md5}")
        
        if recv_md5 == self.original_md5:
            output = "received_" + self.basename
            with open(output, 'wb') as f:
                f.write(decoded)
            print(f"✅ MD5 일치")
            print()
            print(f"다운로드 완료 ▶ {output}")
            return
        
        print(f"\n⚠ MD5 불일치 → WiFi 전체 재다운로드 시도")
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
                    print(f"\n✅ 자동 복구 성공")
                    print()
                    print(f"다운로드 완료 ▶ {output}")
                else:
                    print(f"❌ 재다운로드도 불일치")
        except Exception as e:
            print(f"❌ 복구 실패: {e}")


# ═══════════════════════════════════════════════════════════
#  진입점
# ═══════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(f"사용법: python {sys.argv[0]} <파일명>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    if not os.path.exists(input_file):
        print(f"❌ 파일 없음: {input_file}")
        sys.exit(1)
    
    try:
        lifi = UARTLiFiChannel(PORT, BAUD)
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