"""扫 `stat_anchor_scale`：每个起点回合跑两档，反解「要多少补偿才能追平真实终局」。

背景：`stat_anchor_scale=1.45` 只在 t38 标定。锚点（报文 `stat_gains`）是**当前回合**的
训练真值，从早局起步时它冻结在「还没长大的训练」上，整局沿用同一个系数就会低估终局。

做法：起点回合 t 的真值快照（`E:/HJM/jidianbeiAI0.22/out/_st_s2_t<t>.json`）+ 该局真实
终局五维和（sess2 = card 114101，t78 = 8,484）。
growth(s) = 投影终局五维 − 起点五维，对 s 近似线性 → 两个 s 解 A/B，再解 required。

用法：`python sweep_anchor_scale.py`（N 用 `MC_N` 覆盖，默认 2000）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "target", "release", "matsuri-dist.exe")
STATE_DIR = "E:/HJM/jidianbeiAI0.22/out"
OUT_DIR = os.path.join(HERE, "out")
N = os.environ.get("MC_N", "2000")

# 该局真实终局（card 114101 / sess2 t78：五维 2244/1750/1845/1349/1296，PT 8,450）
REAL_FINAL = 8484
# 起点快照的真实五维和（报文实测，见 docs/羁绊差值实测_20261029.md）
START_FIVE = {15: 1530, 20: 1997, 25: 2501, 30: 3009, 37: 3946, 45: 4895, 60: 6581}
TURNS = [15, 20, 25, 30, 37, 45, 60]
SCALES = [1.0, 1.6]


def run(turn: int, scale: float) -> dict:
    state = f"{STATE_DIR}/_st_s2_t{turn}.json"
    out = f"{OUT_DIR}/_dist_anchor_t{turn}_s{scale}.json"
    env = dict(os.environ, MC_N=N, MC_STAT_ANCHOR_SCALE=str(scale))
    subprocess.run([EXE, state, out], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(out, encoding="utf-8") as f:
        d = json.load(f)
    return {
        "five": d["debug"]["five_sum_mean"],
        "pt": d["debug"]["pt_mean"],
        "real": d["real_score"]["mean"],
        "median": d["real_score"]["median"],
    }


def main() -> None:
    print(f"N={N}  真实终局五维和={REAL_FINAL}")
    print(f"{'turn':>5} {'start':>6} | " + " | ".join(f"s={s:<4} five/pt/real" for s in SCALES))
    rows = {}
    for t in TURNS:
        cells = []
        for s in SCALES:
            r = run(t, s)
            cells.append(f"{r['five']:>4.0f}/{r['pt']:>5.0f}/{r['real']:>6.0f}")
            rows[(t, s)] = r
        print(f"{t:>5} {START_FIVE[t]:>6} | " + " | ".join(cells))
        sys.stdout.flush()
    print()
    print(f"{'turn':>5} {'A(每单位s)':>10} {'B(固定)':>8} {'need_s':>7}  (need_s 让投影追平 {REAL_FINAL})")
    for t in TURNS:
        a0 = rows[(t, SCALES[0])]["five"]
        a1 = rows[(t, SCALES[1])]["five"]
        need = REAL_FINAL - START_FIVE[t]
        A = (a1 - a0) / (SCALES[1] - SCALES[0])
        B = a0 - A * SCALES[0] - START_FIVE[t]
        s_need = (need - B) / A if A else float("nan")
        print(f"{t:>5} {A:>10.1f} {B:>8.1f} {s_need:>7.3f}")


if __name__ == "__main__":
    main()
