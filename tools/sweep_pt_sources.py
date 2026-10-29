#!/usr/bin/env python3
"""B2 剩余「绝对值重分配」诊断：开关法分解各 PT 来源的贡献。

做法：逐项关闭某个 PT 来源，跑 matsuri-dist，看 pt_mean 相对基线的下降量。
最后恢复 data/scenario_matsuri.json 原值。

用法：python tools/sweep_pt_sources.py [N]
"""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data", "scenario_matsuri.json")
STATE = "E:/HJM/jidianbeiAI0.22/out/_mc_state.json"
DIST = os.path.join(HERE, "target", "release", "matsuri-dist.exe")

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000


def load():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def run_dist():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        out = tf.name
    env = dict(os.environ)
    env["MC_N"] = str(N)
    r = subprocess.run(
        [DIST, STATE, out], cwd=HERE, env=env, capture_output=True, text=True,
        check=False,
    )
    if r.returncode != 0:
        print("  dist 失败:", r.stderr[-500:])
        return None
    with open(out, encoding="utf-8") as f:
        res = json.load(f)
    os.unlink(out)
    return res


def metrics(res):
    d = res.get("debug", {})
    return {
        "pt": d.get("pt_mean"),
        "five": d.get("five_sum_mean"),
        "real": res.get("real_score", {}).get("mean"),
        "median": res.get("real_score", {}).get("median"),
    }


def main():
    orig = load()
    try:
        baseline = run_dist()
        bm = metrics(baseline)
        print(f"基线（全开, N={N}）: pt={bm['pt']:.0f} five={bm['five']:.0f} "
              f"real={bm['real']:.0f} med={bm['median']:.0f}")

        # 每个实验：深拷贝原 data，改一处，跑，恢复
        experiments = [
            ("关训练 PT", lambda d: d["value_scale"].update({"train_pt": 0.0})),
            ("关稽古 PT", lambda d: d["value_scale"].update({"keiko_pt": 0.0})),
            ("关比赛 PT", lambda d: d.update({"race_pt_target": 0.0, "race_pt_target_late": 0.0})),
            ("关発表会 PT", lambda d: [cp.update({"pt": 0}) for cp in d["check_points"]]),
            ("关隐藏事件", lambda d: d.update({"hidden_events": []})),
            ("关剧情事件", lambda d: d.update({"scenario_events": []})),
        ]
        for name, mut in experiments:
            d = copy.deepcopy(orig)
            mut(d)
            save(d)
            res = run_dist()
            if res is None:
                print(f"{name}: 失败")
                continue
            m = metrics(res)
            print(f"{name}: pt={m['pt']:.0f} (Δ{bm['pt']-m['pt']:+.0f}) "
                  f"five={m['five']:.0f} real={m['real']:.0f}")
    finally:
        save(orig)
        print("已恢复原 data。")


if __name__ == "__main__":
    main()
