#!/usr/bin/env python3
"""继承（种马）当输入：把 t31/t55「心意的继承」抬升量对**继承配置**做剂量核对。

一条命令复现 `docs/继承剂量_种马输入_20261104.md` 里所有数字：

    python tools/inherit_gain_dosage.py                # 扫报文 + 出报告 + 落 CSV/JSON（约 4 分钟）
    python tools/inherit_gain_dosage.py --from-cache   # 复用缓存只重出报告（秒级）

背景：新靶 = 稳定 ≥ LG15（85,600），种马被用户判为「占大头」。#37
（`docs/继承事件一手_20261103.md`）否掉接线是因为「驱动量 = 继承双亲，而双亲属性不在报文里」。
本文换路：**把种马当输入**，先查输入能不能定位，再量剂量关系。

三类一手证据，不混用：
  I 开局配置   = `single_mode_start_request_common.start_chara`（本局**使用**的双亲：自有 id + 租借 viewer/id）
  S 终局快照   = 带 `rank_score` 的练成马娘记录（内含 `succession_chara_array` = 双亲的卡/稀有度/因子清单，
                 和 `factor_info_array` = **本局产出**的因子，两者方向相反，判错整条作废）
  A 逐回合     = `data.chara_info`（`succession_trained_chara_id_1/2`、五维、`max_*`）

不产出任何拟合常数；斜率只报形状，不写进配置。
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import math
import os
import statistics as st
import sys
import time

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402

RAW = PS.RAW
AXES = ("speed", "stamina", "power", "guts", "wiz")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = "out/_inherit_pkt_cache_20261104.json"
OUT_CSV = "out/_inherit_dosage_20261104.csv"
OUT_TXT = "out/_inherit_dosage_20261104.txt"
OUT_JSON = "out/_inherit_dosage_summary_20261104.json"
EVENT_TURNS = (31, 55)          # 报文/面板口径；引擎 turn = 面板 − 1
LF_LINE, LG_LINE, LG15_LINE = 91400, 76000, 85600


# ---------------------------------------------------------------- 工具

def norm(o):
    """msgpack 解出来的 dict 可能有非字符串键；这里转成可 JSON 化的紧凑结构。"""
    if isinstance(o, dict):
        return {str(k): norm(v) for k, v in o.items()}
    if isinstance(o, list):
        return [norm(v) for v in o]
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "replace")
    return o


def find_paths(node, subs, path="", out=None, depth=0):
    """返回所有「键名含 subs 之一」的 (path, value)，深度上限 9。"""
    if out is None:
        out = []
    if depth > 9:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            p = path + "." + str(k)
            if isinstance(k, str) and any(s in k.lower() for s in subs):
                out.append((p, v))
            find_paths(v, subs, p, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            find_paths(v, subs, path + "[]", out, depth + 1)
    return out


def nonempty(v):
    if v is None:
        return False
    if isinstance(v, (list, dict, str)):
        return len(v) > 0
    return True


def fac_list(arr):
    """因子数组 → [(factor_id, level)]，顺序保留。"""
    out = []
    for f in arr or []:
        if isinstance(f, dict):
            out.append([f.get("factor_id"), f.get("level")])
    return out


def snap_brief(tc):
    """练成马娘记录里「本局继承配置 + 产出因子」的一手摘录。"""
    five = [tc.get(a) for a in AXES]
    par = []
    for p in (tc.get("succession_chara_array") or []):
        if not isinstance(p, dict):
            continue
        par.append({"position_id": p.get("position_id"), "card_id": p.get("card_id"),
                    "owner_viewer_id": p.get("owner_viewer_id"), "rank": p.get("rank"),
                    "rarity": p.get("rarity"), "talent_level": p.get("talent_level"),
                    "five_sum": (sum(int(x or 0) for x in
                                      [p.get(a) for a in AXES])
                                 if any(p.get(a) is not None for a in AXES) else None),
                    "fac": fac_list(p.get("factor_info_array")),
                    "keys": sorted(str(k) for k in p)})
    return {"file": None, "jpath": None, "viewer": tc.get("viewer_id"),
            "owner": tc.get("owner_viewer_id"), "tc_id": tc.get("trained_chara_id"),
            "chara": tc.get("single_mode_chara_id"), "card_id": tc.get("card_id"),
            "scen": tc.get("scenario_id"), "route": tc.get("route_id"),
            "rank_score": tc.get("rank_score"), "rank": tc.get("rank"),
            "five": five, "five_sum": sum(int(v or 0) for v in five),
            "create_time": tc.get("create_time"), "chara_seed": tc.get("chara_seed"),
            "fans": tc.get("fans"), "succ_num": tc.get("succession_num"),
            "succ_1": tc.get("succession_trained_chara_id_1"),
            "succ_2": tc.get("succession_trained_chara_id_2"),
            "fac_out": fac_list(tc.get("factor_info_array")),
            "fac_ext": fac_list(tc.get("factor_extend_array")),
            "par": par, "n_keys": len(tc), "keys": sorted(str(k) for k in tc)}


# ---------------------------------------------------------------- 扫描

def scan(limit=0):
    paths = sorted(glob.glob(os.path.join(RAW, "*.bin")), key=PS.sort_key)
    if limit:
        paths = paths[:limit]
    path_counter = collections.Counter()      # 键路径 -> 出现次数（含空值）
    path_nonempty = collections.Counter()     # 键路径 -> 非空次数
    starts = []                               # 开局配置（I 类）
    pending = None                            # 待绑定到 run 的开局请求
    runs = {}                                 # run key -> 逐回合信息（A 类，只留需要的列）
    examples = collections.defaultdict(list)  # 数据块路径 -> 最多 2 个一手样例
    snaps = {}                                # (viewer,tc_id,rank_score) -> 终局快照（S 类）
    stats = collections.Counter()

    for path in paths:
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            stats["decode_fail"] += 1
            continue
        if not isinstance(d, dict):
            continue
        stats["files"] += 1
        viewer = d.get("viewer_id")
        if viewer is None and isinstance(d.get("data_headers"), dict):
            viewer = d["data_headers"].get("viewer_id")

        if name.endswith("_notify_request.bin"):
            b = d.get("single_mode_start_request_common")
            if isinstance(b, dict):
                sc = b.get("start_chara") or {}
                rec = {"file": name, "viewer": viewer,
                       "card_id": sc.get("card_id"), "scen": sc.get("scenario_id"),
                       "succ_1": sc.get("succession_trained_chara_id_1"),
                       "succ_2": sc.get("succession_trained_chara_id_2"),
                       "rental": norm(sc.get("rental_succession_trained_chara")),
                       "friend_sup": norm(sc.get("friend_support_card_info")),
                       "sup_ids": norm(sc.get("support_card_ids")),
                       "succ_rank_point": b.get("current_succession_rank_point"),
                       "run": None}
                starts.append(rec)
                pending = rec            # 下一条带 chara_info 的响应即这局
            for k in d:
                if isinstance(k, str) and k.endswith("_request_common"):
                    for p, v in find_paths(d[k], ("succession", "factor")):
                        fp = "REQ." + k + p
                        path_counter[fp] += 1
                        if nonempty(v):
                            path_nonempty[fp] += 1
            continue

        data = d.get("data")
        if not isinstance(data, dict):
            continue
        nfac = sum(len(a.get("factor_info_array") or [])
                   for a in data.get("event_effected_factor_array") or [] if isinstance(a, dict))
        ci = PS.get_ci(data)
        run = None
        turn = None
        if isinstance(ci, dict) and ci.get("turn") is not None:
            turn = ci.get("turn")
            run = "%s@%s@%s" % (viewer, ci.get("single_mode_chara_id"), ci.get("start_time"))
            r = runs.setdefault(run, {"run": run, "viewer": viewer,
                                      "chara": ci.get("single_mode_chara_id"),
                                      "start_time": ci.get("start_time"),
                                      "scen": ci.get("scenario_id"),
                                      "card_id": ci.get("card_id"),
                                      "succ_1": ci.get("succession_trained_chara_id_1"),
                                      "succ_2": ci.get("succession_trained_chara_id_2"),
                                      "pkts": [], "files": [name, name], "n_snap": 0})
            r["pkts"].append({"turn": turn, "file": name,
                              "five": [int(ci.get(a) or 0) for a in AXES],
                              "max": [int(ci.get("max_" + a) or 0) for a in AXES],
                              "dmax": [int(ci.get("default_max_" + a) or 0) for a in AXES],
                              "fans": ci.get("fans"), "sp": ci.get("skill_point"),
                              "nfac": nfac,
                              "eff_fac": [norm(a) for a in data.get("event_effected_factor_array")
                                          or [] if isinstance(a, dict)]})
            r["files"][1] = name
            r["n_snap"] += 1
            if pending is not None and pending["run"] is None:
                pending["run"] = run
                pending["run_chara_info_succ"] = [r["succ_1"], r["succ_2"]]
                pending["run_card_id"] = r["card_id"]

        for p, v in find_paths(data, ("succession", "factor")):
            if p.startswith(".chara_info.succession_trained_chara_id"):
                continue
            key = ("CI." + p[len(".chara_info"):]) if p.startswith(".chara_info") else p
            path_counter[key] += 1
            if nonempty(v):
                path_nonempty[key] += 1
                if not p.startswith(".chara_info") and len(examples[key]) < 2:
                    examples[key].append({"file": name, "run": run, "turn": turn, "path": p,
                                          "value": trim_block(p, v)})

        for tc, jp in snap_paths(data):
            rec = snap_brief(tc)
            k = (rec["viewer"], rec["tc_id"], rec["rank_score"])
            u = snaps.setdefault(k, dict(rec, n_seen=0, files=[]))
            u["n_seen"] += 1
            if len(u["files"]) < 3:
                u["files"].append(name)
            if u.get("jpath") is None:
                u["jpath"] = jp
        stats["resp"] += 1
    return {"stats": dict(stats), "path_counter": dict(path_counter),
            "path_nonempty": dict(path_nonempty), "starts": starts,
            "runs": runs, "block_examples": {k: v for k, v in examples.items()},
            "snaps": list(snaps.values()),
            "meta": {"raw": RAW, "limit": limit, "event_turns": list(EVENT_TURNS)}}


def trim_block(p, v):
    """数据块只留「能不能当输入用」所需的最小结构，避免缓存爆炸。"""
    if isinstance(v, list):
        out = []
        for x in v[:8]:
            if isinstance(x, dict):
                if "factor_info_array" in x or "card_id" in x and "rank" in x:
                    out.append(snap_brief(x))
                else:
                    out.append(norm(x))
            else:
                out.append(norm(x))
        out.append("_len=%d" % len(v))
        return out
    if isinstance(v, dict):
        return {str(k): (trim_block(p + "." + str(k), x) if isinstance(x, (list, dict)) else norm(x))
                for k, x in v.items()}
    return norm(v)


def snap_paths(node, path="", depth=0, out=None):
    """带 rank_score + speed + skill_array 的练成马娘记录（同 lf_packet_scan 判据）。"""
    if out is None:
        out = []
    if depth > 7:
        return out
    if isinstance(node, dict):
        if "rank_score" in node and "speed" in node and "skill_array" in node:
            out.append((node, path))
            return out
        for k, v in node.items():
            snap_paths(v, path + "." + str(k), depth + 1, out)
    elif isinstance(node, list):
        for v in node:
            snap_paths(v, path + "[]", depth + 1, out)
    return out



def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = st.fmean(xs), st.fmean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def axis_sum(v):
    return sum(int(x or 0) for x in (v or []))


def event_legs(cache):
    """两条腿的一手读数：**相邻报文**差分（cap_trace 的口径，不是跨回合差分）。

    取 t31 / t55（报文=面板口径）上 `max_*` 或 `default_max_*` 发生变化的那一跳，
    `ds` = 同一条报文里的五维差分，`dm` = 上限差分。跨回合差分会把训练增益混进来，
    这里刻意不用。
    """
    rows = []
    for run, r in sorted(cache["runs"].items()):
        if r.get("scen") != 15:
            continue
        pk = r["pkts"]
        for i in range(1, len(pk)):
            prev, cur = pk[i - 1], pk[i]
            if cur["turn"] not in EVENT_TURNS:
                continue
            dm = [b - a for a, b in zip(prev["max"], cur["max"])]
            dd = [b - a for a, b in zip(prev["dmax"], cur["dmax"])]
            if not any(dm) and not any(dd):
                continue
            ds = [b - a for a, b in zip(prev["five"], cur["five"])]
            rows.append({
                "run": run, "run_chara": r["chara"], "start_time": r["start_time"],
                "viewer": r["viewer"], "card_id": r["card_id"],
                "turn_panel": cur["turn"], "turn_eng": cur["turn"] - 1,
                "turn_prev_panel": prev["turn"], "file": cur["file"],
                "file_prev": prev["file"],
                "ds": ds, "dm": dm, "dd": dd,
                "dfans": (cur["fans"] or 0) - (prev["fans"] or 0),
                "dsp": (cur["sp"] or 0) - (prev["sp"] or 0),
                "nfac_out": cur["nfac"],          # 事件抽到的因子条数（输出侧）
                "eff_fac": cur["eff_fac"],
                "max_before": prev["max"], "five_before": prev["five"],
                "succ_1": r["succ_1"], "succ_2": r["succ_2"],
            })
    return rows


# ---------------------------------------------------------------- 判据（先写死，再跑）

# 单元 = **局**（每局两条腿之和），不是腿。理由：同一局的两腿共享同一个输入（双亲固定），
# 把两腿当独立样本会把 n 虚报一倍。
#
# 分层/对比规则（跑之前定死，跑完不改）：
#   R1 config 身份 = 两个双亲的**签名**（五维向量, rank_score, card_id, 因子 id 多重集）排序后的对。
#      同一个种马每局被复制成新的本地 id，签名能把它们并回同一个输入。
#   R2 只有 n≥2 的 config 组才允许谈「组内极差 = 噪声底」；n=1 的组只作描述，不进检验。
#   R3 主对比 = 预先指定的两组（双亲签名里「最强种马」组 vs 「最弱种马」组）：
#      精确 Mann-Whitney 双尾 p + Hodges-Lehmann 位置差 + 全部成对差的 [min, max] 当区间。
#   R4 连续剂量变量清单（写死，不许事后加）：Σ双亲因子条数 / Σ双亲 rank_score /
#      Σ双亲五维和 / 双亲中最高 rank_score / 双亲中最高五维和 / 双亲因子按 factor_type 计数之和。
#      每个变量做 Spearman ρ + 精确置换双尾 p（置换的是局的标签）。
#   R5 n ≤ 10 ⇒ 斜率/比例一律只报**形状**，不折算成常数，不写进 `data/`、不加配置开关。
#   R6 输出侧变量（事件抽到的因子条数 `nfac_out`）与输入侧分开列，不许拿输出回归输出。
#
# 跑完之后**只往严格方向**补的两条（不改上面任何一条）：
#   R7 伪重复披露（2026-11-04 见到分组后加）：同 config 的局输入完全相同 ⇒ 局级 n 不是独立输入数，
#      必须同时报 config 级（n=独立输入数）并给出该设计下精确置换 p 的下限 2/n!。
#   R4b 将双亲拆成「租借种马侧 / 自有侧」两组变量：发现 R4 的 Σ/最高 类变量被**可读性**污染
#      （4 个 config 里只有 1 个的自有双亲属性可读），所以拆侧。这些是**事后探索性**变量，
#      只报形状，不作为任何确认性结论的依据。
# R3 的操作化：组排序键 = 组内 `par_rank_max` 的上界，取 n≥2 的组里最高 vs 最低。
DOSE_VARS = ("par_n_fac", "par_rank_sum", "par_five_sum", "par_rank_max", "par_five_max")
MASTER = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
CONST = os.path.join(REPO, "data", "constants.json")


def factor_types():
    """`succession_factor.factor_id -> factor_type`（表侧类别，不碰 value 语义）。"""
    import sqlite3
    db = sqlite3.connect("file:%s?mode=ro" % MASTER.replace("\\", "/"), uri=True)
    m = dict(db.execute("select factor_id, factor_type from succession_factor"))
    db.close()
    return m


def score_table():
    return json.load(open(CONST, encoding="utf-8"))["five_status_final_score"]


def revise(v):
    return v if v <= 1200 else 2 * v - 1200


def stat_score(tbl, five):
    last = len(tbl) - 1
    return sum(tbl[min(int(revise(v or 0)), last)] for v in five)


# ---------------------------------------------------------------- 统计（无拟合）

def mannwhitney(a, b):
    """精确双尾 Mann-Whitney U + Hodges-Lehmann 位置差 + 成对差极差。"""
    from itertools import combinations
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    obs = sum(1 for x in a for y in b if x > y) + 0.5 * sum(1 for x in a for y in b if x == y)
    vals = list(a) + list(b)
    n = na + nb
    null = collections.Counter()
    for idx in combinations(range(n), na):
        s = sum(1 for i in idx for j in range(n) if j not in idx and vals[i] > vals[j])
        s += 0.5 * sum(1 for i in idx for j in range(n) if j not in idx and vals[i] == vals[j])
        null[round(s, 1)] += 1
    tot = sum(null.values())
    mu = na * nb / 2.0
    p = sum(v for k, v in null.items() if abs(k - mu) >= abs(obs - mu) - 1e-9) / tot
    pairs = sorted(x - y for x in a for y in b)
    return {"n": (na, nb), "U": round(obs, 1), "p_exact": round(p, 4),
            "p_floor": round(2.0 / tot, 4) if len(set(vals)) > 1 else 1.0,
            "hl": round(st.median(pairs), 1), "pair_range": [pairs[0], pairs[-1]]}


def spearman_perm(xs, ys):
    """Spearman ρ + 精确（n≤8）/ 抽样（n>8）置换双尾 p。"""
    from itertools import permutations
    n = len(xs)
    if n < 4:
        return None

    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[o[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    obs = pearson(rx, ry)
    if obs is None:
        return None
    cnt = 0
    trials = 0
    if n <= 8:
        pool = list(permutations(range(n)))
    else:
        import random
        rnd = random.Random(20261104)
        pool = [tuple(rnd.sample(range(n), n)) for _ in range(20000)]
    for p in pool:
        r = pearson(rx, [ry[p[i]] for i in range(n)])
        if r is None:
            continue
        trials += 1
        if abs(r) >= abs(obs) - 1e-12:
            cnt += 1
    return {"n": n, "rho": round(obs, 3), "p_perm": round(cnt / trials, 4) if trials else None,
            "trials": trials}


# ---------------------------------------------------------------- 双亲解析

OWN_VIEWER = 238107205      # 报文库里唯一的自有账号 viewer（一手：所有 run 的 viewer 只有这一个）


def fac_set(rec):
    """因子 id **集合**（同一匹马每局被复制成新的本地 id，id 不能进身份键）。"""
    return tuple(sorted({f[0] for f in (rec["fac_out"] or [])}))


def pattrs(rec):
    return (rec["card_id"], tuple(int(x or 0) for x in (rec["five"] or [])), rec["rank_score"])


def par_sig(s):
    """R1 签名 = 五维 + rank_score + card_id + 因子 id 集合（不含本地 id）。"""
    return "S%s|%s|%s|%s" % (tuple(s["five"]), s["rank_score"], s["card_id"], s["fac_set"])


def run_parents(cache, legs, ft):
    """每局一手：本局使用的双亲 → 属性 → 剂量变量。

    归属判据（与 #51 独立核，两条正交路）：
      主键 = 「产出马娘记录的 `succession_trained_chara_id_1/2` == 该局每个回合
              `chara_info` 里那对 id」。这对 id 每局唯一（租借马娘每局被复制成新的
              本地 id），所以 1:1；候选数 ≠ 1 就是串局风险的直接来源，逐局报出。
      副键 = 产出记录里 `succession_chara_array` 的 position 10 / 20（两个双亲槽）
              按 (card_id, 因子集合) 与本地记录对齐，`owner_viewer_id ≠ 0` ⇒ 租借。
      属性 = 本地副本记录（自己账号的 add_trained_chara / trained_chara / 账号列表）；
              本地缺失时按 (card_id, 因子集合) 在全库找同一匹马的其它副本（含他人
              ranking / succession_deck 记录），要求所有候选属性一致才采信。
    """
    allsnap = cache["snaps"]
    by_local = {}
    for s in allsnap:
        if s["viewer"] == OWN_VIEWER:
            by_local.setdefault(s["tc_id"], s)
    twin = collections.defaultdict(list)     # (card, 因子集合) -> 所有记录
    for s in allsnap:
        twin[(s["card_id"], fac_set(s))].append(s)

    out = []
    for run in sorted({x["run"] for x in legs}):
        r = cache["runs"][run]
        lg = sorted([x for x in legs if x["run"] == run], key=lambda x: x["turn_panel"])
        pair = (r["succ_1"], r["succ_2"])
        prod = [s for s in allsnap if s["viewer"] == OWN_VIEWER
                and (s["succ_1"], s["succ_2"]) == pair]
        five_end = r["pkts"][-1]["five"]
        prod_five = [s for s in prod if s["five"] == five_end]
        pr = prod_five[0] if prod_five else None
        slots = [p for p in ((pr or {}).get("par") or []) if p.get("position_id") in (10, 20)]
        starts = [s for s in cache["starts"] if s.get("run") == run]
        rel = starts[0].get("rental") if starts else None
        rel_ref = (rel or {}).get("trained_chara_id"), (rel or {}).get("viewer_id")

        pars, used = [], set()
        # 第一遍：本地记录能直接命中的槽位
        for tc in pair:
            rec = by_local.get(tc)
            if rec is None:
                pars.append({"tc_id": tc, "unresolved": True})
                continue
            cands = twin.get((rec["card_id"], fac_set(rec)), [])
            slot = next((p for p in slots
                         if (p["card_id"], tuple(sorted({f[0] for f in (p["fac"] or [])})))
                         == (rec["card_id"], fac_set(rec)) and id(p) not in used), None)
            if slot is not None:
                used.add(id(slot))
            owner = slot.get("owner_viewer_id") if slot else None
            rented = (owner not in (None, 0)) if slot else any(
                c["viewer"] != OWN_VIEWER and pattrs(c) == pattrs(rec) for c in cands)
            pars.append(mk_par(tc, rec, "本地副本", rented, rel_ref, ft,
                               len({pattrs(c) for c in cands}), slot))
        # 第二遍：本地缺失的槽位，用「剩下的那个 position 10/20 槽」定位，再按身份键取属性
        for i, p in enumerate(pars):
            if not p.get("unresolved"):
                continue
            free = next((s for s in slots
                         if (s["card_id"], tuple(sorted({f[0] for f in (s["fac"] or [])})))
                         not in {(x["card_id"], x["fac_set"]) for x in pars if not x.get("unresolved")}
                         and id(s) not in used), None)
            if free is None:
                continue
            used.add(id(free))
            cands = twin.get((free["card_id"], tuple(sorted({f[0] for f in (free["fac"] or [])}))), [])
            if not cands:
                continue
            agree = {pattrs(c) for c in cands}
            if len(agree) != 1:
                continue                      # 属性不一致 ⇒ 不采信（宁缺毋滥）
            pars[i] = mk_par(p["tc_id"], cands[0], "同身份键的另一副本（%s）"
                             % ("他人记录" if any(c["viewer"] != OWN_VIEWER for c in cands) else "本账号其它局"),
                             free.get("owner_viewer_id") not in (None, 0), rel_ref, ft,
                             len(cands), free)
        pars = [p if not p.get("unresolved") else None for p in pars]
        conf = "×".join(sorted(p["sig"] for p in pars if p)) if all(pars) else None
        dsum = {v: None for v in DOSE_VARS}
        if all(pars):
            dsum["par_n_fac"] = sum(p["n_fac"] for p in pars)
            dsum["par_rank_sum"] = sum(int(p["rank_score"] or 0) for p in pars)
            dsum["par_five_sum"] = sum(int(p["five_sum"] or 0) for p in pars)
            dsum["par_rank_max"] = max(int(p["rank_score"] or 0) for p in pars)
            dsum["par_five_max"] = max(int(p["five_sum"] or 0) for p in pars)
        stud = next((p for p in pars if p and p["role"] == "租借"), None)
        ownp = next((p for p in pars if p and p["role"] == "自有"), None)
        for k, src in (("stud", stud), ("own", ownp)):
            for f in ("rank", "five", "fac"):
                dsum["%s_%s" % (k, f)] = (src[("rank_score" if f == "rank" else
                                               ("five_sum" if f == "five" else "n_fac"))]
                                          if src else None)
        dsum["own_attr_ok"] = 1 if (ownp and not ownp["attr_zeroed"]) else 0
        ftot = collections.Counter()
        for p in pars:
            if p:
                for kk, v in p["ft"].items():
                    ftot[kk] += v
        row = {"run": run, "chara": r["chara"], "card_id": r["card_id"],
               "start_succ_pair": list(pair), "n_prod_cand": len(prod),
               "n_prod_cand_five_ok": len(prod_five),
               "prod_tc": pr["tc_id"] if pr else None,
               "prod_rank_score": pr["rank_score"] if pr else None,
               "chara_seed": pr.get("chara_seed") if pr else None,
               "gp_slots": len(pr["par"]) if pr else 0,
               "gp_cards": "|".join(str(p.get("card_id")) for p in (pr["par"] if pr else [])),
               "gp_pos": "|".join(str(p.get("position_id")) for p in (pr["par"] if pr else [])),
               "gp_nfac": "|".join(str(len(p.get("fac") or [])) for p in (pr["par"] if pr else [])),
               "gp_owner": "|".join(str(p.get("owner_viewer_id")) for p in (pr["par"] if pr else [])),
               "gp_rarity": "|".join(str(p.get("rarity")) for p in (pr["par"] if pr else [])),
               "gp_talent": "|".join(str(p.get("talent_level")) for p in (pr["par"] if pr else [])),
               "rental_remote": ("%s@%s" % rel_ref) if rel else None,
               "start_succ": [starts[0]["succ_1"], starts[0]["succ_2"]] if starts else None,
               "succ_rank_point": starts[0]["succ_rank_point"] if starts else None,
               "parents": pars, "config": conf, **dsum,
               "par_fac_type": json.dumps(dict(ftot), ensure_ascii=False),
               "five_sum_end": axis_sum(five_end), "max_sum_end": axis_sum(r["pkts"][-1]["max"]),
               "t_last": r["pkts"][-1]["turn"], "n_pkt": len(r["pkts"]),
               "ds1": axis_sum(lg[0]["ds"]) if lg else None,
               "dm1": axis_sum(lg[0]["dm"]) if lg else None,
               "ds2": axis_sum(lg[1]["ds"]) if len(lg) > 1 else None,
               "dm2": axis_sum(lg[1]["dm"]) if len(lg) > 1 else None,
               "ds_run": sum(axis_sum(x["ds"]) for x in lg),
               "dm_run": sum(axis_sum(x["dm"]) for x in lg),
               "nfac_draw1": lg[0]["nfac_out"] if lg else None,
               "nfac_draw2": lg[1]["nfac_out"] if len(lg) > 1 else None,
               "ds1_axis": "|".join(str(v) for v in lg[0]["ds"]) if lg else None,
               "dm1_axis": "|".join(str(v) for v in lg[0]["dm"]) if lg else None,
               "ds2_axis": "|".join(str(v) for v in lg[1]["ds"]) if len(lg) > 1 else None,
               "dm2_axis": "|".join(str(v) for v in lg[1]["dm"]) if len(lg) > 1 else None,
               "max_before1": lg[0]["max_before"] if lg else None,
               "five_before1": lg[0]["five_before"] if lg else None,
               "n_leg": len(lg)}
        out.append(row)
    return out


def mk_par(tc, rec, src, rented, rel_ref, ft, n_twin, slot=None):
    """把一个双亲槽归一成一条一手记录。"""
    ftc = collections.Counter(ft.get(f[0], -1) for f in (rec["fac_out"] or []))
    five = [int(x or 0) for x in (rec["five"] or [])]
    return {"tc_id": tc, "card_id": rec["card_id"], "rank_score": rec["rank_score"],
            "rank_code": rec["rank"], "five": five, "five_sum": int(rec["five_sum"] or 0),
            "rarity": (slot or {}).get("rarity", rec.get("rarity")),
            "talent_level": (slot or {}).get("talent_level", rec.get("talent_level")),
            "n_fac": len(rec["fac_out"] or []), "n_fac_ext": len(rec["fac_ext"] or []),
            "fac_set": fac_set(rec),
            "ft": {str(k): v for k, v in sorted(ftc.items())},
            "sig": par_sig({"five": five, "rank_score": rec["rank_score"],
                            "card_id": rec["card_id"], "fac_set": fac_set(rec)}),
            "role": "租借" if rented else "自有", "src": rec["files"][0],
            "src_path": rec["jpath"], "route": src, "n_twin": n_twin,
            "attr_zeroed": (int(rec["five_sum"] or 0) == 0 and not rec["rank_score"]),
            "remote_ref": ("%s@%s" % rel_ref) if isinstance(rel_ref, tuple) and rel_ref[1] else None}


# ---------------------------------------------------------------- 结构核对（一手，只解本样本用过的报文）

def structural(cache, stubs):
    """三件结构事：① 逐回合继承对是否恒定且两两不同 ② 两格的事件身份 ③ 全 0 轻量条目的键清单。

    `stubs` = 需要回查的轻量条目 (trained_chara_id, card_id, 文件, 报文内路径)。
    只解本样本用过的报文（约 3.9k 个，20–30 s）；结果写回缓存，之后 `--from-cache` 免费。
    """
    pairs = {}
    ev = {}
    n_ci = n_nonnull = 0
    for rk, r in sorted(cache["runs"].items()):
        cnt = collections.Counter()
        ids = collections.defaultdict(set)
        for p in r["pkts"]:
            fp = os.path.join(RAW, p["file"])
            if not os.path.exists(fp):
                continue
            data = (ml.decode_file(fp) or {}).get("data") or {}
            ci = PS.get_ci(data)
            if not isinstance(ci, dict):
                continue
            n_ci += 1
            a = ci.get("succession_trained_chara_id_1")
            b = ci.get("succession_trained_chara_id_2")
            if a is not None and b is not None:
                n_nonnull += 1
            cnt["%s/%s" % (a, b)] += 1
            if p["turn"] in EVENT_TURNS:
                s = json.dumps(data, ensure_ascii=False, default=str)
                ids[p["turn"]] |= {t for t in ("400000040", "7040", "7041") if t in s}
        pairs[rk] = dict(cnt)
        ev[rk] = {str(k): sorted(v) for k, v in sorted(ids.items())}
    stub = {}
    for want in stubs:                                    # [(tc, card, file, 报文内路径), …]
        fp = os.path.join(RAW, want[2])
        if not os.path.exists(fp):
            continue
        d = ml.decode_file(fp) or {}
        stack, rec = [d.get("data") if isinstance(d, dict) else d], None
        while stack and rec is None:                       # 全结构里找 (trained_chara_id, card_id) 命中
            o = stack.pop()
            if isinstance(o, dict):
                if o.get("trained_chara_id") == want[0] and o.get("card_id") == want[1]:
                    rec = o
                else:
                    stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
        if rec is None:
            stub[want[0]] = {"missing": True, "file": want[2], "path": want[3]}
            continue
        sc = {k: v for k, v in rec.items() if not isinstance(v, (list, dict))}
        falsy = (0, None, "", -1)
        stub[want[0]] = {"block": want[3], "file": want[2], "n_keys": len(rec),
                         "scen": rec.get("scenario_id"),
                         "zero": sorted(k for k, v in sc.items() if v in falsy),
                         "readable": sorted(k for k, v in sc.items() if v not in falsy),
                         "n_fac": len(rec.get("factor_info_array") or []),
                         "gp": len(rec.get("succession_chara_array") or [])}
    return {"n_ci_packets": n_ci, "n_succ_nonnull": n_nonnull, "pairs_per_run": pairs,
            "event_ids": ev, "stub_records": stub}


def extra_inputs(cache):
    """每一局的继承对 + 两侧身份可解析度；用来区分「新输入」与「同一身份的又一副本」。"""
    out = []
    for rk, r in sorted(cache["runs"].items()):
        turns = {p["turn"] for p in r["pkts"]}
        ident = {}
        for tc in (r["succ_1"], r["succ_2"]):
            hits = [s for s in cache["snaps"] if s["tc_id"] == tc]
            own = [s for s in hits if s["viewer"] == OWN_VIEWER]
            pick = (own or hits or [None])[0]
            ident[tc] = None if pick is None else {
                "card": pick["card_id"], "rank": pick["rank_score"], "five": pick["five_sum"],
                "fac": len(pick["fac_out"]), "n_copy": len(hits)}
        out.append({"run": rk, "pair": [r["succ_1"], r["succ_2"]],
                    "covers_both_legs": all(t in turns for t in EVENT_TURNS),
                    "turn_range": [min(turns), max(turns)] if turns else None,
                    "ident": {str(k): v for k, v in ident.items()}})
    return out


def value_legs(tbl, legs):
    """把每条腿折成評価点（口径同 #37 §3：送分=抬前现值定价，带宽分=多出来的空间定价）。"""
    by = {}
    for x in legs:
        last = len(tbl) - 1
        gain = sum(tbl[min(revise(v + d), last)] - tbl[min(revise(v), last)]
                   for v, d in zip(x["five_before"], x["ds"]))
        band = sum(tbl[min(revise(min(m + dm, 2500)), last)] - tbl[min(revise(m), last)]
                   for m, dm in zip(x["max_before"], x["dm"]))
        by[(x["run"], x["turn_panel"])] = {"gain_score": gain, "band_score": band}
    return by


