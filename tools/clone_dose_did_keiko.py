"""#68 第 1 步：稽古行的「两腿恒等式」逐格核验（只读报文，不碰引擎、不写 data/）。

#56 §4 留下的话是「稽古行的面板自报 stat_gains ＝ 普通训练预告 ＋ 该板自报演目加项的**和**」，
并据此说稽古行要先拆项才能进 DiD。本脚本把这句话拆成**三条**逐格残差来判，因为「和」这个说法
里混了两件事：面板自报的是哪条腿，与实到等于什么，是**不同**的等式。

  R1  面板自报[axis] − home_info[510x][axis]            ← 面板那条数到底是哪条腿
  R2  面板自报[axis] − (home[axis] + festa[axis])       ← #56 那句「和」若成立应为 0
  R3  实到[axis]     − (home[axis] + festa[axis])       ← 真正的两腿恒等式（#34 在 PT 上 227/229）

其中（口径全部照 `tools/keiko_curve.py` / `tools/keiko_two_leg_identity.py`，不新造读法）：
  - 普通训练腿（home 腿）＝ 板包 `home_info.command_info_array[cid].params_inc_dec_info_array`
    的 target_type 1..5（10＝体力、30＝技能Pt 一并核）。
  - 演目腿（festa 腿）＝ `festa_data_set.command_info_array[cid]` 同键。
  - 面板自报 ＝ `out/clone_states/*.json` 里该格的 `stat_gains`（由面板 `live_reader.pick_state`
    从同一个板包产出，`packet_to_state.py` 的产物）。
  - 实到 ＝ 同一 `(run, turn)` 窗口内「最后一个带 510x 稽古板的报文」→「其后第一个带
    `command_result` 的报文」的 `chara_info` 五轴差分；**只在 `command_result.command_id == 该格 cid`
    的手上比**（否则板只是显示过，差分来自别的命令）。

判据（跑前先写死，不许事后挑）：
  - R1 每轴逐格全 0 ⇒ 面板那条数**就是 home 腿本身**，#56「面板自报是两者之和」被否证。
  - R3 每轴逐格 0 的比例 ≥ 95%（余下按失败手/钳位单列，不摊派）⇒ 两腿恒等式成立，稽古行可进 DiD。
  - 任一不满足 ⇒ 停在第 1 步，不做第 2 步。

    python tools/clone_dose_did_keiko.py identity            # 三条残差全量
    python tools/clone_dose_did_keiko.py identity --noscan    # 只跑 R1/R2（不扫 10,583 个报文）
"""

import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
STATES = "out/clone_states"
AXES = ["速度", "耐力", "力量", "根性", "智力"]
TT = {1: "速度", 2: "耐力", 3: "力量", 4: "根性", 5: "智力", 10: "体力", 30: "技能Pt"}
KEIKO_IDS = {5101, 5102, 5103, 5104, 5105}
#: 轴 → chara_info 的五维字段与上限字段（与 `E:/HJM/_festa/extract_festa2.py` 同一套）
PKFIELD = {"速度": "speed", "耐力": "stamina", "力量": "power", "根性": "guts", "智力": "wiz"}
CAPFIELD = {k: "max_" + v for k, v in PKFIELD.items()}


def params_of(cmd):
    """报文条目 → {target_type: 值}；同 target_type 多行按求和（与 keiko_curve.leg_map 同口径）。"""
    acc = {}
    for e in (cmd or {}).get("params_inc_dec_info_array") or []:
        if not isinstance(e, dict):
            continue
        tt = e.get("target_type")
        if tt is None:
            continue
        acc[tt] = acc.get(tt, 0) + (e.get("value") or 0)
    return acc


def legs_of_board(data):
    """板包 → {cid: (home_params, festa_params)}，只留稽古条目（510x）。"""
    home = {c.get("command_id"): c for c in
            ((data.get("home_info") or {}).get("command_info_array") or [])
            if isinstance(c, dict) and c.get("command_id") in KEIKO_IDS}
    festa = {c.get("command_id"): c for c in
             ((data.get("festa_data_set") or {}).get("command_info_array") or [])
             if isinstance(c, dict) and c.get("command_id") in KEIKO_IDS}
    return {cid: (params_of(home.get(cid)), params_of(festa.get(cid)))
            for cid in set(home) | set(festa)}


