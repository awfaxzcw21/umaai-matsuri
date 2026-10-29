"""扫参：race_stat_gain（每场生涯比赛额外五维奖励）对终局 five / PT / real 的影响。

用法：python sweep_race_gain.py [state.json] [N]
默认 state = E:/HJM/jidianbeiAI0.22/out/_st_t15_nona.json（t15 无锚点，纯模型路径）。

改 data/scenario_matsuri.json 的 race_stat_gain 后跑 matsuri-dist，最后恢复原文件
（保留原格式，仅替换数字），确保 git 工作区回到原状。
"""

import json
import os
import re
import shutil
import subprocess
import sys

ROOT = r"E:\HJM\umaai-matsuri"
DATA = os.path.join(ROOT, "data", "scenario_matsuri.json")
DIST = os.path.join(ROOT, "target", "release", "matsuri-dist.exe")
DEFAULT_STATE = r"E:\HJM\jidianbeiAI0.22\out\_st_t15_nona.json"
OUT = os.path.join(ROOT, "out", "_sweep_gain_tmp.json")
GAINS = [0, 10, 15, 20, 25]


def main() -> int:
    state = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STATE
    n = sys.argv[2] if len(sys.argv) > 2 else "2000"
    if not os.path.exists(state):
        print(f"state 不存在: {state}")
        return 1

    orig = open(DATA, encoding="utf-8").read()
    bak = DATA + ".bak"
    shutil.copy(DATA, bak)
    rows = []
    try:
        for gain in GAINS:
            new = re.sub(r'"race_stat_gain":\s*\d+', f'"race_stat_gain": {gain}', orig)
            assert new != orig or gain == 0, "race_stat_gain 未被替换"
            open(DATA, "w", encoding="utf-8").write(new)
            env = dict(os.environ, MC_N=n)
            r = subprocess.run(
                [DIST, state, OUT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900
            )
            if r.returncode != 0:
                print(f"gain={gain} FAIL rc={r.returncode}: {r.stderr[-400:]}")
                continue
            j = json.load(open(OUT, encoding="utf-8"))
            d = j["debug"]
            rs = j["real_score"]
            rows.append(
                {
                    "gain": gain,
                    "five": d["five_sum_mean"],
                    "pt": d["pt_mean"],
                    "real_mean": rs["mean"],
                    "real_median": rs["median"],
                }
            )
            print(
                f"gain={gain:>2}: five={d['five_sum_mean']:8.1f} pt={d['pt_mean']:7.1f} "
                f"real_mean={rs['mean']:8.0f} median={rs['median']:8.0f}"
            )
    finally:
        shutil.copy(bak, DATA)
        os.remove(bak)

    dst = os.path.join(ROOT, "out", "_sweep_gain.json")
    json.dump(rows, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("saved:", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
