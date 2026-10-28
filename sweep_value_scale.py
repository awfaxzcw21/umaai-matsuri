"""二维扫 value_scale（train_stat × train_pt），把五维和 / PT 同时拉回真实锚点。

真实锚点（报文参考局，card 114101，78 回合）：
  - 五维和 8,484
  - 终局 skill_pt 8,449
keiko_stat / keiko_pt 按既有比例跟随（keiko_stat = train_stat×4.5/3.4，keiko_pt = train_pt×20.8/24.0）。

用法：python sweep_value_scale.py [N] [stats] [pts]
  stats / pts 用逗号分隔；不给则跑默认网格。
读 data/scenario_matsuri.json → 改 value_scale → 跑 matsuri-dist → finally 复原原值。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "scenario_matsuri.json"
STATE = Path("E:/HJM/jidianbeiAI0.22/out/_mc_state_races.json")
OUT = Path("E:/HJM/jidianbeiAI0.22/out/_dist_sweep.json")
EXE = ROOT / "target" / "release" / "matsuri-dist.exe"

N = sys.argv[1] if len(sys.argv) > 1 else "1200"
STATS = [float(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["5.2", "5.35", "5.5", "5.65"])]
PTS = [float(x) for x in (sys.argv[3].split(",") if len(sys.argv) > 3 else ["20.0", "21.0", "22.0", "23.0"])]

TARGET_FIVE = 8484
TARGET_PT = 8449

STAT_RATIO = 4.5 / 3.4   # keiko_stat / train_stat
PT_RATIO = 20.8 / 24.0   # keiko_pt / train_pt

orig = DATA.read_text(encoding="utf-8")
print("原始 value_scale:", json.dumps(json.loads(orig)["value_scale"], ensure_ascii=False))
print("目标：five_sum %d / pt %d    N=%s" % (TARGET_FIVE, TARGET_PT, N))
print()
print("%-7s %-7s %9s %9s %9s %9s %9s" % ("stat", "pt", "five_sum", "pt_mean", "real_mean", "real_p50", "real_p90"))
print("-" * 68)

rows = []
try:
    for stat in STATS:
        for pt in PTS:
            d = json.loads(orig)
            vs = d["value_scale"]
            vs["train_stat"] = round(stat, 3)
            vs["keiko_stat"] = round(stat * STAT_RATIO, 3)
            vs["train_pt"] = round(pt, 3)
            vs["keiko_pt"] = round(pt * PT_RATIO, 3)
            DATA.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if OUT.exists():
                OUT.unlink()
            subprocess.run(
                [str(EXE), str(STATE), str(OUT)],
                env={**os.environ, "MC_N": N},
                capture_output=True,
                check=True,
            )
            r = json.loads(OUT.read_text(encoding="utf-8"))
            dbg = r.get("debug") or {}
            rs = r.get("real_score") or {}
            five = dbg.get("five_sum_mean", 0)
            ptm = dbg.get("pt_mean", 0)
            score = abs(five - TARGET_FIVE) / TARGET_FIVE + abs(ptm - TARGET_PT) / TARGET_PT
            rows.append((score, stat, pt, five, ptm, rs.get("mean", 0), rs.get("median", 0), rs.get("p90", 0)))
            print("%-7.2f %-7.2f %9.0f %9.0f %9.0f %9.0f %9.0f" % (
                stat, pt, five, ptm, rs.get("mean", 0), rs.get("median", 0), rs.get("p90", 0)))
finally:
    DATA.write_text(orig, encoding="utf-8")
    print()
    print("已复原 scenario_matsuri.json 原值")

rows.sort()
print()
print("最优 5 组（按 |Δfive|/8484 + |Δpt|/8449 升序）：")
for score, stat, pt, five, ptm, mean, med, p90 in rows[:5]:
    print("  stat=%.2f pt=%.2f → five %.0f (%+.1f%%) / pt %.0f (%+.1f%%) / real mean %.0f median %.0f p90 %.0f  [score %.4f]" % (
        stat, pt, five, 100 * (five - TARGET_FIVE) / TARGET_FIVE, ptm, 100 * (ptm - TARGET_PT) / TARGET_PT,
        mean, med, p90, score))
