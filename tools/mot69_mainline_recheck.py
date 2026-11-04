#!/usr/bin/env python3
"""#69 主线独立复算：干劲逐回合序列（一手报文，不经 #69 那条脚本）。

口径（自己定死，与 #69 文档 §3 对表）：
  run   = chara_info.single_mode_chara_id
  格子  = (run, panel_turn)，panel_turn = chara_info.turn（1 基，1..78）
  mot   = chara_info.motivation；同格取时间序首个＝mot_start、末个＝mot_end
  赛    = 该格任一 response 的 data 里出现 race_reward_info（赛后奖励；race_history / race_add_reward_info 同集合，三者各自 282 格）
  合宿  = 面板窗 13-16 / 37-40 / 61-64；合宿开始＝13/37/61
  发表会= 面板 24/48/68
  手    = 该格出现过的 command_result.command_id（稽古 5101-5105／普通训练 101-106）

用法：python -u tools/mot69_mainline_recheck.py [all|cov|bucket|hand|cp|race]
"""
import os
import sys
import collections

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
CAMP_ON = (13, 37, 61)
CAMP_WIN = frozenset(list(range(13, 17)) + list(range(37, 41)) + list(range(61, 65)))
FESTA = frozenset({24, 48, 68})
KEIKO = frozenset({5101, 5102, 5103, 5104, 5105})
TRAIN = frozenset({101, 102, 103, 104, 105, 106})
RACE_KEYS = ("race_reward_info",)  # 赛后奖励在场＝该回合真跑了比赛
OFFER_KEYS = ("race_start_info",)


def ci_of(data):
    ci = data.get("chara_info")
    if isinstance(ci, dict) and ci.get("turn") is not None:
        return ci
    lc = data.get("single_mode_load_common")
    if isinstance(lc, dict) and isinstance(lc.get("chara_info"), dict):
        return lc["chara_info"]
    return None


def load():
    files = sorted(os.listdir(RAW), key=lambda f: (os.path.getmtime(os.path.join(RAW, f)), f))
    # cell -> {mots:[...], race:bool, cmds:set, pkts:int}
    cells = collections.defaultdict(lambda: {"mots": [], "race": False, "cmds": set(),
                                             "pkts": 0, "with_mot": 0, "seq": []})
    for fn in files:
        if not fn.endswith("_notify_response.bin"):
            continue
        try:
            d = ml.decode_file(os.path.join(RAW, fn))
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("response_code") != 1:
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = ci_of(data)
        if not isinstance(ci, dict) or ci.get("turn") is None:
            continue
        run = ci.get("single_mode_chara_id")
        t = ci.get("turn")
        c = cells[(run, t)]
        c["pkts"] += 1
        m = ci.get("motivation")
        if isinstance(m, int):
            c["mots"].append(m)
            c["with_mot"] += 1
        if any(k in data for k in RACE_KEYS):
            c["race"] = True
        cr = data.get("command_result")
        cid = None
        if isinstance(cr, dict) and isinstance(cr.get("command_id"), int):
            cid = cr.get("command_id")
        elif isinstance(cr, list):
            for e in cr:
                if isinstance(e, dict) and isinstance(e.get("command_id"), int):
                    cid = e.get("command_id")
                    break
        if cid is not None:
            c["cmds"].add(cid)
            c["seq"].append((cid, m, c["mots"][-2] if len(c["mots"]) > 1 else None))
    return cells


def build(cells):
    """成表：一格一行，mot_start/mot_end/d_mot_turn。"""
    rows = {}
    for (run, t), c in cells.items():
        if not c["mots"]:
            continue
        rows[(run, t)] = {
            "run": run, "t": t, "pkts": c["pkts"], "n_mot": len(c["mots"]),
            "mot_start": c["mots"][0], "mot_end": c["mots"][-1],
            "d": c["mots"][-1] - c["mots"][0],
            "in_turn_moved": len(set(c["mots"])) > 1,
            "race": c["race"], "cmds": set(c["cmds"]),
            "camp_on": t in CAMP_ON, "camp_win": t in CAMP_WIN, "festa": t in FESTA,
            "seq": list(c["seq"]),
        }
    return rows


