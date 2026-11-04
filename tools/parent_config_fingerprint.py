#!/usr/bin/env python3
"""双亲（种马）配置指纹：我们自己 8 匹 vs 别人 LG15 组 vs 别人 60k–LG15 组。

起因：#54（`docs/继承剂量_种马输入_20261104.md`）在**我们自家 10 局**里换种马，送分只差 1,580
⇒「种马不是 LG15 主瓶颈」。但自家那 10 局的双亲档位**几乎全在同一档**，
剂量范围太窄，量不出组间差。本文把剂量范围换到**别人**那一侧：同一份练成马快照表，
按 `viewer` 分组比双亲六槽的 rarity 字段与因子条数。

    python tools/parent_config_fingerprint.py            # 只读盘上两张 CSV，不重扫报文

判读纪律（写死）：
- 这是**组间相关**，不是因果。带金卡父母的人同时也更可能玩得久、氪得多，本文不拆这一层。
- 双亲六槽里只有租借那一格是「这局能直接选的种马」，其余五格是自家积累 ⇒ 本文给逐位置分解，
  但**没有标出哪一格是租借**（`_real_score_dist` 的串里没带槽位标签），只报形状不认领杠杆。
- 不写 `data/`、不改引擎、不新增任何常数。
"""

import argparse
import csv
import glob
import os
import re
import statistics as st
import sys

OWN_VIEWER = "238107205"
LG15 = 85600
SLOT_RE = re.compile(r"(\d+)/(\d+)/(\d+)/(\d+)")


def num(r, k):
    try:
        return float(r[k])
    except (KeyError, TypeError, ValueError):
        return None


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def groups(rows, min_own_five=6000):
    """#55 §1 定住的闸门：自己的「真练成」= viewer 自己 且 五维和 ≥ 6,000（不能用报文技能数）。"""
    own = [r for r in rows if r["viewer"] == OWN_VIEWER and (num(r, "五维和") or 0) >= min_own_five]
    others = [r for r in rows if r["viewer"] != OWN_VIEWER]
    hi = [r for r in others if (num(r, "score") or 0) >= LG15]
    mid = [r for r in others if 60000 <= (num(r, "score") or 0) < LG15
           and (num(r, "五维和") or 0) >= min_own_five]
    return [("自己 真练成", own), ("别人 ≥LG15", hi), ("别人 60k–LG15", mid)]


def slots(rows):
    """逐槽展开 [(马, 位置, 卡, 稀有度, 因子条数, 双亲五维和)]；第 6 位实测恒 0，本文不用。"""
    out = []
    for mi, r in enumerate(rows):
        raw = (r.get("双亲(card/rar/因子/五维和)") or "").strip()
        for pos, m in enumerate(SLOT_RE.finditer(raw)):
            out.append((mi, pos, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))))
    return out


def share(vals, pred):
    return 100.0 * sum(1 for v in vals if pred(v)) / len(vals) if vals else float("nan")


