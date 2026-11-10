#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LiFi-WiFi 핸드오버 예측 — 대규모 학습 데이터셋 생성
══════════════════════════════════════════════════════════════════
차광 조건을 네 가지 그룹으로 나누어 폭넓게 변화시킨 시나리오를 생성하고,
FPGA 수신 로그와 1초 윈도우 특징을 CSV 로 내보낸다.

  python export_full.py --traces 200 --out ./dataset
"""
import csv, os, argparse, time
from multiprocessing import Pool
import numpy as np
import ho_sim as H

# ── 차광 조건 그룹 ─────────────────────────────────────────
# 이름,          가려짐[ms],   완전차광[ms],  걷힘[ms],     간격[ms],      주변광
GROUPS = [
    ('fast',   (60, 200),    (200, 800),    (60, 200),   (1500, 4000), (0.00, 0.10)),
    ('normal', (150, 500),   (400, 1500),   (100, 400),  (2000, 5000), (0.03, 0.15)),
    ('slow',   (400, 900),   (800, 2500),   (300, 800),  (2500, 6000), (0.05, 0.20)),
    ('long',   (100, 700),   (1500, 4000),  (100, 600),  (3000, 7000), (0.00, 0.25)),
]
DUR_MS = 60_000
Hz = int(H.HORIZON_MS / H.DT)
STEP = int(H.MONITOR_MS / H.DT)

FEAT = ['minority_mean', 'minority_std', 'minority_slope',
        'crc_fail_rate_200ms', 'crc_fail_rate_500ms', 'crc_fail_rate_1000ms',
        'consecutive_crc_fail', 'ms_since_last_ok', 'max_frame_gap_ms',
        'window_goodput_B', 'no_response_ratio']
STATUS = {1: 'CRC_OK', 2: 'CRC_FAIL', 0: 'NO_RESPONSE'}


def make_trace(rng, deg, blk, rec, gap):
    """지정한 조건 범위로 링크 품질 궤적을 생성"""
    n = DUR_MS // H.DT
    q = np.ones(n)
    spans = []
    t = rng.uniform(1500, 3500)
    while True:
        td, tb, tr = (rng.uniform(*deg), rng.uniform(*blk), rng.uniform(*rec))
        i0 = int(t / H.DT)
        a, b, c = int(td / H.DT), int(tb / H.DT), int(tr / H.DT)
        if i0 + a + b + c >= n:
            break
        q[i0:i0 + a] = np.linspace(1, 0, a)
        q[i0 + a:i0 + a + b] = 0
        q[i0 + a + b:i0 + a + b + c] = np.linspace(0, 1, c)
        spans.append((i0, i0 + a + b + c, td, tb, tr))
        t += td + tb + tr + rng.uniform(*gap)
    q = np.clip(q + rng.normal(0, 0.02, n), 0, 1)
    return q, q <= H.Q_FAIL, spans


def observe(rng, q, amb):
    """FPGA 응답 패킷 단위 관측. amb 가 클수록 주변광 잡음이 크다."""
    n = len(q)
    kind = np.full(n, -1, np.int8)
    mino = np.zeros(n)
    step = max(1, int(round(H.FRAME_MS / H.DT)))
    for i in range(0, n, step):
        qi = q[i]
        p = np.clip(0.42 * (1 - qi) ** 1.3 + amb * 0.25, 0, 0.5)
        mino[i] = np.clip(rng.binomial(8, p), 0, 4)
        if qi > H.Q_OK:
            kind[i] = 1 if rng.random() > (0.004 + amb * 0.02) else 2
        elif qi > H.Q_FAIL:
            kind[i] = 2 if rng.random() < 0.65 else 1
        else:
            kind[i] = 0
    return kind, mino


def build(args):
    """트레이스 1개 생성 → (메타, 원시로그, 학습행, 이벤트)"""
    idx, gi, seed = args
    name, deg, blk, rec, gap, ambr = GROUPS[gi]
    rng = np.random.default_rng(seed)
    amb = float(rng.uniform(*ambr))
    q, un, spans = make_trace(rng, deg, blk, rec, gap)
    kind, mino = observe(rng, q, amb)
    tid = f'T{idx:04d}'

    raw, train, events = [], [], []
    last_ok = 0
    for i in range(len(q)):
        if kind[i] < 0:
            continue
        t = i * H.DT
        if kind[i] == 1:
            last_ok = t
        raw.append([tid, t, STATUS[int(kind[i])], '0xFEFE', f'0x{int(kind[i]):02X}',
                    int(mino[i]), H.CHUNK_SIZE if kind[i] == 1 else 0, t - last_ok])

    for j in range(H.WIN, len(q) - Hz, STEP):
        f = H.features(kind, mino, j)
        train.append([tid, j * H.DT] + [round(float(x), 4) for x in f]
                     + [int(un[j:j + Hz].any())])

    for k, (a, b, td, tb, tr) in enumerate(spans, 1):
        seg = un[a:b]
        w = np.where(seg)[0]
        if len(w) == 0:
            continue
        events.append([tid, f'E{k:02d}', a * H.DT, b * H.DT,
                       round(td, 1), round(tb, 1), round(tr, 1),
                       (a + w[0]) * H.DT, (a + w[-1]) * H.DT,
                       (w[-1] - w[0] + 1) * H.DT])

    meta = [tid, name, round(amb, 4), len(spans), len(raw), len(train),
            sum(r[-1] for r in train),
            f'{deg[0]}-{deg[1]}', f'{blk[0]}-{blk[1]}', f'{rec[0]}-{rec[1]}']
    return meta, raw, train, events


def write(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    mb = os.path.getsize(path) / 1e6
    print(f'  → {os.path.basename(path):<28} {len(rows):>9,} 행  {mb:>6.1f} MB')
    return mb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--traces', type=int, default=200)
    ap.add_argument('--out', default='./dataset')
    ap.add_argument('--jobs', type=int, default=2)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    jobs = [(i + 1, i % len(GROUPS), 10_000 + i) for i in range(a.traces)]
    print(f'시나리오 {a.traces}개 생성 — 조건 그룹 {len(GROUPS)}종, 프로세스 {a.jobs}개')
    t0 = time.time()
    metas, raws, trains, evs = [], [], [], []
    with Pool(a.jobs) as pool:
        for n, (m, r, tr, e) in enumerate(pool.imap_unordered(build, jobs, chunksize=4), 1):
            metas.append(m); raws += r; trains += tr; evs += e
            if n % 20 == 0 or n == a.traces:
                print(f'  {n:>4}/{a.traces}  누적 학습행 {len(trains):>8,}  '
                      f'{time.time()-t0:5.0f} s')
    metas.sort(); raws.sort(key=lambda x: (x[0], x[1]))
    trains.sort(key=lambda x: (x[0], x[1])); evs.sort()

    print('\nCSV 저장')
    tot = 0
    tot += write(os.path.join(a.out, '01_raw_frame_log.csv'),
                 ['trace_id', 'time_ms', 'status', 'header', 'status_byte',
                  'minority_count', 'payload_bytes', 'ms_since_last_ok'], raws)
    tot += write(os.path.join(a.out, '02_training_dataset.csv'),
                 ['trace_id', 'time_ms'] + FEAT + ['label_blocked_within_300ms'], trains)
    tot += write(os.path.join(a.out, '03_label_summary.csv'),
                 ['trace_id', 'event_id', 'event_start_ms', 'event_end_ms',
                  'degrade_ms', 'blocked_ms', 'recover_ms',
                  'unusable_start_ms', 'unusable_end_ms', 'unusable_duration_ms'], evs)
    tot += write(os.path.join(a.out, '04_trace_conditions.csv'),
                 ['trace_id', 'condition_group', 'ambient_level', 'n_events',
                  'n_frames', 'n_samples', 'n_positive',
                  'degrade_range_ms', 'blocked_range_ms', 'recover_range_ms'], metas)

    pos = sum(r[-1] for r in trains)
    print(f'\n  합계 {tot:.1f} MB · 학습 {len(trains):,}행 · '
          f'양성 {pos:,} ({pos/len(trains)*100:.1f} %) · '
          f'차광 이벤트 {len(evs):,}회 · {time.time()-t0:.0f} s')
    return dict(n_trace=a.traces, raw=len(raws), train=len(trains),
                ev=len(evs), pos=pos, mb=tot, out=a.out)


if __name__ == '__main__':
    main()
