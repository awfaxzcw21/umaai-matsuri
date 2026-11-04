#!/usr/bin/env python3
"""#69 事实层（包级台账）主线独立复算。

母口径＝**相邻包跳**：同一局内按采集序（文件名 时分秒_全局序号，再退到 mtime）排所有带
`chara_info.motivation` 的 response 包，相邻两包的 mot 差＝一跳。自己判 kind，不读他们的脚本。

kind 判定（自己定，与他们 §1 表对表）：
  race            该包带 race_reward_info / race_history / trophy_reward_info（赛后结算面）
  at_settle_*     该包带 command_result，cmd ∈ 稽古 5101-5105 / 普通 101-106 / 外出 301-304
  post_settle_*   上一包是结算包且 result_state==1（失败），这一包没有自己的结算
  festa           该包带 festa_data_set（大祭预告板）
  turn_first      这一跳跨面板
  other           同回合内的后续包

用法：python -u tools/mot69_packet_recheck.py [ledger|kind|cell|join]
"""
import os
import sys
import csv
import collections

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
THEIRS = "E:/HJM/umaai-matsuri/out/mot_69/fact_packet_dmot.csv"
KEIKO = frozenset({5101, 5102, 5103, 5104, 5105})
TRAIN = frozenset({101, 102, 103, 104, 105, 106})
OUTING = frozenset({301, 302, 303, 304})
RACE_KEYS = ("race_reward_info", "race_history", "trophy_reward_info")


def ci_of(data):
    ci = data.get("chara_info")
    if isinstance(ci, dict) and ci.get("turn") is not None:
        return ci
    lc = data.get("single_mode_load_common")
    if isinstance(lc, dict) and isinstance(lc.get("chara_info"), dict):
        return lc["chara_info"]
    return None


def fname_key(f):
    """采集序：文件名是 时分秒_全局序号 ⇒ 全局序号单调；跨连接重置用 mtime 兜底。"""
    try:
        hh, mm = f.split("_")[0], int(f.split("_")[1])
        h, mi, s = hh[0:2], hh[2:4], hh[4:6]
        return (int(h) * 3600 + int(mi) * 60 + int(s), mm)
    except Exception:
        return (0, 0)


def order_key(f):
    """采集序主键＝mtime：文件名 时分秒 跨午夜回绕、全局序号跨连接重置，两者都不能单独当序。"""
    return (os.path.getmtime(os.path.join(RAW, f)), fname_key(f))


def packets():
    files = [f for f in os.listdir(RAW) if f.endswith("_notify_response.bin")]
    files.sort(key=order_key)
    out = []
    for f in files:
        try:
            d = ml.decode_file(os.path.join(RAW, f))
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
        if ci.get("scenario_id") != 15:
            continue
        m = ci.get("motivation")
        if not isinstance(m, int):
            continue
        cr = data.get("command_result")
        cid = rs = None
        if isinstance(cr, dict):
            cid, rs = cr.get("command_id"), cr.get("result_state")
        out.append({
            "file": f, "run": ci.get("single_mode_chara_id"), "turn": ci.get("turn"),
            "mot": m, "cmd": cid if isinstance(cid, int) else None,
            "rstate": rs if isinstance(rs, int) else None,
            "race": any(k in data for k in RACE_KEYS),
            "festa": "festa_data_set" in data,
            "cp": ci.get("chara_effect_id_array") is not None,
        })
    return out


def ledger(ps):
    rows = []
    prev = None
    for p in ps:
        if prev is not None and prev["run"] == p["run"]:
            d = p["mot"] - prev["mot"]
            if p["race"]:
                kind = "race"
            elif p["cmd"] is not None:
                fam = ("keiko" if p["cmd"] in KEIKO else
                       "plain" if p["cmd"] in TRAIN else
                       "outing" if p["cmd"] in OUTING else "other_cmd")
                kind = "at_settle_" + fam + ("_s2" if p["rstate"] == 2 else "_s1" if p["rstate"] == 1 else "")
            elif prev["rstate"] == 1:
                kind = "post_settle_prevfail_" + ("keiko" if prev["cmd"] in KEIKO else "other")
            elif prev["cmd"] == 390:
                kind = "post_settle_390"
            elif p["turn"] != prev["turn"]:
                kind = "turn_first"
            elif p["festa"]:
                kind = "festa"
            else:
                kind = "other"
            rows.append({"run": p["run"], "turn": p["turn"], "prev_turn": prev["turn"],
                         "prev": prev["mot"], "mot": p["mot"], "d": d, "kind": kind,
                         "cmd": p["cmd"], "prev_cmd": prev["cmd"], "file": p["file"]})
        prev = p
    return rows


