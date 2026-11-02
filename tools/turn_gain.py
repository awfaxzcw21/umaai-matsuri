"""每回合收益表：从逐回合 `chara_info` 量「这一手练了什么 → 五维各加多少」，
并检验收益是否随「距上限的剩余空间」衰减（用来替代没有报文依据的 cap_decay 拟合参数）。

口径：同一 run 内按 turn 排序，第 t 回合的 chara_info 是**结算后**状态；
before = 第 t−1 回合的结算后状态，delta = after − before，归给该回合报文里的 command_result。
对齐关系（command 落在 t 还是 t+1）用数据本身验，不假设。
"""

import collections
import csv
import json
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
from msgpack_lite import decode_file  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
PK = ["speed", "stamina", "power", "guts", "wiz"]
MK = ["max_" + k for k in PK]
AX = ["速度", "耐力", "力量", "根性", "智力"]


def collect():
    """run -> turn -> 该回合最后一条带 chara_info 的报文（结算后）+ 该回合出现过的 command。"""
    snap = collections.defaultdict(dict)
    for f in sorted(os.listdir(RAW)):
        if not f.endswith(".bin"):
            continue
        try:
            d = decode_file(RAW + f)
        except Exception:
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = data.get("chara_info")
        if not isinstance(ci, dict) or not all(k in ci for k in MK):
            continue
        rid, t = ci.get("single_mode_chara_id"), ci.get("turn")
        if not isinstance(t, int):
            continue
        cr = data.get("command_result")
        rec = {
            "file": f,
            "turn": t,
            "five": [ci[k] for k in PK],
            "caps": [ci[k] for k in MK],
            "vital": ci.get("vital"),
            "motivation": ci.get("motivation"),
            "skill_point": ci.get("skill_point"),
            "effects": tuple(sorted(set(ci.get("chara_effect_id_array") or []))),
            "command": (cr or {}).get("command_id"),
            "sub_id": (cr or {}).get("sub_id"),
            "result_state": (cr or {}).get("result_state"),
            "partners": len((data.get("gain_partner_support_effect_array") or [])),
        }
        prev = snap[rid].get(t)
        if prev is None or (rec["command"] is not None and prev["command"] is None):
            snap[rid][t] = rec
    return snap


def rows(snap):
    out = []
    for rid, per in sorted(snap.items()):
        ts = sorted(per)
        for a, b in ((per[x], per[y]) for x, y in zip(ts, ts[1:])):
            if b["command"] is None or b["turn"] != a["turn"] + 1:
                continue  # 跨回合缺口会把好几手的增量混成一手，丢掉
            out.append({
                "run": rid, "turn": b["turn"], "file": b["file"], "command": b["command"], "sub_id": b["sub_id"],
                "result_state": b["result_state"], "partners": b["partners"],
                "before": a["five"], "after": b["five"], "caps": b["caps"],
                "delta": [b["five"][i] - a["five"][i] for i in range(5)],
                "d_vital": (b["vital"] or 0) - (a["vital"] or 0),
                "d_sp": (b["skill_point"] or 0) - (a["skill_point"] or 0),
                "effects_before": list(a["effects"]), "effects_after": list(b["effects"]),
            })
    return out


def align_check(rows):
    """command 落在本回合还是下一回合？看「该 command 的主属性增量」在两种对齐下的均值。"""
    main = {101: 0, 102: 1, 103: 2, 104: 3, 105: 4}  # 待验证：先按 101..105 = 五轴训练假设
    by_turn = collections.defaultdict(dict)
    for r in rows:
        by_turn[r["run"]][r["turn"]] = r
    cur, nxt = [], []
    for rid, per in by_turn.items():
        for r in per.values():
            i = main.get(r["command"])
            if i is None:
                continue
            cur.append(r["delta"][i])
            nb = None
            for t in sorted(per):
                if t > r["turn"]:
                    nb = per[t]
                    break
            if nb is not None and nb["turn"] == r["turn"] + 1:
                nxt.append(nb["delta"][i])
    print(f"对齐自检 command→本回合 delta 均值 {sum(cur) / len(cur):.1f} (n={len(cur)})  "
          f"→下一回合 {sum(nxt) / len(nxt):.1f} (n={len(nxt)})")


def main():
    snap = collect()
    rs = rows(snap)
    print(f"run {len(snap)} 个，可归因回合 {len(rs)} 条")
    align_check(rs)

    # command 分布：认它是哪种动作
    cnt = collections.Counter(r["command"] for r in rs)
    print("\ncommand_id 出现次数:", dict(sorted(cnt.items(), key=lambda kv: -kv[1])))
    print("sub_id × result_state 组合:", dict(collections.Counter((r["sub_id"], r["result_state"]) for r in rs).most_common(8)))

    # 每个 command 的各轴平均增量
    print("\n每个 command 的平均增量（速/耐/力/根/智 / 体力Δ / SPΔ）:")
    for c in sorted(cnt, key=lambda x: -cnt[x]):
        g = [r for r in rs if r["command"] == c]
        m = [sum(r["delta"][i] for r in g) / len(g) for i in range(5)]
        print(f"  {c:4d} n={len(g):4d} " + " ".join(f"{AX[i]}{m[i]:+7.1f}" for i in range(5))
              + f"  体力{sum(r['d_vital'] for r in g) / len(g):+6.1f}  SP{sum(r['d_sp'] for r in g) / len(g):+6.1f}")

    with open("out/_turn_gain.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rs[0].keys()))
        w.writeheader()
        for r in rs:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in r.items()})
    json.dump(rs, open("out/_turn_gain.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("\n落盘 out/_turn_gain.csv / out/_turn_gain.json")


if __name__ == "__main__":
    main()
