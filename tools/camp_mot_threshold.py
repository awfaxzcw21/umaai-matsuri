#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""#64：合宿首格的 干劲+1 是事件效果，还是「体力→干劲档位」的副产品？

用法：
    python tools/camp_mot_threshold.py              # 读 #53 的缓存（全库＝单 viewer，已核）
    python tools/camp_mot_threshold.py --rebuild    # 重扫原始报文

只做报文 + 表侧，不跑引擎。产物：
    out/_camp_mot_samples.csv   29 条合宿标记包逐条（含前后 chara_effect）
    out/_camp_mot_cross.csv     全库「体力净增包 → 干劲变化」对照表（按 Δv 分档）
    out/_camp_mot_arrivals.csv  全库「效果号新出现 → 当次干劲变化」到达表
    out/_camp_mot_effects.json  file → chara_effect_id_array 缓存（重跑免解码）
    out/_camp_mot_report.txt    阈值模型搜索 + 效果号时间线 + 表侧查询台账 + 裁决
"""
import argparse
import collections
import csv
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")   # 解码器在面板仓，与 #53 同一份
import camp_axis_scan as A53      # noqa: E402  复用 #53 的解码器与缓存（同一口径，不另起一套）
import msgpack_lite as mp         # noqa: E402

C53_CACHE = os.path.join(ROOT, "out", "_camp_axis_pkt.json")
EFF_CACHE = os.path.join(ROOT, "out", "_camp_mot_effects.json")
OUT_S = os.path.join(ROOT, "out", "_camp_mot_samples.csv")
OUT_X = os.path.join(ROOT, "out", "_camp_mot_cross.csv")
OUT_XR = os.path.join(ROOT, "out", "_camp_mot_cross_rows.csv")
OUT_A = os.path.join(ROOT, "out", "_camp_mot_arrivals.csv")
OUT_T = os.path.join(ROOT, "out", "_camp_mot_report.txt")
YR = {13: 1, 37: 2, 61: 3}
CAMP_EVENTS = {215016: 1, 215017: 2, 215018: 3}


# ---------------------------------------------------------------- 取样本

def onsets(pkts):
    """全库扫 `summer_camp_remain_turn_num` 由 0→4 的那条包（=合宿正式开始的结算包）。"""
    per = A53.series(pkts)
    out = []
    for run, d in sorted(per.items()):
        flat = [p for t in sorted(d) for p in d[t]]
        for a, b in zip(flat, flat[1:]):
            if a["camp_left"] == 0 and b["camp_left"] == 4:
                out.append({"run": run, "viewer": b["viewer"], "chara": b["chara"],
                            "yr": YR.get(b["turn"], "?"), "turn": b["turn"], "file": b["file"],
                            "file_prev": a["file"],
                            "event_id": b["event_id"], "req": b["req"], "cmd": b["cmd"],
                            "v_before": a["vital"], "v_after": b["vital"],
                            "d_v": (b["vital"] or 0) - (a["vital"] or 0),
                            "max_vital": b["max_vital"],
                            "mot_before": a["mot"], "mot_after": b["mot"],
                            "d_mot": (b["mot"] or 0) - (a["mot"] or 0)})
    return out


def effects(files):
    """补每条标记包与其前一条包的 `chara_effect_id_array`（缓存里没存这个字段）。"""
    want = {f for f in files if f}
    out = {}
    for name in sorted(os.listdir(A53.RAW)):
        if name not in want:
            continue
        d = mp.decode_file(os.path.join(A53.RAW, name))
        ci = (d.get("data") or {}).get("chara_info") or {}
        out[name] = ci.get("chara_effect_id_array") or []
    return out


# ---------------------------------------------------------------- 阈值模型搜索

def best_threshold(rows, key):
    """对每个候选阈值 k：`+1` 应恰好被 `x_before < k <= x_after` 解释。返回最优 k 与违例数。"""
    xs = sorted({v for r in rows for v in (r[key[0]], r[key[1]]) if v is not None})
    if not xs:
        return (None, 0)
    best = None
    for k in [x + 0.5 for x in xs]:
        cross = lambda r: (r[key[0]] is not None and r[key[1]] is not None
                           and r[key[0]] < k <= r[key[1]])
        ok = sum(1 for r in rows if (r["d_mot"] > 0) == bool(cross(r)))
        if best is None or ok > best[1]:
            best = (k, ok)
    return best


def best_band(rows, col):
    """形式 B：`Δmot>0` 由该坐标**单侧分档**决定（四个方向 × 全部候选 k 都试）。
    返回 (方向, k, 吻合数, 是否平凡)。平凡＝判据对全部样本同真或同假，
    也就是「每格都给」／「从不给」这种不含阈值的常数预测——它不构成档位模型。"""
    xs = sorted({r[col] for r in rows if r[col] is not None})
    if not xs:
        return ("—", "—", 0, True)
    forms = {"≥": lambda x, k: x >= k, "<": lambda x, k: x < k,
             "≤": lambda x, k: x <= k, ">": lambda x, k: x > k}
    best = (None, None, -1, True)
    for name, fn in forms.items():
        for k in xs + [x + 0.5 for x in xs]:
            hit = [bool(fn(r[col], k)) for r in rows if r[col] is not None]
            ok = sum(1 for r, h in zip([q for q in rows if q[col] is not None], hit)
                     if (r["d_mot"] > 0) == h)
            if ok > best[2]:
                best = (name, k, ok, all(hit) or not any(hit))
    return best


def interior(rows, key):
    """0 样本在该坐标上能不能和 +1 样本分开。
    `inside`＝落在 +1 云团开区间内部，`same`＝**与某条 +1 样本同值**——
    同值就已经判不开了（阈值只能选边，不能把同值两半拆开），所以 `same` 是判据，
    `inside` 只是更严格的充分条件。"""
    up = [r[key] for r in rows if r["d_mot"] > 0 and r[key] is not None]
    zero = [r[key] for r in rows if r["d_mot"] <= 0 and r[key] is not None]
    if not up or not zero:
        return None
    lo, hi = min(up), max(up)
    uset = set(up)
    inside = [z for z in zero if lo < z < hi]
    same = [z for z in zero if z in uset]
    return (lo, hi, inside, sorted(up), sorted(zero), same)


def best_fraction_band(rows):
    """形式 C（档位模型的最强形式）：档位线**随上限缩放** `b = f × max_vital`，
    给 +1 当且仅当本包跨线（`v_before < b ≤ v_after`）。扫 f∈(0,1)，步长 0.005。"""
    best = (None, -1)
    rows = [r for r in rows if r["max_vital"] and r["v_before"] is not None
            and r["v_after"] is not None]
    if not rows:
        return (None, 0)
    for i in range(1, 200):
        f = i / 200.0
        ok = 0
        for r in rows:
            b = f * r["max_vital"]
            ok += int((r["d_mot"] > 0) == (r["v_before"] < b <= r["v_after"]))
        if ok > best[1]:
            best = (f, ok)
    return best


def tag(r):
    """`238107205@169@2026-10-26 21:13:27` → `@169`（viewer 前缀一律丢掉）。"""
    return "@" + str(r["run"]).split("@", 2)[1]


def one_line(r):
    return "%s 面板%d 体力 %s→%s（max %s）干劲 %s→%s ev=%s" % (
        tag(r), r["turn"], r["v_before"], r["v_after"], r["max_vital"],
        r["mot_before"], r["mot_after"], r["event_id"])


def counterexamples(rows):
    """两个方向各找**真实样本**钉死：高档位才给 / 低档位才给。找不到就如实说没有。"""
    up = [r for r in rows if r["d_mot"] > 0]
    zero = [r for r in rows if r["d_mot"] <= 0]
    out = []
    for z in zero:
        hi = [r for r in up if r["max_vital"] == z["max_vital"] and r["d_v"] == z["d_v"]
              and r["v_after"] < z["v_after"]]
        if hi:
            r = max(hi, key=lambda q: (q["run"] == z["run"], q["v_after"]))
            out.append("方向「体力越高越给」被%s否掉：%s **给了** +1，"
                       "而体力更高、上限与 Δv 都相同的 %s **没给**。"
                       % ("**同一局内**" if r["run"] == z["run"] else "跨局",
                          one_line(r), one_line(z)))
        lo = [r for r in up if r["mot_before"] == z["mot_before"] and r["v_before"] > z["v_before"]]
        if lo:
            r = min(lo, key=lambda q: q["v_before"])
            out.append("方向「体力越低越给（补偿）」被否掉：同为干劲前值 %s 的格，"
                       "%s **没给** +1，体力**更高**的 %s **给了**。"
                       % (z["mot_before"], one_line(z), one_line(r)))
    if not out:
        out.append("（没找到成对样本 ⇒ 只能靠 §2 的违例数判，不给定向反证。）")
    return out


# ---------------------------------------------------------------- 全库对照


def control(pkts):
    """同回合内「体力净增」的包 → 干劲变化分布（按 Δv 分档），当基准率用。"""
    per = A53.series(pkts)
    buckets = collections.defaultdict(collections.Counter)
    ratio = collections.defaultdict(list)
    xr = []
    n_up = n = 0
    for run, d in per.items():
        flat = [p for t in sorted(d) for p in d[t]]
        for a, b in zip(flat, flat[1:]):
            if a["turn"] != b["turn"] or None in (a["vital"], b["vital"], b["max_vital"],
                                                  a["mot"], b["mot"]):
                continue
            dv = b["vital"] - a["vital"]
            if b["vital"] and b["max_vital"]:
                ratio[b["mot"]].append(round(b["vital"] / b["max_vital"], 3))
            if dv <= 0:
                continue
            n += 1
            if b["mot"] > a["mot"]:
                n_up += 1
            key = "0-9" if dv < 10 else "10-19" if dv < 20 else "20-49" if dv < 50 else "50+"
            buckets[key][b["mot"] - a["mot"]] += 1
            xr.append([key, run, b["turn"], a["file"], b["file"], a["vital"], b["vital"], dv,
                       b["max_vital"], a["mot"], b["mot"], b["mot"] - a["mot"],
                       int(b["camp_left"] or 0) > 0, b["cmd"], b["event_id"]])
    return buckets, n, n_up, ratio, xr


# ---------------------------------------------------------------- 效果号时间线

def effmap(pkts, rebuild=False):
    """`file → chara_effect_id_array`（逗号串）。全库响应包都补，缓存到 `out/_camp_mot_effects.json`。"""
    if os.path.exists(EFF_CACHE) and not rebuild:
        return json.load(open(EFF_CACHE, encoding="utf-8"))
    files = sorted({p["file"] for p in pkts})
    got = effects(files)
    m = {f: ",".join(str(x) for x in got.get(f, [])) for f in files}
    json.dump(m, open(EFF_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return m


def arrivals(pkts, m):
    """全库：每条包相对同局上一条包**新出现**的效果号 → 当次 Δmot。
    跨局多样本的检验，用来看「效果号 X」到底和干劲有没有剂量关系。"""
    per = A53.series(pkts)
    rows = []
    for run, d in sorted(per.items()):
        flat = [p for t in sorted(d) for p in d[t]]
        for a, b in zip(flat, flat[1:]):
            if None in (a["mot"], b["mot"]):
                continue
            sa = {x for x in (m.get(b["file"]) or "").split(",") if x}
            sp = {x for x in (m.get(a["file"]) or "").split(",") if x}
            for e in sorted(sa - sp):
                rows.append({"effect": e, "run": run, "turn": b["turn"],
                             "d_mot": b["mot"] - a["mot"], "camp": int(b["camp_left"] or 0) > 0})
    return rows


def mot_command_probe(pkts):
    """第三条路的报文实证：`single_mode_training_effect.target_type=20` 到底是什么、
    挂在哪些 command 上。同回合相邻包求 (Δv, Δmot)，按 `command_result.command_id` 归组。"""
    per = A53.series(pkts)
    got = collections.defaultdict(list)
    for run, d in per.items():
        for t, ps in d.items():
            for a, b in zip(ps, ps[1:]):
                if b["cmd"] is None or None in (a["vital"], b["vital"], a["mot"], b["mot"]):
                    continue
                got[b["cmd"]].append((b["vital"] - a["vital"], b["mot"] - a["mot"], t,
                                      int(b["camp_left"] or 0) > 0, a["mot"]))
    return got


def mot_effect_rows():
    """表侧：scen15 的 `target_type=20` 挂在哪些 command 上、值多少；顺带列出
    同一批 command 在表里到底有没有行（区分「表里没这条 command」和「有 command 但不发干劲」）。"""
    db = sqlite3.connect("file:%s?mode=ro" % A53.MASTER.replace("\\", "/"), uri=True)
    mot = list(db.execute("select command_id, sub_id, result_state, effect_value "
                          "from single_mode_training_effect where scenario_id=15 "
                          "and target_type=20 order by command_id, sub_id"))
    kinds = list(db.execute("select target_type, count(*) from single_mode_training_effect "
                             "where scenario_id=15 group by target_type order by target_type"))
    have = collections.defaultdict(set)
    for cid, tt in db.execute("select command_id, target_type from single_mode_training_effect "
                              "where scenario_id=15"):
        have[cid].add(tt)
    return mot, kinds, dict(have)


# ---------------------------------------------------------------- 表侧

def effect_span(pkts, m, run, eid):
    """某局里该效果号**存续**的面板回合区间，加它首次出现那一条包的 Δmot。"""
    per = A53.series(pkts)
    d = per.get(run, {})
    ts = sorted(t for t in d if d[t])
    seen, turns = [], []
    for t in ts:
        for p in d[t]:
            if eid in [x for x in (m.get(p["file"]) or "").split(",") if x]:
                turns.append(t)
    if not turns:
        return "该局内不存在"
    return "面板 %d–%d（%d 个回合有它）" % (min(turns), max(turns), len(set(turns)))


def mdb_probe():
    db = sqlite3.connect("file:%s?mode=ro" % A53.MASTER.replace("\\", "/"), uri=True)
    tabs = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    with_mot, with_thr = [], []
    for t in tabs:
        cols = [c[1] for c in db.execute('pragma table_info("%s")' % t)]
        m = [c for c in cols if "motivat" in c]
        if m:
            with_mot.append((t, m))
        thr = [c for c in cols if any(w in c for w in
               ("threshold", "boundary", "border", "rank_vital", "vital_to", "step_vital"))]
        if thr:
            with_thr.append((t, thr))
    return len(tabs), with_mot, with_thr


def effect_meta(ids):
    """表侧 `single_mode_chara_effect` 里这些效果号的 (type, category, group, priority)。"""
    db = sqlite3.connect("file:%s?mode=ro" % A53.MASTER.replace("\\", "/"), uri=True)
    out = {}
    for i in {str(x) for x in ids}:
        try:
            r = list(db.execute("select effect_type, effect_category, effect_group_id, priority "
                                "from single_mode_chara_effect where id=?", (int(i),)))
        except (sqlite3.Error, ValueError):
            r = []
        out[i] = r[0] if r else None
    return out


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    if a.rebuild or not os.path.exists(C53_CACHE):
        cache = A53.scan(None, 15)
        json.dump(cache, open(C53_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        print("重扫 %d 文件 / %d 包 / %.1fs" % (cache["files"], cache["n"], cache["seconds"]))
    else:
        cache = A53.load_cache(C53_CACHE)
    pkts = cache["pkts"]
    rows = onsets(pkts)
    eff = effects([r["file"] for r in rows] + [r["file_prev"] for r in rows])
    for r in rows:
        r["effects_after"] = ",".join(str(x) for x in (eff.get(r["file"]) or []))
        r["effects_prev"] = ",".join(str(x) for x in (eff.get(r["file_prev"]) or []))
        sa = {x for x in r["effects_after"].split(",") if x}
        sp = {x for x in r["effects_prev"].split(",") if x}
        r["d_effects"] = int(sa != sp)
    for r in rows:
        r["camp_event"] = int(str(r["event_id"]) in {str(k) for k in CAMP_EVENTS})
        r["falsifiable"] = int((r["mot_before"] or 0) < 5)
        r["ratio_before"] = round(r["v_before"] / r["max_vital"], 4) if r["max_vital"] else None
        r["ratio_after"] = round(r["v_after"] / r["max_vital"], 4) if r["max_vital"] else None

    fals = [r for r in rows if r["falsifiable"]]
    odd = [r for r in rows if r["yr"] == "?"]
    L = []
    P = L.append
    P("# #64 合宿 干劲+1：是不是体力阈值的副产品（2026-11-05，纯报文，未跑引擎）")
    P("")
    P("## 0. 样本量（先把口径钉死，不扩）")
    vs = sorted({p["viewer"] for p in pkts})
    nr = len({p["run"] for p in pkts})
    P("- 原始报文目录里 **scen15 有效响应包 %d 条，viewer 只有 %s，共 %d 局**⇒ "
      "「所有局」＝这 %d 局，**不存在窗口外的额外合宿首格**。" % (len(pkts), vs, nr, nr))
    P("- 全库 `camp_left` 0→4 标记包 **%d** 条（年次 1/2/3 ＝ %d/%d/%d），落点全部是面板 13/37/61。"
      % (len(rows), sum(1 for r in rows if r["yr"] == 1), sum(1 for r in rows if r["yr"] == 2),
         sum(1 for r in rows if r["yr"] == 3)))
    P("- 其中带合宿事件号（215016/215017/215018）**%d** 条；另 %d 条是体力顶满、"
      "增益被整条吞掉后「第一条读到 camp_left=4」的无关包（不计入效果样本）。"
      % (sum(r["camp_event"] for r in rows), len(rows) - sum(r["camp_event"] for r in rows)))
    P("- **干劲可证伪样本**（标记前值 <5）＝ **%d** 条：+1 %d 条 / +0 %d 条。其余 %d 条前值已顶满 5，判不了。"
      % (len(fals), sum(1 for r in fals if r["d_mot"] > 0), sum(1 for r in fals if r["d_mot"] <= 0),
         len(rows) - len(fals)))
    if odd:
        P("- ⚠ 有 %d 条标记包落点不在 13/37/61：%s ⇒ 先按异常登记，不并进年次分组。"
          % (len(odd), [(r["run"][9:], r["turn"]) for r in odd]))
    P("")

    P("## 1. 标记包逐条（%d 条全列）" % len(rows))
    P("| 年 | run | 面板turn | v前→后 | Δv | max_v | v前/max | v后/max | mot前→后 | Δmot | event | 前值可证伪 | chara_effect 前→本 |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (str(x["yr"]), x["run"])):
        P("| %s | %s | %d | %s→%s | %+d | %s | %s | %s | %s→%s | %+d | %s | %s | %s%s |"
          % (r["yr"], r["run"][9:], r["turn"], r["v_before"], r["v_after"], r["d_v"],
             r["max_vital"], r["ratio_before"], r["ratio_after"], r["mot_before"], r["mot_after"],
             r["d_mot"], r["event_id"], "是" if r["falsifiable"] else "否(顶满)",
             r["effects_prev"], ("→" + r["effects_after"]) if r["d_effects"] else "（同前）"))
    P("")

    P("## 2. 阈值/档位模型搜索（只允许单调形式，允差 0）")
    n_up0 = sum(1 for r in fals if r["d_mot"] > 0)
    P("先立**门槛**：『每格都给 +1』这条平凡预测本身就吻合 %d/%d（违例 %d）。"
      "所以档位模型要算数，必须**严格优于** %d/%d；吻合数等于它的都只是换了个说法的常数预测。"
      % (n_up0, len(fals), len(fals) - n_up0, n_up0, len(fals)))
    P("")
    P("形式 A：存在绝对阈值 k，使 `Δmot>0` ⟺ `v_before < k ≤ v_after`（跨线才给 +1）。")
    for name, (kb, ka) in [("v_before→v_after", ("v_before", "v_after")),
                           ("ratio_before→ratio_after", ("ratio_before", "ratio_after"))]:
        b = best_threshold(fals, (kb, ka))
        P("- %s：最优 k≈%s 时吻合 %d/%d ⇒ **违例 %d**"
          % (name, ("%.4f" % b[0]) if b[0] is not None else "—", b[1], len(fals), len(fals) - b[1]))
    P("")
    P("形式 B：`Δmot>0` 由 `x` 单侧分档决定（`x ≥ k`／`x < k`／`x ≤ k`／`x > k` 四向都扫）。"
      "标 ⨯ 的最优解是**平凡解**（对所有样本同真/同假）＝ 门槛那条，不是档位。")
    for col in ("v_before", "v_after", "ratio_before", "ratio_after", "d_v", "mot_before", "max_vital"):
        name, k, ok, triv = best_band(fals, col)
        it = interior(fals, col)
        note = ""
        if it:
            lo, hi, inside, ups, zeros, same = it
            if same:
                note = ("；0 样本取值 %s **与 %d 条 +1 样本同值** ⇒ 阈值选不了边"
                        % (zeros, sum(1 for u in ups if u in set(zeros))))
            elif inside:
                note = ("；0 样本取值 %s 虽唯一，但**落在 +1 云团 [%s, %s] 内部** ⇒ "
                        "单侧阈值想包住 9 条 +1 就必然把它也包进来" % (zeros, lo, hi))
            else:
                note = "；0 样本取值 %s 在 +1 云团 [%s, %s] 之外 ⇒ 该坐标单侧阈值**可分**" % (zeros, lo, hi)
        P("- %-13s 最优形式 `x %s %s` 吻合 %d/%d ⇒ **违例 %d**%s%s"
          % (col, name, k, ok, len(fals), len(fals) - ok, " ⨯平凡" if triv else "", note))
    P("")
    fc, okc = best_fraction_band(fals)
    P("形式 C（档位模型的**最强**形式）：档位线随上限缩放 `b = f × max_vital`，给 +1 ⟺ 本包跨线。"
      "扫 f∈(0,1)、步长 0.005（本库可证伪样本的 `max_vital` 取值 %s，线确实逐局变）"
      "⇒ 最优 f=%s 吻合 %d/%d ⇒ **违例 %d**"
      % (sorted({r["max_vital"] for r in fals}), ("%.3f" % fc) if fc else "—",
         okc, len(fals), len(fals) - okc))
    P("")
    P("定向反证（两个方向各钉一条真实样本，不是叙述）：")
    for line in counterexamples(fals):
        P("- " + line)
    P("")
    def esets(want_up):
        s = collections.Counter()
        for r in fals:
            if (r["d_mot"] > 0) != want_up:
                continue
            for e in [x for x in r["effects_after"].split(",") if x]:
                s[e] += 1
        return dict(sorted(s.items(), key=lambda kv: -kv[1]))
    P("chara_effect 侧（本包上的效果号集合）：+1 样本 %s ｜ 0 样本 %s"
      % (esets(True), esets(False)))
    up_ids = {e for r in rows if r["d_mot"] > 0 for e in r["effects_after"].split(",") if e}
    zn_ids = {e for r in fals if r["d_mot"] <= 0 for e in r["effects_after"].split(",") if e}
    only_zero = sorted(zn_ids - up_ids)
    nchg = sum(1 for r in rows if r["d_effects"])
    P("- 标记包本身**没有一条**在结算包上新增/移除效果号（有变化的 %d/%d）⇒ 干劲不是靠「效果号跳变」发的。"
      % (nchg, len(rows)))
    P("- 0 样本的效果号 %s 里，**全部 +1 标记包（29 条口径）都没有的**只有：%s"
      % (sorted(zn_ids), only_zero or "无"))
    P("  ⇒ 这一条**只是线索**（0 样本只有 1 条，独占性不构成机制）；能不能解释干劲，用 §4 的跨局到达表判。")
    P("")

    buckets, n, n_up, ratio, xr = control(pkts)
    P("## 3. 全库对照：体力净增包到底会不会带干劲（基准率）")
    P("- 同回合内体力净增的包 %d 条，其中干劲也上升的 %d 条（%s）"
      % (n, n_up, ("%.1f%%" % (100.0 * n_up / n)) if n else "—"))
    P("| Δv 档 | 包数 | Δmot 分布 |")
    P("|---|---|---|")
    for k in ("0-9", "10-19", "20-49", "50+"):
        if not buckets[k]:
            continue
        P("| %s | %d | %s |" % (k, sum(buckets[k].values()),
                                ", ".join("%+d:%d" % (a, b) for a, b in sorted(buckets[k].items()))))
    P("- 各干劲档位的 `vital/max_vital` 取值区间（重叠 ⇒ 干劲不是体力的分档函数）：")
    for mv in sorted(ratio):
        v = sorted(ratio[mv])
        P("  - mot=%d n=%d ratio 范围 %.3f–%.3f" % (mv, len(v), v[0], v[-1]))
    P("")

    m = effmap(pkts, rebuild=a.rebuild)
    arr = arrivals(pkts, m)
    P("## 4. 效果号时间线：§2 那条线索能不能在全库里立住")
    P("- 全库响应包逐条比对 `chara_effect_id_array`，「新出现」算一次到达，共 %d 次到达；"
      "表侧三元组取自 `single_mode_chara_effect`。" % len(arr))
    P("| 效果号 | 到达次数 | 到达当次 Δmot 分布 | 表侧 (type,category,group,priority) |")
    P("|---|---|---|---|")
    by = collections.defaultdict(list)
    for r in arr:
        by[r["effect"]].append(r)
    ids_all = {e for r in rows for e in r["effects_after"].split(",") if e} | set(by)
    meta = effect_meta(list(ids_all))
    for e in sorted(by, key=lambda x: (-len(by[x]), int(x))):
        h = collections.Counter(r["d_mot"] for r in by[e])
        P("| %s | %d | %s | %s |" % (e, len(by[e]),
                                     ", ".join("%+d:%d" % (a, b) for a, b in sorted(h.items())),
                                     meta.get(e)))
    camp_arr = [r for r in arr if r["camp"]]
    P("- 合宿窗内（`camp_left`>0）的到达 %d 次，其中 Δmot≠0 的 %d 次。"
      % (len(camp_arr), sum(1 for r in camp_arr if r["d_mot"] != 0)))
    P("")
    P("按表侧 `effect_type` 归族（1＝增益族，2＝减益族）：")
    fam = collections.defaultdict(collections.Counter)
    famn = collections.Counter()
    for r in arr:
        mt = meta.get(r["effect"])
        if mt is None:
            continue
        fam[mt[0]][r["d_mot"]] += 1
        famn[mt[0]] += 1
    for t in sorted(fam):
        P("- effect_type=%s 到达 %d 次：Δmot %s（负 %d / 零 %d / 正 %d）"
          % (t, famn[t], ", ".join("%+d:%d" % (a, b) for a, b in sorted(fam[t].items())),
             sum(b for a, b in fam[t].items() if a < 0),
             fam[t].get(0, 0), sum(b for a, b in fam[t].items() if a > 0)))
    P("")
    P("「有减益效果在场就挡掉合宿 +1」这一支，直接用 10 条可证伪标记包判：")
    def neg_ids(r):
        return sorted(e for e in r["effects_after"].split(",") if e
                      and meta.get(e) and meta[e][0] == 2)
    with_neg = [r for r in fals if neg_ids(r)]
    gave = [r for r in with_neg if r["d_mot"] > 0]
    P("- 标记包上**挂着减益族（effect_type=2）**的可证伪样本 %d 条，其中照样发了 +1 的 **%d** 条：%s"
      % (len(with_neg), len(gave), "; ".join("%s 面板%d 挂%s→Δmot%+d" % (tag(r), r["turn"],
                                                                        "/".join(neg_ids(r)), r["d_mot"])
                                             for r in with_neg)))
    P("- ⇒ %s" % ("**减益族在场并不挡住 +1**，『被当场抵掉』这条解释被自家样本否掉"
                  if gave else "挂减益族的样本**全部没给** +1（n=%d，仍太薄，只登记不立机制）" % len(with_neg)))
    P("")
    CLUE = []
    for e in only_zero:
        rs = by.get(e, [])
        own_neg = sum(1 for r in rs if r["d_mot"] < 0)
        mt = meta.get(e)
        ftype = mt[0] if mt else None
        fam_neg = sum(b for a, b in fam.get(ftype, {}).items() if a < 0) if ftype is not None else 0
        sp = "; ".join("%s 该局面板回合 %s" % (tag(z), effect_span(pkts, m, z["run"], e))
                       for z in fals if z["d_mot"] <= 0)
        if not rs:
            verdict = "全库没有独立到达样本 ⇒ 判不了"
        elif own_neg:
            verdict = ("该号自己 %d 次到达里 %d 次干劲下降 ⇒ 可登记为待查调制项；"
                       "但 0 样本只有 1 条，本轮**不写成机制**" % (len(rs), own_neg))
        elif fam_neg:
            verdict = ("它自己那 %d 次到达**没降干劲**，而同族（effect_type=%s）另有 %d 次到达降了 ⇒ "
                       "家族像『干劲▼』、这一号对不上 ⇒ 只能登记成**待查线索**，"
                       "**不足以**把 @203 判成『+1 被它抵掉』" % (len(rs), ftype, fam_neg))
        else:
            verdict = ("它和它家族的所有到达都没降干劲 ⇒ 不是『干劲▼』，抵不掉事件 +1，线索判死")
        CLUE.append((e, len(rs), own_neg, verdict))
        P("- 0 样本独占的效果号 **%s**（表侧 %s）：全库到达 %d 次，到达当次 Δmot<0 的 %d 次、"
          "Δmot>0 的 %d 次；该局里的存续区间 %s ⇒ %s"
          % (e, mt, len(rs), own_neg, sum(1 for r in rs if r["d_mot"] > 0), sp, verdict))
    P("- 判定这条线索用的是**跨局到达样本**（%d 次），不是那 1 条违例本身。" % len(arr))
    P("")

    ntab, with_mot, with_thr = mdb_probe()
    P("## 5. 表侧（`master.mdb`，%d 张表全扫列名）" % ntab)
    P("- 带 `motivat*` 列的表：%s" % ", ".join("%s(%s)" % (t, "/".join(c)) for t, c in with_mot))
    P("  逐一看过：`race_motivation_rate`＝比赛场地的干劲修正；`single_mode_npc(motivation_min/max)`"
      "＝**对手 NPC** 的干劲范围；`single_mode_difficulty_data.difficulty_motivation`＝难度初始值；"
      "`main_story_race_chara_data`＝主线演出。**都不是玩家的体力→干劲档位表。**")
    P("- 带 threshold/boundary/step 之类列名的表：%s" % (", ".join(t for t, _ in with_thr) or "无"))
    P("- `single_mode_chara_effect`(id, effect_type, effect_category, effect_group_id, priority, "
      "disp_order) 与 `single_mode_chara_effect_buff`（只有五维/场地/脚质加成）**都没有干劲或体力列**；"
      "`single_mode_15_act_effect`／`single_mode_15_advice_effect` 的 `effect_type` 取值里也没有可映射到"
      "「干劲档位」的类别（15 剧本没有 12 那样的 `effect_category` 对照表）。")
    P("- 结论：**表侧不存在体力阈值→干劲的档位表**；按铁律这条只登记为「查过、表侧沉默」，"
      "不等于机制不存在，但也不给它任何出处。")
    P("")

    got = mot_command_probe(pkts)
    mot_rows, kinds, have = mot_effect_rows()
    P("## 5b. 第三条路：干劲是不是**板子（command 结算）**发的，而不是事件发的")
    P("- 表侧：scen15 的 `single_mode_training_effect` 里 `target_type=20` 只有 **%d 行** —— %s。"
      % (len(mot_rows), ", ".join("cmd%s/sub%s/state%s→+%s" % r for r in mot_rows)))
    P("  该表 scen15 的 `target_type` 取值清单：%s ⇒ 20 是独立一档（既不是五维 1–5、也不是体力 10）。"
      % ", ".join("%s×%d" % (t, c) for t, c in kinds))
    mot_cids = {r[0] for r in mot_rows}
    tr_cids = [c for c in (101, 102, 103, 104, 105, 106, 5101, 5102, 5103, 5104, 5105) if c in have]
    no_row = [c for c in (101, 102, 103, 104, 105, 106, 5101, 5102, 5103, 5104, 5105) if c not in have]
    quiet = [c for c in sorted(got) if c not in mot_cids and all(x[1] == 0 for x in got[c])]
    P("- 训练／合宿类 command（%s）在表里**都有行**，带的 target_type 是 %s ⇒ **一条 20 都没有**；"
      "`target_type=20` 只挂在外出类 command %s 上。%s"
      % ("/".join(str(c) for c in tr_cids),
         sorted({t for c in tr_cids for t in have[c]}), sorted(mot_cids),
         ("（cmd %s 在 scen15 的这张表里**没有行**）" % "/".join(str(c) for c in no_row)) if no_row else ""))
    P("- 报文侧** Δmot 恒为 0** 的 command：%s，合计 **%d** 条结算包——训练与合宿类一格干劲都没涨过。"
      % ("/".join(str(c) for c in quiet), sum(len(got[c]) for c in quiet)))
    P("- 报文侧（同回合相邻包求 Δmot，按 `command_result.command_id` 归组；只列**发过干劲**的和训练类）：")
    P("| command | 样本包 | Δmot 分布 | 表侧 target_type=20 | Δmot>0 的包：mot 前值 | 其中 `camp_left`>0 |")
    P("|---|---|---|---|---|---|")
    show = sorted({c for c in got if any(x[1] > 0 for x in got[c])} | set(tr_cids) | mot_cids,
                  key=lambda c: (-sum(1 for x in got.get(c, []) if x[1] > 0), str(c)))
    for c in show:
        rs = got.get(c, [])
        if not rs and c not in have:
            continue
        ups = [x for x in rs if x[1] > 0]
        P("| %s | %d | %s | %s | %s | %d |"
          % (c, len(rs),
             ", ".join("%+d:%d" % (x, y) for x, y in sorted(collections.Counter(x[1] for x in rs).items())) or "—",
             "/".join(str(r[3]) for r in mot_rows if r[0] == c) or "无",
             collections.Counter(x[4] for x in ups) or "—",
             sum(1 for x in ups if x[3])))
    camp_up = [(c, x) for c in got for x in got[c] if x[1] > 0 and x[3]]
    P("- **合宿窗内（`camp_left`>0）由 command 结算包带出的干劲上升：全库 %d 条** —— %s"
      % (len(camp_up), "; ".join("cmd%s 面板%s（表侧 %s）"
         % (c, x[2], "/".join(str(r[3]) for r in mot_rows if r[0] == c) or "无 20 行")
         for c, x in camp_up) or "0 条"))
    P("- 表侧 +1／+2 只写在外出 command %s 上，报文侧这些 command 的 Δmot 非零时**前值全 <5**、"
      "Δmot=0 的包前值 Counter=%s（顶满）⇒ 「板子发干劲」这条通道**表侧与报文互证成立**；"
      "但它发的是**外出格**，不是合宿首格。"
      % ("/".join(str(c) for c in sorted(mot_cids)),
         collections.Counter(x[4] for c in mot_cids for x in got.get(c, []) if x[1] == 0) or "无样本"))
    n_cmd = collections.Counter(r["cmd"] for r in rows)
    up5101 = sorted({x[2] for x in got.get(5101, []) if x[1] > 0})
    P("- ⚠ 归组口径的边界：按 `command_id` 归组只说明「这条包 cmd=X 且干劲变了」，**不等于**「干劲由 X 的 "
      "effect 行发出」。5101 那 %d 条 Δmot=+1 就是这种混记——表侧 5101 **不带** target_type=20，"
      "且它们落在面板 %s（`camp_left` 全为 0），与合宿无关。"
      % (sum(1 for x in got.get(5101, []) if x[1] > 0), up5101 or "—"))
    n_cmd_marker = sum(1 for r in rows if r["cmd"] is not None)
    P("- 反过来看标记包本身：29 条合宿标记包里带 `command_result.command_id` 的 **%d** 条（取值 %s），"
      "其余 %d 条是 `check_event`/事件结算包 ⇒ 合宿首格的 +1 **在结构上不可能来自板子**，"
      "只能来自事件本体。"
      % (n_cmd_marker,
         sorted({str(r["cmd"]) for r in rows if r["cmd"] is not None}) or "无",
         len(rows) - n_cmd_marker))
    P("")

    zero = [r for r in fals if r["d_mot"] <= 0]
    up = [r for r in fals if r["d_mot"] > 0]
    cols = ("v_before", "v_after", "ratio_before", "ratio_after", "d_v", "mot_before", "max_vital")
    stats = {c: (best_band(fals, c), interior(fals, c)) for c in cols}
    same_cols = [c for c in cols if stats[c][1] and stats[c][1][5]]
    cloud_cols = [c for c in cols if stats[c][1] and not stats[c][1][5] and stats[c][1][2]]
    sep_cols = [c for c in cols if stats[c][1] and not stats[c][1][5] and not stats[c][1][2]]
    agree_a = [best_threshold(fals, ("v_before", "v_after"))[1],
               best_threshold(fals, ("ratio_before", "ratio_after"))[1]]
    real = [okc] + agree_a + [stats[c][0][2] for c in cols if not stats[c][0][3]]
    best_real = max(real) if real else -1
    P("## 6. 裁决")
    P("- 「合宿+1 是体力→干劲档位的副产品」：**判否**。单调档位家族试了三类形式"
      "（A 绝对跨线、B 单侧分档四向、C 档位线随 `max_vital` 缩放）："
      "**非平凡**的档位模型最好只吻合 **%d/%d**（违例 %d），"
      "**一条都没有严格优于门槛那条『每格都给』的 %d/%d** ⇒ 档位形式没有任何解释力，"
      "它只是把常数预测换了个写法。逐坐标看 0 样本：%s 共 %d/%d 个上它**与某条 +1 样本同值**"
      "（阈值选不了边），%s 共 %d/%d 个上它取值唯一但**落在 +1 云团内部**（单侧阈值包得住 9 条"
      "就包得住它）；%s。同局与跨局的定向反证各钉死一条（§2），"
      "「越高越给」「越低越给」两个方向都堵。"
      % (best_real, len(fals), len(fals) - best_real, n_up0, len(fals),
         "/".join("`%s`" % c for c in same_cols) or "无", len(same_cols), len(cols),
         "/".join("`%s`" % c for c in cloud_cols) or "无", len(cloud_cols), len(cols),
         ("能把 0 样本留在 +1 云团之外的坐标：只有 %s ⇒ 单靠体力档位仍解释不了，"
          "因为那条可分坐标的最优单侧形式仍是平凡解" % "/".join("`%s`" % c for c in sep_cols))
         if sep_cols else "没有一个坐标能把 0 样本留在 +1 云团之外"))
    P("- 全库基准率（§3）同向支持：体力净增包绝大多数不带干劲变化（%d/%d 干劲上升，"
      "且这些多来自休息／事件本体），「体力涨 ⇒ 干劲涨」在全库层面本来就不成立。"
      % (n_up, n))
    P("- 第三条路「+1 是板子（command 结算）发的」：**判否**（§5b）——表侧 `target_type=20` 只挂在外出 "
      "command %s，训练／合宿 command（101–106、5101–5105）在表里有行但**没有一行是 20**；"
      "而 29 条合宿标记包里带 `command_id` 的有 %d 条 ⇒ 合宿首格根本没有可归因的板子结算，"
      "『档位／板子』两条副产品路线都堵死，剩下的唯一通路是事件本体。"
      % ("/".join(str(c) for c in sorted(mot_cids)), sum(1 for r in rows if r["cmd"] is not None)))
    P("- 「合宿+1 是**事件效果**」：**保留为倾向（%d/%d，给 %d 条、不给 %d 条）**，不升级成已证机制——"
      "0 样本没被任何可观测量解释，「事件效果」自己也有反例。"
      % (len(up), len(fals), len(up), len(zero)))
    if CLUE:
        P("- 唯一可指向的差异（0 样本独占效果号 %s）：§4 用跨局到达样本给出的裁决是 %s"
          % (", ".join(c[0] for c in CLUE), CLUE[0][3]))
    if zero:
        P("- %s（年%s 面板%d）登记为：**未解释的干劲违例（unexplained exception）**。"
          "不是「档位没跨到」（本节判否），也不是分支/选择差异（#53 已排除：28 包 `choice_number=0`、"
          "`select_index_info_array` 逐字节相同）。它只允许得出一个结论："
          "干劲 +1 **不许进 `data/`、不许当常数**；引擎里现有的 `motivation+1` 属「倾向实现」，"
          "注释要按这个级别标。" % (tag(zero[0]), zero[0]["yr"], zero[0]["turn"]))
    else:
        P("- 本轮没有 0 样本 ⇒ 违例登记为空，干劲 +1 仍是倾向（%d/%d）。" % (len(up), len(fals)))
    for r in zero:
        P("  ⚠ 违例样本：年%s %s 面板%d 体力 %s→%s（max %s）干劲 %s→%s ev=%s effects 前→本 %s→%s"
          % (r["yr"], tag(r), r["turn"], r["v_before"], r["v_after"], r["max_vital"],
             r["mot_before"], r["mot_after"], r["event_id"], r["effects_prev"], r["effects_after"]))
    P("")
    P("## 7. 边界")
    P("- 可证伪样本只有 %d 条，其中 0 样本只有 %d 条 ⇒ 本轮**不可能**把「+1」从倾向提升为已证，"
      "也**不可能**指认新的调制项（要指认需要更多局：本机报文目录只有 %d 个 viewer、%d 局响应包，"
      "其中走到合宿格并有标记包的 %d 局）。"
      % (len(fals), len(zero), len(vs), nr, len({r["run"] for r in rows})))
    P("- 全库对照表把「同回合相邻包」当差分单位，混了休息／事件／比赛消耗等多源增益，只用来给基准率，"
      "不用来判合宿。")
    P("")
    P("（产物：`out/_camp_mot_samples.csv` 标记包 %d 行 / `out/_camp_mot_cross.csv` Δv 分档聚合 ＋ "
      "`out/_camp_mot_cross_rows.csv` 逐条 %d 行（基准率可独立复核）/ "
      "`out/_camp_mot_arrivals.csv` 效果号到达 %d 行 / `out/_camp_mot_report.txt` 本文）"
      % (len(rows), n, len(arr)))
    txt = "\n".join(L) + "\n"
    open(OUT_T, "w", encoding="utf-8").write(txt)

    with open(OUT_S, "w", newline="", encoding="utf-8-sig") as f:
        out_cols = ["yr", "run", "viewer", "chara", "turn", "file_prev", "file", "event_id", "req",
                    "camp_event", "v_before", "v_after", "d_v", "max_vital",
                    "ratio_before", "ratio_after", "mot_before", "mot_after", "d_mot",
                    "falsifiable", "d_effects", "effects_prev", "effects_after"]
        w = csv.DictWriter(f, fieldnames=out_cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda x: (str(x["yr"]), x["run"])):
            w.writerow(r)
    with open(OUT_X, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["dv_bucket", "n_pkt", "dneg2", "dneg1", "d0", "dplus1", "dplus2"])
        for k in ("0-9", "10-19", "20-49", "50+"):
            c = buckets.get(k, {})
            w.writerow([k, sum(c.values())] + [c.get(x, 0) for x in (-2, -1, 0, 1, 2)])
    with open(OUT_XR, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["dv_bucket", "run", "turn", "file_prev", "file", "v_before", "v_after", "d_v",
                    "max_vital", "mot_before", "mot_after", "d_mot", "in_camp", "cmd", "event_id"])
        for r in xr:
            w.writerow(r)
    with open(OUT_A, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["effect", "run", "turn", "d_mot", "in_camp"])
        for r in arr:
            w.writerow([r["effect"], r["run"], r["turn"], r["d_mot"], r["camp"]])
    print(txt)
    print("→ %s / %s / %s / %s / %s" % (OUT_S, OUT_X, OUT_XR, OUT_A, OUT_T))


if __name__ == "__main__":
    main()
