#!/usr/bin/env python3
"""这次发布到底会改掉几次面板顶栏？——窗内局面扫描。

背景：#98 删的是 `mccli.rs` 里发表会兜底的**候选重排**，它在「下一个检查点 15 手以内
且有演目未达标」时，把引擎自己算出来的「最接近达标」那个演目强推到 top-1。
单一局面探针没有区分力（那个局面上被强推的演目恰好也是搜索排第一），
所以这里扫一批**合成的**窗内局面，逐格比 发布前 exe vs 发布后 exe 的 top-1。

★口径写死：这些状态是把 t30 锚点手改成「演目 Lv 未达标」的**合成态**，
只用来数「建议变不变」，**不是**分数读数，也不能拿来当引擎水平。

    python tools/panel_top1_impact_sweep.py                    # 默认 8 局面 × 2 seed
    python tools/panel_top1_impact_sweep.py --levels "[[4,4,4,4,4]]"
"""
import argparse
import copy
import json
import os
import subprocess
import sys

PANEL = r"E:/HJM/jidianbeiAI0.22"
MAIN = r"E:/HJM/umaai-matsuri"
BASE_STATE = os.path.join(MAIN, "out", "gate98", "state_t40.json")
TRAIN_ORDER = ["速度", "耐力", "力量", "根性", "智力"]  # mccli 里 act idx 的名字序


def make_state(base, levels, gauges, turn, vital):
    d = copy.deepcopy(base)
    d["turn"] = turn
    d["vital"] = vital
    by_train = {a["train"]: a for a in d["acts"]}
    for i, name in enumerate(TRAIN_ORDER):
        act = by_train.get(name)
        if act is None:
            sys.exit(f"合成失败：acts 里没有 {name}")
        act["level"] = levels[i]
        act["gauge"] = gauges[i]
    return d


def run(exe, cwd, state_path, out_path, k, seed):
    env = dict(os.environ, MC_K=str(k), MC_PLY="2", MC_SEED=str(seed))
    r = subprocess.run([exe, state_path, out_path, "200"], cwd=cwd, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"rc={r.returncode} {r.stderr[:200]}")
    d = json.load(open(out_path, encoding="utf-8"))
    return [(x["label"], x["score"]) for x in (d.get("ranked") or [])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=PANEL)
    ap.add_argument("--old-exe", default="matsuri-mccli.exe.bak_20261106_4abcce6")
    ap.add_argument("--new-exe", default="matsuri-mccli.exe")
    ap.add_argument("--k", type=int, default=1024, help="面板默认预算 1024")
    ap.add_argument("--seeds", default="0,1")
    base = json.load(open(BASE_STATE, encoding="utf-8"))
    # (levels, gauges, turn, vital) —— need 由剧本 check_points 决定：t47 need=5、t67 need=6
    cases = [
        ("等差全欠", [4, 4, 4, 4, 4], [5, 4, 3, 2, 1], 40, 76),
        ("单格落后", [4, 5, 5, 5, 5], [3, 3, 3, 3, 3], 40, 76),
        ("两格落后", [4, 4, 5, 5, 5], [3, 3, 3, 3, 3], 40, 76),
        ("差一级+高gauge", [3, 4, 4, 4, 4], [5, 4, 3, 2, 1], 40, 76),
        ("一格差两级", [2, 5, 5, 5, 5], [4, 4, 4, 4, 4], 40, 76),
        ("四格欠(智力已到)", [4, 4, 4, 4, 5], [5, 4, 3, 2, 1], 40, 76),
        ("全达标(不该触发)", [5, 5, 5, 5, 5], [5, 5, 5, 5, 5], 40, 76),
        ("窗内但低体力", [4, 4, 4, 4, 4], [5, 4, 3, 2, 1], 40, 41),
        ("t32 窗右沿", [4, 4, 4, 4, 4], [5, 4, 3, 2, 1], 32, 76),
        ("t55 CP3 窗", [5, 5, 5, 5, 5], [5, 5, 5, 5, 5], 55, 76),
        ("t58 CP3 欠6", [5, 5, 5, 5, 5], [5, 5, 5, 5, 5], 58, 76),
        ("t60 窗内三欠", [4, 5, 5, 4, 5], [3, 3, 3, 3, 3], 60, 76),
    ]
    a = ap.parse_args()
    rust = os.path.join(a.panel, "rust")
    old, new = os.path.join(rust, a.old_exe), os.path.join(rust, a.new_exe)
    for p in (old, new):
        if not os.path.isfile(p):
            sys.exit(f"缺 exe：{p}")
    seeds = [int(s) for s in a.seeds.split(",")]
    tmpdir = os.path.join(MAIN, "out", "publish_sweep")
    os.makedirs(tmpdir, exist_ok=True)

    print(f"旧件 {os.path.basename(old)}  新件 {os.path.basename(new)}  "
          f"K={a.k} PLY=2 seeds={seeds} 合成态数={len(cases)}\n")
    hdr = f"{'局面':<18}{'seed':>5}{'兜底要逼的格':>14}{'旧 top1':>12}{'新 top1':>12}{'变否':>6}{'旧/新值差':>12}"
    print(hdr)
    changed = total = 0
    per_case = {}
    for name, levels, gauges, turn, vital in cases:
        st = make_state(base, levels, gauges, turn, vital)
        sp = os.path.join(tmpdir, f"synth_{name}.json")
        json.dump(st, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
        for seed in seeds:
            try:
                ro = run(old, rust, sp, os.path.join(tmpdir, "res_old.json"), a.k, seed)
                rn = run(new, rust, sp, os.path.join(tmpdir, "res_new.json"), a.k, seed)
            except Exception as e:  # noqa: BLE001
                print(f"{name:<18}{seed:>5} 跑失败 {e}")
                continue
            # 兜底会挑「gap 最小、gauge 最大」那格（gap 全等时纯按 gauge）
            need = 6 if turn > 47 else 5
            deficit = [(i, need - lv, gauges[i]) for i, lv in enumerate(levels) if lv < need]
            forced = None
            if deficit and (need_turn_gap(turn) <= 15):
                forced = min(deficit, key=lambda x: (x[1], -x[2]))[0]
            tgt = TRAIN_ORDER[forced] if forced is not None else "-"
            diff = ro[0][0] != rn[0][0]
            changed += diff
            total += 1
            per_case.setdefault(name, [0, 0])
            per_case[name][0] += diff
            per_case[name][1] += 1
            delta = f"{ro[0][1] - rn[0][1]:+.0f}"
            print(f"{name:<18}{seed:>5}{tgt:>14}{ro[0][0]:>12}{rn[0][0]:>12}{'变' if diff else '—':>6}{delta:>12}")
    print(f"\ntop-1 被这次发布改掉的格数：{changed} / {total}"
          f"（兜底规则只在窗内且未达标时才有机会生效；上面『兜底要逼的格』那一列是自己按规则算的，不是读引擎）")
    for name, (c, n) in per_case.items():
        if c:
            print(f"   {name}: {c}/{n} 变了 top-1")


def need_turn_gap(turn):
    """下一个 check_point 的 turn − 当前 turn（剧本 15：23/47/67/77）。"""
    for cp in (23, 47, 67, 77):
        if cp >= turn:
            return cp - turn
    return 99


if __name__ == "__main__":
    main()
