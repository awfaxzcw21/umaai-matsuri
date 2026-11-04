#!/usr/bin/env python3
"""马房档位普查：报文里的**账号级完整马娘记录**（带五轴和 rarity 的那种），我们自己攒到了什么档位。

起因：#61（`docs/双亲配置指纹_20261105.md` §2b/§3）把六槽里的**租借格**标出来了，读数是我们 8 匹
每匹都借（8/8），但借来那格 rarity≥4 只有 12%（LG15 组 82%）。剩下一句「其余五格是自家积累」当时**没法验**
——槽条目只有 8 个键、读不到五轴。但**账号级全量记录读得到**：报文里 `succession_trained_chara_data` /
`user_trained_chara_array` / `directory_card_array[].trained_chara` 这类数组的元素是**完整马娘记录**
（53 键，含 `rarity / talent_level / 五轴 / rank_score / factor_info_array / scenario_id`）[一手]。

    python tools/stud_pool_census.py                  # 全量扫 10,583 个报文，约 21 s
    python tools/stud_pool_census.py --limit 4000      # 先抽样

判读纪律（写死）：
- **必须按 `scenario_id` 分层**：不加这层会出假账——自家 245 条记录里 199 条是 scen14 产的，
  直接汇总会把 scen14 攒的高档马算进 scen15 的池子（scen14 有 74 匹 rarity≥4，scen15 只有 6 匹）。
- 同一匹马在报文里出现多次、且**不同来源的副本字段完整度不同**（账号列表常不带五轴）⇒
  每匹马只留**五维和最完整**的那一次观测（`nobs` 记观测次数），否则会把「读不到」当成 0。
- 这批记录是**报文里出现过的账号列表**（好友表 / 排行榜 / 继承目录 / 开局复制），**不是租借池**：
  盘上没有租借列表报文（`rental_succession_trained_chara` 只出现在我们 10 局开局请求里），
  所以本脚本答「自家攒到什么档、哪几匹真被用去当父母」，**不答**「租借能挑到什么样的马」。
- `rarity` 沿用 #60/#61 的用法：只当**序数**（实测值域 1–5），不贴 R/SR/SSR 标签。
- 不写 `data/`、不改引擎、不新增任何常数。
"""

import argparse
import collections
import glob
import os
import statistics as st
import sys

OWN_VIEWER = "238107205"
SCEN = 15
AXES = ("speed", "stamina", "power", "guts", "wiz")
POOL_KEYS = ("rarity", "talent_level", "trained_chara_id", "viewer_id")   # 完整记录的必要键


def share(vals, pred):
    return 100.0 * sum(1 for v in vals if pred(v)) / len(vals) if vals else float("nan")


def med(vals):
    return st.median(vals) if vals else float("nan")


def facs(rec):
    """因子集合＝`(factor_id, level)` 对的集合。
    ⚠ 只用 `factor_id` 会把两匹不同的马认成同一匹（实测 `data/trained_chara` 里自己的马
    和被租借槽的因子 id 集合撞上，但 level 不同）⇒ 身份必须带上 level [一手]。"""
    return frozenset((f.get("factor_id"), f.get("level"))
                     for f in (rec.get("factor_info_array") or []) if isinstance(f, dict))


def sig(fs):
    """因子集合的可哈希写法（元素可能是 int/None，统一成字符串再排序）。"""
    return tuple(sorted(map(str, fs)))


def axes_sum(rec):
    return sum(int(rec.get(a) or 0) for a in AXES)


def iter_records(node, out, trail=()):
    """任何「元素是完整马娘记录」的数组都收；靠键集合判定，不靠 jpath 硬编码。
    同时带出**来源数组的标签**（父键/数组键）——「自家花名册」和「继承目录里别人的马」
    长得一样，不标来源就会把目录当池子。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, list):
                for e in v:
                    if isinstance(e, dict) and all(x in e for x in POOL_KEYS):
                        out.append(("/".join(trail[-1:] + (k,)), e))
            iter_records(v, out, trail + (k,))
    elif isinstance(node, list):
        for v in node:
            iter_records(v, out, trail)
    return out


def walk_snap(o, out):
    """终局练成马快照（带 rank_score + 六槽），口径同 #60/#61。"""
    if isinstance(o, dict):
        if "rank_score" in o and "succession_chara_array" in o:
            out.append(o)
        for v in o.values():
            walk_snap(v, out)
    elif isinstance(o, list):
        for v in o:
            walk_snap(v, out)
    return out


