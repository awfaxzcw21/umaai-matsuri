#!/usr/bin/env python3
"""#21 影子模式验收：把面板 `out/_shadow_log.jsonl` 聚成分歧率读数。

面板每回合无条件落一行「规则线 / 引擎侧结论 / MCTS 计划」（`blackboard_panel.py::_shadow_record`），
本脚本只做聚合，不改面板任何东西。

    python tools/shadow_divergence.py                       # 读面板默认路径
    python tools/shadow_divergence.py --log <某个 jsonl>     # 读轮转出去的旧日志
    python tools/shadow_divergence.py --tsv out/divergence.tsv

**判分歧只看 `agree_top`**：`agree` 比的是 `matsuri_ai` 的裸结论，而屏幕上那句话是
`_unified_line` 过了合宿/体力/出行闸门之后的，两者不是一回事（面板注释里同一条口径）。

⚠ 但 `top_pick` 也**不等于**用户真看到的那句——2026-11-06 照 `_mc_poll` 源码核对出来的映射：
  · `rule`   = `matsuri_ai.recommend` 裸结论
  · `top`/`top_pick` = `Panel._unified_line(st)`（闸门后的规则线）
  · `engine` = `_shadow_record` 的第 5 个实参：主路径传 `mc_txt`=`ranked[0].label`（MCTS 结论），
    **live 分支传 `_lbl_live`=`live[0].label`**（实测净收益首位）——同一列有两种来源
  · 顶栏真显示：`live` 非空 ⇒ 就是这一行的 `engine`（面板无条件用 `live[0]` 覆盖，`engine_took` 恒真）；
    `live` 空 ⇒ 走主路径，`engine_took` 为真才是 `engine`(=MCTS)，为假则屏幕退回规则线
  所以 `agree_top` 读作「闸门后的规则线 vs 引擎侧结论」；而「引擎侧结论」在 live 非空时
  **按构造只会是训练**（`mccli.rs:301-329` 的 `live` 只从 `commands[].train` 生成，标签一律「训练 X」）
  ⇒ 稽古/休息/出行在 live 非空的回合结构上进不了顶栏。这条要单独统计，见输出的「顶栏真显示」段。

分局靠「回合回落」切：同一局内 turn 单调不减，`turn <= 上一行` 即视为新的一局开始。
这是近似——中途翻回合再翻回来会多算一行，所以每局只报行数与覆盖回合，不报「满局」。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

PANEL_LOG = "E:/HJM/jidianbeiAI0.22/out/_shadow_log.jsonl"
START_STAMP = "E:/HJM/jidianbeiAI0.22/out/_shadow_log.START.json"

#: 面板口径的回合分段（合宿/收尾期都按面板号，不是引擎号）
BANDS = ((1, 12, "开局 1-12"), (13, 36, "合宿1-2 13-36"), (37, 60, "中盘 37-60"),
         (61, 72, "合宿3-收尾前 61-72"), (73, 78, "收尾期 73-78"))

#: 「稽古·<轴>」是同一个训练格换名，不是另一个动作：
#:   · 合宿期五个训练全变稽古（面板 534 行 + 命令号 5101~5105）
#:   · 非合宿期写「训练 稽古·X」的也是同格
#: 不换算会把「同格同意」数成分歧——本局实测虚高 7 行/34。
_AXES = ("速度", "耐力", "力量", "根性", "智力")


def canon(label: str) -> str:
    """把两侧标签折算成可比的动作身份。"""
    s = (label or "").strip()
    m = re.search(r"稽古[·]?(%s)" % "|".join(_AXES), s)
    if m and ("合宿" in s or s.startswith("训练")):
        return "训练 " + m.group(1)
    if s.startswith("友人") or s.startswith("出行"):
        return "出行"
    return s.split("（")[0].strip()


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


def verify_blindness(rows: list[dict]) -> None:
    """#76 改判的两条可证伪预测，只对**最后一局**（= 下一局开跑后自然变成新局）出判定。

    P1 合宿换名：合宿行里规则线的 `稽古·X` 与引擎侧的 `训练 X` 是同一格
       ⇒ 折算后这些行该判成一致；若某合宿行两侧轴不同，那是真分歧不是换名。
    P2 顶栏构造盲区：`live` 非空 ⇒ 那一行的 `engine`（=顶栏）按构造只能是训练
       ⇒ 若出现 `live` 非空而 `engine` 不是训练的行，本推断**被推翻**。
    顺带量「MCTS 想非训练却被 live 顶掉」的手数——这是改判后唯一还有代价的部分。
    """
    n_camp = n_camp_same = 0
    n_live = 0
    violated = []
    mcts_wanted_other = []
    for r in rows:
        rule, eng = canon(r.get("top_pick")), canon(r.get("engine"))
        live = r.get("live") or []
        plan = r.get("plan") or []
        m0 = canon((plan[0].get("action") if plan else "") or "")
        if (r.get("camp_remain") or 0) > 0 and "稽古" in (r.get("top_pick") or ""):
            n_camp += 1
            if rule == eng:
                n_camp_same += 1
        if live:
            n_live += 1
            if not (eng or "").startswith("训练"):
                violated.append((r.get("turn"), eng, live[0]))
            if m0 and not m0.startswith("训练") and m0 != eng:
                mcts_wanted_other.append((r.get("turn"), m0, eng, r.get("camp_remain")))
    print("\n== #76 改判验证（最后一局 %d 手）==" % len(rows))
    print("  P1 合宿换名：合宿稽古行 %d，折算后同格 %d ⇒ 换名解释覆盖 %.0f%%"
          % (n_camp, n_camp_same, 100.0 * n_camp_same / n_camp if n_camp else 0.0))
    print("     若这里不满 100%%，差额就是**真分歧**（轴不同），不是命名问题")
    print("  P2 构造盲区：live 非空 %d 行；其中顶栏不是训练的 %d 行 ⇒ %s"
          % (n_live, len(violated), "预测被推翻，`live` 不止训练" if violated else "预测成立（构造上只能给训练）"))
    for t, e, l0 in violated[:5]:
        print("     ✗ t=%s 顶栏=%s live[0]=%s" % (t, e, l0))
    if mcts_wanted_other:
        print("  MCTS 想非训练、却被 live 顶掉：%d 手" % len(mcts_wanted_other))
        for t, m0, e, cr in mcts_wanted_other[:10]:
            print("     t=%-4s MCTS=%-12s 顶栏=%-10s camp_remain=%s" % (t, m0, e, cr))
    else:
        print("  MCTS 想非训练、却被 live 顶掉：0 手（本局两侧本来就同向）")


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
    print("  agree_top（规则线 vs 引擎侧结论）：%s ⇒ **分歧率 %.1f%%**"
          % (rate(n_top, len(rows)), 100.0 * (1 - n_top / len(rows))))
    print("  agree（规则裸结论 vs 引擎侧结论）：%s（只作参照，不是判据）" % rate(n_rule, len(rows)))
    print("  engine_took（引擎实际接管顶栏）  ：%s" % rate(n_took, len(rows)))
    n_c = sum(1 for r in rows if canon(r.get("top_pick")) == canon(r.get("engine")))
    print("  合宿稽古/同格换名折算后一致      ：%s ⇒ **真分歧率 %.1f%%**（字面那个偏高 %d 行）"
          % (rate(n_c, len(rows)), 100.0 * (1 - n_c / len(rows)), n_c - n_top))

    # 顶栏真显示口径：live 非空 ⇒ 顶栏 = live[0]，而 live[] 按构造只可能是「训练 X」
    n_live = sum(1 for r in rows if (r.get("live") or []))
    live_fam = collections.Counter(family((r.get("live") or [""])[0])
                                   for r in rows if (r.get("live") or []))
    # 规则线想稽古：`合宿稽古 …` 与 `稽古 …` 都算（family 把前者单列，这里按子串并回来）
    n_keiko_rule = sum(1 for r in rows if "稽古" in (r.get("top_pick") or ""))
    plan0_fam = collections.Counter(family((r.get("plan") or [{}])[0].get("action")) for r in rows)
    print("\n== 顶栏真显示（live 结构盲区）==")
    print("  live 非空的行：%d/%d ⇒ 这些回合顶栏按构造只能是 %s"
          % (n_live, len(rows), "、".join("%s×%d" % (k, v) for k, v in live_fam.most_common()) or "无"))
    print("  规则线想稽古的行：%d/%d；MCTS ranked[0] 的大类：%s"
          % (n_keiko_rule, len(rows),
             "、".join("%s×%d" % (k, v) for k, v in plan0_fam.most_common())))

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

    print("\n== 分歧落在哪类动作上（规则线大类 → 引擎侧大类）==")
    pairs = collections.Counter()
    for r in rows:
        a, b = canon(r.get("top_pick")), canon(r.get("engine"))
        if a != b:
            pairs[(family(a), family(b))] += 1
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

    div = [r for r in rows if canon(r.get("top_pick")) != canon(r.get("engine"))]
    if div:
        print("\n== 真分歧样本（最近 15 条，已折算同格换名）==")
        print("%-6s %-14s %-14s %-12s %s"
              % ("回合", "规则线", "引擎侧结论", "MCTS榜首", "体力/上限"))
        for r in div[-15:]:
            _p0 = ((r.get("plan") or [{}])[0] or {}).get("action") or "—"
            print("%-6s %-14s %-14s %-12s %s/%s"
                  % (r.get("turn"), r.get("top_pick") or "—", r.get("engine") or "—", _p0,
                     r.get("vital"), r.get("max_vital")))
    verify_blindness(games[-1] if games else [])
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
