#!/usr/bin/env python3
"""#55 真机侧分数分布：从报文里捞**带 rank_score 的练成马娘记录**，看 LG15 在真机侧长什么样。

判据沿用 `tools/inherit_gain_dosage.py` 的 S 类定义（`rank_score + speed + skill_array` 三键齐全
= 练成马娘记录），不新造判据；`scenario_id == 15` 才收。

⚠ 这种记录**大部分不是我们自己的马**——同一判据在名册、排行榜、对手马、继承池、通知推送里都出现。
所以本脚本先按 `落点 jpath + viewer_id` 分族，再谈分布；把 426 条混在一起算「真机批发率」会把
排行榜上别人的高分马当成我们的成绩（本脚本第一版就踩过，见 docs/新靶读数_LG15_20261105.md §1）。

产出：
  1. 逐快照 CSV（`out/_real_score_dist_20261105.csv`，含五维/技能/比赛三条腿字段）
  2. 分族后的 rank_score 分布 + 与 LG / LG15 / LG24 / LF 四档线咬合的位置
  3. 同五维和分桶「我们自己 vs 别人」的分数对照——把「差多少分」拆成量/价两条腿
     只做形状描述，不做拟合、不写常数、不接线。
"""
from __future__ import annotations

import collections
import csv
import glob
import os
import statistics as st
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import inherit_gain_dosage as IG  # noqa: E402  复用 snap_paths / 字段口径
import pt_ledger_scan as PS  # noqa: E402  复用 get_ci，局键与 #57 同构

RAW = IG.RAW
SCEN = 15
AXES = IG.AXES
LINES = [("LG", 76000), ("LG15", 85600), ("LG24", 90900), ("LF", 91400)]


