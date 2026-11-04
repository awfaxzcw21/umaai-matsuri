#!/usr/bin/env python3
"""双亲（种马）配置指纹：我们自己 8 匹 vs 别人 LG15 组 vs 别人 60k–LG15 组。

起因：#54（`docs/继承剂量_种马输入_20261104.md`）在**我们自家 10 局**里换种马，送分只差 1,580
⇒「种马不是 LG15 主瓶颈」。但自家那 10 局的双亲档位**几乎全在同一档**，
剂量范围太窄，量不出组间差。本文把剂量范围换到**别人**那一侧：同一份练成马快照表，
按 `viewer` 分组比双亲六槽的 rarity 字段与因子条数。

    python tools/parent_config_fingerprint.py                     # 只读一张盘上 CSV（【0】【6】【7】【8】需要 --probe-packets）
    python tools/parent_config_fingerprint.py --probe-packets 2000  # 再加报文侧（约 6 秒 + 一次 #54 缓存读）

判读纪律（写死）：
- 这是**组间相关**，不是因果。带金卡父母的人同时也更可能玩得久、氪得多，本文不拆这一层。
- 判读纪律：双亲六槽里只有租借那一格是「这局能直接选的种马」，其余五格是自家积累。
  【8】用 #54 缓存把租借格钉在 position 10/20（自己侧 9/9 零违例），【7】按这一维拆开看；
  别人那侧没有请求报文，`owner_viewer_id≠0` 只是同构假设，不拿来换算分数。
- 不写 `data/`、不改引擎、不新增任何常数。
"""

import argparse
import collections
import csv
import json
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


AXES = ("speed", "stamina", "power", "guts", "wiz")   # wiz 不是 wis，键名以 inherit_gain_dosage.AXES 为准
SCEN = 15
PAR_ORDER = (10, 20, 11, 12, 21, 22)                  # 报文里 succession_chara_array 的书写顺序（实测固定，见【6】）


def probe_packets(n):
    """开 `n` 个报文文件，① 核 `succession_chara_array` 条目到底带哪些键、五轴在不在里面；
    ② 按 `position_id` 重做一遍 rarity／因子分解（这条是**报文侧一手**，和 CSV 那侧互相核）。
    五轴那条决定本文能不能报「双亲五维和」——实测**读不到**（键集合里压根没有五轴）。"""
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
    snaps = {}          # 去重键 -> 一条快照（口径同 tools/real_score_dist.py：只收 scen15）
    n_scen15 = 0
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
                if any(s.get(a) for a in AXES):
                    nz += 1
            if node.get("scenario_id") != SCEN:
                continue
            n_scen15 += 1
            five = [int(node.get(a) or 0) for a in AXES]
            key = (node.get("trained_chara_id"), node.get("viewer_id"), int(node["rank_score"]))
            snaps[key] = {
                "score": int(node["rank_score"]), "sum": sum(five),
                "viewer": str(node.get("viewer_id")),
                "par": [(x.get("position_id"), x.get("card_id"), x.get("rarity"),
                         len(x.get("factor_info_array") or []), bool(x.get("owner_viewer_id")))
                        for x in (node.get("succession_chara_array") or []) if isinstance(x, dict)],
            }
        if nf >= n:
            break
    print("\n【0 报文侧核对（--probe-packets）】")
    print("  开了 %d 个报文，%d 个 succession 槽条目 ｜ 带非零五轴的 %d 条" % (nf, nslot, nz))
    print("  条目实际键（出现次数）：%s" % ", ".join("%s×%d" % kv for kv in sorted(keys.items())))
    print("  ⇒ 五轴不在键集合里 ⇒ 双亲五维和**读不到**，不是「双亲五维低」")
    print("  （口径注：上面这 %d 条**不分剧本**；按 scen15 过滤后终局快照 %d 条、去重 %d 匹，见【6】）"
          % (nslot, n_scen15, len(snaps)))
    return snaps


def _groups_from_snaps(snaps, min_own_five=6000):
    """报文侧的分组闸门，和 groups()（CSV 侧）同一套 #55 §1 判据。"""
    return [("自己 真练成", [s for s in snaps.values()
                          if s["viewer"] == OWN_VIEWER and s["sum"] >= min_own_five]),
            ("别人 ≥LG15", [s for s in snaps.values()
                            if s["viewer"] != OWN_VIEWER and s["score"] >= LG15]),
            ("别人 60k–LG15", [s for s in snaps.values() if s["viewer"] != OWN_VIEWER
                               and 60000 <= s["score"] < LG15 and s["sum"] >= min_own_five])]


