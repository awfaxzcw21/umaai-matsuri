"""扫 value_scale 的 train_pt / keiko_pt，看终局 PT 与五维和落在哪。

用法：python sweep_train_pt.py [N]
读 data/scenario_matsuri.json → 改 value_scale → 跑 matsuri-dist → 复原原值。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "scenario_matsuri.json"
STATE = Path("E:/HJM/jidianbeiAI0.22/out/_mc_state_races.json")
OUT = Path("E:/HJM/jidianbeiAI0.22/out/_dist_sweep.json")
EXE = ROOT / "target" / "release" / "matsuri-dist.exe"

N = sys.argv[1] if len(sys.argv) > 1 else "1500"

COMBOS = [
    (7.5, 6.5),    # 现状
    (12.0, 10.0),
    (15.0, 13.0),
    (20.0, 17.0),
    (25.0, 21.0),
]

orig = DATA.read_text(encoding="utf-8")
orig_vs = json.loads(orig)["value_scale"]
print("原始 value_scale:", json.dumps(orig_vs, ensure_ascii=False))
print()
print("%-8s %-8s %9s %9s %9s %9s" % ("train_pt", "keiko_pt", "pt_mean", "five_sum", "real_mean", "real_p90"))
print("-" * 60)

try:
    for train_pt, keiko_pt in COMBOS:
        d = json.loads(orig)
        d["value_scale"]["train_pt"] = train_pt
        d["value_scale"]["keiko_pt"] = keiko_pt
        DATA.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if OUT.exists():
            OUT.unlink()
        subprocess.run([str(EXE), str(STATE), str(OUT)], env={**__import__("os").environ, "MC_N": N},
                       capture_output=True, check=True)
        r = json.loads(OUT.read_text(encoding="utf-8"))
        dbg = r.get("debug") or {}
        rs = r.get("real_score") or {}
        print("%-8.1f %-8.1f %9.0f %9.0f %9.0f %9.0f" % (
            train_pt, keiko_pt, dbg.get("pt_mean", 0), dbg.get("five_sum_mean", 0),
            rs.get("mean", 0), rs.get("p90", 0)))
finally:
    DATA.write_text(orig, encoding="utf-8")
    print()
    print("已复原 scenario_matsuri.json 原值")