def main():
    files = sorted(glob.glob(os.path.join(RAW, "*.bin")), key=lambda p: os.path.basename(p))
    snaps = {}
    runs = {}
    n_fail = 0
    for path in files:
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            n_fail += 1
            continue
        if not isinstance(d, dict):
            continue
        data = d.get("data")
        if isinstance(data, dict):
            ci = PS.get_ci(data)
            if isinstance(ci, dict) and ci.get("turn") is not None and ci.get("scenario_id") == SCEN:
                v = d.get("viewer_id")
                if v is None and isinstance(d.get("data_headers"), dict):
                    v = d["data_headers"].get("viewer_id")
                rk = "%s@%s@%s" % (v, ci.get("single_mode_chara_id"), ci.get("start_time"))
                r = runs.setdefault(rk, {"run": rk, "viewer": v, "chara": ci.get("single_mode_chara_id"),
                                         "card_id": ci.get("card_id"), "start_time": ci.get("start_time"),
                                         "max_turn": 0})
                if isinstance(ci.get("turn"), int):
                    r["max_turn"] = max(r["max_turn"], ci["turn"])
        for node, jp in IG.snap_paths(d.get("data")):
            if node.get("scenario_id") != SCEN:
                continue
            rs = node.get("rank_score")
            if not isinstance(rs, (int, float)):
                continue
            key = (node.get("trained_chara_id"), node.get("viewer_id"), int(rs))
            five = [int(node.get(a) or 0) for a in AXES]
            par = []
            for p in (node.get("succession_chara_array") or []):
                if isinstance(p, dict):
                    prs = p.get("rank_score")
                    psum = sum(int(p.get(a) or 0) for a in AXES)
                    par.append((p.get("card_id"), p.get("rarity"),
                                int(prs) if isinstance(prs, (int, float)) else None,
                                len(p.get("factor_info_array") or []), psum))
            snaps[key] = {
                "score": int(rs), "five": five, "sum": sum(five),
                "card": node.get("card_id"), "chara": node.get("single_mode_chara_id"),
                "rank": node.get("rank"), "route": node.get("route_id"),
                "fans": node.get("fans"), "succ_num": node.get("succession_num"),
                "n_fac": len(node.get("factor_info_array") or []),
                "n_fac_ext": len(node.get("factor_extend_array") or []),
                # 分数三条腿：五维 / 技能 / 比赛（+适性）——#55 要把「差 1 万分」拆到腿
                "spt": node.get("total_skill_pt"),
                "n_skill": len(node.get("skill_array") or []),
                "wins": node.get("wins"),
                "n_race": len(node.get("race_result_list") or []),
                "grade": node.get("chara_grade"),
                "apt": tuple(node.get(k) for k in
                             ("proper_distance_short", "proper_distance_mile",
                              "proper_distance_middle", "proper_distance_long")),
                "par": par, "jpath": jp, "file": os.path.basename(path),
                "viewer": node.get("viewer_id"), "owner": node.get("owner_viewer_id"),
                "ct": node.get("create_time"), "seed": node.get("chara_seed"),
            }
    print(f"报文 {len(files)} 个 / 解码失败 {n_fail} ⇒ scen15 终局快照（去重）N={len(snaps)}")
    if not snaps:
        print("!! 没有 scen15 快照 ⇒ 本库尚跑不出真机分数分布，先扩样本")
        return
    rows = sorted(snaps.values(), key=lambda r: -r["score"])
    print(f"\n{'score':>7s} {'五维和':>6s} {'五维明细':26s} {'卡':>7s} {'因子':>4s} "
          f"{'粉丝':>7s} {'双亲(card/rar/score/因子/五维和)':30s}")
    for r in rows:
        par = " ".join(f"{c}/{ra}/{s}/{f}/{ps}" for c, ra, s, f, ps in r["par"]) or "—"
        print(f"{r['score']:7d} {r['sum']:6d} {str(r['five']):26s} {str(r['card']):>7s} "
              f"{r['n_fac']:4d} {str(r['fans']):>7s} {par:30s}")
    sc = sorted(r["score"] for r in rows)
    print(f"\n分布: mean={st.mean(sc):,.0f} med={st.median(sc):,.0f} max={sc[-1]:,} "
          f"min={sc[0]:,} std={(st.pstdev(sc) if len(sc) > 1 else 0):,.0f}")
    for name, th in LINES:
        c = sum(1 for x in sc if x >= th)
        print(f"  P(>= {name} {th:,}) = {c}/{len(sc)}" + (f"   最高分距线 {th - sc[-1]:,}" if c == 0 else ""))

    # ---------------------------------------------------------- 归属与对齐
    print(f"\n【归属】落点 jpath：")
    for k, v in collections.Counter(r["jpath"] for r in rows).most_common():
        print(f"  {v:5d}  {k}")
    print("  viewer_id：" + str(collections.Counter(str(r["viewer"]) for r in rows).most_common(6)))
    print("  owner_viewer_id：" + str(collections.Counter(str(r["owner"]) for r in rows).most_common(6)))
    n_horse = len({k[0] for k in snaps})
    print(f"  记录 {len(snaps)} 条 / 去重后 trained_chara_id {n_horse} 匹"
          f"（同马多分数=随时间演进，取每条独立计）")
    ct = sorted(r["ct"] for r in rows if isinstance(r["ct"], (int, float)))
    if ct:
        print(f"  create_time 跨度：{ct[0]} .. {ct[-1]}（中位 {ct[len(ct)//2]}）")

    print(f"\n【对齐】本库抓到实机流量的 scen15 局 {len(runs)} 个：")
    for r in sorted(runs.values(), key=lambda x: str(x["start_time"])):
        cands = [s for s in rows if s["chara"] == r["chara"]
                 and isinstance(s["ct"], (int, float)) and isinstance(r["start_time"], (int, float))
                 and s["ct"] >= r["start_time"]]
        best = max(cands, key=lambda s: s["score"]) if cands else None
        print(f"  {r['run']}  max_turn={r['max_turn']:2d} card={r['card_id']}"
              f"  ⇒ 名册里晚于开局的同马记录 {len(cands)} 匹"
              + (f"，其中最高 rank_score={best['score']:,}（五维和 {best['sum']:,}）"
                 if best else "，无可对齐记录"))
    per_chara = collections.Counter(r["chara"] for r in rows)
    print(f"  名册按 single_mode_chara_id：{len(per_chara)} 个角色，"
          f"每角色记录数 max={max(per_chara.values())} med={st.median(sorted(per_chara.values())):.0f}")
    print("  名册按卡（card_id）最高分：")
    by_card = collections.defaultdict(list)
    for r in rows:
        by_card[r["card"]].append(r["score"])
    for c_, v in sorted(by_card.items(), key=lambda kv: -max(kv[1]))[:12]:
        print(f"    card {c_}: n={len(v):3d} max={max(v):7,d} med={int(st.median(v)):7,d}")

    def psum(r):
        return sum(ps for _, _, _, _, ps in r["par"])

    def med(vals):
        return st.median(vals) if vals else 0

    hi = [r for r in rows if r["score"] >= 85600]
    lo = [r for r in rows if r["score"] < 85600]
    print(f"\n【LG15 上/下 分组中位（只描述，不拟合）】>=85,600 n={len(hi)} / <85,600 n={len(lo)}")
    for label, f in [("五维和", lambda r: r["sum"]),
                     ("单轴最大", lambda r: max(r["five"])),
                     ("自马因子条数", lambda r: r["n_fac"]),
                     ("因子扩展条数", lambda r: r["n_fac_ext"]),
                     ("粉丝", lambda r: int(r["fans"] or 0)),
                     ("继承代数 succession_num", lambda r: int(r["succ_num"] or 0)),
                     ("双亲五维和合计", psum),
                     ("双亲因子条数合计", lambda r: sum(f_ for _, _, _, f_, _ in r["par"])),
                     ("双亲里 rarity=5 的个数", lambda r: sum(1 for _, ra, _, _, _ in r["par"] if ra == 5))]:
        hv, lv = med([f(r) for r in hi]), med([f(r) for r in lo])
        print(f"  {label:24s} 上={hv:>10,.0f}  下={lv:>10,.0f}  差={hv - lv:>+10,.0f}")
    print(f"  单轴最大值（全库）：{[max(r['five'][i] for r in rows) for i in range(5)]}"
          f"  逐轴对应 {AXES}")

    # 按落点分族：自己的终局 vs 排行榜/对手/继承池（别人的马）——不能混着当「真机我们这局」
    OWN = ("single_mode_finish_common",)

    def family(r):
        if OWN[0] in r["jpath"]:
            return "自己终局(finish)"
        if "ranking_user_list" in r["jpath"]:
            return "排行榜(别人)"
        if "opponent_chara" in r["jpath"]:
            return "对手马(别人)"
        if "succession" in r["jpath"]:
            return "继承池(可借种)"
        if "directory_card_array" in r["jpath"]:
            return "名马卡片夹(别人)"
        return "其它/自己名册"

    print("\n【按落点分族】")
    fam = collections.defaultdict(list)
    for r in rows:
        fam[family(r)].append(r)
    for f_, rs in sorted(fam.items(), key=lambda kv: -len(kv[1])):
        sc_ = sorted(x["score"] for x in rs)
        own_viewer = sum(1 for x in rs if str(x["viewer"]) == "238107205")
        print(f"  {f_:18s} n={len(rs):4d} 自己账号={own_viewer:3d} "
              f"mean={st.mean(sc_):9,.0f} med={st.median(sc_):9,.0f} max={sc_[-1]:9,} "
              f"五维和 med={med([x['sum'] for x in rs]):7,.0f} max={max(x['sum'] for x in rs):7,} "
              f"P(>=LG15)={sum(1 for x in sc_ if x >= 85600)}/{len(sc_)}")
        if f_.startswith("自己") or f_ == "其它/自己名册":
            for x in rs:
                print(f"      {x['score']:7,d} 五维和 {x['sum']:6,d} {str(x['five']):26s} "
                      f"chara={x['chara']} card={x['card']} 因子={x['n_fac']} "
                      f"扩展={x['n_fac_ext']} 粉丝={x['fans']} ct={x['ct']} "
                      f"viewer={x['viewer']} {x['file']}")

    print(f"\n  双亲记录里五维和恒 0 ⇒ 该形态不带 {AXES}；"
          f"能带的只有 card/rarity/因子条数（本表已列）。")

    # 分数三条腿：五维 / 技能 / 比赛。`n_skill == 1` 的记录只有固有技能（PT 没换成技能
    # 或该形态不展开），与满技能马不在同一价上——分桶比较必须先剔除。
    print("\n【三条腿中位：我们自己账号的马 vs 排行榜/对手（别人）】")
    def leg(rs, label):
        if not rs:
            print(f"  {label:26s} —")
            return
        print(f"  {label:26s} n={len(rs):3d} 五维和={med([r['sum'] for r in rs]):7,.0f} "
              f"total_skill_pt={med([int(r['spt'] or 0) for r in rs]):7,.0f} "
              f"技能数={med([r['n_skill'] for r in rs]):5,.0f} "
              f"wins={med([int(r['wins'] or 0) for r in rs]):4,.0f} "
              f"出赛={med([r['n_race'] for r in rs]):4,.0f} 分数={med([r['score'] for r in rs]):8,.0f}")
    own_h = [r for r in rows if str(r["viewer"]) == "238107205" and r["jpath"] !=
             ".single_mode_finish_common.trained_chara[]" and r["score"] > 0]
    race_runs = [r for r in rows if r["jpath"].endswith("trained_chara[]") and
                 "single_mode_finish" in r["jpath"] and str(r["viewer"]) == "238107205"]
    oth_full = [r for r in rows if str(r["viewer"]) != "238107205" and r["n_skill"] > 1]
    oth_1 = [r for r in rows if str(r["viewer"]) != "238107205" and r["n_skill"] == 1]
    leg(own_h, "自己名册/考试高分位(真练成)")
    leg(race_runs, "自己终局(自选比赛刷局)")
    leg(oth_full, "别人·技能数>1")
    leg(oth_1, "别人·技能数=1")
    leg([r for r in oth_full if r["score"] >= 85600], "别人·技能数>1 且 >=LG15")

    print("\n  五维和 vs 分数的形状（只描述，不拟合）：")
    pairs = sorted((r["sum"], r["score"]) for r in rows)
    for s_, sc_ in pairs:
        print(f"    五维和 {s_:5d} → score {sc_:7,d}")
    print(f"\n  演目上限侧（真机各局末值 Lv 向量）见 docs/外出演目Lv一手_20261105.md C5 "
          f"与 out/_outing_act_level_turns.csv 的 板_Lv 列；本脚本不重复解。")

    out_csv = os.path.join("out", "_real_score_dist_20261105.csv")
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["score", "五维和"] + list(AXES) +
                   ["card", "chara", "rank", "route", "fans", "succ_num", "因子", "因子扩展",
                    "total_skill_pt", "技能数", "wins", "出赛", "chara_grade", "适性(短/一/中/长)",
                    "viewer", "owner", "create_time", "chara_seed", "落点族", "jpath",
                    "双亲(card/rar/因子/五维和)", "file"])
        for r in rows:
            w.writerow([r["score"], r["sum"]] + r["five"] +
                       [r["card"], r["chara"], r["rank"], r["route"], r["fans"], r["succ_num"],
                        r["n_fac"], r["n_fac_ext"], r["spt"], r["n_skill"], r["wins"], r["n_race"],
                        r["grade"], "/".join(str(x) for x in r["apt"]),
                        r["viewer"], r["owner"], r["ct"], r["seed"],
                        family(r), r["jpath"],
                        ";".join(f"{c}/{ra}/{f_}/{ps}" for c, ra, _s, f_, ps in r["par"]),
                        r["file"]])
    print(f"  ⇒ 逐快照落盘 {out_csv}（{len(rows)} 行），后续切片不必再全量扫。")


if __name__ == "__main__":
    main()
