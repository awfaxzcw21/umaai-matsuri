# -*- coding: utf-8 -*-
"""主线独立复算 #52-A（URA 收尾期窗口报文核对）的逐回合计数。

只读 out/_ura_window_turns.csv，自己重切 K1/K2/Q2/Q3a/Q3b，
不复用 tools/ura_window_scan.py 的判定代码。
"""
import csv
import collections

CSV = "out/_ura_window_turns.csv"


def V(x):
    return x in ("True", "1.0", "1")


def has_hand(r):
    """CSV 的 hand 列是 0/1 标志（无手时为 '0'），不能按字符串非空判真。"""
    return V(r["hand"])


def num(x):
    try:
        return float(x)
    except Exception:
        return None


rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
runs = sorted({r["run"] for r in rows})
print(f"CSV rows={len(rows)} runs={len(runs)}")
eng = [int(r["eng_turn"]) for r in rows]
print(f"eng_turn range: {min(eng)}..{max(eng)}")

# #52-A 只用「面板 65–78 报文齐」的 9 局做边界判定
BOUND = set(range(64, 78))
by_run_turns = collections.defaultdict(set)
for r in rows:
    by_run_turns[r["run"]].add(int(r["eng_turn"]))
full = sorted(k for k, v in by_run_turns.items() if BOUND <= v)
print(f"边界判定局数(64-77齐全) = {len(full)}: {[k.split('@')[1] for k in full]}")

sel = [r for r in rows if r["run"] in full and int(r["eng_turn"]) in BOUND]
print(f"参与计数 (run,turn) 格数 = {len(sel)}")


def agg(pred, label, turns=None):
    """逐引擎回合输出 (支持, 违例)：pred 返回 True/False/None。"""
    out = {}
    for t in range(64, 78):
        if turns and t not in turns:
            continue
        s = v = 0
        for r in sel:
            if int(r["eng_turn"]) != t:
                continue
            a = pred(r)
            if a is True:
                s += 1
            elif a is False:
                v += 1
        out[t] = (s, v)
    print(f"\n[{label}]")
    print("  " + " ".join(f"{t}:{out[t][0]}/{out[t][0]+out[t][1]}" for t in sorted(out)))
    print("  违例: " + " ".join(f"{t}×{out[t][1]}" for t in sorted(out) if out[t][1]))
    return out


# K2：稽古态且非合宿（全 2 态 + camp_left=0）—— 文档称最干净的左沿判别子
k2 = agg(lambda r: (V(r["state_all2"]) and V(r["camp0"])) if r["state_all2"] else None,
         "K2 板上全2态且非合宿")
# K1：板上只剩稽古格
k1 = agg(lambda r: (V(r["keiko_only"]) if r["keiko_only"] else None), "K1 只剩稽古格")
# Q2：所选演目 Lv+1 且不花槽
q2 = agg(lambda r: (V(r["perk_lv"]) if r["perk_lv"] else None), "Q2 Lv+1 不花槽")
# Q3a：干劲 +1（同回合配对）
# 判定口径 = 板前干劲 < 上限 5 才可判；满档时 +1 被吃掉，是「判不了」不是「违例」
q3a = agg(lambda r: (True if int(r["d_mot_hand"]) == 1 else False)
          if (has_hand(r) and r["d_mot_hand"] and int(r["mot_at_board"]) < 5) else None,
          "Q3a 干劲+1（带满档遮蔽过滤）")

print("\n=== onset（K2 首个 9/9 支持、其前一格起 0/9 违例）===")
for t in range(64, 78):
    s, v = k2[t]
    print(f"  eng {t}: 支持 {s}/9  违例 {v}")

print("\n=== 三态裁决：窗口 [lo,hi] 内 K1/K2 的 支持/违例 总数 ===")
for name, lo, hi in [("现实现 67..=76", 67, 76), ("memo字面 68..=77", 68, 77),
                     ("报文形状 72..=77", 72, 77), ("备选 72..=76", 72, 76)]:
    for label, tab in [("K1", k1), ("K2", k2), ("Q2", q2), ("Q3a", q3a)]:
        S = sum(tab[t][0] for t in range(lo, hi + 1) if t in tab)
        Vv = sum(tab[t][1] for t in range(lo, hi + 1) if t in tab)
        print(f"  {name} {label}: 支持 {S} / 违例 {Vv}")
    print()

# Q3b：单手体力残差里有没有 +30
hands = [r for r in rows if has_hand(r) and r["resid_vital"] and num(r["resid_vital"]) is not None]
near30 = [r for r in hands if abs(num(r["resid_vital"]) - 30) <= 3]
near0 = [r for r in hands if abs(num(r["resid_vital"])) <= 3]
print(f"=== Q3b 体力残差：可判手 {len(hands)}，其中 ≈+30 有 {len(near30)} 手，≈0 有 {len(near0)} 手 ===")

# 引擎 73/75/77（面板 74/76/78）有无结算手：用全部 run，不限边界用局
race_turns = {73, 75, 77}
race_hands = [r for r in rows if int(r["eng_turn"]) in race_turns and has_hand(r)]
nonrace = collections.Counter()
for r in rows:
    t = int(r["eng_turn"])
    if t in race_turns:
        continue
    if has_hand(r):
        nonrace[t] += 1
print(f"=== race 回合(引擎73/75/77) 结算手 = {len(race_hands)} ；"
      f"非 race 回合结算手分布(取收尾段 64-77) = "
      f"{ {t: c for t, c in sorted(nonrace.items()) if t in BOUND} }")

# CP 只在面板 24/48/68 长 n_cp
cp = collections.Counter()
for r in rows:
    if r["n_cp_start"] and r["n_cp_end"] and int(r["n_cp_end"]) > int(r["n_cp_start"]):
        cp[int(r["panel_turn"])] += 1
print(f"=== n_cp 变长的面板回合 = {dict(sorted(cp.items()))} ===")
