"""LF 靶子：从真机排行榜报文提取 trained_chara，用官方查表口径复现评价点。

一手来源：E:/HJM/jidianbeiAI0.22/out/hachimi_raw/001342_0068_notify_response.bin
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
from msgpack_lite import decode_file

PACKET = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/001342_0068_notify_response.bin"
MASTER = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
DATA = "E:/HJM/_s7x2/data"
LF_LINE = 91400
COEF = 1.0746

PK = ["speed", "stamina", "power", "guts", "wiz"]
AX = ["速度", "耐力", "力量", "根性", "智力"]


def revise(v):
    return v if v <= 1200 else 2 * v - 1200


def load():
    tbl = json.load(open(f"{DATA}/constants.json", encoding="utf-8"))["five_status_final_score"]
    lim = json.load(open(f"{DATA}/scenario_matsuri.json", encoding="utf-8"))["limits"]
    text = json.load(open(f"{DATA}/text_data_dict.json", encoding="utf-8"))
    cards = json.load(open(f"{DATA}/cardDB.json", encoding="utf-8"))
    tc = decode_file(PACKET)["data"]["directory_card_array"][90]["trained_chara"]
    return tbl, lim, text, cards, tc


def main():
    tbl, lim, text, cards, tc = load()

    def score(v):
        return tbl[min(revise(v), len(tbl) - 1)]

    five = [tc[k] for k in PK]
    total5 = sum(five)
    stat_rev = sum(score(v) for v in five)
    stat_dir = sum(tbl[min(v, len(tbl) - 1)] for v in five)

    db = sqlite3.connect(MASTER)
    skills = json.load(open("out/_lf_resolved.json", encoding="utf-8"))
    sum_grade_names = sum(s["grade"] for s in skills)  # 截图名反解，会串号
    ids = sorted({s["skill_id"] for s in tc["skill_array"]})
    sum_grade = int(db.execute("select sum(grade_value) from skill_data where id in (%s)" % ",".join("?" * len(ids)), ids).fetchone()[0])
    resid = tc["rank_score"] - stat_rev

    ctype = {0: "速", 1: "耐", 2: "力", 3: "根", 4: "智", 5: "友"}

    deck = []
    for s in tc["support_card_list"]:
        cid = s["support_card_id"]
        row = db.execute("select chara_id from support_card_data where id=?", [cid]).fetchone()
        ref = cards.get(str(cid))
        deck.append(
            {
                "support_card_id": cid,
                "name": text["75"].get(str(cid)),
                "chara": text["6"].get(str(row[0])),
                "type": ctype.get(ref["cardType"]) if ref else None,
                "exp": s.get("exp"),
            }
        )

    def card_name(cid, cat):
        row = db.execute("select chara_id from card_data where id=?", [cid]).fetchone()
        return text[cat].get(str(cid)), text["6"].get(str(row[0]))

    main_name, main_chara = card_name(tc["card_id"], "4")
    succ = [{"card_id": s["card_id"], "name": card_name(s["card_id"], "4")[0]} for s in tc["succession_chara_array"]]

    pop_coef = None
    if os.path.exists("out/_coef_scen15.json"):
        cf = json.load(open("out/_coef_scen15.json", encoding="utf-8"))
        c = cf["coefficient"]
        pop_coef = {
            "n_cards": cf["population"]["used"],
            "k_pooled": c["k_pooled"],
            "k_pooled_ci95": c["k_pooled_ci95"],
            "k_median": c["k_median"],
            "per_card_k_sd": c["k_sd"],
            "predicted_total_with_population_k": None,
            "model_noise_1sd": round(c["k_sd"] * sum_grade),
        }
    K_USE = pop_coef["k_median"] if pop_coef else COEF

    shape = [v / total5 for v in five]
    pt_to_grade = sum_grade / tc["total_skill_pt"]

    def five_sum_needed(pt):
        skill_part = K_USE * pt * pt_to_grade

        def enough(total):
            return sum(score(round(total * p)) for p in shape) + skill_part >= LF_LINE

        lo, hi = 6000, 14000
        for _ in range(60):
            mid = (lo + hi) // 2
            if enough(mid):
                hi = mid
            else:
                lo = mid
        return hi

    frontier = [{"total_skill_pt": pt, "five_sum_needed": five_sum_needed(pt)} for pt in (7000, 8000, 9000, 10000, tc["total_skill_pt"])]
    if pop_coef:
        pop_coef["predicted_total_with_population_k"] = round(stat_rev + K_USE * sum_grade, 1)

    out = {
        "_evidence": "一手报文（真机排行榜 trained_chara），非推测、非拟合",
        "source": {
            "packet": PACKET,
            "decoded_by": "E:/HJM/jidianbeiAI0.22/msgpack_lite.py",
            "json_path": "data.directory_card_array[90].trained_chara",
            "replicated_in": [
                "001343_0070_notify_response.bin",
                "234751_0064_notify_response.bin:ranking_user_list[0]",
                "234808_0066_notify_response.bin",
            ],
        },
        "identity": {
            "card_id": tc["card_id"],
            "card_name": main_name,
            "chara": main_chara,
            "viewer_id": tc["viewer_id"],
            "trained_chara_id": tc["trained_chara_id"],
            "single_mode_chara_id": tc["single_mode_chara_id"],
            "scenario_id": tc["scenario_id"],
            "route_id": tc["route_id"],
            "chara_grade": tc["chara_grade"],
            "rarity": tc["rarity"],
            "talent_level": tc["talent_level"],
            "create_time": tc.get("create_time"),
        },
        "five_status": {
            "axis_order": AX,
            "packet_keys": PK,
            "values": five,
            "sum": total5,
            "base_cap": lim["five_status_limit_base"],
            "upper_cap": lim["five_status_limit_upper"],
            "over_base_cap": {AX[i]: five[i] - lim["five_status_limit_base"][i] for i in range(5)},
            "all_axes_over_base_cap": all(five[i] > lim["five_status_limit_base"][i] for i in range(5)),
            "headroom_to_upper": {AX[i]: lim["five_status_limit_upper"][i] - five[i] for i in range(5)},
        },
        "skills": {
            "total_skill_pt": tc["total_skill_pt"],
            "skill_count": len(ids),
            "sum_grade_by_id": sum_grade,
            "sum_grade_by_name_legacy": sum_grade_names,
            "list": "out/_lf_resolved.json",
        },
        "score_verify": {
            "rank_score": tc["rank_score"],
            "rank_id": tc["rank"],
            "formula": "評価点 = Σ table[revise(v)] + k × Σ grade_value(逐技能 id)",
            "stat_score_with_revise": stat_rev,
            "stat_score_direct_no_revise": stat_dir,
            "predicted_total": round(stat_rev + COEF * sum_grade, 1),
            "diff_vs_official": round(stat_rev + COEF * sum_grade - tc["rank_score"], 1),
            "residual_after_five": resid,
            "residual_over_sum_grade": round(resid / sum_grade, 4),
            "residual_over_skill_pt": round(resid / tc["total_skill_pt"], 4),
            "population_coef": pop_coef,
        },
        "deck": deck,
        "succession": succ,
        "aptitude": {k: v for k, v in tc.items() if k.startswith("proper_")},
        "races": {
            "wins": len(tc["race_result_list"]),
            "running_style": tc["running_style"],
            "turns": [r.get("turn") for r in tc["race_result_list"]],
            "program_ids": [r.get("program_id") for r in tc["race_result_list"]],
            "result_ranks": [r.get("result_rank") for r in tc["race_result_list"]],
        },
        "scenario_data": tc["scenario_data_array"],
        "factors": {
            "factor_info": len(tc["factor_info_array"]),
            "factor_extend": len(tc["factor_extend_array"]),
            "succession_num": tc["succession_num"],
            "fans": tc["fans"],
        },
        "derived": {
            "shape_ratio": [round(p, 4) for p in shape],
            "pt_to_grade_rate": round(pt_to_grade, 4),
            "marginal_score_per_pt_official_k": round(COEF * pt_to_grade, 4),
            "marginal_score_per_pt_k_in_use": round(K_USE * pt_to_grade, 4),
            "lf_line_score": LF_LINE,
            "lf_line_rank_id": 124,
            "this_card_rank_id": tc["rank"],
            "frontier_five_sum_at_lf_line": frontier,
        },
    }
    json.dump(out, open("out/_lf_target.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("five", five, "sum", total5)
    print("over_base_cap", out["five_status"]["over_base_cap"], "all_over", out["five_status"]["all_axes_over_base_cap"])
    print("stat_rev", stat_rev, "dir_no_revise", stat_dir)
    print("predicted", out["score_verify"]["predicted_total"], "actual", tc["rank_score"], "diff", out["score_verify"]["diff_vs_official"])
    print("resid", resid, "/grade", out["score_verify"]["residual_over_sum_grade"], "/pt", out["score_verify"]["residual_over_skill_pt"])
    print("grade by skill_id", sum_grade, "(名反解 legacy %d，差 %d)" % (sum_grade_names, sum_grade - sum_grade_names))
    if pop_coef:
        print("population k(n=%d): median %.5f  pooled %.5f  每卡 sd %.5f  → 预测总分 %.1f ±%.0f"
              % (pop_coef["n_cards"], pop_coef["k_median"], pop_coef["k_pooled"], pop_coef["per_card_k_sd"],
                 pop_coef["predicted_total_with_population_k"], pop_coef["model_noise_1sd"]))
    print("deck", [(d["name"], d["type"]) for d in deck])
    print("frontier", frontier)


if __name__ == "__main__":
    main()