def mkrec(r, f, src=""):
    return {"viewer": str(r.get("viewer_id")), "card": r.get("card_id"),
            "tc": r.get("trained_chara_id"), "rarity": r.get("rarity"),
            "scen": r.get("scenario_id"), "sum": axes_sum(r),
            "score": int(r.get("rank_score") or 0),
            "nfac": len(r.get("factor_info_array") or []), "fs": facs(r),
            "talent": r.get("talent_level"), "src": src,
            "ct": str(r.get("create_time") or "")[:10], "file": os.path.basename(f),
            "nobs": 1}


def scan(limit=0):
    pdir = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
    if "E:/HJM/jidianbeiAI0.22" not in sys.path:
        sys.path.insert(1, "E:/HJM/jidianbeiAI0.22")
    import msgpack_lite as ml  # noqa: PLC0415

    files = sorted(glob.glob(os.path.join(pdir, "*.bin")))
    if limit:
        files = files[:limit]
    pool = {}
    src_own = collections.Counter()                          # 来源数组键 -> 自己侧出现次数
    src_oth = collections.Counter()
    slot_hits = collections.Counter()                       # (card, 因子集合) -> 出现在自控格的局数
    slot_rar = collections.defaultdict(set)                 # (card, 因子集合) -> 观测到的 rarity 集合
    rarity_series = collections.defaultdict(set)
    games = {}                                              # 自己 scen15 真练成快照，按 trained_chara_id 去重
    nf = nfail = nrec = 0
    for p in files:
        try:
            d = ml.decode_file(p)
        except Exception:
            nfail += 1
            continue
        nf += 1
        for src, r in iter_records(d, []):
            nrec += 1
            key = (str(r.get("viewer_id")), r.get("trained_chara_id"))
            (src_own if key[0] == OWN_VIEWER else src_oth)[src] += 1
            old = pool.get(key)
            if old is None:
                pool[key] = mkrec(r, p, src)
            else:
                old["nobs"] += 1
                s = axes_sum(r)
                if s > old["sum"] or (s == old["sum"] and int(r.get("rank_score") or 0) > old["score"]):
                    pool[key] = dict(mkrec(r, p, src), nobs=old["nobs"])
            if key[0] == OWN_VIEWER:
                rarity_series[key].add(r.get("rarity"))
        for node in walk_snap(d, []):
            if node.get("scenario_id") != SCEN or str(node.get("viewer_id")) != OWN_VIEWER:
                continue
            if axes_sum(node) < 6000:                       # #55 §1 的「真练成」闸门
                continue
            gk = node.get("trained_chara_id")
            if gk in games:
                continue
            own_par, rent_par, grand = [], [], []
            for s in (node.get("succession_chara_array") or []):
                if not isinstance(s, dict):
                    continue
                pos, card, fs, rar = (s.get("position_id"), s.get("card_id"), facs(s), s.get("rarity"))
                owner = str(s.get("owner_viewer_id") or 0)
                entry = (pos, card, fs, rar, owner)
                if pos in (11, 12, 21, 22):
                    grand.append(entry)
                elif owner == "0":
                    own_par.append(entry)        # owner_viewer_id=0 ⇒ **自家**那匹马（实测见【4c】）
                else:
                    rent_par.append(entry)       # 非零 ⇒ 这匹马属于别的账号（租借带进来的）
            games[gk] = {"card": node.get("card_id"), "ct": str(node.get("create_time") or "")[:10],
                         "score": int(node.get("rank_score") or 0), "sum": axes_sum(node),
                         "nfac": len(node.get("factor_info_array") or []),
                         "own": own_par, "rent": rent_par, "grand": grand}
            for (_pos, card, fs, rar, _owner) in own_par:
                slot_hits[(card, fs)] += 1
                slot_rar[(card, fs)].add(rar)
    print("报文 %d 个（解码失败 %d）｜完整记录出现 %d 次 → 去重 %d 条 ｜ 涉及账号 %d 个 ｜ 自己 %d 条 ｜ "
          "自己 scen15 真练成快照 %d 匹"
          % (nf, nfail, nrec, len(pool), len({k[0] for k in pool}),
             sum(1 for k in pool if k[0] == OWN_VIEWER), len(games)))
    print("  来源数组（自己侧）：%s" % ", ".join("%s×%d" % kv for kv in src_own.most_common()))
    print("  来源数组（别人侧）：%s" % ", ".join("%s×%d" % kv for kv in src_oth.most_common(6)))
    return list(pool.values()), slot_hits, slot_rar, rarity_series, games


