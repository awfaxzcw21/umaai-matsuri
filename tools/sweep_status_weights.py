#!/usr/bin/env python3
"""`status_weights` 旋钮的 CRN 扫描：既答「门禁分不分得出」（分辨率），也答
「判官量的是产出还是定价」（分解），还能只跑没背过题的种子（holdout）。

权重住在 `crates/matsuri-core/src/config.rs` → `value.rs`（乘在五维增量分上），
所以改它确实改决策。

跑法需要一个可丢弃的实验壳（port 工作树的 tar 副本 + 分臂二进制 + trainer overlay）：

    S7X2_SHELL=E:/HJM/_s7x2 python tools/sweep_status_weights.py            # 探针原口径（seed 0/1/2）
    S7X2_SHELL=E:/HJM/_s7x2 python tools/sweep_status_weights.py --seeds 3,4,5 --tag sw2 --out sweep_weights_holdout.tsv

`--seeds` 默认 0/1/2 = 探针那组；#23 的 holdout 用 3,4,5（**不与探针同种子**，免背题）。
`--arms 智x2,耐x0.5` 只跑指定臂（base 永远跑，配对要用）。
overlay 是 **trainer 整段替换**，所以每臂只改 `status_weights` 一行、其余字节不动。
配对口径与 `docs/S7定价2x2_20261103.md` 的复跑段完全一致：同一 dist 二进制、`MC_N=4000`、
`MC_SEED` 各 0/1/2，判官只看「贴近幅度」95% 区间跨不跨 0。

红线：锚点真值目前高于所有臂的均值 ⇒ 凡抬高总产出的臂都会被判「更近」。
本脚本的输出只登记为**分辨率证明与方向线索**，不登记为旋钮成果。
"""

import argparse
import collections
import json
import os
import re
import statistics
import subprocess
import sys

SHELL = os.environ.get("S7X2_SHELL", r"E:/HJM/_s7x2")
EXP = os.path.join(SHELL, "_exp")
DIST = os.path.join(EXP, "dist_off.exe")
GATE = os.path.join(SHELL, "target", "release", "matsuri-gate.exe")
STATE = r"E:/HJM/umaai-matsuri/_state_t30.json"
TEMPLATE = os.path.join(EXP, "_overlay_f20.toml")
N = "4000"
SEEDS = (0, 1, 2)
TAG = "sw"
# 锚点 = run179/cid179 终局真值（五维和 8,041 / 五维分 42,201 / PT 8,339）
ANCHORS = ("1998,1470,1656,1344,1573", "8339", "42201")
ANCHOR_FIVE = [int(x) for x in ANCHORS[0].split(",")]
AXES = ("速", "耐", "力", "根", "智")
COMPONENTS = ("五维和", "五维分", "PT")


def arms(only=None):
    """一臂 = 标签 + 五维权重向量（出厂 = 全 1.0）。逐轴 ×0.5 / ×2 的坐标试探。"""
    out = [("base", (1.0,) * 5)]
    for i in range(5):
        for mult, tag in ((0.5, "x0.5"), (2.0, "x2")):
            w = [1.0] * 5
            w[i] = mult
            out.append((f"{AXES[i]}{tag}", tuple(w)))
    if only:
        want = set(only)
        out = [a for a in out if a[0] == "base" or a[0] in want]
    return out


def write_overlay(path, weights):
    src = open(TEMPLATE, encoding="utf-8").read()
    lit = "[" + ", ".join(f"{v:g}" for v in weights) + "]"
    out, n = re.subn(r"(?m)^status_weights = \[[^\]]*\]$", "status_weights = " + lit, src)
    if n != 1:
        raise SystemExit("overlay 模板里 status_weights 行匹配到 %d 处，改模板" % n)
    open(path, "w", encoding="utf-8").write(out)


