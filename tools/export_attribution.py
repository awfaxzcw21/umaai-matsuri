#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出「逐源归因」给复盘线消费（契约 v1）。

为什么要这份导出：复盘方案 `review_improvement_plan_2026-11-06.md` §4.3 第 4 条写
「没有分环节归因」——这半边我们**已经做出来了**（#80，`docs/PT缺口逐源_20261106.md`），
所以不该让他们再造一遍。本脚本把两侧同口径的产物拼成一个文件：

  · **真机侧**：`E:/HJM/_pttrace/windows.csv`（一手报文，R1–R8 归因规则的输入）
  · **引擎侧**：插桩 `MC_DUMP` 的 `ptrace` 列（同一套窗口口径，`tools/engine_pt_ledger.py`）

回合号换算**必须显式**（这是踩过的坑）：报文 `current_turn` 是**面板口径**（1 基），
引擎 `ptrace` 的 turn 是**引擎口径**（0 基），关系＝**引擎＝面板−1**
（一手锚点自证：cap-10 锚点 `_state_t30_cap10.json` 的 `turn=30`，
 而该局 ptrace 第一条落在 turn **29**）。导出里两边都另给 `turn0`＝面板−1，统一成 0 基。

**校准状态照写**：引擎现在有三处已知未修偏差，任何「你本可以拿 X 分」都不能直接吃这份数（见 JSON 的
`calibration.doNotUseFor`）。这不是客套——今天刚量到关软上限会让稽古单手率**更差**，
说明这台模拟器对「该怪谁」还没有发言权。

只读输入，产物落 `out/attribution/`。不写 `data/`、不动引擎、不动复盘线那三个仓。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import engine_pt_ledger as EL  # noqa: E402  引擎侧归因（src_of）
import pt_ledger as PL  # noqa: E402  真机侧归因（source_of，R1–R8 唯一实现）

PTTRACE = "E:/HJM/_pttrace"
SCHEMA_VERSION = 1
OUT_DIR = os.path.join(ROOT, "out", "attribution")

# R1–R8 的中文源名 → 复盘线好懂的 endpoint 别名
ENDPOINT = {
    "训练手": "exec_command",
    "稽古/演目": "exec_command",
    "外出": "exec_command",
    "比赛": "race",
    "发表会": "check_point",
    "事件": "check_event",
    "支出": "skill_purchase",
    "其他命令": "other",
    "未归因": "unattributed",
}


def real_windows(run_like, turn_from):
    """真机侧一手窗口，带 R1–R8 源名。归因**不重写**——直接调 `pt_ledger.source_of`。"""
    if not os.path.isdir(PTTRACE):
        return []
    rows, race_turns = PL.load()
    race_ids, _, _ = PL.race_ids_by_rate(rows, race_turns)
    out = []
    for r in rows:
        if not r["run"] or r["same_run"] != "1" or r["d"] is None or r["T"] is None:
            continue                      # 跨局窗口会把上一局的 PT 当成扣除（已知陷阱）
        if run_like and run_like not in r["run"]:
            continue
        if r["T"] < turn_from:            # r["T"]＝post 快照回合＝面板口径
            continue
        out.append({
            "turn0": r["T"] - 1, "turnPanel": r["T"],
            "source": PL.source_of(r, race_ids),
            "endpoint": ENDPOINT.get(PL.source_of(r, race_ids), "other"),
            "dPt": r["d"], "dSkills": EL.num(r.get("d_skills")),
            "prePt": EL.num(r.get("pre_pt")), "postPt": EL.num(r.get("post_pt")),
            "channel": r["channel"], "commandId": EL.num(r.get("cid")),
            "eventId": EL.num(r.get("event_id")), "run": r["run"], "file": r.get("file"),
        })
    return out