# ---------------------------------------------------------------- 报告

def rlab(run):
    """局标签：马娘 id @ 开局时间（viewer 恒为自有账号，不必占宽）。"""
    a = run.split("@")
    return "%s@%s" % (a[1], a[2])


def short(sig):
    """把 R1 签名压成一行可读身份：card / 官方分 / 五维和 / 因子条数。"""
    if not sig:
        return "-"
    parts = []
    for one in sig.split("×"):
        f = one[1:].split("|")
        five = [int(x) for x in f[0].strip("()").split(",") if x.strip()]
        nf = len([x for x in f[3].strip("()").split(",") if x.strip()]) if len(f) > 3 else 0
        parts.append("卡%s 分%s 五维和%s 因子%d" % (f[2], f[1], sum(five), nf))
    return " ＋ ".join(parts)


def report(cache, runs_rows, legs, fh, tbl):
    def w(s=""):
        print(s)
        fh.write(s + "\n")

    pc, pn = cache["path_counter"], cache["path_nonempty"]
    w("== Q1 输入侧：继承配置在报文里能读到什么（一手计数；非空 / 出现）==")
    keys = [k for k in pc if "succession" in k or "factor" in k]
    for k in sorted(keys, key=lambda x: (-pn.get(x, 0), -pc[x]))[:25]:
        w("  %-74s 非空 %-6d / 出现 %d" % (k[:74], pn.get(k, 0), pc[k]))
    w("  …全量 %d 类路径见 %s" % (len(keys), OUT_JSON))

    w("\n== Q1a 双亲到底在哪个字段（一手，逐局连接判据）==")
    w("主键 = 产出马娘记录的 succession_trained_chara_id_1/2 == 该局 chara_info 的同名字段")
    w("%-20s %-13s %-11s %-7s %-7s %-9s %s" %
      ("run", "chara_info 双亲", "开局请求", "候选数", "五维吻合", "产出 tc", "官方分"))
    for r in runs_rows:
        w("%-20s %-13s %-11s %-7d %-7d %-9s %s" % (
            rlab(r["run"]), "%s/%s" % (r["start_succ_pair"][0], r["start_succ_pair"][1]),
            "%s/%s" % tuple(r["start_succ"]) if r["start_succ"] else "无开局包",
            r["n_prod_cand"], r["n_prod_cand_five_ok"], r["prod_tc"], r["prod_rank_score"]))
    w("候选数 ≠ 1 的局 = %d（串局风险直接来源）；五维不吻合 = %d"
      % (sum(1 for r in runs_rows if r["n_prod_cand"] != 1),
         sum(1 for r in runs_rows if r["n_prod_cand_five_ok"] != 1)))

    w("\n== Q1b 双亲记录能不能取回属性（角色由 succession_chara_array 的 owner_viewer_id 定）==")
    for r in runs_rows:
        w("  %-20s 租借远程号=%s" % (rlab(r["run"]), r["rental_remote"] or "无开局包"))
        for i, p in enumerate(r["parents"], 1):
            if p is None:
                w("      双亲槽 %d（本地 tc %s）= 全库找不到同身份记录" % (i, r["start_succ_pair"][i - 1]))
                continue
            w("      槽%d tc=%-5s %-4s card=%-7s 分=%-6s 五维=%s 和=%-5s 因子=%-3d(+%d) "
              " rar=%s tal=%s 取回路=%s 同身份副本=%d%s"
              % (i, p["tc_id"], p["role"], p["card_id"], p["rank_score"], p["five"], p["five_sum"],
                 p["n_fac"], p["n_fac_ext"], p["rarity"], p["talent_level"], p["route"], p["n_twin"],
                 "  ⚠属性全 0（记录来自 %s）" % p["src_path"] if p["attr_zeroed"] else ""))

    w("\n== Q1c 产出 vs 双亲：方向核对（同一份终局记录里两组字段反号）==")
    for r in runs_rows:
        if not r["prod_tc"]:
            continue
        pr = [s for s in cache["snaps"] if s["tc_id"] == r["prod_tc"]][0]
        w("  %-20s 产出 tc=%-5s 本局**产出**因子=%d(+%d)｜**双亲**因子=%s｜双亲/祖父母 6 槽 pos=%s"
          " card=%s 条数=%s owner=%s"
          % (rlab(r["run"]), r["prod_tc"], len(pr["fac_out"]), len(pr["fac_ext"]),
             "|".join(str(p["n_fac"]) if p else "-" for p in r["parents"]),
             r["gp_pos"], r["gp_cards"], r["gp_nfac"], r["gp_owner"]))
    hit = tot = 0
    for r in runs_rows:
        if not isinstance(r["rental_remote"], str) or "@" not in r["rental_remote"]:
            continue
        rv = int(r["rental_remote"].split("@")[1])
        tot += 1
        hit += 1 if rv in {int(x) for x in r["gp_owner"].split("|") if x.isdigit()} else 0
    w("  反号检验（独立于上面的因子集合匹配）：开局请求里租借的 viewer_id 是否出现在该局产出记录"
      " 6 个 succession_chara 槽的 owner_viewer_id 里 = %d/%d 命中" % (hit, tot))

    stc = cache.get("struct")
    w("\n== Q1d 结构核对（逐回合继承对恒定 / 两格事件身份 / 全 0 轻量条目键清单；一手重解报文）==")
    if not stc:
        w("  缓存里没有 struct 块 ⇒ 用 --struct 跑一次（只解本样本用过的报文，约 20–30 s，结果写回缓存）")
    else:
        w("  带 chara_info 的报文 %d 个，其中 succession_trained_chara_id_1/2 **双非空 %d 个**"
          % (stc["n_ci_packets"], stc["n_succ_nonnull"]))
        dist = {k: v for k, v in stc["pairs_per_run"].items()}
        one = sum(1 for v in dist.values() if len(v) == 1)
        w("  每局内继承对 distinct=1 的局数 = %d/%d；13 局之间两两不同的对数 = %d"
          % (one, len(dist), len({tuple(sorted(x)) for v in dist.values() for x in v})))
        for rk, v in sorted(dist.items(), key=lambda kv: rlab(kv[0])):
            w("    %-20s %s" % (rlab(rk), v))
        t31 = t55 = 0
        for rk, ids in stc["event_ids"].items():
            if "400000040" in ids.get("31", []) and "7040" in ids.get("31", []):
                t31 += 1
            if "400000040" in ids.get("55", []) and "7041" in ids.get("55", []):
                t55 += 1
        w("  事件身份复核：面板 31 的报文含 400000040+7040 = %d 局｜面板 55 含 400000040+7041 = %d 局"
          % (t31, t55))
        for tc, s in sorted(stc["stub_records"].items()):
            if s.get("missing"):
                w("  轻量条目 tc=%s：报文 %s 里按 (trained_chara_id, card_id) 没回查到 ⇒ 未证" % (tc, s["file"]))
                continue
            w("  轻量条目 tc=%-5s 来自 %-28s（%d 键，scenario_id=%s，因子 %d 条，自带祖父母槽 %d 个）文件 %s"
              % (tc, s["block"], s["n_keys"], s["scen"], s["n_fac"], s["gp"], s["file"]))
            w("      为 0/缺 = %s" % ",".join(s["zero"]))
            w("      可读   = %s" % ",".join(s["readable"]))

    w("\n== Q1e 每一局的继承对与身份解析（区分「新输入」和「同一身份的又一副本」）==")
    ex = extra_inputs(cache)
    sig = {}
    for e in ex:
        key = tuple(sorted((json.dumps(v, sort_keys=True) if v else "取不到")
                           for v in e["ident"].values()))
        sig.setdefault(key, []).append(e)
    for e in ex:
        w("  %-20s 覆盖两格=%-3s 回合 %s 对=%s" % (
            rlab(e["run"]), "是" if e["covers_both_legs"] else "否", e["turn_range"], e["pair"]))
        for tc, v in sorted(e["ident"].items(), key=lambda kv: str(kv[0])):
            w("      tc=%-5s %s" % (tc, "这个本地 id 无记录（身份要按同签名副本取回，见 Q1b）" if v is None else
                                    "卡=%s 分=%s 五维和=%s 因子=%s 同 id 副本=%d"
                                    % (v["card"], v["rank"], v["five"], v["fac"], v["n_copy"])))
    dup = [(k, [x["run"] for x in v]) for k, v in sig.items() if len(v) > 1]
    for k, runs in dup:
        w("  ⚠ 身份签名相同的局（同一对双亲的重复测量，不是独立输入）：%s"
          % ", ".join(rlab(r) for r in runs))

    w("\n== Q2 剂量：每局两条腿（报文 turn=面板 turn，引擎 turn=面板−1）==")
    w("%-20s %-6s %-6s %-8s %-8s %-22s %-22s %-8s" %
      ("run", "t面板", "t引擎", "Σds", "Σdm", "ds 逐轴", "dm 逐轴", "抽到因子"))
    for x in legs:
        w("%-20s %-6d %-6d %-8d %-8d %-22s %-22s %-8d" % (
            rlab(x["run"]), x["turn_panel"], x["turn_eng"], axis_sum(x["ds"]), axis_sum(x["dm"]),
            "|".join(str(v) for v in x["ds"]), "|".join(str(v) for v in x["dm"]), x["nfac_out"]))
    ds = [axis_sum(x["ds"]) for x in legs]
    dm = [axis_sum(x["dm"]) for x in legs]
    dd = [axis_sum(x["dd"]) for x in legs]
    w("腿 n=%d｜Σds %d–%d 均值 %.1f｜Σdm %d–%d 均值 %.1f｜Σdd 非零腿数 %d"
      % (len(legs), min(ds), max(ds), st.fmean(ds), min(dm), max(dm), st.fmean(dm),
         sum(1 for v in dd if v)))

    w("\n== Q2b config 分组（R1/R2：同输入=重复测量）==")
    groups = collections.defaultdict(list)
    for r in runs_rows:
        groups[r["config"] or "未解析"].append(r)
    for g, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        dmr = [x["dm_run"] for x in rs]
        dsr = [x["ds_run"] for x in rs]
        w("  n=%d  输入=%s" % (len(rs), short(g)))
        w("      局 %s" % ", ".join(x["run"].split("@")[1] for x in rs))
        w("      dm_run %s（极差 %d）｜ds_run %s（极差 %d）｜par_n_fac=%s par_rank_sum=%s par_five_sum=%s"
          % (dmr, max(dmr) - min(dmr), dsr, max(dsr) - min(dsr),
             {x["par_n_fac"] for x in rs}, {x["par_rank_sum"] for x in rs},
             {x["par_five_sum"] for x in rs}))
        w("      种马侧 rank=%s five=%s fac=%s｜自有侧 rank=%s five=%s fac=%s"
          % ({x["stud_rank"] for x in rs}, {x["stud_five"] for x in rs}, {x["stud_fac"] for x in rs},
             {x["own_rank"] for x in rs}, {x["own_five"] for x in rs}, {x["own_fac"] for x in rs}))
        if len(rs) == 1:
            w("      ⚠ 单局组：只作描述，不进检验（R2）")

    w("\n== Q2c 主对比（R3：预先指定「双亲身份签名里 rank_score 上界最高 vs 最低」的两个 n≥2 组）==")
    ranked = sorted((g for g, rs in groups.items() if len(rs) >= 2),
                    key=lambda g: -max(x["par_rank_max"] or 0 for x in groups[g]))
    for g in ranked:
        rs = groups[g]
        w("  组 %-46s n=%d  par_rank_max 上界=%s  ds=%s dm=%s"
          % (short(g)[:46], len(rs), max(x["par_rank_max"] or 0 for x in rs),
             [x["ds_run"] for x in rs], [x["dm_run"] for x in rs]))
    if len(ranked) >= 2:
        hi, lo = groups[ranked[0]], groups[ranked[-1]]
        for var in ("dm_run", "ds_run"):
            res = mannwhitney([x[var] for x in hi], [x[var] for x in lo])
            w("  %s：高组 %s (n=%d) vs 低组 %s (n=%d)｜U=%s 精确 p=%s（该设计下 p 下限 %s）"
              " HL=%s 成对差区间=%s"
              % (var, [x[var] for x in hi], len(hi), [x[var] for x in lo], len(lo),
                 res["U"] if res else "n/a", res["p_exact"] if res else "n/a",
                 res["p_floor"] if res else "n/a", res["hl"] if res else "n/a",
                 res["pair_range"] if res else "n/a"))
            w("     完全分离（低组最大值 < 高组最小值）：%s"
              % ("是" if res and max(x[var] for x in lo) < min(x[var] for x in hi) else "否"))

    w("\n== Q2d 连续剂量变量（R4 预先写死的 5 个；R5 n≤10 只报形状）==")
    usable = [r for r in runs_rows if r["config"]]
    conf_pts = {}
    for r in usable:
        conf_pts.setdefault(r["config"], []).append(r)
    nc = len(conf_pts)
    floor = 2.0 / math.factorial(nc) if nc else 1.0
    w("  ⚠ 伪重复披露：局级 n=%d 里只有 %d 个独立输入（同 config 的局输入完全相同）；"
      "config 级 n=%d 的精确置换 p 下限 = 2/%d! = %.3f ⇒ 任何 config 级检验都到不了 0.05"
      % (len(usable), nc, nc, nc, floor))
    w("  ⚠ 可读性污染披露（一手，见 Q2f）：%d/%d 局的**自有**双亲只有属性全 0 的轻量记录 ⇒ "
      "par_rank_sum / par_five_sum / par_five_max / own_rank / own_five 的组间差里含「记录取不取得到」"
      "这一测量假象；不受影响的是 par_n_fac 与 stud_*（因子条数和种马侧属性都有记录）。"
      % (sum(1 for r in usable if not r["own_attr_ok"]), len(usable)))
    for var in list(DOSE_VARS) + ["stud_rank", "stud_five", "stud_fac",
                                  "own_rank", "own_five", "own_fac"]:
        pts = [(r[var], r["dm_run"], r["ds_run"]) for r in usable if r[var] is not None]
        if len({p[0] for p in pts}) < 2 or not pts:
            w("  %-14s 取值只有一种（%s）⇒ 无剂量对比可用" % (var, sorted({p[0] for p in pts})))
            continue
        rd = spearman_perm([p[0] for p in pts], [p[1] for p in pts])
        rs_ = spearman_perm([p[0] for p in pts], [p[2] for p in pts])
        cw = {}
        for g, rr in conf_pts.items():
            vv = [x[var] for x in rr if x[var] is not None]
            if vv:
                cw[st.fmean(vv)] = (st.fmean([x["dm_run"] for x in rr]),
                                    st.fmean([x["ds_run"] for x in rr]))
        rc = spearman_perm(sorted(cw), [cw[k][0] for k in sorted(cw)]) if len(cw) >= 4 else None
        rcs = spearman_perm(sorted(cw), [cw[k][1] for k in sorted(cw)]) if len(cw) >= 4 else None
        tag = "" if var in DOSE_VARS else "  [R4b 事后加：仅探索性]"
        w("  %-14s n=%d 取值 %s｜局级 ρ(dm)=%s p=%s ρ(ds)=%s p=%s｜config 级 n=%d ρ(dm)=%s p=%s ρ(ds)=%s p=%s%s"
          % (var, len(pts), sorted({p[0] for p in pts}), rd and rd["rho"], rd and rd["p_perm"],
             rs_ and rs_["rho"], rs_ and rs_["p_perm"], len(cw),
             rc and rc["rho"], rc and rc["p_perm"], rcs and rcs["rho"], rcs and rcs["p_perm"], tag))
    w("  —— 输入侧与输出侧分开（R6）——")
    for var, lab in (("nfac_draw1", "t31 抽到的因子条数"), ("nfac_draw2", "t55 抽到的因子条数")):
        pts = [(r[var], r["dm1"] if var.endswith("1") else r["dm2"],
                r["ds1"] if var.endswith("1") else r["ds2"]) for r in usable if r[var] is not None]
        rd = spearman_perm([p[0] for p in pts], [p[1] for p in pts])
        w("  %-22s 取值 %s｜ρ(本腿 dm)=%s p=%s (n=%d)"
          % (lab, sorted({p[0] for p in pts}), rd and rd["rho"], rd and rd["p_perm"], len(pts)))

    w("\n== Q2e 两腿折成評価点（口径同 #37 §3，送分=下界、带宽分=乐观上限，不相加）==")
    vl = value_legs(tbl, legs)
    per_run = collections.defaultdict(lambda: [0, 0])
    for (run, t), v in vl.items():
        per_run[run][0] += v["gain_score"]
        per_run[run][1] += v["band_score"]
    bycfg = collections.defaultdict(list)
    for r in runs_rows:
        if r["config"]:
            bycfg[r["config"]].append(per_run[r["run"]])
    for run, (g, b) in sorted(per_run.items()):
        w("  %-20s 两腿送分合计 %-7d 带宽分合计 %-7d" % (rlab(run), g, b))
    for cfg, vs in sorted(bycfg.items(), key=lambda kv: -len(kv[1])):
        w("  组 %-52s n=%d 送分 %s 带宽分 %s"
          % (short(cfg)[:52], len(vs), [v[0] for v in vs], [v[1] for v in vs]))
    gs = [v[0] for v in per_run.values()]
    bs = [v[1] for v in per_run.values()]
    w("  送分 %d–%d 均值 %.0f｜带宽分 %d–%d 均值 %.0f｜局数 %d"
      % (min(gs), max(gs), st.fmean(gs), min(bs), max(bs), st.fmean(bs), len(per_run)))

    w("\n== Q2f 双亲属性可读性（哪一侧取不到，直接决定接线要用户导出什么）==")
    for r in runs_rows:
        miss = [p for p in r["parents"] if p is None or p["attr_zeroed"]]
        w("  %-20s 双亲=%s｜缺属性 %d 个%s"
          % (rlab(r["run"]), "|".join((p["role"] if p else "未解析") for p in r["parents"]),
             len(miss), "（自有马娘在本账号记录里是**属性全 0 的轻量条目**）" if miss else ""))

    w("\n== Q2g 水位置换对照（#37 §4 的两条负面控制，在新样本上复算）==")
    lv = [(axis_sum(x["five_before"]), axis_sum(x["max_before"]),
           axis_sum(x["max_before"]) - axis_sum(x["five_before"]),
           axis_sum(x["ds"]), axis_sum(x["dm"])) for x in legs]
    for xi, yi, lab in ((0, 3, "Σds vs 抬前现值"), (0, 4, "Σdm vs 抬前现值"),
                        (2, 3, "Σds vs 抬前余裕(上限−现值)"), (2, 4, "Σdm vs 抬前余裕")):
        rp = spearman_perm([p[xi] for p in lv], [p[yi] for p in lv])
        w("  %-26s 腿级 n=%d ρ=%s p=%s（同局两腿不独立，只看方向）"
          % (lab, len(lv), rp and rp["rho"], rp and rp["p_perm"]))
    w("  R3 两组的水位对照（抬前现值 / 抬前余裕，t31 那格）：")
    for g, rr in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if g == "未解析":
            continue
        w("     %-52s n=%d t31 现值和 %s 上限和 %s"
          % (short(g)[:52], len(rr), [sum(x["five_before1"]) for x in rr],
             [sum(x["max_before1"]) for x in rr]))
    w("\n== Q3 上限够不够（新靶 LG15=85,600；一手分布 + 自己局的对照）==")
    bands = [(LG15_LINE, LF_LINE, "LG15–LF"), (LF_LINE, 10 ** 9, "≥LF"),
             (60000, LG15_LINE, "6万–LG15")]
    s15 = [s for s in cache["snaps"] if s["scen"] == 15 and s["rank_score"]]
    for lo, hi, lab in bands:
        vs = sorted(s["five_sum"] for s in s15 if lo <= (s["rank_score"] or 0) < hi)
        w("  %-10s 官方分区间 [%d,%d) 终局快照 n=%-4d 五维和 %s–%s 中位 %s"
          % (lab, lo, hi, len(vs), vs[0] if vs else "-", vs[-1] if vs else "-",
             vs[len(vs) // 2] if vs else "-"))
    w("  自己局（终值五维和 / Σmax_end / 两腿 dm / 官方分 / 覆盖到第几回合）：")
    for r in sorted(runs_rows, key=lambda x: -(x["five_sum_end"] or 0)):
        w("    %-20s 五维和 %-6s Σmax_end %-6s dm_run %-4s 官方分 %-7s 末回合 t%s"
          % (rlab(r["run"]), r["five_sum_end"], r["max_sum_end"], r["dm_run"],
             r["prod_rank_score"], r["t_last"]))
    need = LG15_LINE - max(x["prod_rank_score"] or 0 for x in runs_rows)
    best = max(x["prod_rank_score"] or 0 for x in runs_rows)
    w("  最好一局官方分 %d，距 LG15 还差 %d" % (best, need))
    cfgv = {short(g): (st.fmean([v[0] for v in vs]), st.fmean([v[1] for v in vs]), len(vs))
            for g, vs in bycfg.items() if len(vs) >= 2}
    for c, (gv, bv, nn) in sorted(cfgv.items(), key=lambda kv: -kv[1][0]):
        w("     n=%d 组 %-52s 送分均值 %6.0f 带宽分均值 %5.0f" % (nn, c[:52], gv, bv))
    if len(cfgv) >= 2:
        gv = [v[0] for v in cfgv.values()]
        bv = [v[1] for v in cfgv.values()]
        w("     ⇒ 换种马在这两格能移动的评估点（组均值差）：送分 %.0f、带宽分 %.0f"
          "，各占 LG15 缺口的 %.1f%% / %.1f%%（送分=下界、带宽分=乐观上限，不可相加）"
          % (max(gv) - min(gv), max(bv) - min(bv),
             100.0 * (max(gv) - min(gv)) / need, 100.0 * (max(bv) - min(bv)) / need))
    w("     两腿送分本身跨度 %d–%d ⇒ 占缺口的 %.1f%%–%.1f%%"
      % (min(gs), max(gs), 100.0 * min(gs) / need, 100.0 * max(gs) / need))
    w("  ⚠ 混杂未解（同 config 组的水位对照）：")
    for g, rr in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if g == "未解析":
            continue
        w("     %-52s n=%d 终值五维和 %s Σmax_end %s"
          % (short(g)[:52], len(rr), [x["five_sum_end"] for x in rr],
             [x["max_sum_end"] for x in rr]))
    fs = [x["five_sum_end"] for g, rr in groups.items() if g != "未解析" for x in rr]
    ms = [x["max_sum_end"] for g, rr in groups.items() if g != "未解析" for x in rr]
    w("     ⇒ 一手观察：不同种马组的**整局终值水位也不同**（五维和 %d–%d），而 Σmax_end 各组几乎一样"
      "（%d–%d）。⇒ ds/dm 的组间差**不能全部归因于种马**：水位（手法/是否托管/是否练满）没有在数据里"
      "和种马分开，本线未核这两批局的手法来源。" % (min(fs), max(fs), min(ms), max(ms)))
    return usable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--csv", default=OUT_CSV)
    ap.add_argument("--txt", default=OUT_TXT)
    ap.add_argument("--json", default=OUT_JSON)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--struct", action="store_true",
                    help="补算结构核对（逐回合继承对 / 事件身份 / 轻量条目），结果写回缓存")
    a = ap.parse_args()
    os.chdir(REPO)
    os.makedirs("out", exist_ok=True)
    if a.from_cache and os.path.exists(a.cache):
        cache = json.load(open(a.cache, encoding="utf-8"))
        print("读缓存 %s（未重扫报文；files=%s）" % (a.cache, cache["stats"].get("files")))
    else:
        t0 = time.time()
        cache = scan(a.limit)
        json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
        print("扫描用时 %.0fs → %s" % (time.time() - t0, a.cache))
    ft = factor_types()
    tbl = score_table()
    legs = event_legs(cache)
    runs_rows = run_parents(cache, legs, ft)
    if a.struct:
        stubs = sorted({(p["tc_id"], p["card_id"], p["src"], p["src_path"])
                        for r in runs_rows for p in r["parents"] if p and p["attr_zeroed"]})
        t0 = time.time()
        cache["struct"] = structural(cache, stubs)
        json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
        print("结构核对用时 %.0fs（只解本样本用过的 %d 个报文 + %d 个轻量条目所在包）→ 已写回 %s"
              % (time.time() - t0, sum(len(r["pkts"]) for r in cache["runs"].values()),
                 len(stubs), a.cache))
    fh = open(a.txt, "w", encoding="utf-8")
    usable = report(cache, runs_rows, legs, fh, tbl)
    fh.close()
    cols = []
    for r in runs_rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in runs_rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                        for k, v in r.items()})
    json.dump({"n_files": cache["stats"].get("files"), "n_runs_with_both_legs": len(runs_rows),
               "n_legs": len(legs), "n_config_resolved": len(usable),
               "dose_rules": ["R1 签名分组", "R2 n>=2 才检验", "R3 预设两组 Mann-Whitney 精确",
                              "R4 Spearman 置换", "R5 只报形状不折算常数", "R6 输入输出分开",
                              "R7 伪重复披露（config 级 + p 下限）"],
               "path_counter": cache["path_counter"], "path_nonempty": cache["path_nonempty"],
               "struct": cache.get("struct"), "extra_inputs": extra_inputs(cache),
               "runs": runs_rows, "legs": legs, "starts": cache["starts"]},
              open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n剂量表 %d 行 → %s｜读数 → %s｜摘要 → %s" % (len(runs_rows), a.csv, a.txt, a.json))


if __name__ == "__main__":
    main()
