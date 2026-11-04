#!/usr/bin/env python3
"""#69 出处层：给包级台账里每一跳 干劲 找一个报文里的**号**。

母口径与 `mot69_packet_recheck.py` 同一条（复用它的 `ci_of` / `order_key`），只加两件事：
1. 只连**同一局内**的相邻包（跨局边界不当一跳——那是上一局的末包，见下）；
2. 每跳按优先序归一个出处，并把四套号的增删都留在列里，优先序盖住的共存可复核。

出处优先序：`settle_cmd > event_consumed > effect_added > effect_removed > turn_cross > none`
  settle_cmd      该包带 `command_result.command_id`
  event_consumed  `data.unchecked_event_array` 里上一包在、本包没了的号（＝本包被消费）
  effect_added    `chara_info.chara_effect_id_array` 本包新增的号
  effect_removed  同上集合本包消失的号
  turn_cross      这一跳跨面板
  none            都没有（比赛格内的瞬时爬升族，见 §3A.10-R）

`ev_cleared_detail` 形如 `21256:c0/s809052001/t6` ＝ 事件号:chara_id/story_id/play_timing。

用法：
  python -u tools/mot69_packet_src.py emit [outfile]   # 落 CSV 并打表（默认 out/mot_69/fact_packet_src_regen.csv）
  python -u tools/mot69_packet_src.py diff             # 与已登记的 fact_packet_src.csv 逐格对
  python -u tools/mot69_packet_src.py why              # 只查被跨局边界污染的那两跳

只读 `E:/HJM/jidianbeiAI0.22/out/hachimi_raw`；不碰引擎、不写 `data/`、无拟合常数。
"""
import os
import sys
import csv
import sqlite3
import collections

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml          # noqa: E402
import mot69_packet_recheck as mp  # noqa: E402

RAW = mp.RAW
MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
CAMP_EVENTS = {215016, 215017, 215018}
COLS = ["run", "panel_turn", "prev_turn", "d_mot", "mot_prev", "mot", "src", "src_id",
        "ev_cleared", "ev_cleared_detail", "ev_appear", "eff_added", "eff_removed",
        "this_cmd", "this_state", "prev_cmd", "prev_state", "in_finale", "camp_left",
        "race", "camp_ev", "prev_vital", "vital", "file"]
FINALE = range(73, 79)


def ids(lst):
    """`unchecked_event_array` → {event_id: 'event_id:c../s../t..'}（同包重复号后见覆盖先见）。"""
    out = {}
    for e in (lst or []):
        if isinstance(e, dict) and isinstance(e.get("event_id"), int):
            eid = e["event_id"]
            out[eid] = "%d:c%s/s%s/t%s" % (eid, e.get("chara_id", 0),
                                           e.get("story_id", 0), e.get("play_timing", 0))
    return out


def effs(ci):
    a = ci.get("chara_effect_id_array")
    return {x for x in a if isinstance(x, int)} if isinstance(a, list) else set()


def scan():
    """一遍解码，按采集序返回带出处字段的包序列（母体＝同 `mp.packets()` 的过滤条件）。"""
    files = [f for f in os.listdir(RAW) if f.endswith("_notify_response.bin")]
    files.sort(key=mp.order_key)
    ps = []
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
        ci = mp.ci_of(data)
        if not isinstance(ci, dict) or ci.get("turn") is None or ci.get("scenario_id") != 15:
            continue
        m = ci.get("motivation")
        if not isinstance(m, int):
            continue
        cr = data.get("command_result")
        cr = cr if isinstance(cr, dict) else {}
        fi = (data.get("festa_data_set") or {}).get("festa_info") or {}
        cl = fi.get("summer_camp_remain_turn_num")
        ps.append({
            "file": f, "run": ci.get("single_mode_chara_id"), "turn": ci["turn"], "mot": m,
            "cmd": cr.get("command_id") if isinstance(cr.get("command_id"), int) else None,
            "state": cr.get("result_state") if isinstance(cr.get("result_state"), int) else None,
            "ev": ids(data.get("unchecked_event_array")),
            "eff": effs(ci),
            "vital": ci.get("vital") if isinstance(ci.get("vital"), int) else None,
            "camp_left": cl if isinstance(cl, int) else 0,
            "race": any(k in data for k in mp.RACE_KEYS),
        })
    return ps


