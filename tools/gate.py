#!/usr/bin/env python3
"""三分量门禁：把 `matsuri-dist` 的预测与一局真实养成的终局数值逐项对照。

为什么不用 `real_score` 一栏了（2026-10-31）：
    `real_score = 五维分(真表) + 1.0746 × round(PT × 2.35)`，其中 **2.35 是为凑 72,142
    反解出来的拟合系数**，不是量出来的机制。拿它当门禁，机制改对了分也未必对，机制改错了
    分也可能被系数带偏。所以门禁改成三个**可独立核对**的分量：
        五维和 / 五维分(游戏原生表) / PT
    Σgrade 在引擎里不存在，只能从游戏技能清单直接数；本工具只把它当"实测输入"，不折算。

用法：
    python tools/gate.py <dist.json> --five 1998,1470,1656,1344,1573 --pt 8339 --grade 17022
    python tools/gate.py <dist.json> --five ... --pt ... --stat-score 42201   # 不导入 score.py

退出码：0 正常；2 输入不合法。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

DEFAULT_SCORE_PY = r"E:\HJM\jidianbeiAI0.22\score.py"


def load_status_points():
    """从黑板 `score.py` 取游戏原生五维分表；不可用时返回 None。"""
    path = os.environ.get("MATSURI_SCORE_PY", DEFAULT_SCORE_PY)
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("_bb_score", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "status_points", None)


def fmt(v: float) -> str:
    return f"{v:,.0f}"


def row(name: str, sim, real, note: str = "") -> str:
    mean = sim["mean"]
    median = sim["median"]
    p10, p90 = sim["p10"], sim["p90"]
    if real is None:
        return (
            f"| {name} | {fmt(mean)} | {fmt(median)} | {fmt(p10)}~{fmt(p90)} "
            f"| — | — | {note} |"
        )
    d = mean - real
    pct = (mean / real - 1.0) * 100.0 if real else 0.0
    return (
        f"| {name} | {fmt(mean)} | {fmt(median)} | {fmt(p10)}~{fmt(p90)} "
        f"| {fmt(real)} | {d:+,.0f} ({pct:+.1f}%) | {note} |"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三分量门禁对照")
    ap.add_argument("dist_json", help="matsuri-dist 输出 json")
    ap.add_argument("--five", help="实际终局五维，逗号分隔（速,耐,力,根,智）")
    ap.add_argument("--pt", type=int, help="实际终局技能点（獲得スキルPt）")
    ap.add_argument("--grade", type=int, help="Σgrade。**必须注明来源**：①实测＝在游戏里数技能清单；"
                                               "②反解＝(評価点 − 五维分) ÷ 1.0746（依赖拟合系数 b，不是实测）")
    ap.add_argument("--grade-src", choices=["measured", "backslash"], dest="grade_src",
                    default=None, help="Σgrade 来源：measured=数技能清单 / backslash=反解")
    ap.add_argument("--stat-score", type=int, dest="stat_score",
                    help="实际五维分；不给则用 --five 现算（需 score.py）")
    args = ap.parse_args(argv)

    try:
        with open(args.dist_json, encoding="utf-8") as fh:
            dist = json.load(fh)
    except OSError as exc:
        print(f"读不到 dist json: {exc}", file=sys.stderr)
        return 2
    gate = dist.get("gate")
    if not gate:
        print("dist json 里没有 `gate` 字段 —— 用的还是旧版 matsuri-dist，先重新 build。",
              file=sys.stderr)
        return 2

    five = None
    if args.five:
        try:
            five = [int(x) for x in args.five.replace("，", ",").split(",") if x.strip()]
        except ValueError:
            print("--five 格式应为 5 个整数，如 1998,1470,1656,1344,1573", file=sys.stderr)
            return 2
        if len(five) != 5:
            print(f"--five 需要 5 个值，收到 {len(five)} 个", file=sys.stderr)
            return 2

    stat_score = args.stat_score
    if stat_score is None and five is not None:
        sp = load_status_points()
        if sp is not None:
            stat_score = sp(five)
        else:
            print("⚠ 找不到 score.py，五维分跳过（可加 --stat-score 或设 MATSURI_SCORE_PY）",
                  file=sys.stderr)

    five_sum_real = sum(five) if five else None

    print(f"=== 三分量门禁 ===")
    print(f"dist: {args.dist_json}  turn={dist.get('turn')}  N={dist.get('n')}")
    print()
    print("| 分量 | 模拟 mean | 模拟 median | 模拟 p10~p90 | 实际 | 差 | 备注 |")
    print("|---|---|---|---|---|---|---|")
    print(row("五维和", gate["five_sum"], five_sum_real))
    print(row("五维分", gate["stat_score"], stat_score, "游戏原生表，无拟合"))
    print(row("PT", gate["pt"], args.pt))
    print()
    print("Σgrade：", "-" if args.grade is None else f"{args.grade:,}"
          + {"measured": "（实测：数技能清单）",
             "backslash": "（⚠ 反解：(評価点 − 五维分) ÷ 1.0746，依赖拟合系数 b，不是实测）",
             None: "（⚠ 未注明来源——请用 --grade-src 说明是实测还是反解）"}[args.grade_src])
    print("模拟 grade_est（折算 ×%.2f，**不可当真值**）：%s"
          % (gate.get("grade_per_pt_used", 0.0), fmt(gate["grade_est"]["mean"])))
    if args.grade is not None:
        print("→ 技能分（硬，两次相减、不依赖任何系数）：实际 %.0f vs 模拟 %.0f"
              % (args.grade * 1.0746, gate["grade_est"]["mean"] * 1.0746))
        print("   ⚠ 但 Σgrade 本身若为反解，则它依赖 b=1.0746（也是拟合系数，RMSE 316）。"
              "只有「数技能清单」得到的 Σgrade 才是实测。")
    print()
    print("注：模拟是 %d 次 run_full_game 的分布，实际通常只有 1 局。"
          "均值差的绝对值可以看，\"系统性偏差\"这种结论要等更多真实局。" % dist.get("n", 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