def sample(tag, seed):
    dump = os.path.join(EXP, "%s_%s_s%d.jsonl" % (TAG, tag, seed))
    env = dict(os.environ, MC_N=N, MC_SEED=str(seed), MC_DUMP=dump)
    r = subprocess.run([DIST, STATE, os.path.join(EXP, "%s_%s_s%d.json" % (TAG, tag, seed))],
                       cwd=SHELL, env=env, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dump):
        raise SystemExit("采样失败 %s seed%d: %s" % (tag, seed, r.stderr[-300:]))
    return dump


def pair(a, b):
    r = subprocess.run([GATE, "--a", a, "--b", b,
                        "--five", ANCHORS[0], "--pt", ANCHORS[1],
                        "--stat-score", ANCHORS[2]],
                       cwd=SHELL, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("判官失败: " + r.stderr[-300:])
    got = {}
    for line in r.stdout.splitlines():
        if not line.startswith("| "):
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) < 13 or f[1] not in COMPONENTS:
            continue
        lo, hi = (float(x) for x in re.findall(r"-?\d+\.?\d*", f[9]))
        got[f[1]] = {"near": float(f[8].replace(",", "")), "lo": lo, "hi": hi,
                     "p": f[10], "win": f[11], "verdict": f[12]}
    if len(got) != 3:
        raise SystemExit("判官输出没抓全三个分量：" + r.stdout[:400])
    return got


def per_axis(dump):
    """逐局终局五轴 / 五维和 / 五维分均值（用来把「产出」和「配比」拆开）。"""
    rows = [json.loads(line) for line in open(dump, encoding="utf-8")]
    five = [[r["five"][i] for r in rows] for i in range(5)]
    return ([statistics.fmean(col) for col in five],
            statistics.fmean(sum(v) for v in zip(*five)),
            statistics.fmean(r["stat"] for r in rows))


