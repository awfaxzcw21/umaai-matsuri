"""扫参：race_stat_gain 对两个起点（t15 / t37，均有锚点）的终局 five / PT / real 影响。

对每个 gain 改 data/scenario_matsuri.json 后跑 matsuri-dist，最后恢复原文件。
"""

import json
import os
import re
import shutil
import subprocess

ROOT = r"E:\HJM\umaai-matsuri"
DATA = os.path.join(ROOT, "data", "scenario_matsuri.json")
DIST = os.path.join(ROOT, "target", "release", "matsuri-dist.exe")
STATES = {
    "t15": r"E:\HJM\jidianbeiAI0.22\out\_st_s2_t15.json",
    "t37": r"E:\HJM\jidianbeiAI0.22\out\_st_s2_t37.json",
}
GAINS = [0, 10, 15, 20, 25]
OUT = os.path.join(ROOT, "out", "_tmp_gain.json")


def main() -> int:
    orig = open(DATA, encoding="utf-8").read()
    bak = DATA + ".bak"
    shutil.copy(DATA, bak)
    rows = []
    try:
        for name, state in STATES.items():
            for gain in GAINS:
                new = re.sub(r'"race_stat_gain":\s*\d+', f'"race_stat_gain": {gain}', orig)
                open(DATA, "w", encoding="utf-8").write(new)
                env = dict(os.environ, MC_N="2000")
                r = subprocess.run(
                    [DIST, state, OUT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900
                )
                if r.returncode != 0:
                    print(f"{name} gain={gain} FAIL rc={r.returncode}: {r.stderr[-300:]}")
                    continue
                j = json.load(open(OUT, encoding="utf-8"))
                d, rs = j["debug"], j["real_score"]
                rows.append(
                    {
                        "state": name,
                        "gain": gain,
                        "five": d["five_sum_mean"],
                        "pt": d["pt_mean"],
                        "real": rs["mean"],
                    }
                )
                print(f"{name} gain={gain:>2}: five={d['five_sum_mean']:8.1f} pt={d['pt_mean']:7.1f} real={rs['mean']:8.0f}")
    finally:
        shutil.copy(bak, DATA)
        os.remove(bak)
    json.dump(rows, open(os.path.join(ROOT, "out", "_sweep_gain2.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("saved:", os.path.join(ROOT, "out", "_sweep_gain2.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