def pos_table(snaps, min_own_five=6000):
    """报文侧按 position_id 分解，分组闸门与【1】同一套（#55 §1）。"""
    gs = _groups_from_snaps(snaps, min_own_five)
    print("\n【6 报文侧按 position_id 分解（这条给【3】的书写顺序补上语义）】")
    order = collections.Counter(tuple(x[0] for x in s["par"]) for s in snaps.values())
    print("  数组书写顺序的取值（n=%d 匹马）：%s"
          % (len(snaps), "; ".join("%s×%d" % (k, v) for k, v in order.most_common(3))))
    fixed = order.get(PAR_ORDER, 0)
    print("  ⇒ %d/%d 匹按 %s 书写 ⇒ 【3】的位0..位5 = 位(10,20)双亲 ＋ 位(11,12,21,22)祖父母 [一手]"
          % (fixed, len(snaps), str(PAR_ORDER)))
    print("  ⚠ 10/20 谁是父谁是母、11/12 挂哪一侧，报文里没有字段定住 ⇒ 本文只说「双亲槽／祖父母槽」，不写「父方／母方」[判读]。")
    print("  %-16s %6s ｜ %s" % ("组", "马 n", " ".join("%14s" % ("槽%d" % pid) for pid in PAR_ORDER)))
    for name, g in gs:
        for what, idx, fmt in (("rarity≥4", 2, "%13.0f%%"), ("因子 med", 3, "%14s")):
            line = "  %-16s %6d %s ｜" % (name if what == "rarity≥4" else "", len(g), what)
            for pid in PAR_ORDER:
                vals = [x[idx] for s in g for x in s["par"] if x[0] == pid]
                if what == "rarity≥4":
                    line += fmt % share(vals, lambda v: v >= 4) if vals else "%14s" % "—"
                else:
                    line += fmt % (st.median(vals) if vals else "—")
            print(line)
    print("  owner_viewer_id 非零占比（本侧只有「是不是别人的马」这一维，没有「这局借的」标签）：")
    for name, g in gs:
        line = "  %-16s" % name
        for pid in PAR_ORDER:
            vals = [x[4] for s in g for x in s["par"] if x[0] == pid]
            line += "%15.0f%%" % share(vals, lambda v: v) if vals else "%15s" % "—"
        print(line)
    print("  ⚠ 祖父母槽的非零占比同样很高 ⇒ `owner_viewer_id≠0` 只在**双亲两格（10/20）**上指租借；"
          "祖父母槽的非零含义本文没拆，不拿来用（对齐验法见【8】）")
    return gs


def rental_split(snaps):
    """把双亲两格按 `owner_viewer_id` 拆成「租借侧／自有侧」，再看 rarity 与因子。
    前提由【8】核实：`owner_viewer_id≠0` 在槽 10/20 上确实指「这局借进来的那一格」。"""
    print("\n【7 双亲两格拆「租借侧／自有侧」（只看槽 10/20，槽 11/12/21/22 不参与）】")
    print("  %-16s %10s %10s ｜ %10s %10s ｜ %10s %10s"
          % ("组", "租借格 n", "自有格 n", "租借≥4", "自有≥4", "租借因子", "自有因子"))
    for name, g in _groups_from_snaps(snaps):
        par = [(s, x) for s in g for x in s["par"] if x[0] in (10, 20)]
        rent = [x for _, x in par if x[4]]
        own = [x for _, x in par if not x[4]]
        nrent = sum(1 for s in g if any(x[4] for x in s["par"] if x[0] in (10, 20)))
        print("  %-16s %10d %10d ｜ %9.0f%% %9.0f%% ｜ %9s %9s"
              % (name, len(rent), len(own),
                 share([x[2] for x in rent], lambda v: v >= 4),
                 share([x[2] for x in own], lambda v: v >= 4),
                 st.median([x[3] for x in rent]) if rent else "—",
                 st.median([x[3] for x in own]) if own else "—"))
        print("      这组 %d 匹里 %d 匹（%.0f%%）双亲两格中至少一格是租借"
              % (len(g), nrent, 100.0 * nrent / len(g) if g else 0))


