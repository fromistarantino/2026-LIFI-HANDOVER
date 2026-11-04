#핸드오버를 위한 http 서버 불러오기

import http.server
import socketserver
import os
import sys

class RangeHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Range 요청 지원 HTTP 서버 (이어받기 가능)"""
    
    def do_GET(self):
        path = self.translate_path(self.path)
        
        if not os.path.exists(path) or os.path.isdir(path):
            self.send_error(404, "File not found")
            return
        
        file_size = os.path.getsize(path)
        range_header = self.headers.get('Range')
        
        if range_header:
            # 형식: "bytes=START-END" 또는 "bytes=START-"
            try:
                _, range_spec = range_header.split('=')
                start_str, end_str = range_spec.split('-')
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else file_size - 1
                
                if start >= file_size or start > end:
                    self.send_error(416, "Range Not Satisfiable")
                    return
                
                content_length = end - start + 1
                
                self.send_response(206)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(content_length))
                self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                
                with open(path, 'rb') as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk = f.read(min(8192, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                
                print(f"[Range] {os.path.basename(path)}: "
                      f"{start}-{end} ({content_length}바이트) 전송 완료")
                
            except Exception as e:
                self.send_error(400, f"Invalid Range: {e}")
        else:
            # 전체 파일
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(file_size))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            
            with open(path, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            
            print(f"[Full] {os.path.basename(path)}: {file_size}바이트 전송 완료")
    
    def log_message(self, format, *args):
        pass  # 기본 액세스 로그 끄기 (위에서 커스텀 로그 사용)


def run_server(port=8000, directory='.'):
    os.chdir(directory)
    
    print("=" * 60)
    print("LiFi 백업 HTTP 서버 (WiFi 채널)")
    print(f"포트: {port}")
    print(f"디렉토리: {os.path.abspath(directory)}")
    print(f"URL: http://localhost:{port}/")
    print("Range 요청 지원: ✓ (이어받기 가능)")
    print("=" * 60)
    print("Ctrl+C로 종료")
    print()
    
    try:
        with socketserver.TCPServer(("", port), RangeHTTPRequestHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료")
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"✗ 포트 {port}이 이미 사용 중입니다.")
            print(f"  다른 포트 시도: python http_server.py 8001")
        else:
            print(f"✗ 서버 시작 실패: {e}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    run_server(port)