"""
LiFi-WiFi 핸드오버 — 반응형 vs 예측형(AI) 비교 시뮬레이션
══════════════════════════════════════════════════════════════════
모든 타이밍 상수는 01-real-lifi/python/lifi_handover1.py 에서 그대로 가져왔다.

    LIFI_TIMEOUT_MS    = 2000    # 청크 미수신 → WiFi 트리거
    DEGRADE_TTT_MS     = 200     # 열화 확정 시간
    RECOVERY_STABLE_MS = 1500    # LiFi 복구 확인
    MONITOR_MS         = 50      # 결정 엔진 주기
    TX_RATE            = 1000    # 광 비트레이트
    CHUNK_SIZE         = 16      # 청크 데이터 바이트

비교 정책
    P0  반응형 (현재)   overdue 2000 ms + TTT 200 ms 후 전환
    P1  규칙 개선       CRC 연속 실패 N회 → 즉시 전환
    P2  예측형 (AI)     1초 윈도우 특징 → "300 ms 내 링크 사용불가" 확률 → 전환

관측 가능한 신호 (실제 하드웨어에서 얻을 수 있는 것만 사용)
    · 프레임 응답 STATUS      0x01 정상 / 0x02 CRC 실패 / 무응답
    · minority_count          오버샘플러 3샘플 다수결의 소수파 수 (제안한 Verilog 추가분)
"""
import numpy as np, json, os
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

RNG = np.random.default_rng(7)

# ── lifi_handover1.py 상수 ─────────────────────────────────
LIFI_TIMEOUT_MS = 2000
DEGRADE_TTT_MS = 200
RECOVERY_STABLE_MS = 1500
MONITOR_MS = 50
TX_RATE = 1000
CHUNK_SIZE = 16
FRAME_B = CHUNK_SIZE + 4 + 7            # 위치4 + 데이터16 + L2 오버헤드7
FRAME_MS = FRAME_B * 8 / TX_RATE * 1000  # 216 ms/프레임 @1 kbps
WIFI_BPS = 2_000_000

DT = 10                                  # 시뮬레이션 틱 [ms]
HORIZON_MS = 300                         # 예측 지평
Q_OK, Q_FAIL = 0.55, 0.25                # 링크 품질 임계 (정상 / CRC실패 / 침묵)


# ── 차광 이벤트 생성 ───────────────────────────────────────
def make_trace(dur_ms=60_000, n_block=12):
    """링크 품질 q(t) ∈ [0,1] 궤적과 '사용 불가' 구간 생성"""
    n = dur_ms // DT
    q = np.ones(n)
    spans = []
    t = 3000
    for _ in range(n_block):
        t_deg = RNG.uniform(80, 700)      # 가려지는 데 걸리는 시간
        t_blk = RNG.uniform(400, 2500)    # 완전 차광 지속
        t_rec = RNG.uniform(80, 600)      # 걷히는 데 걸리는 시간
        gap = RNG.uniform(2500, 6000)
        i0 = int(t / DT)
        a, b, c = int(t_deg / DT), int(t_blk / DT), int(t_rec / DT)
        if i0 + a + b + c >= n:
            break
        q[i0:i0 + a] = np.linspace(1, 0, a)
        q[i0 + a:i0 + a + b] = 0
        q[i0 + a + b:i0 + a + b + c] = np.linspace(0, 1, c)
        spans.append((i0, i0 + a + b + c))
        t += t_deg + t_blk + t_rec + gap
    q = np.clip(q + RNG.normal(0, 0.02, n), 0, 1)
    unusable = q <= Q_FAIL                # 실제로 데이터가 전혀 안 흐르는 구간
    return q, unusable, spans


# ── 관측 생성 (PC 가 실제로 보는 것) ───────────────────────
def observe(q):
    """프레임 단위 관측: 0=무응답, 1=CRC_OK, 2=CRC_FAIL, 그리고 minority_count"""
    n = len(q)
    kind = np.full(n, -1, np.int8)
    minority = np.zeros(n)
    step = max(1, int(round(FRAME_MS / DT)))
    for i in range(0, n, step):
        qi = q[i]
        # minority_count: 8샘플 중 소수파 개수 ≈ 잡음 지표, q 가 낮을수록 커짐
        m = np.clip(RNG.binomial(8, np.clip(0.42 * (1 - qi) ** 1.3, 0, 0.45)), 0, 4)
        minority[i] = m
        if qi > Q_OK:
            kind[i] = 1 if RNG.random() > 0.004 else 2
        elif qi > Q_FAIL:
            kind[i] = 2 if RNG.random() < 0.65 else 1
        else:
            kind[i] = 0
    return kind, minority


