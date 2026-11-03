#!/usr/bin/env python3
"""配对门禁的一条命令封装：跑采样 → 调 `matsuri-gate` → 把「能不能下结论」写在判定里。

**必须从仓根跑**（状态文件 `_state_t30.json`、判官与 dist 二进制都在仓内相对路径下）。

两种用法：

    # A) 两臂各留一份 dist 二进制（一次性 worktree / 每臂一个 exe 的约定）
    python tools/crn_gate.py --dist-a <臂A.exe> --dist-b <臂B.exe> --seeds 0,1,2 --n 4000

    # B) 复用已有 dump —— 报告第一行会打「重放旧 dump ≠ 新门禁」，别把它当新证据
    python tools/crn_gate.py --dump-a a.jsonl --dump-b b.jsonl

它多做的一件事，也是唯一的判断：**双臂同侧于锚点 ⇒ 「更近幅度」退化为该分量的原始差值**
（实测斜率 0.997、残差 ≤0.5，见 `docs/旋钮定价判读_20261103.md`）。这种情况下判官给的
「更近 / 更远」只是产出计，脚本把它降成 `⚠️同侧·产出计(不可结论)`；只有跨种子同向
且至少一个分量未被降级，才给 `可结论`。

真值默认锚点 run179/cid179：五维 [1998,1470,1656,1344,1573] / 五维分 42,201 / PT 8,339。
退出码：0 出报告（含「无显著差异」）；2 输入不合法。
"""

import argparse
import os
import re
import subprocess
import sys

COMPONENTS = ("五维和", "五维分", "PT")
ANCHOR = ("1998,1470,1656,1344,1573", "8339", "42201")
REAL = {"五维和": 1998 + 1470 + 1656 + 1344 + 1573, "五维分": 42201.0, "PT": 8339.0}
NUM = r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?"


def exe(path):
    """Windows 下补 .exe；已带后缀或不存在则原样返回。"""
    if os.path.exists(path) or os.path.splitext(path)[1]:
        return path
    return path + ".exe" if os.path.exists(path + ".exe") else path


def parse_gate(stdout):
    """吃 `matsuri-gate` 的表格，返回 {分量: {mean_a, mean_b, near, lo, hi, verdict}}。"""
    got = {}
    for line in stdout.splitlines():
        if not line.startswith("| ") or line.startswith("|---"):
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) < 13 or f[1] not in COMPONENTS:
            continue
        num = lambda s: float(s.replace(",", ""))  # noqa: E731
        ci = re.findall(NUM, f[9].replace(",", ""))
        if len(ci) != 2:
            raise SystemExit("判官的 95%%CI 解析失败：%r" % f[9])
        got[f[1]] = {
            "mean_a": num(f[3]),
            "mean_b": num(f[4]),
            "near": num(f[8]),
            "lo": float(ci[0]),
            "hi": float(ci[1]),
            "win": f[11],
            "verdict": f[12],
        }
    if len(got) != 3:
        raise SystemExit("判官输出没抓全三个分量：" + stdout[:400])
    return got


def side(v, real):
    return 0 if abs(v - real) < 1e-9 else (1 if v > real else -1)


def classify(d):
    """判官原判定 + 同侧降级。返回 (方向, 标签, 可否作结论)。"""
    real = REAL[d["comp"]]
    same = side(d["mean_a"], real) == side(d["mean_b"], real) != 0
    if d["lo"] > 0:
        direction = "更近"
    elif d["hi"] < 0:
        direction = "更远"
    else:
        return "跨0", "跨0·无显著差异(留档)", False
    if same:
        return direction, "⚠️同侧·产出计(%s,不可结论)" % direction, False
    return direction, direction + "(可结论)", True


