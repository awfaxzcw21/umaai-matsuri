#!/usr/bin/env python3
"""上限（`max_*` / `default_max_*`）逐报文轨迹 —— 找「哪一步把上限抬起来的」。

从仓根跑：

    python tools/cap_trace.py                # 全部 run
    python tools/cap_trace.py --run 170      # 只看某个 run
    python tools/cap_trace.py --turns 31 55  # 只看这几个回合
    python tools/cap_trace.py --value        # 再折成評価点（送分 / 带宽分）

一手来源 = `E:/HJM/jidianbeiAI0.22/out/hachimi_raw` 的 msgpack 报文（只读）。
时间序用 mtime（`pt_ledger_scan.sort_key` 的口径），会话键 = (chara_id, start_time)。

口径：一个 response 报文一个快照。上限变化 = 本快照 vs **上一快照** 的 `max_*` /
`default_max_*` 差分，并带上「触发它的那条 request」（channel + event_id / command_*）。
不改任何配置、不算任何拟合系数。
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import statistics as st
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402

AXES = ("speed", "stamina", "power", "guts", "wiz")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# chara_info 的轴名是 power，master.mdb 用的是 pow；这里只读报文，按报文键取。
REQ_KEYS = ("event_id", "command_type", "command_id", "command_group_id", "select_id")


def req_desc(common_by_file):
    """把触发请求压成一串可读标记。"""
    if common_by_file is None:
        return "-"
    ch, common = common_by_file
    bits = []
    if ch:
        bits.append(ch)
    for k in REQ_KEYS:
        v = common.get(k)
        if v not in (None, "", 0):
            bits.append(f"{k}={v}")
    return " ".join(bits) if bits else "-"


def caps(ci, prefix):
    return [ci.get(f"{prefix}_{a}") for a in AXES]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="", help="只报这个 run 前缀（如 170）")
    ap.add_argument("--turns", nargs="*", default=[], help="只报这些回合附近的抬升")
    ap.add_argument("--raw", default=PS.RAW)
    ap.add_argument("--csv", default="", help="把逐次抬升导成 CSV（复核用）")
    ap.add_argument("--value", action="store_true",
                    help="把每次抬升折成評価点（送分 / 带宽分），用 data/constants.json 的现成表")
    args = ap.parse_args()
    only_turns = {int(t) for t in args.turns}

    files = sorted(glob.glob(os.path.join(args.raw, "*.bin")), key=PS.sort_key)
    last_req = None            # (channel, common) 最近一条局内请求
    ev_story = {}              # (event_id, chara_id) -> story_id（来自宣告报文）
    prev = None                # 上一快照
    prev_key = None
    events = collections.defaultdict(list)   # run_key -> [抬升记录]
    starts = {}                # run_key -> (初始 max, 初始 default, fans)
    stats = collections.Counter()

    for path in files:
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            stats["decode_fail"] += 1
            continue
        if not isinstance(d, dict):
            continue
        if name.endswith("_notify_request.bin"):
            hit = next((k for k in d if k in PS.CHANNELS), None)
            last_req = (PS.CHANNELS.get(hit, ""), d[hit] or {}) if hit else None
            continue
        if not name.endswith("_notify_response.bin"):
            continue
        data = d.get("data") or {}
        for ann in data.get("unchecked_event_array") or []:
            if isinstance(ann, dict) and ann.get("event_id") is not None:
                ev_story[(ann.get("event_id"), ann.get("chara_id"))] = ann.get("story_id")
        ci = PS.get_ci(data)
        if ci is None or ci.get("turn") is None:
            continue
        key = (ci.get("single_mode_chara_id"), ci.get("start_time"))
        run = f"{key[1]:%Y-%m-%d %H:%M:%S}" if hasattr(key[1], "strftime") else str(key)
        run = str(key[0]) + "@" + run
        if args.run and args.run not in run:
            prev, prev_key = None, None
            continue
        cur = {
            "turn": ci.get("turn"),
            "max": caps(ci, "max"),
            "dmax": caps(ci, "default_max"),
            "stats": [ci.get(a) for a in AXES],
            "nick": ci.get("nickname_id_array") or [],
            "fans": ci.get("fans"),
            "succ": (ci.get("succession_trained_chara_id_1"),
                     ci.get("succession_trained_chara_id_2")),
            # 因子数组只在事件响应里非空（对照见 docs/继承事件一手_20261103.md §4）
            "nfac": sum(len(a.get("factor_info_array") or [])
                        for a in data.get("event_effected_factor_array") or []),
            "file": name,
        }
        if key not in starts:
            starts[key] = cur
            stats["runs"] += 1
        if prev is not None and prev_key == key:
            dm = [b - a for a, b in zip(prev["max"], cur["max"])]
            dd = [b - a for a, b in zip(prev["dmax"], cur["dmax"])]
            if any(dm) or any(dd):
                ds = [b - a for a, b in zip(prev["stats"], cur["stats"])]
                gained = [n for n in cur["nick"] if n not in prev["nick"]]
                rc = last_req[1] if last_req else {}
                events[key].append(
                    {"turn": cur["turn"], "file": cur["file"], "dm": dm, "dd": dd,
                     "ds": ds, "nick": gained, "stats": prev["stats"],
                     "nfac": cur["nfac"],
                     "succ": cur["succ"],
                     "story": ev_story.get((rc.get("event_id"), rc.get("chara_id"))),
                     "max": cur["max"], "dmax": cur["dmax"],
                     "fans": cur["fans"], "pfans": prev["fans"], "req": last_req}
                )
        prev, prev_key = cur, key

    print(f"== 上限轨迹（{stats['runs']} 局）==")
    print("  dm = max_* 差分   dd = default_max_* 差分（dd 非 0 ⇒ 改的是**默认**上限）")
    print("  ds = 同一条报文里的五维差分（速/耐/力/根/智）")
    allrows = []
    for key, evs in sorted(events.items(), key=lambda kv: str(kv[0])):
        run = f"chara{key[0]} start{key[1]}"
        print(f"\n-- {run} --   起点 max={starts[key]['max']} default={starts[key]['dmax']}")
        for e in evs:
            if only_turns and e["turn"] not in only_turns:
                continue
            head = [m - s for m, s in zip(e["max"], e["stats"])]
            dfans = (e["fans"] or 0) - (e["pfans"] or 0)
            print(f"  t{e['turn']:>3} {e['file']}  dd={e['dd']}  fans {e['pfans']}→{e['fans']}({dfans:+})"
                  f"  因子={e['nfac']}")
            print(f"        dm={e['dm']}  ds={e['ds']}  合计 ds={sum(e['ds'])} dm={sum(e['dm'])}")
            print(f"        抬前 現値={e['stats']} 上限={start_max_of(e)} 余裕={head}")
            print(f"        あだ名追加={e['nick']}  触发: {req_desc(e['req'])}"
                  f"  story_id={e['story']}  继承={e['succ']}")
            allrows.append({"run": run, "chara": key[0], "start": key[1], "turn": e["turn"],
                            "file": e["file"], "story": e["story"], "succ": e["succ"],
                            "dm": e["dm"], "dd": e["dd"], "ds": e["ds"], "nfac": e["nfac"],
                            "stats": e["stats"], "max_before": start_max_of(e)})

    print("\n== 汇总：同一通道发了多少（速/耐/力/根/智）==")
    print("  run            turn story_id      继承对        因子 ds(五维)              dm(上限)")
    for r in sorted(allrows, key=lambda r: (r["turn"], r["run"])):
        print(f"  {r['run']:<14} t{r['turn']:<4} {str(r['story']):<13} "
              f"{str(r['succ'][0]) + '/' + str(r['succ'][1]):<12} "
              f"{r['nfac']:<5}{str(r['ds']):<22} {r['dm']}")

    # §4 的相关系数：因子条数 vs 抬幅。只是「一起动」，不是公式（语义未核，别当输入用）。
    fac = [r["nfac"] for r in allrows]
    dms = [sum(r["dm"]) for r in allrows]
    dss = [sum(r["ds"]) for r in allrows]

    def pear(x, y):
        if len(x) < 2:
            return float("nan")
        mx, my = st.mean(x), st.mean(y)
        num = sum((a - mx) * (b - my) for a, b in zip(x, y))
        den = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
        return num / den if den else float("nan")

    if len(allrows) >= 2:
        print(f"\n== §4 相关（n={len(allrows)}，只是同向变动，不构成公式）==")
        print(f"  r(因子条数, Σdm) = {pear(fac, dms):+.3f}   r(因子条数, Σds) = {pear(fac, dss):+.3f}")
        print(f"  因子条数范围 {min(fac)}–{max(fac)}   Σdm {min(dms)}–{max(dms)}   Σds {min(dss)}–{max(dss)}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["run", "start_time", "turn", "file", "story_id",
                        "succ_1", "succ_2", "n_fac",
                        "ds_speed", "ds_stamina", "ds_power",
                        "ds_guts", "ds_wiz", "dm_speed", "dm_stamina", "dm_power",
                        "dm_guts", "dm_wiz", "max_before", "stats_before"])
            for r in sorted(allrows, key=lambda r: (r["run"], r["turn"])):
                w.writerow([r["chara"], r["start"], r["turn"], r["file"], r["story"],
                            r["succ"][0], r["succ"][1], r["nfac"], *r["ds"], *r["dm"],
                            "|".join(map(str, r["max_before"])),
                            "|".join(map(str, r["stats"]))])
        print(f"\nTSV/CSV -> {args.csv}")
    if args.value:
        const = json.load(open(os.path.join(REPO, "data", "constants.json"), encoding="utf-8"))
        tbl = const["five_status_final_score"]

        def score(v):
            return tbl[v if v <= 1200 else 2 * v - 1200]

        runs = collections.defaultdict(lambda: [0, 0])
        print("\n== 折成評価点（送分腿 / 带宽分腿；口径 = data/constants.json 的 five_status_final_score）==")
        print("  送分   = Σ score(現値 + ds) − score(現値)        ← 抬前现值定价，边际**下限**")
        print("  带宽分 = Σ score(min(上限+dm,2500)) − score(min(上限,2500))  ← 假设空间填满，**乐观上限**")
        print("  ⚠ 两条腿不可相加（未证不重复计入），也不含適性 / ヒント腿 ⇒ 整条通道是下界")
        for r in sorted(allrows, key=lambda r: (r["run"], r["turn"])):
            gift = sum(score(b + g) - score(b) for b, g in zip(r["stats"], r["ds"]))
            band = sum(score(min(mb + d, 2500)) - score(min(mb, 2500))
                       for mb, d in zip(r["max_before"], r["dm"]))
            runs[r["run"]][0] += gift
            runs[r["run"]][1] += band
            print(f"  {r['run']:<14} t{r['turn']:<4} 送分 +{gift:<6} 带宽 +{band}")
        print("\n  -- 每局（= 两次之和）--")
        tg = tb = 0
        for run, (gift, band) in sorted(runs.items()):
            tg += gift
            tb += band
            print(f"  {run:<14} 送分 {gift:<7} 带宽 {band}")
        n = len(runs)
        print(f"\n  合计：送分 {tg}（每局均值 {tg / n:.0f}）  带宽 {tb}（每局均值 {tb / n:.0f}）  n={n} 局")
        print(f"  范围：送分 {min(v[0] for v in runs.values())}–{max(v[0] for v in runs.values())}"
              f"  带宽 {min(v[1] for v in runs.values())}–{max(v[1] for v in runs.values())}")

    print("\n说明：同一回合内多条报文会全部列出；`dd` 全 0 而 `dm` 非 0 ⇒ 上限抬走的是效果通道。")


def start_max_of(e):
    """抬升前的上限 = 抬后的 max − dm（省一次快照传递）。"""
    return [m - d for m, d in zip(e["max"], e["dm"])]


if __name__ == "__main__":
    main()