def coverage_shape(five):
    """逐轴覆盖率 (m_i/anchor_i) 对平均覆盖率的绝对偏差和：小 = 配比更像锚点。

    ⚠️ 这个量**同样会被产出缺口污染**（垫底的轴一旦补上，偏差就缩小），
    所以它只用来判断「Δ五维和 是不是纯靠总量」，不能当定价结论。
    """
    ratios = [five[i] / ANCHOR_FIVE[i] for i in range(5)]
    return sum(abs(r - statistics.fmean(ratios)) for r in ratios)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", default="0,1,2", help="CRN 种子，逗号分隔（holdout 用 3,4,5）")
    ap.add_argument("--arms", default="", help="只跑这些臂（逗号分隔，base 永远跑）")
    ap.add_argument("--tag", default="sw", help="dump 前缀，换种子时另起一个避免覆盖探针数据")
    ap.add_argument("--out", default="sweep_weights_probe.tsv", help="TSV 文件名（写在壳的 _exp 下）")
    args = ap.parse_args(argv)

    global SEEDS, TAG
    SEEDS = tuple(int(x) for x in args.seeds.split(","))
    TAG = args.tag
    lst = arms(args.arms.split(",") if args.arms else None)
    n_cell = len(SEEDS)

    for exe in (DIST, GATE, STATE, TEMPLATE):
        if not os.path.exists(exe):
            raise SystemExit("缺文件：%s（壳路径用 S7X2_SHELL 覆盖）" % exe)

    dumps = {}
    for tag, w in lst:
        write_overlay(os.path.join(SHELL, "matsuri_config.toml"), w)
        for s in SEEDS:
            dumps[(tag, s)] = sample(tag, s)
        print("采样完成 %s %s" % (tag, w), flush=True)
    # 壳的配置恢复出厂，避免下一次跑在最后一个臂上
    write_overlay(os.path.join(SHELL, "matsuri_config.toml"), (1.0,) * 5)

    rows = []
    for tag, w in lst:
        if tag == "base":
            continue
        for s in SEEDS:
            g = pair(dumps[("base", s)], dumps[(tag, s)])
            for c in COMPONENTS:
                d = g[c]
                rows.append((tag, "[" + ",".join(f"{v:g}" for v in w) + "]", s, c,
                             d["near"], d["lo"], d["hi"], d["p"], d["win"], d["verdict"]))
        print("判读完成 %s" % tag, flush=True)

    tsv = os.path.join(EXP, args.out)
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("arm\tweights\tseed\tcomp\tnear\tci_lo\tci_hi\tp\twin\tverdict\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print("TSV -> " + tsv)

    # 产出 vs 配比分解：每臂跨种子合并逐轴 / 五维和 / 五维分均值
    agg5 = collections.defaultdict(list)
    agg5_stat = collections.defaultdict(list)
    for tag, _ in lst:
        for s in SEEDS:
            five, ssum, sstat = per_axis(dumps[(tag, s)])
            agg5[tag].append((five, ssum))
            agg5_stat[tag].append(sstat)
    ref5 = [statistics.fmean(x[0][i] for x in agg5["base"]) for i in range(5)]
    ref_sum = statistics.fmean(x[1] for x in agg5["base"])
    ref_stat = statistics.fmean(agg5_stat["base"])
    ref_shape = coverage_shape(ref5)
    print("base 逐轴均值 " + " ".join("%s=%.1f" % (AXES[i], ref5[i]) for i in range(5))
          + "  覆盖率 " + " ".join("%.3f" % (ref5[i] / ANCHOR_FIVE[i]) for i in range(5)))

    sig = lambda r: r[5] > 0 or r[6] < 0
    means = collections.defaultdict(lambda: collections.defaultdict(list))
    cnt = collections.Counter()
    tot = collections.Counter()
    for r in rows:
        means[r[0]][r[3]].append(r[4])
        tot[r[0]] += 1
        if sig(r):
            cnt[r[0]] += 1

    print("\n=== 每臂跨种子均值：贴近幅度（正=更近）‖ 产出/配比分解 ===")
    print("Δ产出 = 该臂五维和均值 − base；分解列 = 判官读数能不能用「总量多了」解释掉")
    print("臂        五维和    五维分      PT  |Δ产出   Δ和−Δ产出  Δ分−Δ五维分  覆盖率偏差  显著/%d 全绿"
          % n_cell)
    summary = []
    for tag, _ in lst:
        if tag == "base":
            continue
        m = {c: sum(v) / len(v) for c, v in means[tag].items()}
        arm5 = [statistics.fmean(x[0][i] for x in agg5[tag]) for i in range(5)]
        arm_sum = statistics.fmean(x[1] for x in agg5[tag])
        arm_stat = statistics.fmean(agg5_stat[tag])
        d_out = arm_sum - ref_sum
        d_stat = arm_stat - ref_stat
        shape = coverage_shape(arm5)
        green = cnt[tag] == tot[tag] and all(min(means[tag][c]) > 0 for c in means[tag])
        print("%-9s %+8.1f %+9.1f %+7.1f  %6.1f   %+8.1f   %+9.1f    %.3f→%.3f   %d/%d  %s"
              % (tag, m["五维和"], m["五维分"], m["PT"], d_out,
                 m["五维和"] - d_out, m["五维分"] - d_stat,
                 ref_shape, shape, cnt[tag], tot[tag], "YES" if green else "-"))
        summary.append((tag, m["五维和"], m["五维分"], m["PT"], d_out, d_stat, shape))

    # 判官读数与产出的拟合优度：斜率≈1、残差≈0 ⇒ 该分量对这只旋钮只是产出计
    xs = [s[4] for s in summary]
    for comp, key in (("五维和", 1), ("五维分", 2)):
        ys = [s[key] for s in summary]
        slope = (sum((x - statistics.fmean(xs)) * (y - statistics.fmean(ys)) for x, y in zip(xs, ys))
                 / sum((x - statistics.fmean(xs)) ** 2 for x in xs))
        resid = max(abs(y - statistics.fmean(ys) - slope * (x - statistics.fmean(xs)))
                    for x, y in zip(xs, ys))
        print("\n%s 贴近幅度 vs Δ产出：斜率 %.3f，最大残差 %.1f ⇒ %s"
              % (comp, slope, resid,
                 "≈纯产出计（无定价信息）" if abs(slope - 1) < 0.05 and resid < 15 else "含再分配信号"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
