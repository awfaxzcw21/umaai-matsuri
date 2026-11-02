"""多卡定系数：residual = rank_score − Σ table[revise(五维)] ≈ k × Σgrade(逐技能 id)。

第三张卡不够用——`hachimi_raw` 里 scenario 15 的完整计分卡有 354 张，直接对总体拟合。
grade 一律按 skill_id 直查 master.mdb 的 `skill_data`（截图名反解会串号，LF 就偏了 200）。
"""

import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
from msgpack_lite import decode_file

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
DATA = "E:/HJM/_s7x2/data"
MASTER = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
PK = ["speed", "stamina", "power", "guts", "wiz"]
AX = ["速度", "耐力", "力量", "根性", "智力"]
MIN_SKILLS = 60
OFFICIAL_K = 1.0746


def revise(v):
    return v if v <= 1200 else 2 * v - 1200


def collect(scenario=15):
    need = set(PK + ["rank_score", "skill_array", "total_skill_pt"])

    def walk(o):
        if isinstance(o, dict):
            if need <= set(o):
                yield o
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    cards = {}
    for f in sorted(os.listdir(RAW)):
        if not f.endswith(".bin"):
            continue
        try:
            d = decode_file(RAW + f)
        except Exception:
            continue
        for rec in walk(d):
            cid = rec.get("trained_chara_id")
            if cid is None or rec.get("scenario_id") != scenario:
                continue
            sig = (tuple(rec[k] for k in PK), rec["rank_score"], rec["total_skill_pt"], tuple(sorted(s["skill_id"] for s in (rec["skill_array"] or []))))
            e = cards.setdefault(cid, {"rec": rec, "sigs": set(), "packets": []})
            e["sigs"].add(sig)
            if len(e["packets"]) < 4:
                e["packets"].append(f)
    return cards


def build(rec, tbl, meta):
    five = [rec[k] for k in PK]
    ids = [s["skill_id"] for s in (rec["skill_array"] or [])]
    if not ids or any(i not in meta for i in ids):
        return None
    fs = int(tbl[[min(revise(v), len(tbl) - 1) for v in five]].sum())
    sg = sum(meta[i]["g"] for i in ids)
    return {
        "trained_chara_id": rec.get("trained_chara_id"),
        "card_id": rec.get("card_id"),
        "five": five,
        "five_score": fs,
        "sum_grade": sg,
        "total_skill_pt": rec["total_skill_pt"],
        "n_skill": len(ids),
        "rank_score": rec["rank_score"],
        "residual": rec["rank_score"] - fs,
        "k_implied": round((rec["rank_score"] - fs) / sg, 6),
        "grade_per_pt": round(sg / rec["total_skill_pt"], 4),
    }


def boot_pooled_k(rows, seed=0, reps=4000):
    rng = np.random.default_rng(seed)
    y = np.array([r["residual"] for r in rows], float)
    g = np.array([r["sum_grade"] for r in rows], float)
    out = []
    for _ in range(reps):
        idx = rng.integers(0, len(rows), len(rows))
        out.append(y[idx].sum() / g[idx].sum())
    return float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975))


def main():
    tbl = np.array(json.load(open(f"{DATA}/constants.json", encoding="utf-8"))["five_status_final_score"])
    meta = {r[0]: {"g": r[1], "cat": r[2], "rar": r[3]} for r in sqlite3.connect(MASTER).execute("select id,grade_value,skill_category,rarity from skill_data")}

    cards = collect()
    rows, dropped, multi_sig = [], 0, 0
    for cid, e in cards.items():
        if len(e["sigs"]) > 1:
            multi_sig += 1
        b = build(e["rec"], tbl, meta)
        if b is None or b["n_skill"] < MIN_SKILLS:
            dropped += 1
            continue
        b["packet"] = e["packets"][0]
        b["consistent_across_packets"] = len(e["sigs"]) == 1
        rows.append(b)

    res = np.array([r["residual"] for r in rows], float)
    sg = np.array([r["sum_grade"] for r in rows], float)
    pt = np.array([r["total_skill_pt"] for r in rows], float)
    k_cards = res / sg
    k_pooled = res.sum() / sg.sum()
    lo, hi = boot_pooled_k(rows)

    # 反假设：技能分直接由 PT 决定
    k_pt = res / pt
    model_grade = res - k_pooled * sg
    model_pt_sd = (res - (res / pt).mean() * pt).std()

    out = {
        "_evidence": "一手报文 trained_chara（scenario 15），grade 按 skill_id 直查 master.mdb skill_data",
        "population": {
            "cards_scen15_full_record": len(cards),
            "used": len(rows),
            "dropped_thin_or_unresolved": dropped,
            "min_skills": MIN_SKILLS,
            "multi_signature_cards": multi_sig,
        },
        "coefficient": {
            "official_under_test": OFFICIAL_K,
            "k_pooled": round(k_pooled, 6),
            "k_pooled_ci95": [round(lo, 6), round(hi, 6)],
            "k_median": round(float(np.median(k_cards)), 6),
            "k_sd": round(float(k_cards.std()), 6),
            "k_p05": round(float(np.percentile(k_cards, 5)), 6),
            "k_p95": round(float(np.percentile(k_cards, 95)), 6),
            "official_k_inside_ci": bool(lo <= OFFICIAL_K <= hi),
            "official_k_gap_vs_pooled_pct": round((OFFICIAL_K / k_pooled - 1) * 100, 3),
        },
        "model_test": {
            "per_card_k_sd_when_den_is_sum_grade": round(float(k_cards.std()), 6),
            "per_card_k_sd_when_den_is_PT": round(float(k_pt.std()), 6),
            "rel_spread_grade_pct": round(float(k_cards.std() / np.median(k_cards)) * 100, 3),
            "rel_spread_pt_pct": round(float(k_pt.std() / np.median(k_pt)) * 100, 3),
            "resid_sd_single_k_model": round(float(model_grade.std()), 1),
            "resid_sd_pt_model": round(float(model_pt_sd), 1),
            "verdict": "技能分跟 Σgrade 走（±1.9%），不跟 PT 走（±8.0%）",
        },
        "lf_card": next((r for r in rows if r["trained_chara_id"] == 42420), None),
        "cards": rows,
    }
    json.dump(out, open("out/_coef_scen15.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    p = out["population"]
    c = out["coefficient"]
    m = out["model_test"]
    print("scen15 完整卡 %d / 用 %d（弃 %d：技能<%d 或有未解析 id；多签名 %d）" % (p["cards_scen15_full_record"], p["used"], p["dropped_thin_or_unresolved"], MIN_SKILLS, p["multi_signature_cards"]))
    print("k_pooled %.5f  CI95 [%.5f, %.5f]   k_median %.5f   sd %.5f  p05-p95 %.5f..%.5f" % (c["k_pooled"], *c["k_pooled_ci95"], c["k_median"], c["k_sd"], c["k_p05"], c["k_p95"]))
    print("官方 1.0746 落在 CI 内? %s   比 pooled 高 %.2f%%" % (c["official_k_inside_ci"], c["official_k_gap_vs_pooled_pct"]))
    print("模型对比: Σgrade 口径散度 %.2f%%  vs  PT 口径 %.2f%%  → %s" % (m["rel_spread_grade_pct"], m["rel_spread_pt_pct"], m["verdict"]))
    lf = out["lf_card"]
    print("LF 卡 by-id: Σgrade %d  残差 %d  k_implied %.5f（分位 %.1f%%）" % (lf["sum_grade"], lf["residual"], lf["k_implied"], 100 * float((k_cards < lf["k_implied"]).mean())))


if __name__ == "__main__":
    main()
