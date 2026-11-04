#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追加参与（分身）读数：`sub_command_partner_array` 在真机盘面里的出现率与结构。

口径全部沿用 tools/train_leg_preview.py 的列定义：
  heads_sub ⊆ heads_deck；heads_own = heads_deck − heads_sub（与引擎站位可比的那一份）。

五张表：① 按 (kind, camp)；② 条件化（heads_deck / tips / level）；
③ 板内结构（有追加的槽位数、板内追加总数）；④ 按时点分段（0→42% 那条开关）；
⑤ 固定 (Lv, tips) 后的时点分层（证明 ④ 不是 Lv/tips 的影子）。只读 CSV，不改任何东西。
"""
import argparse
import collections
import csv


def num(r, k):
    try:
        return int(float(r[k]))
    except (TypeError, ValueError):
        return 0


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def pct(h, n):
    return 100.0 * h / n if n else 0.0


def by_kind_camp(rows):
    print("## 表1 按命令类型")
    st = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        b = st[(r["kind"], num(r, "camp"))]
        b[0] += 1
        s = num(r, "heads_sub")
        b[1] += 1 if s > 0 else 0
        b[2] += s
    for k in sorted(st):
        n, h, s = st[k]
        print("  %-16s 条目%6d  P(追加)=%5.1f%%  追加/条目=%5.3f"
              % ("%s/camp=%d" % k, n, pct(h, n), s / n))


def conditioned(rows, kind):
    print("## 表2 条件化（kind=%s）" % kind)
    sub = [r for r in rows if r["kind"] == kind]
    for field, fn in (("heads_deck", lambda r: num(r, "heads_deck")),
                      ("tips>0", lambda r: 1 if num(r, "tips") > 0 else 0),
                      ("level", lambda r: num(r, "level"))):
        st = collections.defaultdict(lambda: [0, 0, 0])
        for r in sub:
            b = st[fn(r)]
            b[0] += 1
            s = num(r, "heads_sub")
            b[1] += 1 if s > 0 else 0
            b[2] += s
        for k in sorted(st):
            n, h, s = st[k]
            print("  %-12s=%-3s n=%5d  P(追加)=%5.1f%%  追加/条目=%5.3f"
                  % (field, k, n, pct(h, n), s / n))


def board_shape(rows):
    print("## 表3 板内结构")
    bd = collections.defaultdict(list)
    for r in rows:
        bd[(r["file"], r["turn"])].append(r)
    slots = collections.Counter()
    tot = collections.Counter()
    cards = collections.Counter()
    for v in bd.values():
        slots[sum(1 for r in v if num(r, "heads_sub") > 0)] += 1
        tot[sum(num(r, "heads_sub") for r in v)] += 1
        cards[sum(num(r, "heads_deck") for r in v)] += 1
    print("  板数 %d" % len(bd))
    print("  板内有追加的槽位数 %s" % dict(sorted(slots.items())))
    print("  板内追加总数       %s" % dict(sorted(tot.items())))
    print("  板内Σheads_deck    %s" % dict(sorted(cards.items())))
    return bd


def timeline(rows):
    print("## 表4 按时点分段（决定性的一条）")
    st = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for r in rows:
        seg = (num(r, "turn") - 1) // 13
        b = st[seg]
        b[0] += 1
        s = num(r, "heads_sub")
        b[1] += 1 if s > 0 else 0
        b[2] += s
        b[3] += num(r, "level")
        b[4] += 1 if num(r, "tips") > 0 else 0
        b[5] += num(r, "heads_deck")
    for seg in sorted(st):
        n, h, s, lv, tp, hd = st[seg]
        print("  t%2d-%2d 条目%5d  P(追加)=%5.1f%%  追加/条目=%5.3f  平均Lv=%4.2f  tips率=%4.2f  heads_deck/条目=%4.2f"
              % (seg * 13 + 1, seg * 13 + 13, n, pct(h, n), s / n, lv / n, tp / n, hd / n))


def stratified(rows):
    """表5：固定 (kind, level, tips) 后只变回合段 ⇒ 时点门控是不是 Lv/tips 的影子。"""
    print("## 表5 分层后的时点（keiko, level=2, tips>0）")
    for cond, name in ((lambda r: num(r, "heads_own") >= 1, "再叠 own>=1"),
                       (lambda r: True, "仅 (Lv2,tips>0)")):
        st = collections.defaultdict(lambda: [0, 0])
        for r in rows:
            if r["kind"] != "keiko" or num(r, "level") != 2 or num(r, "tips") == 0 or not cond(r):
                continue
            b = st[(num(r, "turn") - 1) // 13]
            b[0] += 1
            if num(r, "heads_sub") > 0:
                b[1] += 1
        seg = [s for s in sorted(st) if st[s][0] >= 15]
        print("  %-16s %s" % (name, "  ".join(
            "t%d-%d:%5.1f%%(n=%d)" % (s * 13 + 1, s * 13 + 13, pct(st[s][1], st[s][0]), st[s][0]) for s in seg)))
    # 合取条件：tips=0 时全部后段是否为 0
    st = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        if r["kind"] != "keiko" or num(r, "level") != 2 or num(r, "tips") != 0:
            continue
        if (num(r, "turn") - 1) // 13 < 2:
            continue
        b = st[(num(r, "turn") - 1) // 13]
        b[0] += 1
        if num(r, "heads_sub") > 0:
            b[1] += 1
    print("  keiko Lv2 tips=0 后段 %s" % "  ".join(
        "t%d-%d:%5.1f%%(n=%d)" % (s * 13 + 1, s * 13 + 13, pct(st[s][1], st[s][0]), st[s][0]) for s in sorted(st)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", default="out/train_leg_preview_20261103.csv")
    a = ap.parse_args()
    rows = load(a.preview)
    print("数据 %s：条目 %d / run %d" % (a.preview, len(rows), len(set(r["run"] for r in rows))))
    by_kind_camp(rows)
    conditioned(rows, "keiko")
    conditioned(rows, "plain")
    board_shape(rows)
    timeline(rows)
    stratified(rows)


if __name__ == "__main__":
    main()
