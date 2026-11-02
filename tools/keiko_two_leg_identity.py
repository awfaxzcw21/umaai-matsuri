#!/usr/bin/env python3
"""稽古两腿恒等式的复算脚本：实到[axis] == home_pred[axis] + p[axis]。

一手源 = `festa_rows2.csv`（由 `E:/HJM/_festa/extract_festa2.py` 从
`E:/HJM/jidianbeiAI0.22/out/hachimi_raw` 的 msgpack 报文产出；CSV 不进仓）。
每行 = 一面稽古板；`home_pred` / `p` 是板上报文自报的逐轴预览值
（`target_type` 1速 2耐 3力 4根 5智 10体力 30技能Pt），`delta` 是那一手的五轴实到。

只在**真正执行了这块板**的手上比（`r_cmd == command_id`）——否则板只是显示过，
差分来自别的命令。判合宿期看 `camp_remain > 0`。

跑法：

    python tools/keiko_two_leg_identity.py E:/HJM/_festa/festa_rows2.csv
"""

import csv
import json
import sys
from collections import Counter

AXES = ("速", "耐", "力", "根", "智")


def board_values(raw):
    """报文板上报文是 {target_type: 值}；缺的轴按 0。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    return {str(k): int(v) for k, v in d.items()}


def main(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    executed = [r for r in rows if r["r_cmd"] == r["command_id"]]
    print(f"板 {len(rows)} 手，其中真正执行了稽古的 {len(executed)} 手")

    stat = Counter()
    misses = []
    for r in executed:
        home = board_values(r["home_pred"])
        festa = board_values(r["p"])
        if home is None or festa is None:
            stat["skip_no_board"] += 1
            continue
        delta = [int(x) for x in r["delta"].split("|")]
        camp = "合宿" if int(r["camp_remain"] or 0) > 0 else "非合宿"
        hit = 0
        for a in range(5):
            want = home.get(str(a + 1), 0) + festa.get(str(a + 1), 0)
            if delta[a] == want:
                hit += 1
            else:
                misses.append(
                    (camp, r["run"], r["turn"], r["command_id"], AXES[a],
                     home.get(str(a + 1), 0), festa.get(str(a + 1), 0), delta[a])
                )
        stat[f"{camp}_hands"] += 1
        stat[f"{camp}_axes"] += hit
        stat[f"{camp}_axes_n"] += 5
        if hit == 5:
            stat[f"{camp}_all5"] += 1
        for key, col in (("vital", "d_vital"), ("pt", "d_sp")):
            tt = "10" if key == "vital" else "30"
            got = int(r[col])
            want = home.get(tt, 0) + festa.get(tt, 0)
            stat[f"{camp}_{key}_n"] += 1
            stat[f"{camp}_{key}"] += got == want
            if key == "vital" and got != want:
                misses.append((camp, r["run"], r["turn"], r["command_id"], "体力",
                               home.get("10", 0), festa.get("10", 0), got))

    print("\n--- 分层命中 ---")
    for camp in ("非合宿", "合宿"):
        h = stat[f"{camp}_hands"]
        print(
            f"{camp}: 手 {h}｜五轴全中 {stat[f'{camp}_all5']}"
            f"｜轴样本 {stat[f'{camp}_axes']}/{stat[f'{camp}_axes_n']}"
            f"｜体力 {stat[f'{camp}_vital']}/{stat[f'{camp}_vital_n']}"
            f"｜Pt {stat[f'{camp}_pt']}/{stat[f'{camp}_pt_n']}"
        )
    print("\n--- 不中的样本（应只剩失败手与赛/演出回合的体力）---")
    for m in misses:
        print("  %s run%s t%s cmd%s %s: home=%d + festa=%d != 实到 %d" % m)
    if stat["skip_no_board"]:
        print(f"\n（{stat['skip_no_board']} 手板上报文没带预览值，跳过）")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