# ── 특징 추출 (1초 윈도우) ─────────────────────────────────
WIN = int(1000 / DT)
FEAT_NAMES = ['minority 평균', 'minority 표준편차', 'minority 기울기',
              'CRC실패율 200ms', 'CRC실패율 500ms', 'CRC실패율 1000ms',
              '연속 CRC실패', '마지막 정상수신 경과', '프레임 간격 최대',
              '윈도우 goodput', '응답없음 비율']


def features(kind, minority, i):
    a = max(0, i - WIN + 1)
    k = kind[a:i + 1]; m = minority[a:i + 1]
    fr = k[k > 0]                                    # 응답이 있었던 프레임
    mm = m[k > 0]
    if len(fr) == 0:
        return np.array([4, 0, 0, 1, 1, 1, 20, 1000, 1000, 0, 1.0], float)

    def failrate(ms):
        b = max(0, i - int(ms / DT) + 1)
        kk = kind[b:i + 1]; f = kk[kk > 0]
        return float(np.mean(f == 2)) if len(f) else 1.0

    slope = 0.0
    if len(mm) >= 3:
        x = np.arange(len(mm))
        slope = float(np.polyfit(x, mm, 1)[0])

    cons = 0
    for v in kind[a:i + 1][::-1]:
        if v == 2:
            cons += 1
        elif v == 1:
            break
    last_ok = 0
    for j in range(i, a - 1, -1):
        if kind[j] == 1:
            break
        last_ok += DT
    idx = np.where(k > 0)[0]
    gapmax = float(np.max(np.diff(idx)) * DT) if len(idx) > 1 else 1000.0
    good = float(np.sum(k == 1) * CHUNK_SIZE)
    slots = k[k >= 0]
    noresp = float(np.mean(slots == 0)) if len(slots) else 1.0
    return np.array([float(np.mean(mm)), float(np.std(mm)), slope,
                     failrate(200), failrate(500), failrate(1000),
                     float(cons), float(last_ok), gapmax, good, noresp], float)


def build_dataset(n_trace=14):
    X, Y = [], []
    for _ in range(n_trace):
        q, un, _ = make_trace()
        kind, mino = observe(q)
        H = int(HORIZON_MS / DT)
        for i in range(WIN, len(q) - H, int(MONITOR_MS / DT)):
            X.append(features(kind, mino, i))
            Y.append(int(un[i:i + H].any()))
    return np.array(X), np.array(Y)