def sample(dist, state, seed, n, tag):
    out = os.path.join("out", "crn_gate")
    os.makedirs(out, exist_ok=True)
    dump = os.path.join(out, "%s_s%d.jsonl" % (tag, seed))
    env = dict(os.environ, MC_N=str(n), MC_SEED=str(seed), MC_DUMP=dump)
    r = subprocess.run([dist, state, os.path.join(out, "%s_s%d.json" % (tag, seed))],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dump):
        raise SystemExit("采样失败 %s seed%d：%s" % (tag, seed, r.stderr[-300:]))
    return dump


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist-a", help="臂 A 的 matsuri-dist 二进制")
    ap.add_argument("--dist-b", help="臂 B 的 matsuri-dist 二进制（B = 改动后）")
    ap.add_argument("--dump-a", help="复用已有 A dump（重放，不作新证据）")
    ap.add_argument("--dump-b", help="复用已有 B dump")
    ap.add_argument("--gate", default="target/release/matsuri-gate", help="判官二进制")
    ap.add_argument("--state", default="_state_t30.json", help="CRN 状态文件（仓根）")
    ap.add_argument("--seeds", default="0,1,2", help="CRN 种子，逗号分隔")
    ap.add_argument("--n", type=int, default=4000, help="每种子局数")
    ap.add_argument("--five", default=ANCHOR[0], help="真值五维，逗号分隔")
    ap.add_argument("--pt", default=ANCHOR[1], help="真值 PT")
    ap.add_argument("--stat-score", default=ANCHOR[2], help="真值五维分")
    ap.add_argument("--out", default="out/crn_gate.tsv", help="TSV 落盘路径")
    args = ap.parse_args(argv)

    seeds = [int(x) for x in args.seeds.split(",")]
    replay = bool(args.dump_a or args.dump_b)
    if replay and not (args.dump_a and args.dump_b):
        raise SystemExit("--dump-a/--dump-b 要一起给；要采样就用 --dist-a/--dist-b")
    if replay and (args.dist_a or args.dist_b):
        raise SystemExit("--dump-* 与 --dist-* 互斥：重放旧 dump 不能冒充新门禁")
    if not replay and not (args.dist_a and args.dist_b):
        raise SystemExit("要采样必须同时给 --dist-a 与 --dist-b")
    if not replay and not os.path.exists(args.state):
        raise SystemExit("缺状态文件 %s（本脚本必须从仓根跑）" % args.state)
    if replay:
        seeds = [0]  # 重放没有「第几号种子」这回事，标签只为排版统一
    for p in (args.dist_a, args.dist_b):
        if p and not os.path.exists(exe(p)):
            raise SystemExit("缺二进制：" + p)

    gate = exe(args.gate)
    if not os.path.exists(gate):
        raise SystemExit("缺判官 %s：先 cargo build --release -p matsuri-cli --offline" % gate)

    print("锚点 五维 %s / PT %s / 五维分 %s" % (args.five, args.pt, args.stat_score))
    if replay:
        print("⚠️ 重放旧 dump：这不是新门禁，只作对账用（同种子同 MC_N 才可配对）")
    else:
        print("CRN 采样：每臂 %d 局 × 种子 %s" % (args.n, seeds))

    rows, pairs = [], []
    for s in seeds:
        if replay:
            a, b = args.dump_a, args.dump_b
        else:
            a = sample(exe(args.dist_a), args.state, s, args.n, "a")
            b = sample(exe(args.dist_b), args.state, s, args.n, "b")
        r = subprocess.run([gate, "--a", a, "--b", b, "--five", args.five,
                            "--pt", args.pt, "--stat-score", args.stat_score],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("判官失败：" + r.stderr[-300:])
        got = parse_gate(r.stdout)
        print("\n== seed %s ==（A=%s B=%s）" % (s, os.path.basename(a), os.path.basename(b)))
        print("分量    A 均值     B 均值    更近幅度   95%区间              判定")
        for comp in COMPONENTS:
            d = got[comp]
            d["comp"] = comp
            d["seed"] = s
            direction, label, ok = classify(d)
            print("%s %10.1f %10.1f  %+9.1f  [%+.1f, %+.1f]  %s"
                  % (comp.ljust(4), d["mean_a"], d["mean_b"], d["near"], d["lo"], d["hi"], label))
            rows.append((s, comp, d["mean_a"], d["mean_b"], d["near"], d["lo"], d["hi"], label))
            pairs.append(d)

    print("\n== 跨种子汇总 ==")
    decided = 0
    for comp in COMPONENTS:
        ds = [d for d in pairs if d["comp"] == comp]
        judged = [classify(d) for d in ds]  # classify 读 d["comp"]，上面已写入
        dirs = [j[0] for j in judged]
        usable = sum(1 for _, _, ok in judged if ok)
        plus, minus, cross = dirs.count("更近"), dirs.count("更远"), dirs.count("跨0")
        if len(set(dirs)) != 1:
            trend = "不同向（更近 %d / 更远 %d / 跨0 %d）⇒ 只留档" % (plus, minus, cross)
        elif cross == len(ds):
            trend = "全部跨 0 ⇒ 无显著差异，只留档不转正"
        elif usable == 0:
            trend = "全部同向(%s)，但**全部同侧于锚点 ⇒ 产出计，不可结论**：贴近幅度恒等于原始差值" \
                    "（实测斜率 0.997），换种子不会改变这件事，必须改臂或去锚点跑" % dirs[0]
        else:
            trend = "全部同向(%s) ⇒ 可结论（未被同侧退化的种子 %d/%d）" % (dirs[0], usable, len(ds))
            decided += 1
        print("%s：%s" % (comp.ljust(4), trend))
    print("\n能作结论的分量：%d / 3 —— 门禁只认五维和 / 五维分 / PT 三分量，"
          "其余一律不算通过。" % decided)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("seed\tcomp\tmean_a\tmean_b\tnear\tci_lo\tci_hi\tlabel\n")
        for r in rows:
            f.write("\t".join("%g" % x if isinstance(x, float) else str(x) for x in r) + "\n")
    print("TSV -> " + args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
