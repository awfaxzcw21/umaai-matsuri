#!/usr/bin/env python3
"""#21 影子模式验收：把面板 `out/_shadow_log.jsonl` 聚成分歧率读数。

面板每回合无条件落一行「屏幕上真显示的建议 vs 引擎结论」（`blackboard_panel.py::_shadow_record`），
本脚本只做聚合，不改面板任何东西。

    python tools/shadow_divergence.py                       # 读面板默认路径
    python tools/shadow_divergence.py --log <某个 jsonl>     # 读轮转出去的旧日志
    python tools/shadow_divergence.py --tsv out/divergence.tsv

**判分歧只看 `agree_top`**：`agree` 比的是 `matsuri_ai` 的裸结论，而屏幕上那句话是
`_unified_line` 过了合宿/体力/出行闸门之后的，两者不是一回事（面板注释里同一条口径）。

分局靠「回合回落」切：同一局内 turn 单调不减，`turn <= 上一行` 即视为新的一局开始。
这是近似——中途翻回合再翻回来会多算一行，所以每局只报行数与覆盖回合，不报「满局」。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

PANEL_LOG = "E:/HJM/jidianbeiAI0.22/out/_shadow_log.jsonl"
START_STAMP = "E:/HJM/jidianbeiAI0.22/out/_shadow_log.START.json"

#: 面板口径的回合分段（合宿/收尾期都按面板号，不是引擎号）
BANDS = ((1, 12, "开局 1-12"), (13, 36, "合宿1-2 13-36"), (37, 60, "中盘 37-60"),
         (61, 72, "合宿3-收尾前 61-72"), (73, 78, "收尾期 73-78"))


def family(label: str) -> str:
    """动作大类：训练/稽古/休息/外出/比赛/其他。"""
    s = (label or "").strip()
    for k in ("训练", "稽古", "休息", "外出", "比赛", "友人", "理事长"):
        if s.startswith(k):
            return k
    return s.split()[0] if s else "空"


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print("  ⚠ 第 %d 行不可解析，跳过：%s" % (ln, e), file=sys.stderr)
    return rows


def split_games(rows: list[dict]) -> list[list[dict]]:
    games, cur, prev = [], [], 0
    for r in rows:
        t = int(r.get("turn") or 0)
        if cur and t <= prev:
            games.append(cur)
            cur = []
        cur.append(r)
        prev = t
    if cur:
        games.append(cur)
    return games


def band(turn: int) -> str:
    for lo, hi, name in BANDS:
        if lo <= turn <= hi:
            return name
    return "越界 %d" % turn


def rate(n: int, d: int) -> str:
    return "%d/%d = %.1f%%" % (n, d, 100.0 * n / d) if d else "0/0 = —"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=PANEL_LOG)
    ap.add_argument("--tsv", help="把分歧样本另落一份 TSV")
    args = ap.parse_args()

    if not os.path.isfile(args.log):
        print("影子日志不存在：%s（面板还没跑过？）" % args.log)
        return 2
    rows = load(args.log)
    print("影子日志：%s｜行数 %d" % (args.log, len(rows)))
    if os.path.isfile(START_STAMP) and os.path.abspath(args.log) == os.path.abspath(PANEL_LOG):
        # 侧车只描述「当前这轮」，读旧日志时不能拿来标后端版本
        st = json.load(open(START_STAMP, encoding="utf-8"))
        print("  本轮后端：commit %s｜built %s｜scenario 表 sha1 %s"
              % (st.get("backend_commit", "?")[:7], st.get("backend_built_at", "?"),
                 st.get("rust_data_scenario_sha1", "?")))
        print("  口径：%s" % st.get("panel_default", "?"))
    if not rows:
        print("  空文件——打几局再来。")
        return 0

    games = split_games(rows)
    print("  分局（按回合回落切，近似）：%d 段｜每段行数 %s"
          % (len(games), [len(g) for g in games]))

    n_top = sum(1 for r in rows if r.get("agree_top"))
    n_rule = sum(1 for r in rows if r.get("agree"))
    n_took = sum(1 for r in rows if r.get("engine_took"))
    print("\n== 总口径 ==")
    print("  agree_top（屏幕真显示 vs 引擎）：%s ⇒ **分歧率 %.1f%%**"
          % (rate(n_top, len(rows)), 100.0 * (1 - n_top / len(rows))))
    print("  agree（规则裸结论 vs 引擎）    ：%s（只作参照，不是判据）" % rate(n_rule, len(rows)))
    print("  engine_took（引擎实际接管）    ：%s" % rate(n_took, len(rows)))

    print("\n== 分回合段 ==")
    print("%-22s %8s %10s %10s" % ("段", "行数", "agree_top", "分歧率"))
    by_band = collections.defaultdict(list)
    for r in rows:
        by_band[band(int(r.get("turn") or 0))].append(r)
    for _, _, name in BANDS:
        rs = by_band.get(name) or []
        if not rs:
            continue
        ok = sum(1 for r in rs if r.get("agree_top"))
        print("%-22s %8d %10s %9.1f%%" % (name, len(rs), rate(ok, len(rs)),
                                          100.0 * (1 - ok / len(rs))))
    extra = [k for k in by_band if k not in {b[2] for b in BANDS}]
    for k in sorted(extra):
        rs = by_band[k]
        ok = sum(1 for r in rs if r.get("agree_top"))
        print("%-22s %8d %10s %9.1f%%" % (k, len(rs), rate(ok, len(rs)),
                                          100.0 * (1 - ok / len(rs))))

    print("\n== 分歧落在哪类动作上（屏幕选的大类 → 引擎选的大类）==")
    pairs = collections.Counter()
    for r in rows:
        if not r.get("agree_top"):
            pairs[(family(r.get("top_pick")), family(r.get("engine")))] += 1
    if not pairs:
        print("  无分歧样本。")
    for (a, b), c in pairs.most_common(12):
        print("  %-10s → %-10s %d 次" % (a or "空", b, c))

    print("\n== 预算与形态 ==")
    print("  ply 分布：%s｜rollouts 中位 %s"
          % (dict(collections.Counter(r.get("ply") for r in rows)),
             sorted(r.get("rollouts") or 0 for r in rows)[len(rows) // 2]))
    print("  顶栏空句（引擎没返回/闸门吞掉）：%d 行"
          % sum(1 for r in rows if not (r.get("top_pick") or "").strip()))

    div = [r for r in rows if not r.get("agree_top")]
    if div:
        print("\n== 分歧样本（最近 15 条）==")
        print("%-6s %-14s %-14s %s" % ("回合", "屏幕选", "引擎选", "该回合体力/上限"))
        for r in div[-15:]:
            print("%-6s %-14s %-14s %s/%s" % (r.get("turn"), r.get("top_pick") or "—",
                                              r.get("engine") or "—",
                                              r.get("vital"), r.get("max_vital")))
    if args.tsv:
        with open(args.tsv, "w", encoding="utf-8") as f:
            f.write("turn\ttop_pick\tengine\tvital\tmax_vital\trollouts\tply\n")
            for r in div:
                f.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\n"
                        % (r.get("turn"), r.get("top_pick"), r.get("engine"),
                           r.get("vital"), r.get("max_vital"), r.get("rollouts"), r.get("ply")))
        print("\n分歧样本 TSV -> %s（%d 条）" % (args.tsv, len(div)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
