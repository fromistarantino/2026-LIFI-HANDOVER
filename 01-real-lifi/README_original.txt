
설명 : UART는 단순 PC to 보드 시리얼통신, 광통신은 LiFi. 1kbps를 사용한 완전 초기 버전
사용 방법 :
C++ lifi.ino로 핸드오버 전용 tx, verilog reallifi로 rx 켜기. 
http_server.py로 백업서버 켜기, lifi_handover1.py로 작동

1.Python : lifi_handover1.py 로 실행
2.Python : http_server.py 로 백업서버

3.C++ : lifi.ino
4.Verilog : realifi1


(Debug : ber_test.py 로 ber 디버깅)
(Debug : lifi_download1.py 에서 단순 다운로드 확인)


pip install requests : 라이브러리 설치
pip install pyserial : 시리얼 통신 설치