def s_ledger(rows):
    nz = [r for r in rows if r["d"] != 0]
    up = [r for r in nz if r["d"] > 0]
    dn = [r for r in nz if r["d"] < 0]
    print("跳总数=%d 非零=%d 涨=%d(+%d) 跌=%d(%d) 净=%+d" % (
        len(rows), len(nz), len(up), sum(r["d"] for r in up), len(dn),
        sum(r["d"] for r in dn), sum(r["d"] for r in nz)))
    agg = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        a = agg[r["kind"]]
        a[0] += 1
        if r["d"] > 0:
            a[1] += 1
        elif r["d"] < 0:
            a[2] += 1
        a[3] += r["d"]
    print("%-26s  包/跳    涨   跌   ΣΔ" % "kind")
    for k in sorted(agg, key=lambda k: -abs(agg[k][3])):
        a = agg[k]
        print("%-26s %7d %4d %4d %5d" % (k, a[0], a[1], a[2], a[3]))
    print("\n跌的非零跳明细：")
    for r in sorted(dn, key=lambda r: (r["run"], r["turn"])):
        print("  @%d 面板%d %d->%d %-22s cmd=%s prev_cmd=%s %s" % (
            r["run"], r["turn"], r["prev"], r["mot"], r["kind"], r["cmd"], r["prev_cmd"], r["file"]))


def s_cell(rows):
    """格级投影：同格内首包→末包（= mot69_mainline_recheck 的 d），验证「回合级是包级的下界」。"""
    cell = collections.OrderedDict()
    for r in rows:
        k = (r["run"], r["turn"])
        c = cell.setdefault(k, {"nz": 0, "sum": 0, "first": r["prev"], "last": r["mot"]})
        c["nz"] += 1 if r["d"] else 0
        c["sum"] += r["d"]
        c["last"] = r["mot"]
    moved = sum(1 for c in cell.values() if c["nz"])
    net0 = sum(1 for c in cell.values() if c["nz"] and c["sum"] == 0)
    print("格=%d 其中包级非零跳>=1 的格=%d；跳非零但格级净 0（同回合抵消）=%d" % (len(cell), moved, net0))


def s_join(rows):
    mine = {(r["run"], r["turn"], r["file"]): r for r in rows if r["d"] != 0}
    theirs = list(csv.DictReader(open(THEIRS, encoding="utf-8")))
    tnz = len(theirs)
    print("我的非零跳=%d  他们的=%d" % (len(mine), tnz))
    tkeys = {(int(x["run"]), int(x["panel_turn"]), x["file"]) for x in theirs}
    mkeys = {(k[0], k[1], k[2]) for k in mine}
    only_t = tkeys - mkeys
    only_m = mkeys - tkeys
    print("只在他们=%d 只在我和=%d 交集=%d" % (len(only_t), len(only_m), len(tkeys & mkeys)))
    for x in sorted(only_t)[:8]:
        print("   只他们 @%d 面板%d %s" % x)
    for x in sorted(only_m)[:8]:
        print("   只我和 @%d 面板%d %s" % x)
    bad = 0
    for x in theirs:
        k = (int(x["run"]), int(x["panel_turn"]), x["file"])
        r = mine.get(k)
        if r and int(x["d_mot"]) != r["d"]:
            bad += 1
    print("交集内 Δ 不一致=%d" % bad)


MODES = {"ledger": s_ledger, "cell": s_cell, "join": s_join}
if __name__ == "__main__":
    rows = ledger(packets())
    mode = sys.argv[1] if len(sys.argv) > 1 else "ledger"
    if mode == "all":
        for f in (s_ledger, s_cell, s_join):
            print("\n===== %s =====" % f.__name__)
            f(rows)
    else:
        MODES[mode](rows)