def request_pin(path):
    """#54 的 cache 里同时有开局请求（`succ_k==0` 指出租借槽）和终局快照（六槽带 owner_viewer_id）。
    两侧对上 ⇒ 槽 1↔position 10、槽 2↔position 20，且 `owner_viewer_id` 就是租借方的 viewer_id。"""
    print("\n【8 请求侧 ↔ 快照侧对齐（定住「租借格」这个标签本身）】")
    if not os.path.exists(path):
        print("  跳过：找不到 %s（这条需要 #54 的报文缓存）" % path)
        return
    cache = json.load(open(path, encoding="utf-8"))
    snaps = [s for s in cache["snaps"] if s.get("scen") == SCEN and s.get("par")]
    by_pair = collections.defaultdict(list)
    for s in snaps:
        by_pair[(s.get("succ_1"), s.get("succ_2"))].append(s)
    ok = bad = nosnap = 0
    for st in cache["starts"]:
        if st.get("scen") != SCEN or str(st.get("viewer")) != OWN_VIEWER:
            continue
        rel = st.get("rental") or {}
        rid = rel.get("viewer_id")
        zero = [k for k in ("succ_1", "succ_2") if st.get(k) == 0]
        if not rid or len(zero) != 1:
            bad += 1
            continue
        want = 10 if zero == ["succ_1"] else 20
        cand = by_pair.get(tuple(st.get("run_chara_info_succ") or []), [])
        if not cand:
            nosnap += 1
            continue
        mp = {p["position_id"]: p.get("owner_viewer_id") for p in cand[0]["par"]
              if p["position_id"] in (10, 20)}
        hit = [pid for pid, ov in mp.items() if ov == rid]
        if hit == [want] and mp.get(10 if want == 20 else 20) in (0, None):
            ok += 1
        else:
            bad += 1
    print("  自己 scen15 开局请求 %d 条 ｜ 两侧一致 %d ｜ 该局没有终局快照 %d ｜ 违例 %d"
          % (ok + bad + nosnap, ok, nosnap, bad))
    if bad == 0 and ok:
        print("  ⇒ **零违例**：请求里 `succession_trained_chara_id_%s = 0` 的那一格，"
              "终局快照里就是 position 10／20 中 `owner_viewer_id = 租借方 viewer_id` 的那一格 [一手]"
              % ("1↔10 / 2↔20"))
        print("  ⇒ 【7】的 `owner_viewer_id≠0` 判租借，在自己这侧是核实过的；"
              "别人那侧没请求报文，只是**同构假设** [判读]")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", default="out/_real_score_dist_20261105.csv",
                    help="练成马快照逐条表（tools/real_score_dist.py 产出）")
    ap.add_argument("--factor-hi", type=int, default=45, help="「多因子」判定线（默认 45，取 LG15 组 p90 附近）")
    ap.add_argument("--probe-packets", type=int, default=0, metavar="N",
                    help="额外开 N 个真机报文做【0】【6】【7】【8】（2,000 个约 6 秒）")
    ap.add_argument("--inherit-cache", default="out/_inherit_pkt_cache_20261104.json",
                    help="#54 的报文缓存，用来做【8】请求侧↔快照侧对齐（只在 --probe-packets 时读）")
    args = ap.parse_args(argv)

    rows = load(args.dist)
    gs = groups(rows)
    print("读入 %s：%d 条 ｜ 分组 %s" % (args.dist, len(rows), [(n, len(g)) for n, g in gs]))
    snaps = probe_packets(args.probe_packets) if args.probe_packets else None

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

    print("\n【3 逐位置分解（CSV 串的书写顺序 0..5；顺序语义在【6】核实：位0/1=双亲槽 10/20，位2..5=祖父母 11/12/21/22）】")
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

    if snaps is not None:
        pos_table(snaps)
        rental_split(snaps)
        request_pin(args.inherit_cache)

    print("\n【9 这条能说什么、不能说什么】")
    print("  能：组间形状差（我们双亲 rarity 低于任一别人高分组；中段与 LG15 组 rarity 同形，"
          "分开它们的是因子条数）[一手·组间]")
    print("  不能：因果。双亲 rarity 与玩家投入共线，本文没拆；租借格在自己这侧标得出（【8】零违例），"
          "别人那侧只有请求报文缺失下的同构假设，所以仍不认领「换哪一格」的分数承诺 [判读]")
    print("  和 #54 的关系：#54 的『种马只差 1,580 分』是**自家窄剂量**下的读数，"
          "本文是**别人宽剂量**下的形状，两者不冲突，但不能拿本文的占比去换算分数。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