# ── 정책 실행 ──────────────────────────────────────────────
def run_policy(q, un, kind, mino, policy, model=None, thr=0.5, ncons=3):
    """
    반환: 전환 지연 목록, 정체시간, 오전환 수, 전환 수, LiFi/WiFi 바이트
    상태: 0 = LIFI_PRIMARY, 1 = DUAL_ACTIVE
    """
    n = len(q)
    state = 0
    overdue_since = -1
    no_overdue_since = -1
    last_ok_t = 0
    switches, false_sw = 0, 0
    stall_ms = 0
    lifi_b = wifi_b = 0.0
    sw_times, loss_times = [], []
    prev_un = False
    unusable_ms = 0
    mon = int(MONITOR_MS / DT)

    for i in range(n):
        t = i * DT
        if kind[i] == 1:
            last_ok_t = t

        # 실제 링크 사용 불가 시작 시각 기록
        if un[i] and not prev_un:
            loss_times.append(t)
        prev_un = un[i]
        if un[i]:
            unusable_ms += DT

        # 데이터 흐름
        if state == 1:
            wifi_b += WIFI_BPS / 8 * DT / 1000
        elif not un[i]:
            lifi_b += TX_RATE / 8 * DT / 1000 * (CHUNK_SIZE / FRAME_B)
        else:
            stall_ms += DT

        if i % mon:
            continue

        if policy == 'P0':                      # 반응형 — 현재 구현
            overdue = (t - last_ok_t) > LIFI_TIMEOUT_MS
            if state == 0:
                if overdue:
                    if overdue_since < 0:
                        overdue_since = t
                    elif t - overdue_since > DEGRADE_TTT_MS:
                        state = 1; switches += 1; sw_times.append(t)
                        no_overdue_since = -1
                else:
                    overdue_since = -1
            else:
                if not overdue:
                    if no_overdue_since < 0:
                        no_overdue_since = t
                    elif t - no_overdue_since > RECOVERY_STABLE_MS:
                        state = 0; no_overdue_since = -1
                else:
                    no_overdue_since = -1

        elif policy == 'P1':                    # 규칙 개선 — CRC 연속 실패
            a = max(0, i - WIN + 1)
            cons = 0
            for v in kind[a:i + 1][::-1]:
                if v < 0:
                    continue
                if v == 1:
                    break
                cons += 1
            if state == 0 and cons >= ncons:
                state = 1; switches += 1; sw_times.append(t)
                no_overdue_since = -1
            elif state == 1:
                if (t - last_ok_t) < 300:
                    if no_overdue_since < 0:
                        no_overdue_since = t
                    elif t - no_overdue_since > RECOVERY_STABLE_MS:
                        state = 0; no_overdue_since = -1
                else:
                    no_overdue_since = -1

        else:                                   # P2 — 예측형 (AI)
            if i >= WIN:
                p = model.predict_proba(features(kind, mino, i).reshape(1, -1))[0, 1]
            else:
                p = 0.0
            if state == 0 and p >= thr:
                state = 1; switches += 1; sw_times.append(t)
                no_overdue_since = -1
            elif state == 1:
                if p < thr * 0.6 and (t - last_ok_t) < 300:
                    if no_overdue_since < 0:
                        no_overdue_since = t
                    elif t - no_overdue_since > RECOVERY_STABLE_MS:
                        state = 0; no_overdue_since = -1
                else:
                    no_overdue_since = -1

    # 오전환 재정의 — 예측형을 공정하게 평가:
    #   전환 시점에 이미 차단 중이거나, 이후 LOOKAHEAD 안에 차단이 시작되면 '정당한 전환'
    LOOKAHEAD = 600
    false_sw = 0
    for st in sw_times:
        k0 = int(st / DT)
        k1 = min(n, k0 + int(LOOKAHEAD / DT))
        if not un[k0:k1].any():
            false_sw += 1

    # 전환 지연: 각 손실 시작에 대해 가장 가까운 '직전 또는 직후' 전환
    lat = []
    for lt in loss_times:
        cand = [s - lt for s in sw_times if -3000 <= s - lt <= 6000]
        if cand:
            lat.append(min(cand, key=abs))
    return {
        'latency': lat, 'stall_ms': stall_ms, 'switches': switches,
        'false_sw': false_sw, 'lifi_b': lifi_b, 'wifi_b': wifi_b,
        'n_loss': len(loss_times), 'unusable_ms': unusable_ms,
        'sw_times': sw_times, 'loss_times': loss_times,
        'dur_ms': n * DT,
    }


def agg(runs):
    lat = [x for r in runs for x in r['latency']]
    return {
        'lat_mean': float(np.mean(lat)) if lat else None,
        'lat_std': float(np.std(lat)) if lat else None,
        'lat_p90': float(np.percentile(lat, 90)) if lat else None,
        'early_ratio': float(np.mean([x < 0 for x in lat])) if lat else 0.0,
        'stall_ms': float(np.mean([r['stall_ms'] for r in runs])),
        'switches': float(np.mean([r['switches'] for r in runs])),
        'false_sw': float(np.mean([r['false_sw'] for r in runs])),
        'wifi_mb': float(np.mean([r['wifi_b'] for r in runs])) / 1e6,
        'n_loss': float(np.mean([r['n_loss'] for r in runs])),
        'unusable_ms': float(np.mean([r['unusable_ms'] for r in runs])),
        'coverage': 1 - float(np.mean([r['stall_ms'] for r in runs]))
                      / max(float(np.mean([r['unusable_ms'] for r in runs])), 1),
        'wifi_duty': float(np.mean([r['wifi_b'] for r in runs]))
                     / (WIFI_BPS / 8 * float(np.mean([r['dur_ms'] for r in runs])) / 1000),
        'false_ratio': float(np.mean([r['false_sw'] for r in runs]))
                       / max(float(np.mean([r['switches'] for r in runs])), 1e-9),
    }


