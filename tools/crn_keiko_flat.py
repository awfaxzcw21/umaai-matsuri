#!/usr/bin/env python3
"""CRN 配对门禁：两个臂各跑 MC_N 局，逐分量输出「贴近幅度」95% 区间与判定。

判官口径与 AGENTS.md 规范 5 相同：同种子 CRN，只看五维和 / 五维分 / PT 三分量的
「贴近幅度」区间跨不跨 0；锚点 = run179/cid179 终局真值（五维和 8,041 / 五维分 42,201 / PT 8,339）。

默认跑的是 #18 第一组（105 平摊值进不进乘区）的 A/B；换旋钮用 `--a/--b/--tag` 复用：

    cd E:/HJM/umaai-matsuri
    cargo build --release -p matsuri-cli --bins
    cp target/release/matsuri-dist.exe out/_gate/dist_B.exe       # B = 改动后
    git stash push -- crates/matsuri-core/src/game/mod.rs
    cargo build --release -p matsuri-cli --bins
    cp target/release/matsuri-dist.exe out/_gate/dist_A.exe       # A = 改动前
    git stash pop
    python tools/crn_keiko_flat.py                                # 写 docs/keiko_flat_A_B_3seeds.tsv
    python tools/crn_keiko_flat.py --a out/_gate/dist_head.exe \\
        --b out/_gate/dist_home.exe --tag keiko_home               # 写 docs/keiko_home_A_B_3seeds.tsv
"""

import argparse
import os
import re
import subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEDIR = os.path.join(HERE, "out", "_gate")
STATE = os.path.join(HERE, "_state_t30.json")
GATE = os.path.join(HERE, "target", "release", "matsuri-gate.exe")
# 锚点 = run179/cid179 终局真值
ANCHORS = ("1998,1470,1656,1344,1573", "8339", "42201")
COMPONENTS = ("五维和", "五维分", "PT")


def sample(arm, exe, seed, n):
    dump = os.path.join(GATEDIR, "crn_%s_s%d.jsonl" % (arm, seed))
    env = dict(os.environ, MC_N=n, MC_SEED=str(seed), MC_DUMP=dump)
    r = subprocess.run([exe, STATE, os.path.join(GATEDIR, "crn_%s_s%d.json" % (arm, seed))],
                       cwd=HERE, env=env, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dump):
        raise SystemExit("采样失败 %s seed%d: %s" % (arm, seed, r.stderr[-300:]))
    return dump


def pair(a, b):
    r = subprocess.run([GATE, "--a", a, "--b", b, "--five", ANCHORS[0], "--pt", ANCHORS[1],
                        "--stat-score", ANCHORS[2]],
                       cwd=HERE, capture_output=True, text=True)
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


def mean_of(dump_path, key_re):
    """从 jsonl 逐局样本里取一个分量的均值，只用于打印对照表。"""
    tot = 0.0
    n = 0
    with open(dump_path, encoding="utf-8") as f:
        for line in f:
            m = key_re.search(line)
            if m:
                tot += float(m.group(1))
                n += 1
    return (tot / n if n else float("nan")), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default=os.path.join(GATEDIR, "dist_A.exe"), help="改动前的 exe")
    ap.add_argument("--b", default=os.path.join(GATEDIR, "dist_B.exe"), help="改动后的 exe")
    ap.add_argument("--tag", default="keiko_flat", help="产出文件名前缀")
    ap.add_argument("--n", default="4000", help="每臂局数")
    ap.add_argument("--seeds", default="0,1,2", help="逗号分隔的种子")
    args = ap.parse_args()

    arms = {"A": os.path.abspath(args.a), "B": os.path.abspath(args.b)}
    seeds = tuple(int(x) for x in args.seeds.split(","))
    for exe in list(arms.values()) + [GATE, STATE]:
        if not os.path.exists(exe):
            raise SystemExit("缺文件：%s" % exe)

    dumps = {}
    for arm in ("A", "B"):
        for s in seeds:
            dumps[(arm, s)] = sample(arm, arms[arm], s, args.n)
        print("采样完成 %s" % arm, flush=True)

    rows = []
    for s in seeds:
        g = pair(dumps[("A", s)], dumps[("B", s)])
        for c in COMPONENTS:
            d = g[c]
            rows.append((s, c, d["near"], d["lo"], d["hi"], d["p"], d["win"], d["verdict"]))
        print("判读完成 seed%d" % s, flush=True)

    tsv = os.path.join(HERE, "docs", "%s_A_B_%dseeds.tsv" % (args.tag, len(seeds)))
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("seed\tcomponent\tnear\tci_lo\tci_hi\tp\twin\tverdict\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print("写入 %s" % tsv)

    # 两臂均值对照（分量均值直接从 A 的 dump 抓，B 同理），只为把「量级」摆在一张表上
    five_re = re.compile(r'"sum":\s*([-0-9.]+)')
    pt_re = re.compile(r'"pt":\s*([-0-9.]+)')
    score_re = re.compile(r'"stat":\s*([-0-9.]+)')
    for arm in ("A", "B"):
        agg = {}
        for s in seeds:
            for key, rx in (("五维和", five_re), ("五维分", score_re), ("PT", pt_re)):
                v, _ = mean_of(dumps[(arm, s)], rx)
                agg.setdefault(key, []).append(v)
        print(arm, {k: round(sum(v) / len(v), 1) for k, v in agg.items()})
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