def line(name, rs, hi):
    rar = [r["rarity"] for r in rs]
    cov = share([r["sum"] for r in rs], lambda v: v > 0)
    print("  %-26s n=%5d ｜ rarity med %3s ≥%d %5.1f%% =5 %5.1f%% ｜ 五维和 med %6.0f ｜ "
          "因子 med %3.0f ｜ rank_score med %6.0f ｜ 带五轴 %4.0f%%"
          % (name, len(rs), med(rar), hi, share(rar, lambda v: (v or 0) >= hi),
             share(rar, lambda v: v == 5), med([r["sum"] for r in rs]),
             med([r["nfac"] for r in rs]), med([r["score"] for r in rs]), cov))


def fmt(r):
    tag = "" if r["sum"] else "（这条记录没带五轴，**不是 0 分**）"
    return ("card %-7s tc %-5s rarity %s scen %-2s 五维和 %5d rank_score %6d 因子 %2d 出生 %s%s"
            % (r["card"], r["tc"], r["rarity"], r["scen"], r["sum"], r["score"], r["nfac"], r["ct"], tag))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="只开前 N 个报文（0 = 全量）")
    ap.add_argument("--rarity-hi", type=int, default=4, help="高档判定线（沿用 #60/#61 的 ≥4）")
    args = ap.parse_args(argv)

    rs, slot_hits, slot_rar, rarity_series, games = scan(args.limit)
    hi = args.rarity_hi
    own = [r for r in rs if r["viewer"] == OWN_VIEWER]
    oth = [r for r in rs if r["viewer"] != OWN_VIEWER]

    print("\n【1 必须按剧本分层（不加这层就是假账）】")
    print("  自己记录按 scenario_id：%s"
          % ", ".join("scen%s:%d" % (k, v) for k, v in
                      sorted(collections.Counter(r["scen"] for r in own).most_common(), key=lambda kv: str(kv[0]))))
    print("  ⇒ 直接汇总会把 scen14 攒的马算进 scen15 的池子；下面所有行都标了剧本。")

    print("\n【2 档位分布（分剧本；最后一列＝这条记录带不带五轴，防止把「读不到」当 0）】")
    line("自己 · scen15", [r for r in own if r["scen"] == SCEN], hi)
    line("自己 · scen14", [r for r in own if r["scen"] == 14], hi)
    line("自己 · 其它剧本", [r for r in own if r["scen"] not in (SCEN, 14)], hi)
    line("别人 · scen15", [r for r in oth if r["scen"] == SCEN], hi)
    line("别人 · scen15 且分≥85,600", [r for r in oth if r["scen"] == SCEN and r["score"] >= 85600], hi)
    line("别人 · 全部剧本", oth, hi)
    print("  ⚠ 「别人 · 全部剧本」里五维和 med 0 是**字段没带**不是真的 0（账号列表副本），"
          "看最后一列的覆盖率；带五轴的行才可用于分数比较 [一手]")

    print("\n【3 自控双亲格用了哪几匹马（身份 = card ＋ 因子 id 集合）】")
    print("  对法：快照里 `position_id∈{10,20}` 且 `owner_viewer_id=0`＝自控格（#61【8】）")
    by_key = {(r["card"], r["fs"]): r for r in own}
    by_card = collections.defaultdict(list)
    for r in own:
        by_card[r["card"]].append(r)
    n_exact = n_agree = n_missing = n_conflict = 0
    for (card, fs), hits in sorted(slot_hits.items(), key=lambda kv: str(kv[0][0])):
        r, how = by_key.get((card, fs)), "因子集合逐字节相同"
        rar_set = slot_rar[(card, fs)]
        if len(rar_set) > 1:
            n_conflict += 1
        if r is None:
            cand = by_card.get(card) or []
            if not cand:
                n_missing += 1
                print("  card %-8s 自控格 %d 局 ｜ 格 rarity %s ｜ 池子里**没有**这张卡的记录"
                      % (card, hits, sorted(map(str, rar_set))))
                continue
            r = max(cand, key=lambda x: len(x["fs"] & fs))
            how = "只同卡（因子重合 %d/%d）" % (len(r["fs"] & fs), len(fs))
        else:
            n_exact += 1
            n_agree += all(x in [r["rarity"]] for x in rar_set)
        print("  card %-8s 自控格 %d 局 ｜ 格 rarity %s ｜ 池子 %s ｜ %s"
              % (card, hits, sorted(map(str, rar_set)), fmt(r), how))
    print("  ⇒ 因子集合逐字节对上 %d 匹 ｜ 格上 rarity 与池子记录**全观测一致** %d 匹 ｜ "
          "格内 rarity 自相矛盾 %d 匹 ｜ 池子里查无此卡 %d 匹"
          % (n_exact, n_agree, n_conflict, n_missing))
    print("  ⇒ 槽位 rarity 就是池子记录同一个字段（#60「随马走不随卡走」再加正向证据）；"
          "「查无此卡」只说明这匹没出现在采到的账号列表里 [一手]")

    print("\n【4 逐局六槽全表 ＋ 该局产出日池子里最好的是谁】")
    print("  格子写法 `槽号:卡/档位/归属`，归属＝`owner_viewer_id`（0＝自家马，非 0＝那匹马属于该账号）")
    glist = sorted(games.items(), key=lambda kv: kv[1]["ct"])
    print("  自己 scen15 真练成 %d 匹，产出日 %s … %s"
          % (len(glist), glist[0][1]["ct"] if glist else "—", glist[-1][1]["ct"] if glist else "—"))
    for gk, g in glist:
        cells = []
        for (pos, card, _fs, rar, owner) in sorted(g["own"] + g["rent"] + g["grand"]):
            cells.append("%d:%s/r%s/%s" % (pos, card, rar, "自家" if owner == "0" else owner))
        pool_best = [x for x in own if x["ct"] and x["ct"] <= g["ct"] and (x["rarity"] or 0) >= hi
                     and x["tc"] != gk]                      # 本匹自己不算进「可选的种马」
        top = max(pool_best, key=lambda x: x["score"]) if pool_best else None
        print("  局 tc %-5s 产出 %s 本匹 card %-7s 五维和 %5d 分 %6d ｜ %s"
              % (gk, g["ct"], g["card"], g["sum"], g["score"], " ".join(cells)))
        print("        自控格 %s ｜ 当日池内 ≥%d 最高分 %s"
              % ("、".join("card %s r%s（池子 %s 分 %d 出生 %s）"
                           % (c, r, x["scen"], x["score"], x["ct"])
                           if x else "card %s r%s（池子里没这张卡）" % (c, r)
                           for (pos, c, f, r, _o) in g["own"]
                           for x in [by_key.get((c, f))]) or "无",
                 hi,
                 "无" if top is None else
                 "card %s tc %s r%s scen %s 分 %6d 出生 %s%s"
                 % (top["card"], top["tc"], top["rarity"], top["scen"], top["score"], top["ct"],
                    "（＝自控格那匹）" if any(top["card"] == c and top["fs"] == f
                                              for (_p, c, f, _r, _o) in g["own"]) else "（没进过自控格）")))
    print("  ⚠ 「当日池内」用 `create_time` ≤ 该局**产出日**近似（开局日读不到），"
          "开局早于产出日 ⇒ 这匹马在开局时可能还没生出来，判读时按上界看 [判读]")

    print("\n【4b 祖父母四格随不随父母两格走（槽 10↔11/12、槽 20↔21/22 的配对假设）】")
    LINEAGE = {10: (11, 12), 20: (21, 22)}
    det = collections.defaultdict(collections.Counter)
    for _gk, g in games.items():
        slots = {p: (c, f, o) for (p, c, f, _r, o) in (g["own"] + g["rent"] + g["grand"])}
        for par_pos, (gp1, gp2) in LINEAGE.items():
            par = slots.get(par_pos)
            if not par or gp1 not in slots or gp2 not in slots:
                continue
            gsig = tuple(sorted("%s|%s" % (slots[gp][0], sig(slots[gp][1])) for gp in (gp1, gp2)))
            det[(par_pos, par[0], sig(par[1]), par[2])][gsig] += 1
    multi = sum(1 for cnt in det.values() if len(cnt) > 1)
    repeat = sum(1 for cnt in det.values() if sum(cnt.values()) > 1)
    same = tot = 0
    for _gk, g in games.items():
        ss = {p: o for (p, _c, _f, _r, o) in (g["own"] + g["rent"] + g["grand"])}
        for par_pos, (gp1, _gp2) in LINEAGE.items():
            if par_pos in ss and gp1 in ss:
                tot += 1
                same += (ss[par_pos] == ss[gp1])
    for key, cnt in sorted(det.items(), key=lambda kv: str(kv[0])):
        print("  槽%-3d card %-7s owner %-10s 出现 %d 局 ｜ 它带出的祖父母(槽%s)取值 %d 种"
              % (key[0], key[1], key[3], sum(cnt.values()), LINEAGE[key[0]], len(cnt)))
    print("  逐局看「子格 owner 是否等于父母格 owner」（槽 11↔10、槽 21↔20）：%d/%d 局相同"
          % (same, tot))
    print("  「父母格身份」共 %d 个 ｜ 跨 ≥2 局重复的 %d 个 ｜ 重复身份里带出过**不同**祖父母组合的 %d 个"
          % (len(det), repeat, multi))
    print("  ⇒ %s"
          % ("每个父母身份只出现 1 局 ⇒ 这条测试**没有分辨力**，说不上谁随谁 [判读]" if repeat == 0 else
             "重复出现的父母身份，每次带出的祖父母组合**逐字节相同** ⇒ 祖父母随父母带出、不占独立自由度；"
             "#61 里「祖父母格也大量非零」由此得到解释 [一手]" if multi == 0 else
             "同一父母身份能带出不同祖父母 ⇒ 祖父母格另有自由度 [一手]"))
    if tot and same == tot:
        print("  ⇒ 槽 11/21 的归属**局局跟着自己的父母格** ⇒ 近一格不是我们填的，"
              "是父母那匹马自带的（租借的父母会把它的双亲一起带进来）[一手]")

    print("\n【4c (card, 因子 id+level) 这个身份能不能唯一认领一匹马】")
    idv = collections.defaultdict(set)
    for r in rs:
        idv[(r["card"], r["fs"])].add(r["viewer"])
    n_id = len(idv)
    n_multi = sum(1 for v in idv.values() if len(v) > 1)
    print("  采集面内身份 %d 个 ｜ 同一身份挂在 >1 个账号名下的 %d 个（%.1f%%）"
          % (n_id, n_multi, 100.0 * n_multi / n_id if n_id else 0))
    rows, rfs = collections.Counter(), {}
    for lbl, lst in (("自控格", [e for g in games.values() for e in g["own"]]),
                     ("租借格", [e for g in games.values() for e in g["rent"]]),
                     ("祖父母", [e for g in games.values() for e in g["grand"]])):
        for (pos, card, fs, rar, owner) in lst:
            kk = (lbl, pos, card, rar, owner)
            rows[kk] += 1
            rfs[kk] = fs
    for kk, n in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]))):
        lbl, pos, card, rar, owner = kk
        vs = idv.get((card, rfs[kk]), set())
        print("    %s 槽%-3d card %-7s r%s owner %-10s ｜ %d 局 ｜ 该身份属于 %d 个账号%s"
              % (lbl, pos, card, rar, owner, n, len(vs), "（含自家）" if OWN_VIEWER in vs else ""))
    print("  ⚠ 因子条目只有 `(factor_id, level)` 两个字段 ⇒ 同卡同因子组合**可能撞车**，"
          "身份相同不等于同一匹马。本节认领归属只认 `owner_viewer_id`（#61【8】请求侧核过的那条）[一手]")

    print("\n【5 rarity 会不会随时间被改（决定「当时低、现在高」这种解释成不成立）】")
    chg = [(k, v) for k, v in rarity_series.items() if len({x for x in v if x is not None}) > 1]
    print("  自己 %d 匹马跨报文观测，rarity 取过多个值的 %d 匹" % (len(rarity_series), len(chg)))
    for k, v in chg[:4]:
        print("    tc %-6s 观测值 %s" % (k[1], sorted(map(str, v))))
    ph = [k for k, _v in chg if k[1] in (0, None)]
    print("  ⇒ %s"
          % ("同一匹马的 rarity 在采集面内不变（变的那 %d 匹是 tc=0 的占位记录）⇒"
             "「产马时档位低、后来解放上去了」这条解释**不成立** [一手]" % len(ph)
             if len(chg) == len(ph) else
             "有 %d 匹**非占位**的马 rarity 跨报文取过多个值 ⇒「当时档位低、后来上去了」这条解释"
             "**没被排除**，下面【6】的「现在的高档」要按上界看 [一手]" % (len(chg) - len(ph))))

    print("\n【6 池子里有、但没被用去当父母的高档马（自控的那一格）】")
    used = set(slot_hits)
    print("  自控格出现过的 (card, 因子集合) %d 匹 ｜ 自控格总次数 %d（= 局数 × 自控格数）"
          % (len(used), sum(slot_hits.values())))
    first_ct = min([g["ct"] for g in games.values() if g["ct"]], default="")
    for sc in (SCEN, 14):
        cand = sorted([r for r in own if r["scen"] == sc and (r["rarity"] or 0) >= hi],
                      key=lambda r: -r["score"])
        avail = [r for r in cand if r["ct"] and r["ct"] < first_ct and r["tc"] not in games]
        print("  scen%s：rarity≥%d 共 %d 匹 ｜ 其中**早于首局产出日 %s 就存在、且不是本批产出**的 %d 匹"
              % (sc, hi, len(cand), first_ct, len(avail)))
        for b in cand[:6]:
            tag = "" if (b["card"], b["fs"]) in used else "← 没进过自控格"
            if b["tc"] in games:
                tag += "（＝本批 %d 局之一的产出，不可能当自己的父母）" % len(games)
            print("    %s %s" % (fmt(b), tag))
        for b in avail[:3]:
            print("    可选而未用 ▸ %s" % fmt(b))
    print("  ⚠ 「没被用」是报文事实，**为什么**没用报文读不到（继承卡组配置的写入包没采到，"
          "`succession_deck_last_used` 在自己侧 0 命中）⇒ 本文不猜动机 [判读]")

    print("\n【7 这条能说什么、不能说什么】")
    xsc = [(c, f, by_key[(c, f)]["scen"]) for (c, f) in slot_hits
           if (c, f) in by_key and by_key[(c, f)]["scen"] != SCEN]
    print("  能：自家攒到的档位（分剧本）、自控格实际用了谁、同一匹马的 rarity 稳定 [一手]")
    print("  能：跨剧本当父母%s——自控格里有 %d 格用的马在池子里标的是 scen%s（【3】） [一手]"
          % ("**成立**" if xsc else "（本批数据里**没观测到**，不能断言）",
             len(xsc), ",".join(sorted({str(s) for (_c, _f, s) in xsc})) or "—"))
    print("  能：六格里**只有父母两格是自由位**——祖父母四格随父母那匹马带出，"
          "槽 11/21 的归属 %d/%d 局跟着父母格（【4b】） [一手]" % (same, tot))
    print("  不能：租借池长什么样——**盘上没有租借列表报文** [一手·缺采]")
    print("  不能：换上去涨多少分。#54 §3 只给了形状（每腿抬 11–42，不预测），本文不换算 [判读]")
    print("  不能：scen14 攒的马五维构成合不合 scen15 的加分曲线——档位同形不等于价值同形 [判读]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
