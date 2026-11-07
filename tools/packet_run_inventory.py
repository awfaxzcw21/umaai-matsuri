#!/usr/bin/env python3
"""#77 第 1 步：把报文语料按「局」列成一张清单——每局用的是哪套卡组、哪个马娘、
演目上限几档、开局五维多少、终局五维 / 技能 / 实分多少。

动机：现有天花板读数（#100 的「0/48 达到 85,600」）跑在 `_state_t30.json` 的锚点卡组上，
而那副是 **R 吾妻（10149）⇒ max_act_level=6**；用户实际打的那副里有 **SSR 吾妻（30323）⇒ 上限 10**。
要把天花板测在「你正在打的那套卡组」上，先得知道报文里到底有几副卡组、哪几局是同一副。

局边界用 `chara_info.start_time`（同一局内恒定）切，不用 turn 回落猜。
分数口径与 `tools/lf_packet_scan.py:42/319` 同一套：
`Σ five_status_final_score[revise(五维)] + 1.0746 × Σ grade_value(技能 id)`。

    python tools/packet_run_inventory.py --out out/_77_run_inventory.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msgpack_lite import decode_file  # noqa: E402
from lf_packet_scan import K_SCEN15, grade_sum, revise, score_tables, stat_score  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
PK = ["speed", "stamina", "power", "guts", "wiz"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/_77_run_inventory.json")
    a = ap.parse_args()

    tbl, grade = score_tables()
    files = sorted(os.listdir(RAW))

    runs = {}
    order = []
    for f in files:
        try:
            d = decode_file(RAW + f)
        except Exception:
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = data.get("chara_info")
        if not isinstance(ci, dict) or not all(k in ci for k in PK):
            continue
        t = ci.get("turn")
        st = ci.get("start_time")
        if not isinstance(t, int) or st is None:
            continue
        if st not in runs:
            runs[st] = {"start_time": str(st), "chara_id": ci.get("single_mode_chara_id"),
                        "card_id": ci.get("card_id"), "first_file": f, "last_file": f,
                        "min_turn": t, "max_turn": t, "n_pkt": 0,
                        "t1": None, "five_first": None, "skills_first": None,
                        "five_last": [ci.get(k) for k in PK], "sp_last": ci.get("skill_point"),
                        "skills_last": ci.get("skill_array"), "deck": None,
                        "max_act_level": None, "act_lv_last": None, "act_gauge_last": None,
                        "best_key": (t, 0),
                        "mot_last": ci.get("motivation"), "vital_last": ci.get("vital"),
                        "caps_last": [ci.get("max_" + k) for k in PK],
                        "inherit": [ci.get("succession_trained_chara_id_1"),
                                    ci.get("succession_trained_chara_id_2")],
                        "effect_last": sorted(set(ci.get("chara_effect_id_array") or []))}
            order.append(st)
        r = runs[st]
        r["n_pkt"] += 1
        r["last_file"] = f
        r["max_turn"] = max(r["max_turn"], t)
        r["min_turn"] = min(r["min_turn"], t)
        if t <= 2 and r["t1"] is None:
            r["t1"] = {"turn": t, "file": f,
                       "five": [ci.get(k) for k in PK],
                       "caps": [ci.get("max_" + k) for k in PK],
                       "vital": ci.get("vital"), "max_vital": ci.get("max_vital"),
                       "motivation": ci.get("motivation"),
                       "sp": ci.get("skill_point"),
                       "deck": [[c.get("support_card_id"), c.get("position"),
                                 c.get("limit_break_count")]
                                for c in (ci.get("support_card_array") or [])],
                       "scenario_id": ci.get("scenario_id")}
            r["five_first"] = r["t1"]["five"]
            r["skills_first"] = ci.get("skill_array")
        # 终局快照取「turn 最大、同 turn 里技能条数最多」的那条：
        # 结算页的 chara_info 会把技能数组清空，直接取最后一条会把终值读成 1 条技能。
        key = (t, len(ci.get("skill_array") or []))
        if key >= r["best_key"]:
            r["best_key"] = key
            r["five_last"] = [ci.get(k) for k in PK]
            r["sp_last"] = ci.get("skill_point")
            r["skills_last"] = ci.get("skill_array")
            r["caps_last"] = [ci.get("max_" + k) for k in PK]
            r["mot_last"] = ci.get("motivation")
            r["vital_last"] = ci.get("vital")
        deck = ci.get("support_card_array")
        if deck:
            r["deck"] = sorted([[c.get("support_card_id"), c.get("position"),
                                c.get("limit_break_count"), c.get("exp")]
                               for c in deck])
        fds = data.get("festa_data_set")
        if isinstance(fds, dict):
            finfo = fds.get("festa_info") or {}
            mal = finfo.get("max_act_level")
            if isinstance(mal, int):
                r["max_act_level"] = mal
            acts = fds.get("act_info_array")
            if acts:
                r["act_lv_last"] = [x.get("level") for x in acts]
                r["act_gauge_last"] = [x.get("gauge") for x in acts]

    out = []
    for st in order:
        r = runs[st]
        if not r["deck"]:
            continue
        r["stat_score_last"] = stat_score(tbl, r["five_last"])
        gsum, r["n_skills"] = grade_sum(grade, r["skills_last"])
        r["skill_score_last"] = K_SCEN15 * gsum
        r["real_last"] = r["stat_score_last"] + r["skill_score_last"]
        r["five_sum_last"] = sum(int(x or 0) for x in r["five_last"])
        r["five_sum_first"] = (sum(int(x or 0) for x in r["five_first"])
                               if r["five_first"] else None)
        r["deck_ids"] = [c[0] for c in r["deck"]]
        out.append(r)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"切出 {len(out)} 局（带卡组）")
    print(f"{'first':>7} {'chara':>7} {'cap':>4} {'turns':>8} {'局报文':>6} "
          f"{'五维和首':>8} {'五维和终':>8} {'五维分':>7} {'技能分':>7} {'总分':>8} {'技能数':>5}  卡组(id/凸)")
    for r in sorted(out, key=lambda x: -(x["real_last"] or 0)):
        dec = f"{r['real_last']:,.0f}" if r["real_last"] else "-"
        s0 = f"{r['five_sum_first']:,}" if r["five_sum_first"] is not None else "-"
        cards = " ".join(f"{c[0]}/{c[2]}" for c in r["deck"])
        print(f"{r['first_file'][:6]:>7} {r['chara_id']:>7} {str(r['max_act_level']):>4} "
              f"{str(r['min_turn']) + '-' + str(r['max_turn']):>8} {r['n_pkt']:>6} "
              f"{s0:>8} {r['five_sum_last']:>8} "
              f"{r['stat_score_last']:>7,.0f} {r['skill_score_last']:>7,.0f} {dec:>8} "
              f"{r['n_skills']:>5}  {cards}")
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
