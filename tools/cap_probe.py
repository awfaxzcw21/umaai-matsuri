"""上限延展溯源：真机每轴都超出 `five_status_limit_base`，抬升到底是跟谁走的？

一次扫包，逐卡记下五维、超上限量、继承因子（factor_info_array）、卡组（support_card_list）。
只读报文与 master.mdb，不动引擎。目的是判断「上限延展」是有机制来源，还是只能靠常数拟合。
"""

import json
import sqlite3
import sys

sys.path.insert(0, "tools")
from score_coef import AX, DATA, MASTER, PK, collect

MIN_SKILLS = 60


def rows():
    base = json.load(open(f"{DATA}/scenario_matsuri.json", encoding="utf-8"))["limits"]["five_status_limit_base"]
    names = {}
    try:
        db = sqlite3.connect(MASTER)
        for fid, name in db.execute("select id, name from text_data_dict"):
            names[str(fid)] = name
    except Exception:
        pass
    cards = collect()
    out = []
    for cid, e in cards.items():
        rec = e["rec"]
        ids = [s["skill_id"] for s in (rec.get("skill_array") or [])]
        if len(ids) < MIN_SKILLS:
            continue
        five = [rec[k] for k in PK]
        out.append({
            "trained_chara_id": cid,
            "card_id": rec.get("card_id"),
            "five": five,
            "over": [five[i] - base[i] for i in range(5)],
            "factors": sorted({f["factor_id"] for f in (rec.get("factor_info_array") or [])}),
            "factor_levels": sorted((f["factor_id"], f.get("level")) for f in (rec.get("factor_info_array") or [])),
            "deck": sorted(s["support_card_id"] for s in (rec.get("support_card_list") or [])),
            "scenario_data": sorted((d.get("type"), d.get("value")) for d in (rec.get("scenario_data_array") or [])),
            "talent_level": rec.get("talent_level"),
            "succession_num": rec.get("succession_num"),
            "fans": rec.get("fans"),
            "rank_score": rec.get("rank_score"),
        })
    return base, out, names


def main():
    base, cards, names = rows()
    print(f"用 {len(cards)} 张 scen15 完整卡  base={base}")

    for i in range(5):
        ov = sorted((c["over"][i] for c in cards), reverse=True)
        hit = sum(1 for c in cards if c["over"][i] >= ov[0])
        print(f"{AX[i]}: 天花板 {base[i] + ov[0]}（超 {ov[0]}，正好顶到 {hit} 张）超出量分布 p50 {ov[len(ov) // 2]} p90 {ov[int(len(ov) * 0.1)]}")

    # 速度轴是主战场：拿「没超上限」的卡和「顶到天花板」的卡对比因子与卡组
    zero = [c for c in cards if c["over"][0] <= 0]
    topv = max(c["over"][0] for c in cards)
    top = [c for c in cards if c["over"][0] >= topv - 2]
    print(f"\n速度未超上限 {len(zero)} 张 / 顶到天花板(≥{topv - 2}) {len(top)} 张")

    def presence(group, key):
        seen = {}
        for c in group:
            for x in c[key]:
                seen[x] = seen.get(x, 0) + 1
        return seen

    fz, ft = presence(zero, "factors"), presence(top, "factors")
    allf = set(fz) | set(ft)
    print("因子  |  零超上限卡里的出现率  |  顶天花板卡里的出现率   （只看有区分度的）")
    for f in sorted(allf, key=lambda x: (ft.get(x, 0) / max(len(top), 1)) - (fz.get(x, 0) / max(len(zero), 1)), reverse=True):
        a, b = fz.get(f, 0) / max(len(zero), 1), ft.get(f, 0) / max(len(top), 1)
        if abs(b - a) > 0.5:
            print(f"  {f:>8}  {a:5.0%}  {b:5.0%}   {names.get(str(f), '')[:24]}")

    # 逐因子看：有它 vs 没它，速度超出量的均值
    print("\n有该因子 → 速度超出量均值 / 无该因子 → 均值（因子出现率>25%）")
    for f in sorted({x for c in cards for x in c["factors"]}, key=lambda f: -sum(1 for c in cards if f in c["factors"])):
        if sum(1 for c in cards if f in c["factors"]) < 0.25 * len(cards):
            continue
        yes = [c["over"][0] for c in cards if f in c["factors"]]
        no = [c["over"][0] for c in cards if f not in c["factors"]]
        print(f"  {f:>8} n={len(yes):3d} 有 {sum(yes) / len(yes):6.1f}   无 {(sum(no) / len(no)) if no else float('nan'):6.1f}   {names.get(str(f), '')[:24]}")

    # 连续量：剧本计数 / 粉丝 / 才能 / 继承数 与每轴超出量的相关
    print("\n与每轴超出量的 Pearson r（|r|>0.2 才谈得上来源）")
    import numpy as np

    feats = {
        "15001计数": [next((v for t, v in c["scenario_data"] if t == 15001), None) for c in cards],
        "scenario_data条数": [len(c["scenario_data"]) for c in cards],
        "fans": [c["fans"] for c in cards],
        "talent_level": [c["talent_level"] for c in cards],
        "succession_num": [c["succession_num"] for c in cards],
        "n_factor": [len(c["factors"]) for c in cards],
    }
    ov = np.array([[c["over"][i] for i in range(5)] for c in cards], float)
    for k, raw in feats.items():
        x = np.array([np.nan if v is None else v for v in raw], float)
        ok = ~np.isnan(x)
        if ok.sum() < 20 or np.nanstd(x) == 0:
            print(f"  {k}: 只有 {int(ok.sum())} 张有值 / 无方差，跳过  取值 {sorted(set(x[ok]))[:6]}")
            continue
        rs = [np.corrcoef(x[ok], ov[ok, i])[0, 1] for i in range(5)]
        print(f"  {k:<16} " + "  ".join(f"{AX[i]}{rs[i]:+.2f}" for i in range(5)) + f"   范围 {np.nanmin(x):.0f}..{np.nanmax(x):.0f}")

    # 速度超出量的形状：是「一个天花板」还是「按卡种分层」
    sp = sorted(c["over"][0] for c in cards)
    print("\n速度超出量直方（每 20 一档）:", {f"{lo}..{lo + 20}": sum(1 for v in sp if lo <= v < lo + 20) for lo in range(-20, 160, 20)})
    by_chara = {}
    for c in cards:
        by_chara.setdefault(c["card_id"], []).append(c["over"][0])
    big = {k: v for k, v in by_chara.items() if len(v) >= 6}
    print("按育成卡分组的速度超出量均值（n≥6）:", {k: round(sum(v) / len(v), 1) for k, v in sorted(big.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:10]})

    # 卡组：顶到天花板 / 完全没超上限 的两组各共用哪些支援卡
    print("\n顶天花板的卡共用的支援卡:", sorted(set.intersection(*[set(c["deck"]) for c in top]) if top else set(), key=str))
    print("零超上限的卡的支援卡:", sorted(set.intersection(*[set(c["deck"]) for c in zero]) if zero else set(), key=str))

    json.dump({"base": base, "cards": cards}, open("out/_cap_probe.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("\n落盘 out/_cap_probe.json")


if __name__ == "__main__":
    main()
