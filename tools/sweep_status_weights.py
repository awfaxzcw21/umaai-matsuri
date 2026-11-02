#!/usr/bin/env python3
"""#23 前置探针：配对门禁分不分得出 `status_weights` 的差异。

权重住在 `crates/matsuri-core/src/config.rs` → `value.rs:429`（乘在五维增量分上），
所以改它确实改决策；本脚本只回答「目标函数有没有分辨率」，**不回答该用哪组权重**。

跑法需要一个可丢弃的实验壳（port 工作树的 tar 副本 + 分臂二进制 + trainer overlay）：

    S7X2_SHELL=E:/HJM/_s7x2 python tools/sweep_status_weights.py

overlay 是 **trainer 整段替换**，所以每臂只改 `status_weights` 那一行、其余字节不动。
配对口径与 `docs/S7定价2x2_20261103.md` 的复跑段完全一致：同一 dist 二进制、`MC_N=4000`、
`MC_SEED` 各 0/1/2，判官只看「贴近幅度」95% 区间跨不跨 0。

红线：锚点真值目前高于所有臂的均值 ⇒ 凡抬高总产出的臂都会被判「更近」。
本脚本的输出只登记为**分辨率证明与方向线索**，不登记为旋钮成果。
"""

import collections
import os
import re
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
# 锚点 = run179/cid179 终局真值（五维和 8,041 / 五维分 42,201 / PT 8,339）
ANCHORS = ("1998,1470,1656,1344,1573", "8339", "42201")
AXES = ("速", "耐", "力", "根", "智")
COMPONENTS = ("五维和", "五维分", "PT")


def arms():
    """一臂 = 标签 + 五维权重向量（出厂 = 全 1.0）。逐轴 ×0.5 / ×2 的坐标试探。"""
    out = [("base", (1.0,) * 5)]
    for i in range(5):
        for mult, tag in ((0.5, "x0.5"), (2.0, "x2")):
            w = [1.0] * 5
            w[i] = mult
            out.append((f"{AXES[i]}{tag}", tuple(w)))
    return out


def write_overlay(path, weights):
    src = open(TEMPLATE, encoding="utf-8").read()
    lit = "[" + ", ".join(f"{v:g}" for v in weights) + "]"
    out, n = re.subn(r"(?m)^status_weights = \[[^\]]*\]$", "status_weights = " + lit, src)
    if n != 1:
        raise SystemExit("overlay 模板里 status_weights 行匹配到 %d 处，改模板" % n)
    open(path, "w", encoding="utf-8").write(out)


def sample(tag, seed):
    dump = os.path.join(EXP, "sw_%s_s%d.jsonl" % (tag, seed))
    env = dict(os.environ, MC_N=N, MC_SEED=str(seed), MC_DUMP=dump)
    r = subprocess.run([DIST, STATE, os.path.join(EXP, "sw_%s_s%d.json" % (tag, seed))],
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


def main():
    for exe in (DIST, GATE, STATE, TEMPLATE):
        if not os.path.exists(exe):
            raise SystemExit("缺文件：%s（壳路径用 S7X2_SHELL 覆盖）" % exe)

    lst = arms()
    dumps = {}
    for tag, w in lst:
        write_overlay(os.path.join(SHELL, "matsuri_config.toml"), w)
        for s in SEEDS:
            dumps[(tag, s)] = sample(tag, s)
        print("采样完成 %s %s" % (tag, w), flush=True)

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

    tsv = os.path.join(EXP, "sweep_weights_probe.tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("arm\tweights\tseed\tcomp\tnear\tci_lo\tci_hi\tp\twin\tverdict\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print("TSV -> " + tsv)

    sig = lambda r: r[5] > 0 or r[6] < 0
    agg = collections.defaultdict(lambda: collections.defaultdict(list))
    cnt = collections.Counter()
    tot = collections.Counter()
    for r in rows:
        agg[r[0]][r[3]].append(r[4])
        tot[r[0]] += 1
        if sig(r):
            cnt[r[0]] += 1
    print("\n=== 每臂均值贴近幅度（正=更近）；显著/9 = CI 不跨 0 的配对数 ===")
    print("臂        五维和    五维分      PT   显著/9  9/9全绿")
    for tag, _ in lst:
        if tag == "base":
            continue
        m = {c: sum(v) / len(v) for c, v in agg[tag].items()}
        green = cnt[tag] == tot[tag] and all(min(agg[tag][c]) > 0 for c in agg[tag])
        print("%-9s %+8.1f %+9.1f %+7.1f   %d/9    %s"
              % (tag, m["五维和"], m["五维分"], m["PT"], cnt[tag], "YES" if green else "-"))


if __name__ == "__main__":
    sys.exit(main())