def load_index():
    with open(os.path.join(STATES, "_index.csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def dist(vals):
    """一维残差的分布：n / 逐格为 0 的占比 / 均值 / 中位 / 最小 / 最大 / 众数前三。"""
    if not vals:
        return None
    z = sum(1 for v in vals if v == 0)
    top = Counter(vals).most_common(3)
    return dict(n=len(vals), zero_pct=100.0 * z / len(vals), mean=statistics.fmean(vals),
                median=statistics.median(vals), lo=min(vals), hi=max(vals),
                modes="/".join(f"{v}x{c}" for v, c in top))


def report(name, rows_by_axis, note=""):
    print(f"\n### {name}  {note}")
    print(f"{'轴':<8}{'n':>6}{'逐格=0':>9}{'均值':>9}{'中位':>7}{'最小':>7}{'最大':>7}  众数")
    for ax in rows_by_axis:
        d = dist(rows_by_axis[ax])
        if not d:
            print(f"{ax:<8}    0")
            continue
        print(f"{ax:<8}{d['n']:>6}{d['zero_pct']:>8.1f}%{d['mean']:>9.2f}{d['median']:>7.1f}"
              f"{d['lo']:>7}{d['hi']:>7}  {d['modes']}")


def stage_r1_r2(only_clone=False):
    """面板自报 vs 两条腿：用 303 块 state 的**同一个板包**，逐格。"""
    r1 = defaultdict(list)   # panel - home
    r2 = defaultdict(list)   # panel - (home + festa)
    r1c = defaultdict(list)  # 只在带分身槽的稽古格上
    r2c = defaultdict(list)
    ncells = nclone = nohome = 0
    per_cell_rows = []
    for rec in load_index():
        st = json.load(open(rec["file"], encoding="utf-8"))
        try:
            data = ml.decode_file(RAW + rec["pack"])["data"]
        except Exception:
            continue
        legs = legs_of_board(data)
        for c in st.get("commands") or []:
            if not c.get("is_keiko"):
                continue
            cid = c.get("command_id")
            sub = c.get("sub_partner_ids") or []
            ncells += 1
            if sub:
                nclone += 1
            if cid not in legs:
                nohome += 1
                continue
            home, festa = legs[cid]
            gains = c.get("stat_gains") or {}
            row = dict(board=rec["file"], run=rec["run"], turn=rec["turn"], cid=cid,
                       n_clone=len(sub))
            for tt, ax in TT.items():
                p = gains.get(ax, (c.get("vital_delta") if ax == "体力"
                                   else c.get("skill_point") if ax == "技能Pt" else 0)) or 0
                h, f = home.get(tt, 0), festa.get(tt, 0)
                row[f"{ax}_panel"] = p
                row[f"{ax}_home"] = h
                row[f"{ax}_festa"] = f
                (r1c if sub else r1)[ax].append(p - h)
                (r2c if sub else r2)[ax].append(p - h - f)
            per_cell_rows.append(row)
    print(f"state 里稽古格 {ncells} 个（带分身槽 {nclone}）｜板包里取不到 510x 条目的 {nohome} 个")
    report("R1 不带分身槽的稽古格：面板自报 − home 腿", r1)
    report("R1 带分身槽的稽古格：面板自报 − home 腿", r1c)
    report("R2 不带分身槽的稽古格：面板自报 − (home + festa)", r2)
    report("R2 带分身槽的稽古格：面板自报 − (home + festa)", r2c)
    with open("out/_keiko_identity_cells_20261105.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(per_cell_rows[0].keys()))
        w.writeheader()
        w.writerows(per_cell_rows)
    print(f"\n逐格明细 → out/_keiko_identity_cells_20261105.csv（{len(per_cell_rows)} 行）")
    return r1, r2


def scan_posts(limit=40):
    """每块 state 板往后找「第一个带 `command_result` 的同 (run,turn) 报文」＝结算后快照。

    不靠「最后一个带板的报文」当 pre：有些重复板包只带条目不带预告（面板把那种行当占位跳过），
    拿它当 pre 会得到 home=festa=0 的假「恒等式成立」。这里 pre 一律用 `_index.csv` 选定的那块板
    （= state 的来源，R1 已证它的 home 腿逐格等于面板自报），post 只从它**之后**的报文里取。
    """
    files = sorted(f for f in os.listdir(RAW) if f.endswith(".bin"))
    pos = {f: i for i, f in enumerate(files)}
    out, scanned = {}, 0
    for rec in load_index():
        p = pos.get(rec["pack"])
        if p is None:
            continue
        run, turn = int(rec["run"]), int(rec["turn"])
        for f in files[p + 1:p + 1 + limit]:
            try:
                data = ml.decode_file(RAW + f)["data"]
            except Exception:
                continue
            scanned += 1
            ci = data.get("chara_info")
            if not isinstance(ci, dict):
                continue
            if ci.get("single_mode_chara_id") != run or ci.get("turn") != turn:
                break  # 窗口不跨回合
            cr = data.get("command_result")
            if isinstance(cr, dict) and cr.get("command_id") is not None:
                out[(run, turn)] = (f, ci, cr)
                break
    print(f"303 块板里找到结算后快照的 (run,turn) {len(out)}｜向前解码 {scanned} 个报文")
    return out


def stage_r3(posts):
    r3 = defaultdict(list)          # axis -> 实到 − (home + festa)，全档合并
    r3b = defaultdict(list)         # axis -> 实到 − home（＝面板那条数 vs 实到）
    by_state = defaultdict(lambda: defaultdict(list))   # r_state -> axis -> residual
    buckets = Counter()
    rowsout = []
    for rec in load_index():
        key = (int(rec["run"]), int(rec["turn"]))
        got = posts.get(key)
        if not got:
            continue
        rf, ci, cr = got
        cid = cr.get("command_id")
        st = json.load(open(rec["file"], encoding="utf-8"))
        cell = next((c for c in st.get("commands") or [] if c.get("command_id") == cid), None)
        if cell is None or not cell.get("is_keiko"):
            continue
        data = ml.decode_file(RAW + rec["pack"])["data"]
        home, festa = legs_of_board(data).get(cid, ({}, {}))
        row = dict(run=key[0], turn=key[1], cid=cid, board_pack=rec["pack"], result_pack=rf,
                   r_state=cr.get("result_state"),
                   n_clone=len(cell.get("sub_partner_ids") or []))
        for tt, ax in TT.items():
            if ax == "体力":
                before, after = st.get("vital"), ci.get("vital")
            elif ax == "技能Pt":
                before, after = st.get("skill_point"), ci.get("skill_point")
            else:
                before, after = (st.get("stats") or {}).get(ax), ci.get(PKFIELD[ax])
            if before is None or after is None:
                continue
            actual, h, f = after - before, home.get(tt, 0), festa.get(tt, 0)
            res = actual - h - f
            row[f"{ax}_home"], row[f"{ax}_festa"] = h, f
            row[f"{ax}_got"], row[f"{ax}_resid"] = actual, res
            row[f"{ax}_cap_hit"] = int(ax in CAPFIELD and after == ci.get(CAPFIELD[ax]))
            if res != 0:
                buckets["非零残差"] += 1
                buckets["非零且该轴顶到上限"] += int(row[f"{ax}_cap_hit"])
                if res > 0:
                    buckets["非零且为正"] += 1
            r3[ax].append(res)
            by_state[str(row["r_state"])][ax].append(res)
            r3b[ax].append(actual - h)
        rowsout.append(row)
    print(f"逐格可核的稽古手 {len(rowsout)}（其中带分身槽的格 "
          f"{sum(1 for r in rowsout if r['n_clone'])}）")
    for state in sorted(by_state):
        n = len(by_state[state][AXES[0]]) if AXES[0] in by_state[state] else 0
        report(f"R3 实到 − (home + festa)　r_state={state}　手 n={n}", by_state[state])
    zh = Counter(str(r["r_state"]) for r in rowsout
                 if all(int(r[f"{ax}_got"]) == 0 for ax in AXES))
    if zh:
        print("实到五轴全为 0 的手（＝这一手没给任何属性，按结果态单列）："
              + "｜".join(f"r_state={k}×{v}" for k, v in zh.most_common()))
    report("R3 实到 − (home + festa)　【合并全部 r_state】", r3)
    print(f"非零残差 {buckets['非零残差']} 个，其中该轴结算后**顶到上限**的 "
          f"{buckets['非零且该轴顶到上限']} 个")
    report("参考：实到 − home（面板那条数 vs 实到，差的正是演目腿）", r3b)
    if rowsout:
        with open("out/_keiko_identity_hands_20261105.csv", "w",
                  newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rowsout[0].keys()))
            w.writeheader()
            w.writerows(rowsout)
        print(f"逐手明细 → out/_keiko_identity_hands_20261105.csv（{len(rowsout)} 行）")
    return r3, buckets, by_state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", nargs="?", default="identity")
    ap.add_argument("--noscan", action="store_true", help="只跑 R1/R2，不找结算后快照")
    args = ap.parse_args()
    if args.stage != "identity":
        raise SystemExit(__doc__)
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    r1, r2 = stage_r1_r2()
    ok1 = all(dist(v) and dist(v)["zero_pct"] == 100.0 for v in r1.values() if v)
    print(f"\n判定 R1（面板自报 == home 腿）：{'逐格全等 ⇒ #56「面板自报是两者之和」被否证' if ok1 else '存在非零残差'}")
    if args.noscan:
        return 0
    r3, buckets, by_state = stage_r3(scan_posts())
    main_state = max(by_state, key=lambda s: len(by_state[s].get(AXES[0], [])))
    five = {ax: d for ax in AXES if (d := dist(by_state[main_state][ax]))}
    worst = min(d["zero_pct"] for d in five.values())
    vital = dist(by_state[main_state]["体力"])
    tot = buckets["非零残差"]
    cov = 100.0 * buckets["非零且该轴顶到上限"] / tot if tot else 1.0
    print(f"\n判定 R3（实到 == home + festa，主结果态 r_state={main_state}）："
          f"五轴最差逐格为 0 的比例 = {worst:.1f}%（阈值 95%）⇒ "
          f"{'成立' if worst >= 95 else '不成立'}"
          f"｜体力 {vital['zero_pct']:.1f}%（不进五轴判据）"
          f"｜全表非零残差 {tot} 个里顶到上限的 {cov:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