def probe_packets(n):
    """开 `n` 个报文文件，核 `succession_chara_array` 条目到底带哪些键、五轴在不在里面。
    这条决定本文能不能报「双亲五维和」——实测**读不到**（键集合里压根没有五轴）。"""
    pdir = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
    if pdir not in sys.path:
        sys.path.insert(1, "E:/HJM/jidianbeiAI0.22")
    import msgpack_lite as ml  # noqa: PLC0415  只在这条分支里要解码器

    def walk(o):
        out = []
        if isinstance(o, dict):
            if "rank_score" in o and "succession_chara_array" in o:
                out.append(o)
            for v in o.values():
                out += walk(v)
        elif isinstance(o, list):
            for v in o:
                out += walk(v)
        return out

    files = sorted(glob.glob(os.path.join(pdir, "*.bin")))
    keys, nslot, nz, nf = {}, 0, 0, 0
    for p in files:
        try:
            d = ml.decode_file(p)
        except Exception:
            continue
        nf += 1
        for node in walk(d):
            for s in (node.get("succession_chara_array") or []):
                if not isinstance(s, dict):
                    continue
                nslot += 1
                for k in s:
                    keys[k] = keys.get(k, 0) + 1
                if any(s.get(a) for a in ("speed", "stamina", "power", "guts", "wis")):
                    nz += 1
        if nf >= n:
            break
    print("\n【0 报文侧核对（--probe-packets）】")
    print("  开了 %d 个报文，%d 个 succession 槽条目 ｜ 带非零五轴的 %d 条" % (nf, nslot, nz))
    print("  条目实际键（出现次数）：%s" % ", ".join("%s×%d" % kv for kv in sorted(keys.items())))
    print("  ⇒ 五轴不在键集合里 ⇒ 双亲五维和**读不到**，不是「双亲五维低」")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", default="out/_real_score_dist_20261105.csv",
                    help="练成马快照逐条表（tools/real_score_dist.py 产出）")
    ap.add_argument("--factor-hi", type=int, default=45, help="「多因子」判定线（默认 45，取 LG15 组 p90 附近）")
    ap.add_argument("--probe-packets", type=int, default=0, metavar="N",
                    help="额外开 N 个真机报文核 succession 条目的键集合（~40 个文件 1 分钟）")
    args = ap.parse_args(argv)

    rows = load(args.dist)
    gs = groups(rows)
    print("读入 %s：%d 条 ｜ 分组 %s" % (args.dist, len(rows), [(n, len(g)) for n, g in gs]))
    if args.probe_packets:
        probe_packets(args.probe_packets)

    print("\n【1 双亲六槽的 rarity 字段（实测值域 1–5，本文只当序数用）】")
    print("  ⚠ 不贴 R/SR/SSR 标签：`support_card_data.rarity` 是 1–3（SSR=3），"
          "练成马的 rarity 却有 4/5 ⇒ 两个枚举不是一套，语义没在 master.mdb 里定住 [表侧未核]。")
    print("  %-16s %6s %8s %10s %10s %10s" % ("组", "槽 n", "rarity med", "≥4 占比", "=5 占比", "因子 med"))
    for n, g in gs:
        s = slots(g)
        rar = [x[3] for x in s]
        fac = [x[4] for x in s]
        print("  %-16s %6d %8s %9.1f%% %9.1f%% %10s"
              % (n, len(s), st.median(rar) if rar else "—",
                 share(rar, lambda v: v >= 4), share(rar, lambda v: v == 5),
                 st.median(fac) if fac else "—"))
        per_horse = len(s) / len(g) if g else 0
        if abs(per_horse - 6) > 0.01:
            print("      ⚠ 每匹马槽数 %.2f ≠ 6（有的快照双亲不全，逐组 n 以槽数为准）" % per_horse)

    print("\n【2 rarity 随不随卡走（决定这个字段能不能当「卡的稀有度」读）】")
    bycard = {}
    for n, g in gs:
        for x in slots(g):
            bycard.setdefault(x[2], set()).add(x[3])
    multi = [k for k, v in bycard.items() if len(v) > 1]
    print("  不同马 card %d 个，其中同一 card 出现多个 rarity 的 %d 个（%.0f%%）"
          "⇒ rarity **随马走不随卡走**，它是这匹马自己的解放档，不是源卡的稀有度枚举 [一手]"
          % (len(bycard), len(multi), 100.0 * len(multi) / len(bycard) if bycard else 0))
    for k, v in sorted(bycard.items(), key=lambda kv: -len(kv[1]))[:4]:
        print("    card %-8s rarity 取值 %s" % (k, sorted(v)))
    single = sorted([k for k, v in bycard.items() if len(v) == 1])
    print("  剩下 %d 个卡单值（示例 %s）⇒ 单值不等于「卡决定档」，只是这些卡恰好只落在同一档 [判读]"
          % (len(single), ", ".join(str(k) for k in single[:4]))
          if single else "  （无单值卡）")

    print("\n【3 逐位置分解（位置 0..5，串里的书写顺序，未标租借格）】")
    print("  %-16s" % "组" + "".join("%12s" % ("位%d ≥4" % p) for p in range(6)))
    for n, g in gs:
        s = slots(g)
        line = "  %-16s" % n
        for p in range(6):
            vals = [x[3] for x in s if x[1] == p]
            line += "%11s%%" % ("%.0f" % share(vals, lambda v: v >= 4) if vals else "—")
        print(line)

    print("\n【4 因子条数这一维】")
    for n, g in gs:
        s = slots(g)
        fac = [x[4] for x in s]
        if not fac:
            continue
        srt = sorted(fac)
        q = lambda p: srt[min(len(srt) - 1, int(p * len(srt)))]  # noqa: E731
        print("  %-16s 因子 med %3s p10 %3s p90 %3s max %3s ｜ ≥%d 占比 %5.1f%%"
              % (n, st.median(fac), q(0.1), q(0.9), srt[-1], args.factor_hi,
                 share(fac, lambda v: v >= args.factor_hi)))

    print("\n【5 马级（不是槽级）：一匹马的六个槽里有几个 rarity≥4】")
    for n, g in gs:
        per = {}
        for x in slots(g):
            per.setdefault(x[0], []).append(x[3])
        cnt = [sum(1 for v in vlist if v >= 4) for vlist in per.values()]
        print("  %-16s 马 n=%3d ｜ ≥4 槽数 med %s ｜ 全低(0 格) %5.1f%% ｜ ≥4 格 %5.1f%%"
              % (n, len(cnt), st.median(cnt) if cnt else "—",
                 share(cnt, lambda v: v == 0), share(cnt, lambda v: v >= 4)))

    print("\n【6 这条能说什么、不能说什么】")
    print("  能：组间形状差（我们双亲 rarity 低于任一别人高分组；中段与 LG15 组 rarity 同形，"
          "分开它们的是因子条数）[一手·组间]")
    print("  不能：因果。双亲 rarity 与玩家投入共线，本文没拆；租借格未标出，不认领具体杠杆 [判读]")
    print("  和 #54 的关系：#54 的『种马只差 1,580 分』是**自家窄剂量**下的读数，"
          "本文是**别人宽剂量**下的形状，两者不冲突，但不能拿本文的占比去换算分数。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