def jumps(ps):
    rows = []
    prev = None
    for p in ps:
        if prev is not None and prev["run"] == p["run"]:
            d = p["mot"] - prev["mot"]
            if d == 0:
                prev = p
                continue
            cleared = sorted(set(prev["ev"]) - set(p["ev"]))
            appear = sorted(set(p["ev"]) - set(prev["ev"]))
            added = sorted(p["eff"] - prev["eff"])
            removed = sorted(prev["eff"] - p["eff"])
            if p["cmd"] is not None:
                src, src_id = "settle_cmd", str(p["cmd"])
            elif cleared:
                src, src_id = "event_consumed", str(cleared[0])
            elif added:
                src, src_id = "effect_added", str(added[0])
            elif removed:
                src, src_id = "effect_removed", str(removed[0])
            elif p["turn"] != prev["turn"]:
                src, src_id = "turn_cross", "0"
            else:
                src, src_id = "none", "0"
            rows.append({
                "run": p["run"], "panel_turn": p["turn"], "prev_turn": prev["turn"],
                "d_mot": d, "mot_prev": prev["mot"], "mot": p["mot"],
                "src": src, "src_id": src_id,
                "ev_cleared": "|".join(str(x) for x in cleared),
                "ev_cleared_detail": "|".join(prev["ev"][x] for x in cleared),
                "ev_appear": "|".join(str(x) for x in appear),
                "eff_added": "|".join(str(x) for x in added),
                "eff_removed": "|".join(str(x) for x in removed),
                "this_cmd": "" if p["cmd"] is None else p["cmd"],
                "this_state": "" if p["state"] is None else p["state"],
                "prev_cmd": "" if prev["cmd"] is None else prev["cmd"],
                "prev_state": "" if prev["state"] is None else prev["state"],
                "in_finale": 1 if p["turn"] in FINALE else 0,
                "camp_left": p["camp_left"],
                "race": 1 if p["race"] else 0,
                "camp_ev": 1 if (set(cleared) & CAMP_EVENTS) else 0,
                "prev_vital": prev["vital"], "vital": p["vital"], "file": p["file"],
            })
        prev = p
    return rows


def s_emit(path):
    rows = jumps(scan())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("写出 %s：%d 跳，ΣΔ=%+d" % (path, len(rows), sum(r["d_mot"] for r in rows)))
    agg = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        a = agg[r["src"]]
        a[0] += 1
        a[1] += r["d_mot"] > 0
        a[2] += r["d_mot"] < 0
        a[3] += r["d_mot"]
    print("%-16s 跳    涨   跌   ΣΔ" % "出处")
    for k in sorted(agg, key=lambda k: -agg[k][0]):
        print("%-16s %4d %4d %4d %5d" % (k, *agg[k]))
    has_id = sum(1 for r in rows if r["src"] in ("settle_cmd", "event_consumed"))
    print("有具体号=%d / %d = %.1f%%；净非零且完全无号=%d 跳"
          % (has_id, len(rows), 100.0 * has_id / len(rows),
             sum(1 for r in rows if r["src"] in ("none", "turn_cross")
                 and not (r["ev_cleared"] or r["ev_appear"] or r["eff_added"] or r["eff_removed"])
                 and r["d_mot"] != 0)))
    ev = [r for r in rows if r["src"] == "event_consumed"]
    up = collections.Counter(r["src_id"] for r in ev if r["d_mot"] > 0)
    dn = collections.Counter(r["src_id"] for r in ev if r["d_mot"] < 0)
    tot = collections.Counter(r["src_id"] for r in ev)
    s = collections.defaultdict(int)
    for r in ev:
        s[r["src_id"]] += r["d_mot"]
    mixed = [i for i in tot if up[i] and dn[i]]
    print("事件号=%d 个：只涨 %d／只跌 %d／混号 %d %s"
          % (len(tot), sum(1 for i in tot if up[i] and not dn[i]),
             sum(1 for i in tot if dn[i] and not up[i]), len(mixed), mixed))
    print("%-8s %4s %5s %4s %4s" % ("号", "跳", "ΣΔ", "涨", "跌"))
    for i in sorted(tot, key=lambda i: (-abs(s[i]), -tot[i])):
        print("%-8s %4d %5d %4d %4d" % (i, tot[i], s[i], up[i], dn[i]))
    print("\n=== 合宿开始号（camp_ev）===")
    for r in rows:
        if r["camp_ev"]:
            print("  @%d 面板%d %d->%d %+d 体力 %s->%s %s" % (
                r["run"], r["panel_turn"], r["mot_prev"], r["mot"], r["d_mot"],
                r["prev_vital"], r["vital"], r["src_id"]))
    print("\n=== race 包（§3A.10-R）===")
    cell = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        k = (r["run"], r["panel_turn"])
        c = cell[k]
        c[0] += r["d_mot"] > 0
        c[1] += r["d_mot"] < 0
        c[2] += r["d_mot"]
    race_runs = collections.defaultdict(list)
    for r in rows:
        if r["race"]:
            race_runs[r["run"]].append((r["panel_turn"], r["d_mot"]))
    for run in sorted(race_runs):
        for turn, d in sorted(race_runs[run]):
            c = cell[(run, turn)]
            print("  @%d 面板%d 跌跳 %+d ⇒ 同格 涨%d 跌%d 净%+d" % (run, turn, d, c[0], c[1], c[2]))


