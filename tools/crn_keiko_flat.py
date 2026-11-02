#!/usr/bin/env python3
"""CRN 配对门禁：A = HEAD（稽古 105 平摊值走乘区）, B = 修复后（平摊值不进乘区、不给主属性）。

跑法（一次性复现）：

    cd E:/HJM/umaai-matsuri
    cargo build --release -p matsuri-cli --bins
    cp target/release/matsuri-dist.exe out/_gate/dist_B.exe
    git stash push -- crates/matsuri-core/src/game/mod.rs
    cargo build --release -p matsuri-cli --bins
    cp target/release/matsuri-dist.exe out/_gate/dist_A.exe
    git stash pop
    python tools/crn_keiko_flat.py

判官口径与 AGENTS.md 规范 5 相同：同种子 CRN，只看五维和 / 五维分 / PT 三分量的
「贴近幅度」95% 区间跨不跨 0。锚点 = run179/cid179 终局真值。
"""

import os
import re
import subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEDIR = os.path.join(HERE, "out", "_gate")
STATE = os.path.join(HERE, "_state_t30.json")
GATE = os.path.join(HERE, "target", "release", "matsuri-gate.exe")
ARMS = {"A": os.path.join(GATEDIR, "dist_A.exe"), "B": os.path.join(GATEDIR, "dist_B.exe")}
N = "4000"
SEEDS = (0, 1, 2)
# 锚点 = run179/cid179 终局真值（五维和 8,041 / 五维分 42,201 / PT 8,339）
ANCHORS = ("1998,1470,1656,1344,1573", "8339", "42201")
COMPONENTS = ("五维和", "五维分", "PT")


def sample(arm, seed):
    dump = os.path.join(GATEDIR, "flat_%s_s%d.jsonl" % (arm, seed))
    env = dict(os.environ, MC_N=N, MC_SEED=str(seed), MC_DUMP=dump)
    r = subprocess.run([ARMS[arm], STATE, os.path.join(GATEDIR, "flat_%s_s%d.json" % (arm, seed))],
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


def main():
    for exe in list(ARMS.values()) + [GATE, STATE]:
        if not os.path.exists(exe):
            raise SystemExit("缺文件：%s" % exe)

    dumps = {}
    for arm in ("A", "B"):
        for s in SEEDS:
            dumps[(arm, s)] = sample(arm, s)
        print("采样完成 %s" % arm, flush=True)

    rows = []
    for s in SEEDS:
        g = pair(dumps[("A", s)], dumps[("B", s)])
        for c in COMPONENTS:
            d = g[c]
            rows.append((s, c, d["near"], d["lo"], d["hi"], d["p"], d["win"], d["verdict"]))
        print("判读完成 seed%d" % s, flush=True)

    tsv = os.path.join(HERE, "docs", "keiko_flat_A_B_3seeds.tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("seed\tcomponent\tnear\tci_lo\tci_hi\tp\twin\tverdict\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print("写入 %s" % tsv)
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
