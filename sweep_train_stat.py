"""扫 value_scale 的 train_stat / keiko_stat，看终局五维和与 real 分落在哪。

用法：python sweep_train_stat.py [N] [train_stat1,train_stat2,...]
读 data/scenario_matsuri.json → 改 value_scale → 跑 matsuri-dist → 复原原值。
keiko_stat 按当前 4.5/3.4 的比例跟随（train_stat × 1.3235），train_pt/keiko_pt 保持不动。
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

N = sys.argv[1] if len(sys.argv) > 1 else "1500"
RATIO = 4.5 / 3.4
if len(sys.argv) > 2:
    edges = [float(x) for x in sys.argv[2].split(",")]
else:
    edges = [3.4, 4.0, 4.5, 5.0, 5.5, 6.0]

orig = DATA.read_text(encoding="utf-8")
orig_vs = json.loads(orig)["value_scale"]
print("原始 value_scale:", json.dumps(orig_vs, ensure_ascii=False), " N=", N)
print()
print("%-9s %-9s %9s %9s %9s %9s %9s" % ("train_st", "keiko_st", "five_sum", "fs_max", "pt_mean", "real_mean", "real_p90"))
print("-" * 74)

try:
    for ts in edges:
        ks = round(ts * RATIO, 2)
        d = json.loads(orig)
        d["value_scale"]["train_stat"] = ts
        d["value_scale"]["keiko_stat"] = ks
        DATA.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if OUT.exists():
            OUT.unlink()
        subprocess.run([str(EXE), str(STATE), str(OUT)], env={**os.environ, "MC_N": N},
                       capture_output=True, check=True)
        r = json.loads(OUT.read_text(encoding="utf-8"))
        dbg = r.get("debug") or {}
        rs = r.get("real_score") or {}
        print("%-9.2f %-9.2f %9.0f %9.0f %9.0f %9.0f %9.0f" % (
            ts, ks, dbg.get("five_sum_mean", 0), dbg.get("five_sum_max", 0),
            dbg.get("pt_mean", 0), rs.get("mean", 0), rs.get("p90", 0)))
finally:
    DATA.write_text(orig, encoding="utf-8")
    print()
    print("已复原 scenario_matsuri.json 原值")