def s_item95(path="E:/HJM/umaai-matsuri/out/mot_69/fact_packet_src_regen.csv"):
    """§3A.10-R 的两条一手：`none` 族爬升腿是否一律挂 `user_item{95,N}` 且 N 逐跳 −1；
    回吐腿是否确实是赛果结算包。只解码用到的包。"""
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    cell = collections.defaultdict(list)
    for r in rows:
        if r["src"] == "none":
            cell[(int(r["run"]), int(r["panel_turn"]))].append(r)
    print("none 族 %d 跳 / %d 格" % (sum(len(v) for v in cell.values()), len(cell)))
    bad_item = bad_seq = bad_race = 0
    for k in sorted(cell):
        seq = sorted(cell[k], key=lambda r: os.path.getmtime(os.path.join(RAW, r["file"])))
        ns, flags = [], []
        for r in seq:
            d = ml.decode_file(os.path.join(RAW, r["file"]))
            ui = d.get("data", {}).get("user_item")
            ns.append(ui.get("number") if isinstance(ui, dict) else None)
            if isinstance(ui, dict) and ui.get("item_id") != 95:
                bad_item += 1
            if r["race"] == "1":
                dd = d.get("data", {})
                flags.append(tuple(x in dd for x in
                                   ("race_reward_info", "add_trophy_info", "live_reward")))
                if not all(f in dd for f in ("race_reward_info", "add_trophy_info")):
                    bad_race += 1
        ups = [n for r, n in zip(seq, ns) if int(r["d_mot"]) > 0]
        drops = [n for r, n in zip(seq, ns) if int(r["d_mot"]) < 0]
        ok = all(b - a == -1 for a, b in zip(ups, ups[1:]))
        bad_seq += 0 if ok else 1
        print("  @%d 面板%d 涨%s 跌%s 爬升 N 序列=%s 逐跳−1=%s 回吐包标志=%s"
              % (k[0], k[1], sum(1 for r in seq if int(r["d_mot"]) > 0),
                 sum(-int(r["d_mot"]) for r in seq if int(r["d_mot"]) < 0),
                 ups, ok, flags or "—"))
    print("⇒ 非 95 号的包=%d；爬升 N 不逐跳 −1 的格=%d；回吐包缺赛果字段=%d"
          % (bad_item, bad_seq, bad_race))
    c = sqlite3.connect(MDB)
    q = c.execute("select id,item_category,effect_type_1,effect_target_1,effect_value_1,"
                  "effect_type_2,effect_value_2 from item_data where id=95").fetchall()
    n34 = c.execute("select count(*) from item_data where item_category=34").fetchone()[0]
    print("表侧 `item_data` id=95 → %s；category 34 全表行数=%d" % (q, n34))


def s_diff(path="E:/HJM/umaai-matsuri/out/mot_69/fact_packet_src.csv"):
    mine = {(int(r["run"]), int(r["panel_turn"]), r["file"]): r for r in jumps(scan())}
    theirs = list(csv.DictReader(open(path, encoding="utf-8")))
    tk = {(int(x["run"]), int(x["panel_turn"]), x["file"]) for x in theirs}
    mk = set(mine)
    print("我=%d 他们=%d 交集=%d 只我=%d 只他们=%d"
          % (len(mk), len(tk), len(mk & tk), len(mk - tk), len(tk - mk)))
    for x in sorted(mk - tk)[:6]:
        print("   只我 @%d 面板%d %s src=%s d=%s" % (*x, mine[x]["src"], mine[x]["d_mot"]))
    for x in sorted(tk - mk)[:6]:
        print("   只他们 @%d 面板%d %s" % x)
    bad = collections.Counter()
    for y in theirs:
        k = (int(y["run"]), int(y["panel_turn"]), y["file"])
        r = mine.get(k)
        if not r:
            continue
        for c in COLS:
            if str(r[c]) != str(y[c]):
                bad[c] += 1
    print("交集内逐列不一致：%s" % (dict(bad) or "无（220 行 × 24 列全等）"))


def s_why():
    ps = scan()
    print("母体包=%d；每局首包（跳过）：" % len(ps))
    seen = set()
    for i, p in enumerate(ps):
        if p["run"] in seen:
            continue
        seen.add(p["run"])
        prev = ps[i - 1] if i else None
        if prev:
            print("  @%d 面板%d mot=%s ← 上一包 @%s 面板%d mot=%s（跨局，不连）"
                  % (p["run"], p["turn"], p["mot"], prev["run"], prev["turn"], prev["mot"]))


MODES = {"emit": s_emit, "diff": s_diff, "why": s_why, "item95": s_item95}
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "emit"
    if mode == "emit":
        s_emit(sys.argv[2] if len(sys.argv) > 2
               else "E:/HJM/umaai-matsuri/out/mot_69/fact_packet_src_regen.csv")
    elif mode == "item95":
        s_item95(sys.argv[2] if len(sys.argv) > 2
                 else "E:/HJM/umaai-matsuri/out/mot_69/fact_packet_src_regen.csv")
    else:
        MODES[mode]()
