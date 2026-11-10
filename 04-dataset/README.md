# 04 · 핸드오버 예측 모델 학습 데이터셋

광 링크가 곧 끊길지를 예측하는 모델의 학습 데이터다.
차광 시나리오 **500개**를 생성하고, 각 시나리오에서 FPGA 수신 로그를 기록한 뒤
1초 관측 윈도우에서 특징을 추출하였다.

| 항목 | 값 |
|---|---:|
| 시나리오 | 500개 (각 60초) |
| 차광 이벤트 | 5,434회 |
| 원시 프레임 로그 | 136,500행 |
| **학습 샘플** | **587,000행 × 11특징** |
| 양성 비율 | 30.1 % |

---

## 파일

| 파일 | 행 수 | 크기 | 내용 |
|---|---:|---:|---|
| `01_raw_frame_log.csv` | 136,500 | 5.5 MB | FPGA 응답 패킷 단위 원시 수신 로그 |
| `02_training_dataset.csv` | 587,000 | 38.3 MB | 학습에 사용하는 특징 행렬 + 정답 |
| `03_label_summary.csv` | 5,434 | 0.3 MB | 차광 이벤트 목록 (정답 생성 근거) |
| `04_trace_conditions.csv` | 500 | 0.1 MB | 시나리오별 생성 조건 |
| `export_full.py` | — | — | 데이터 생성 스크립트 |

모든 CSV 는 UTF-8 BOM 으로 저장되어 Excel 에서 바로 열린다.

---

## 01_raw_frame_log.csv

FPGA 가 PC 로 보내는 응답 패킷 `[0xFE 0xFE][STATUS][LEN][DATA][MINORITY]` 를
프레임마다 한 행으로 기록한 것이다. 광 채널 1,000 bps 에서 프레임 주기는 216 ms 다.

| 열 | 설명 |
|---|---|
| `trace_id` | 시나리오 식별자 |
| `time_ms` | 수신 시각 |
| `status` | `CRC_OK` 정상 / `CRC_FAIL` 동기는 잡혔으나 오류 / `NO_RESPONSE` 무응답 |
| `status_byte` | 응답 패킷의 STATUS 바이트 원값 |
| `minority_count` | 오버샘플러 3샘플 다수결에서 소수파였던 샘플 수 (0~4). 광 품질 지표 |
| `payload_bytes` | 해당 프레임이 전달한 데이터 바이트 |
| `ms_since_last_ok` | 마지막 정상 수신 이후 경과 시간 |

### 왜 예측이 가능한가

한 차광 이벤트의 앞부분을 보면 구조가 드러난다.

```
3,080 ms  CRC_OK       minority 0      정상
3,300 ms  CRC_OK       minority 2      CRC 는 아직 통과하는데 품질 지표가 먼저 움직임
3,520 ms  NO_RESPONSE  minority 3      그제서야 끊김
```

`minority_count` 가 **CRC 실패보다 220 ms 먼저 반응한다.** 이 구간이 예측에
필요한 선행 시간이며, 모델이 학습하는 것도 이 신호다.

---

## 02_training_dataset.csv

위 로그를 1초 윈도우로 훑으며 50 ms 마다 특징 11개를 뽑은 것이다.

| 열 | 설명 |
|---|---|
| `minority_mean` / `_std` / `_slope` | 윈도우 내 `minority_count` 의 평균·표준편차·기울기 |
| `crc_fail_rate_200ms` / `_500ms` / `_1000ms` | 세 구간의 CRC 실패율 |
| `consecutive_crc_fail` | 연속 CRC 실패 횟수 |
| `ms_since_last_ok` | 마지막 정상 수신 이후 경과 시간 |
| `max_frame_gap_ms` | 윈도우 내 최대 프레임 간격 |
| `window_goodput_B` | 윈도우 동안 전달된 바이트 |
| `no_response_ratio` | 무응답 프레임 비율 |
| `label_blocked_within_300ms` | **정답** — 이후 300 ms 안에 링크가 사용 불가가 되면 1 |

---

