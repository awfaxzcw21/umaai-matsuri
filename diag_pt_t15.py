"""诊断：t15 起点各 PT 来源的贡献（开关法）。

跑一个基线 + 逐项关闭某个 PT 来源，看 pt_mean 相对基线的下降量。
最后用原文本恢复 data/scenario_matsuri.json（保格式）。
"""

import copy
import json
import os
import subprocess
import tempfile

ROOT = r"E:\HJM\umaai-matsuri"
DATA = os.path.join(ROOT, "data", "scenario_matsuri.json")
DIST = os.path.join(ROOT, "target", "release", "matsuri-dist.exe")
STATE = os.environ.get("PT_STATE", r"E:\HJM\jidianbeiAI0.22\out\_st_s2_t15.json")
N = os.environ.get("PT_N", "2000")

ORIG_TEXT = open(DATA, encoding="utf-8").read()
ORIG = json.loads(ORIG_TEXT)


def save(d: dict) -> None:
    open(DATA, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2))


def run() -> dict | None:
    out = tempfile.mktemp(suffix=".json")
    env = dict(os.environ, MC_N=N)
    r = subprocess.run(
        [DIST, STATE, out], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        print("  失败:", r.stderr[-300:])
        return None
    j = json.load(open(out, encoding="utf-8"))
    os.unlink(out)
    return j


def metrics(j: dict) -> tuple[float, float, float]:
    d = j["debug"]
    return d["pt_mean"], d["five_sum_mean"], j["real_score"]["mean"]


def main() -> int:
    if not os.path.exists(STATE):
        print("state 不存在:", STATE)
        return 1
    baseline = run()
    if baseline is None:
        return 1
    bp, bf, br = metrics(baseline)
    print(f"基线（{os.path.basename(STATE)}, N={N}）: pt={bp:.0f} five={bf:.0f} real={br:.0f}")

    experiments = [
        ("关训练PT", lambda d: d["value_scale"].update(train_pt=0.0)),
        ("关稽古PT", lambda d: d["value_scale"].update(keiko_pt=0.0)),
        ("关比赛PT", lambda d: d.update(race_pt_target=0.0, race_pt_target_late=0.0)),
        ("关発表会PT", lambda d: [cp.update(pt=0) for cp in d["check_points"]]),
        ("关隐藏事件", lambda d: d.update(hidden_events=[])),
        ("关剧情事件", lambda d: d.update(scenario_events=[])),
    ]
    try:
        for name, mut in experiments:
            d = copy.deepcopy(ORIG)
            mut(d)
            save(d)
            j = run()
            if j is None:
                continue
            p, f, r = metrics(j)
            print(f"{name}: pt={p:.0f} (Δ{bp - p:+.0f}) five={f:.0f} real={r:.0f}")
    finally:
        open(DATA, "w", encoding="utf-8").write(ORIG_TEXT)
        print("已恢复原文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