def bucket(r):
    hand = r["cmds"] & (KEIKO | TRAIN)
    if r["race"]:
        return "race_festa" if r["festa"] else "race"
    if r["camp_on"]:
        return "camp_start"
    if r["festa"]:
        return "festa_pure"
    if hand and r["d"] != 0:
        return "hand_nonzero"
    if r["d"] != 0:
        return "other_nonzero"
    return "no_change"


def d_hand(r, prev):
    """手上的 Δ：该格内「最后一个手前一个快照 → 该格末快照」的粗口径不可得，
    这里按 #69 文档口径的保守版本 = 若该格有手且格内 mot 序列出现变化则取格内手后差。
    本脚本主判据用 d_mot_turn（格首→格末），手级只用 cmd 归属做分类。"""
    return r["d"]


def s_cov(rows):
    by_run = collections.defaultdict(list)
    for r in rows.values():
        by_run[r["run"]].append(r)
    print("run  n_cells  first  last  missing  full78")
    tot = 0
    for run in sorted(by_run):
        rs = sorted(by_run[run], key=lambda r: r["t"])
        tot += len(rs)
        ts = {r["t"] for r in rs}
        missing = sorted(set(range(min(ts), 79)) - ts) if ts else []
        holes = sorted(set(range(1, 79)) - ts)
        head = 0
        if ts:
            head = min(ts) - 1
        tail = 78 - max(ts)
        print("%3d %8d %6d %4d %8d %6s  holes=%d head=%d tail=%d %s" % (
            run, len(rs), min(ts), max(ts), len(holes), len(rs) == 78, len(holes), head, tail,
            ("hole_turns=" + ",".join(map(str, missing[:12]))) if missing else ""))
    print("TOTAL cells =", tot, " runs =", len(by_run))


def s_bucket(rows):
    agg = collections.defaultdict(lambda: {"n": 0, "nz": 0, "sum": 0, "up": 0, "dn": 0,
                                           "hr": 0, "hr_up": 0, "hr_dn": 0, "top5": 0, "lo1": 0})
    for r in rows.values():
        b = bucket(r)
        a = agg[b]
        a["n"] += 1
        a["sum"] += r["d"]
        if r["d"] > 0:
            a["up"] += 1
        elif r["d"] < 0:
            a["dn"] += 1
        if r["d"] != 0:
            a["nz"] += 1
        if r["mot_start"] < 5:
            a["hr"] += 1
            if r["d"] > 0:
                a["hr_up"] += 1
            elif r["d"] < 0:
                a["hr_dn"] += 1
        if r["mot_start"] == 5:
            a["top5"] += 1
        if r["mot_start"] == 1:
            a["lo1"] += 1
    nr = len({r["run"] for r in rows.values()})
    print("bucket            格子 非零  ΣΔ   ΣΔ/局  涨/跌   有头寸  头寸涨/跌  顶格起点 下限起点")
    for b in ("race", "race_festa", "other_nonzero", "hand_nonzero", "camp_start",
              "festa_pure", "no_change"):
        a = agg[b]
        print("%-14s %5d %4d %5d %7.3f %4d/%-3d %6d %6d/%-3d %8d %8d" % (
            b, a["n"], a["nz"], a["sum"], a["sum"] / nr, a["up"], a["dn"],
            a["hr"], a["hr_up"], a["hr_dn"], a["top5"], a["lo1"]))
    a, f = agg["race"], agg["race_festa"]
    print("赛合计 格子=%d ΣΔ=%d ΣΔ/局=%.3f 涨=%d 跌=%d 有头寸=%d 头寸涨/跌=%d/%d 起点1=%d" % (
        a["n"] + f["n"], a["sum"] + f["sum"], (a["sum"] + f["sum"]) / nr,
        a["up"] + f["up"], a["dn"] + f["dn"], a["hr"] + f["hr"], a["hr_up"] + f["hr_up"],
        a["hr_dn"] + f["hr_dn"], a["lo1"] + f["lo1"]))
    print("顶格起点且下跌（5→…）赛合计 =", sum(1 for r in rows.values() if r["race"]
          and r["mot_start"] == 5 and r["d"] < 0))
    print("格子总数 =", sum(a["n"] for a in agg.values()))


