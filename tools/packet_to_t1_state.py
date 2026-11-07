#!/usr/bin/env python3
"""#77 第 2 步：把某一局的**报文开局面**落成引擎能吃的「整局输入」state json。

和面板的 `build_state_from_bin.py` 的区别：那个是**中盘锚点**（把 t30 的五维 / 命令 / 人头
全部冻结进局面，引擎只打完剩下的回合）；这个是 **t1 整局输入**——只带「这局开局就定下的事」：
卡组（卡号 + 凸）、马娘、演目上限、开局五维与上限档、体力 / 干劲，
**不带**任何命令面板、人头分布、赛程锚点 ⇒ 整局由引擎自己打。

为什么要它：现有天花板读数跑在 `_state_t30.json` 的锚点卡组 = R 吾妻（10149，`max_act_level=6`），
而 `out/_77_run_inventory.json` 里 14 局真机有 13 局上限是 10（都带 SSR 吾妻 30323）。
拿上限 6 的卡组去量 85,600 的靶，口径不一致。

输入是 `tools/packet_run_inventory.py` 的清单（不重复解码报文）。

    python tools/packet_run_inventory.py --out out/_77_run_inventory.json
    python tools/packet_to_t1_state.py --run 224 --out out/_77_state_t1_run224.json
    python tools/packet_to_t1_state.py                    # 每局一份 + 汇总
"""
from __future__ import annotations

import argparse
import json
import os

AX = ["速度", "耐力", "力量", "根性", "智力"]
MOT = {1: "绝不调", 2: "不调", 3: "普通", 4: "好调", 5: "绝好调"}


def build_state(r: dict) -> tuple[dict | None, dict]:
    t1 = r.get("t1")
    note = {
        "run": r["chara_id"], "start_time": r["start_time"], "first_file": r["first_file"],
        "card_id": r["card_id"], "max_act_level": r["max_act_level"],
        "inherit_parents": r["inherit"], "n_pkt": r["n_pkt"],
        "turns_seen": [r["min_turn"], r["max_turn"]],
        "act_lv_end": r["act_lv_last"], "five_sum_end_real": r["five_sum_last"],
        "real_end": r["real_last"],
    }
    if not t1 or not t1.get("deck"):
        note["skip"] = f"该局报文没落到 t≤2 的开局面（首回合 turn={t1 and t1['turn']}）"
        return None, note
    deck = sorted(t1["deck"], key=lambda c: c[1] or 0)
    five = [int(x or 0) for x in t1["five"]]
    caps = [int(x or 0) for x in t1["caps"]]
    st = {
        "turn": t1["turn"],
        "chara_id": r["card_id"],
        "scenario_id": t1.get("scenario_id") or 15,
        "support_cards": [{"card_id": c[0], "limit_break": c[2]} for c in deck],
        "max_act_level": r["max_act_level"],
        "stats": {n: five[i] for i, n in enumerate(AX)},
        "stat_caps": {n: caps[i] for i, n in enumerate(AX)},
        "vital": int(t1.get("vital") or 100),
        "max_vital": int(t1.get("max_vital") or 100),
        "skill_point": int(t1.get("sp") or 0),
        "motivation": MOT.get(t1.get("motivation") or 3, "普通"),
        "_77_note": {"run": r["chara_id"], "source": r["first_file"],
                     "deck_pos_break": deck, "parents": r["inherit"]},
    }
    note.update({"deck_pos_break": deck, "five_t1": five, "caps_t1": caps,
                 "vital_t1": st["vital"], "mot_t1": st["motivation"],
                 "turn_t1": t1["turn"], "t1_file": t1["file"]})
    return st, note


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inv", default="out/_77_run_inventory.json")
    ap.add_argument("--run", type=int, help="single_mode_chara_id（见清单）")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    runs = json.load(open(a.inv, encoding="utf-8"))
    if a.run:
        runs = [r for r in runs if r["chara_id"] == a.run]
        if not runs:
            raise SystemExit(f"清单里没有 run={a.run}")
    os.makedirs("out", exist_ok=True)
    notes = []
    for r in sorted(runs, key=lambda x: x["chara_id"]):
        st, note = build_state(r)
        if st is None:
            print(f"-- run {r['chara_id']}: 跳过（{note['skip']}）")
            notes.append(note)
            continue
        path = a.out if (a.out and len(runs) == 1) else \
            f"out/_77_state_t1_run{r['chara_id']}.json"
        json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        note["state_file"] = path
        notes.append(note)
        print(f"== run {r['chara_id']} 马娘 {r['card_id']} 上限 {r['max_act_level']} -> {path}")
        print("   卡组(卡/位/凸):", deck_str(note["deck_pos_break"]))
        print(f"   t{note['turn_t1']} 五维", dict(zip(AX, note["five_t1"])),
              "和", sum(note["five_t1"]), "上限档", note["caps_t1"],
              "体力", note["vital_t1"], "干劲", note["mot_t1"])
        print("   真机终局：演目Lv", note["act_lv_end"], "五维和", note["five_sum_end_real"],
              "实分", (f"{note['real_end']:,.0f}" if note["real_end"] else "-"))
    if len(notes) > 1:
        json.dump(notes, open("out/_77_t1_states.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("-> out/_77_t1_states.json（汇总）")


def deck_str(deck: list) -> str:
    return " ".join(f"{c[0]}@{c[1]}/{c[2]}凸" for c in deck)


if __name__ == "__main__":
    main()