## 04_trace_conditions.csv — 차광 조건 그룹

단순히 양만 늘리지 않고, 차광 속도와 지속시간을 네 그룹으로 나누어 폭넓게 변화시켰다.

| 그룹 | 가려짐 | 완전 차광 | 걷힘 | 트레이스 | 이벤트 |
|---|---|---|---|---:|---:|
| `fast` | 60~200 ms | 200~800 ms | 60~200 ms | 125 | 2,094 |
| `normal` | 150~500 ms | 400~1,500 ms | 100~400 ms | 125 | 1,463 |
| `slow` | 400~900 ms | 800~2,500 ms | 300~800 ms | 125 | 1,016 |
| `long` | 100~700 ms | 1,500~4,000 ms | 100~600 ms | 125 | 861 |

주변광 세기도 트레이스마다 0 ~ 25 % 범위에서 변화시켰다 (`ambient_level`).

---

## 기준 성능

`HistGradientBoostingClassifier(max_depth=6, max_iter=300, learning_rate=0.08)`,
트레이스 단위로 400 / 100 분리.

| 지표 | 값 |
|---|---:|
| 정확도 | 91.09 % |
| AUC | 0.9247 |
| 재현율 | 78.32 % |
| 정밀도 | 90.61 % |

혼동행렬 — TN 79,444 / FP 2,848 / FN 7,611 / TP 27,497

### 차광 속도에 따른 예측 난이도

조건 그룹별로 나누어 보면 뚜렷한 경향이 나타난다.

| 그룹 | 차광 속도 | 정확도 | AUC | 재현율 |
|---|---|---:|---:|---:|
| `fast` | 60~200 ms | 86.51 % | 0.8505 | **55.97 %** |
| `normal` | 150~500 ms | 90.49 % | 0.9148 | 74.34 % |
| `slow` | 400~900 ms | 93.41 % | 0.9609 | 86.50 % |
| `long` | 100~700 ms | 93.96 % | 0.9568 | 88.93 % |

**차광이 빠를수록 예측이 급격히 어려워진다.** 원인은 분명하다.
광 채널 1,000 bps 에서 프레임 주기는 216 ms 인데, 60~200 ms 만에 가려지면
열화 과정에서 관측되는 프레임이 **한 개뿐**이다. 판단할 근거 자체가 없다.

따라서 **전송 속도를 올리면 핸드오버 예측 성능도 함께 올라간다.**
10 kbps 로 올리면 프레임 주기가 21.6 ms 가 되어 같은 구간에서 10개를 관측할 수
있다. 복조기 개선과 핸드오버 개선이 서로 독립된 과제가 아니라는 뜻이다.

---

## 사용법

```python
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

df = pd.read_csv('02_training_dataset.csv')
X = df.drop(columns=['trace_id', 'time_ms', 'label_blocked_within_300ms'])
y = df['label_blocked_within_300ms']

# 트레이스 단위로 분리한다.
# 같은 시나리오가 학습과 검증에 섞이면 시간적 누출로 성능이 과대평가된다.
ids = sorted(df.trace_id.unique())
train = df.trace_id.isin(set(ids[:400]))

clf = HistGradientBoostingClassifier(max_depth=6, max_iter=300,
                                     learning_rate=0.08).fit(X[train], y[train])
p = clf.predict_proba(X[~train])[:, 1]
print(clf.score(X[~train], y[~train]), roc_auc_score(y[~train], p))
```

### 데이터 추가 생성

```bash
python export_full.py --traces 1000 --out ./dataset --jobs 4
```

---

## 주의

본 데이터는 **차광 과정을 모사하여 생성한 것**이며 실측 로그가 아니다.

실제 하드웨어에서 수집하려면 `minority_count` 보고 기능을
[`oversampler.v`](../01-real-lifi/verilog/oversampler.v) 에 추가한 뒤
(응답 패킷에 1바이트 추가), 서보 구동 차광 장치로 같은 형식의 로그를 기록하면 된다.
서보 제어 신호의 타임스탬프가 그대로 정답이 되므로 수작업 라벨링이 필요 없다.