def engine_by_turn(paths):
    """引擎侧：300 局的 ptrace 窗口按 (turn0, 源) 汇总成**局均**参考轨迹（含局均窗口数）。"""
    tot = defaultdict(float)
    cnt = defaultdict(int)
    n = 0
    for p in paths:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            tr = rec.get("ptrace")
            if not tr:
                sys.exit("%s：该行没有 ptrace 列——用错 exe（需要插桩的 matsuri-dist）" % p)
            n += 1
            for row in tr:
                turn, stage, fam, pre, post = row[0], row[1], row[2], row[3], row[4]
                race = row[5] if len(row) > 5 else 0
                d = post - pre
                if d > 0:
                    k = (turn, EL.src_of(stage, fam, race))
                    tot[k] += d
                    cnt[k] += 1
    n = max(1, n)
    rows = [{"turn0": t, "turnPanel": t + 1, "source": s,
             "endpoint": ENDPOINT.get(s, "other"),
             "windowsPerGame": round(c / n, 3),
             "ptMeanPerGame": round(v / n, 3)} for (t, s), v in sorted(tot.items())
            for c in [cnt[(t, s)]]]
    return rows, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps", nargs="*",
                    default=[os.path.join(ROOT, "out", "ptledger", "dump_s*.jsonl")])
    ap.add_argument("--run", default="170", help="真机侧局号（子串匹配，空＝全要）")
    ap.add_argument("--turn-from", type=int, default=30, help="面板口径起始回合（含）")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "attribution_v1.json"))
    args = ap.parse_args()

    paths = []
    for pat in args.dumps:
        if "*" in pat:
            import glob
            paths += sorted(glob.glob(pat))
        elif os.path.exists(pat):
            paths.append(pat)
    if not paths:
        sys.exit("找不到插桩 dump（%s）——先跑 #80 那批" % args.dumps)

    rw = real_windows(args.run, args.turn_from)
    eb, n_games = engine_by_turn(paths)

    def agg(rows, key):
        """`nWindows`＝该源全部窗口（含 0 差分，复盘线要的是覆盖率）；
        `nPositive`＝正差分窗口，等于 #80 表里的「手数」，两个别混。"""
        s = defaultdict(lambda: [0, 0, 0.0])
        for r in rows:
            s[r[key]][0] += 1
            if r["dPt"] > 0:
                s[r[key]][1] += 1
            s[r[key]][2] += r["dPt"]
        return {k: {"nWindows": v[0], "nPositive": v[1], "pt": round(v[2], 1),
                    "ptPerPositive": round(v[2] / v[1], 2) if v[1] else 0}
                for k, v in s.items()}

    real_src = agg(rw, "source")
    eng_src = defaultdict(lambda: [0, 0.0])
    for r in eb:
        eng_src[r["source"]][0] += r["windowsPerGame"]
        eng_src[r["source"]][1] += r["ptMeanPerGame"]

    doc = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedBy": "tools/export_attribution.py",
        "sourceDocs": ["docs/PT缺口逐源_20261106.md", "docs/命令号两族更正_20261106.md"],
        "turnConvention": {
            "unit": "turn0 = 0 基 = 面板回合 − 1 = 引擎内部 turn",
            "why": "cap-10 锚点 _state_t30_cap10.json 的 turn=30（面板），该局 ptrace 第一条落 turn 29",
            "realField": "turnPanel（报文 current_turn 原值）",
            "engineField": "ptrace 第 0 列（已是 0 基）",
        },
        "attributionRules": {
            "source": "真机侧 R1–R8 见 tools/pt_ledger.py 顶部 docstring；引擎侧由插桩 stage/fam/race 映射，"
                      "结构对应但**不是逐字照抄**（引擎侧没有 cid/event_id 判据，隐藏事件被折进比赛腿）",
            "windowCaliber": "一个窗口＝相邻两个局内请求之间的全部 PT 差分，整笔记给该窗口的请求（含同窗口自动加项）",
        },
        "calibration": {
            "status": "未校准——这份数据可以当**描述**，不可以当**玩家责任**",
            "knownEngineDeviations": [
                "PT gross −17.1%（真机同段 6,486 vs 引擎 5,062.9，t30 起、cap-10 锚点）",
                "稽古单手率 72.8/手 vs 真机 121.3/手（＝缺口的 77%）；软上限已实测排除（关掉反降到 66.8）",
                "引擎里没有『技能清单』这个对象：技能分只由存量 PT 过曲线（mod.rs:1717），"
                "而真实技能分有 40% 不吃 PT（金技能 26.7% + 固有系 13.3%）",
            ],
            "doNotUseFor": [
                "『你本可以拿 X 分』/理论可达上限——会把引擎偏差洗成玩家损失",
                "regret 的绝对值（同随机流对照的相对差可以，绝对期望分不行）",
                "任何需要技能清单级粒度的判据（引擎侧根本没有那个对象）",
            ],
        },
        "inputs": {
            "engineDumps": [os.path.basename(p) for p in paths],
            "engineGames": n_games,
            "enginePolicy": "MC_POLICY=search MC_K=64，cap-10 锚点 _state_t30_cap10.json，t30 起跑",
            "realRunFilter": args.run,
            "realWindowsKept": len(rw),
        },
        "perSource": {
            "real": real_src,
            "engine": {k: {"nWindows": v[0], "ptMeanPerGame": round(v[1], 1)}
                       for k, v in sorted(eng_src.items())},
        },
        "referenceTrajectory": {
            "engineByTurnSource": eb,
            "note": "300 局局均、按 (turn0, 源) 展开——这就是复盘线要的『参考轨迹』的引擎侧版本；"
                    "真机侧同形状见 realWindows",
        },
        "realWindows": rw,
    }

    # 自检（导出的东西必须能自己发现自己错）：两侧各自加总必须回到 #80 的已知总量
    eng_sum = sum(r["ptMeanPerGame"] for r in eb)
    real_sum = sum(w["dPt"] for w in rw if w["dPt"] > 0)
    print()
    print("== 导出自检 ==")
    print("  引擎 Σ(局均正差分) %.1f（#80 基线 5,062.9 ⇒ 差 %+.1f）" % (eng_sum, eng_sum - 5062.9))
    print("  真机 Σ(正差分)    %.1f（#80 基线 6,486.0 ⇒ 差 %+.1f）" % (real_sum, real_sum - 6486.0))
    for name, got, want in (("引擎", eng_sum, 5062.9), ("真机", real_sum, 6486.0)):
        if abs(got - want) > 0.5:
            print("  ⚠ %s 侧加总对不上 #80 基线，导出**不要**给复盘线用（先查归因）" % name)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    print("== 导出完成 %s" % args.out)
    print("  真机窗口 %d 条（run~'%s'，面板 turn≥%d，仅 same_run）｜引擎 %d 局 × %d 个 (turn,源) 桶"
          % (len(rw), args.run, args.turn_from, n_games, len(eb)))
    print("  引擎逐源（局均 PT）：")
    for k, v in sorted(eng_src.items(), key=lambda kv: -kv[1][1]):
        print("    %-10s 窗口/局 %6.2f  PT/局 %8.1f  单手率 %6.1f"
              % (k, v[0], v[1], v[1] / v[0] if v[0] else 0))
    if real_src:
        print("  真机逐源（该段合计 PT）：")
        for k, v in sorted(real_src.items(), key=lambda kv: -kv[1]["pt"]):
            print("    %-10s 窗口 %4d／正 %3d  PT=%8.1f  每正手 %6.1f"
                  % (k, v["nWindows"], v["nPositive"], v["pt"], v["ptPerPositive"]))
    else:
        print("  ⚠ 真机侧没带 source 列（windows.csv 是归因**前**的原始窗口表；"
              "逐源请读 ledger_summary.csv 或先跑 tools/pt_ledger.py）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