def main():
    os.makedirs('/home/claude/ai-sim/out', exist_ok=True)
    print('학습 데이터 생성...')
    X, Y = build_dataset(14)
    cut = int(len(X) * .7)
    clf = HistGradientBoostingClassifier(max_depth=5, max_iter=180,
                                         learning_rate=.08, random_state=0)
    clf.fit(X[:cut], Y[:cut])
    auc = roc_auc_score(Y[cut:], clf.predict_proba(X[cut:])[:, 1])
    acc = clf.score(X[cut:], Y[cut:])
    print(f'  샘플 {len(X):,}개 · 양성 {Y.mean()*100:.1f} % · '
          f'검증 정확도 {acc*100:.2f} % · AUC {auc:.4f}')

    print('\n정책 비교 (독립 시나리오 10회, 각 60초)')
    scen = []
    for _ in range(10):
        q, un, _ = make_trace()
        kind, mino = observe(q)
        scen.append((q, un, kind, mino))

    print('\nP1 비교군 공정화 — 연속 실패 임계 N 스윕')
    p1_rows, best_n, best_score = [], 3, -1e18
    for nc in (2, 3, 4, 5, 6, 8):
        a = agg([run_policy(q, un, k, m, 'P1', ncons=nc) for q, un, k, m in scen])
        p1_rows.append({'ncons': nc, **a})
        score = a['coverage'] - a['wifi_duty']          # 커버리지 − WiFi 점유
        print(f"  N={nc}  커버리지 {a['coverage']*100:5.1f}%  WiFi점유 {a['wifi_duty']*100:5.1f}%  "
              f"오전환비 {a['false_ratio']*100:5.1f}%  점수 {score:+.3f}")
        if score > best_score:
            best_score, best_n = score, nc
    print(f'  → 최적 N = {best_n} 을 비교군으로 채택')

    out = {}
    for name, kw in (('P0', {}), ('P1', {'ncons': best_n}), ('P2', {'model': clf, 'thr': 0.5})):
        runs = [run_policy(q, un, k, m, name, **kw) for q, un, k, m in scen]
        out[name] = agg(runs)

    hdr = f"{'':4} {'전환지연':>10} {'표준편차':>9} {'사전전환':>9} {'정체':>9} {'커버리지':>9} {'전환':>6} {'오전환비':>9} {'WiFi점유':>9}"
    print(hdr); print('-' * len(hdr))
    LBL = {'P0': 'P0', 'P1': 'P1', 'P2': 'P2'}
    for k in ('P0', 'P1', 'P2'):
        a = out[k]
        print(f"{LBL[k]:4} {a['lat_mean']:9.0f}ms {a['lat_std']:8.0f}ms {a['early_ratio']*100:8.0f}% "
              f"{a['stall_ms']:8.0f}ms {a['coverage']*100:8.1f}% {a['switches']:6.1f} "
              f"{a['false_ratio']*100:8.1f}% {a['wifi_duty']*100:8.1f}%")

    # 임계값 스윕 (운용점 선택 근거)
    print('\nP2 임계값 스윕')
    thr_rows = []
    for th in (0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
        runs = [run_policy(q, un, k, m, 'P2', model=clf, thr=th) for q, un, k, m in scen]
        a = agg(runs)
        thr_rows.append({'thr': th, **a})
        print(f'  thr={th:.2f}  지연 {a["lat_mean"]:7.0f}ms  정체 {a["stall_ms"]:7.0f}ms  '
              f'오전환 {a["false_sw"]:4.1f}회  WiFi {a["wifi_mb"]:5.2f}MB')

    imp = {}
    for k in ('lat_mean', 'stall_ms'):
        imp[k] = {'P1': (out['P0'][k] - out['P1'][k]) / abs(out['P0'][k]) * 100,
                  'P2': (out['P0'][k] - out['P2'][k]) / abs(out['P0'][k]) * 100}

    json.dump({'meta': {'auc': float(auc), 'acc': float(acc), 'n_samples': int(len(X)),
                        'pos_rate': float(Y.mean()), 'horizon_ms': HORIZON_MS,
                        'monitor_ms': MONITOR_MS, 'window_ms': 1000,
                        'LIFI_TIMEOUT_MS': LIFI_TIMEOUT_MS, 'DEGRADE_TTT_MS': DEGRADE_TTT_MS,
                        'RECOVERY_STABLE_MS': RECOVERY_STABLE_MS,
                        'feat_names': FEAT_NAMES, 'n_scen': len(scen)},
               'policies': out, 'thr_sweep': thr_rows, 'improvement_pct': imp,
               'p1_sweep': p1_rows, 'p1_best_n': best_n},
              open('/home/claude/ai-sim/out/ho_results.json', 'w'), indent=1)
    print('\n→ out/ho_results.json')


if __name__ == '__main__':
    main()