def s_top_drop(rows):
    print("赛格 5→4 清单：")
    n = 0
    for r in sorted(rows.values(), key=lambda r: (r["run"], r["t"])):
        if r["race"] and r["mot_start"] == 5 and r["d"] < 0:
            n += 1
            print("  @%d 面板%d -> %d" % (r["run"], r["t"], r["mot_end"]))
    print("计", n)


def s_hand(rows):
    """手级：一手＝一个带 command_result 的报文。该手的腿间 Δ＝结算报文 mot − 上一快照 mot。"""
    agg = collections.defaultdict(lambda: collections.Counter())
    detail = []
    for r in rows.values():
        for cid, m, prev in r["seq"]:
            fam = "keiko" if cid in KEIKO else ("train" if cid in TRAIN else "other")
            agg[fam]["hands"] += 1
            if prev is None or m is None:
                agg[fam]["na"] += 1
                continue
            d = m - prev
            agg[fam]["sum"] += d
            if d < 0:
                agg[fam]["neg"] += 1
            elif d > 0:
                agg[fam]["pos"] += 1
                detail.append((r["run"], r["t"], cid, prev, m, r["race"], r["festa"], r["camp_win"]))
    for fam in ("keiko", "train", "other"):
        a = agg[fam]
        print("%-6s 手=%-5d 负=%-3d 正=%-3d 零/不可判=%-5d ΣΔ=%d" % (
            fam, a["hands"], a["neg"], a["pos"], a["na"], a["sum"]))
    kt = agg["keiko"]["hands"] + agg["train"]["hands"]
    print("训练类合计 手=%d 负=%d 正=%d" % (kt, agg["keiko"]["neg"] + agg["train"]["neg"],
          agg["keiko"]["pos"] + agg["train"]["pos"]))
    print("正向明细（run,面板,cmd,prev->mot,race,festa,camp窗）：")
    for x in detail:
        print("  ", x)
    cw = [r for r in rows.values() if r["camp_win"] for _ in r["seq"]]
    cwn = sum(1 for r in rows.values() if r["camp_win"] for cid, m, prev in r["seq"]
              if (cid in KEIKO or cid in TRAIN) and prev is not None and m is not None and m - prev < 0)
    cwt = sum(1 for r in rows.values() if r["camp_win"] for cid, m, prev in r["seq"]
              if cid in KEIKO or cid in TRAIN)
    print("合宿窗内出手 %d 手，其中该手腿间下降 %d 手" % (cwt, cwn))


def s_festa(rows):
    pure = [r for r in rows.values() if r["festa"] and not r["race"]]
    withr = [r for r in rows.values() if r["festa"] and r["race"]]
    print("纯发表会格 %d ΣΔ=%d 非零=%d" % (len(pure), sum(r["d"] for r in pure),
          sum(1 for r in pure if r["d"] != 0)))
    print("发表会∧赛格 %d ΣΔ=%d" % (len(withr), sum(r["d"] for r in withr)))
    for r in sorted(withr, key=lambda r: (r["run"], r["t"])):
        print("  @%d 面板%d %d->%d d=%+d" % (r["run"], r["t"], r["mot_start"], r["mot_end"], r["d"]))
    for r in sorted(pure, key=lambda r: (r["run"], r["t"])):
        if r["d"]:
            print("  纯 @%d 面板%d %d->%d" % (r["run"], r["t"], r["mot_start"], r["mot_end"]))


def s_percell(rows):
    by_run = collections.defaultdict(list)
    for r in rows.values():
        by_run[r["run"]].append(r)
    print("run  格子  顶格(mot_end==5)占比  ΣΔ_turn")
    for run in sorted(by_run):
        rs = by_run[run]
        top = sum(1 for r in rs if r["mot_end"] == 5)
        print("@%3d %5d %12.1f%% %8d" % (run, len(rs), 100.0 * top / len(rs),
                                          sum(r["d"] for r in rs)))


MODES = {"cov": s_cov, "bucket": s_bucket, "top": s_top_drop, "hand": s_hand,
         "festa": s_festa, "percell": s_percell}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    rows = build(load())
    if mode == "all":
        for fn in (s_cov, s_bucket, s_top_drop, s_hand, s_festa, s_percell):
            print("\n===== %s =====" % fn.__name__)
            fn(rows)
    else:
        MODES[mode](rows)
