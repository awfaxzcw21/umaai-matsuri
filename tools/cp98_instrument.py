#!/usr/bin/env python3
"""#100 的两个非采样检查：
1. **确定性**：同一 exe、同一 env、同一 seed 重跑，逐局输出必须一字不差
   （两臂之间没有「没被兜底改写」的局时，CRN 自检就没料，只能靠这个证仪器是死的）。
2. **A 臂内剂量**：兜底每局改写次数 vs CP2 大成功——同一臂内部的关联，
   不是因果读数（改写多的局本身就更「落后」），只用来看方向对不对得上双臂差。

    python tools/cp98_instrument.py --a "…/t1K32_A_s*.jsonl"
"""
import argparse
import glob
import os
import json
import statistics as st
import subprocess
import sys


def load(pats):
    out = []
    for pat in pats:
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8"):
                if line.strip():
                    out.append(json.loads(line))
    return {r["seed"]: r for r in out}


def cp_great(r, sc):
    return any(int(c[0]) == sc and bool(c[1]) for c in r["cp_log"])


def determinism(exe, state, seed, n, k, workdir):
    """同一 (seed 基, n) 跑两遍，比 jsonl 全文。"""
    outs = []
    for rep in (1, 2):
        p = os.path.join(workdir, f"det_rep{rep}.jsonl")
        e = dict(os.environ, MC_ARM="B", MC_FROM_T1="1", MC_K=str(k), MC_PLY="2",
                 MC_SEED=str(seed), MC_N=str(n))
        r = subprocess.run([exe, state, p, "200"], cwd=workdir, env=e,
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None, f"跑失败 rc={r.returncode} {r.stderr[:160]}"
        outs.append(open(p, encoding="utf-8").read())
    return outs[0] == outs[1], "两遍逐字节相同" if outs[0] == outs[1] else "★同种子重跑不可复现"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", nargs="+", required=True)
    ap.add_argument("--b", nargs="+", default=[])
    ap.add_argument("--exe", default="E:/HJM/_wt_cp98/target/release/matsuri-cpcensus.exe")
    ap.add_argument("--state", default="E:/HJM/umaai-matsuri/_state_t30.json")
    ap.add_argument("--workdir", default="E:/HJM/_wt_cp98")
    ap.add_argument("--det-n", type=int, default=2, help="确定性重跑每遍几局")
    ap.add_argument("--det-seed", type=int, default=11)
    ap.add_argument("--det-k", type=int, default=32)
    ap.add_argument("--skip-det", action="store_true")
    a = ap.parse_args()

    A, B = load(a.a), load(a.b if a.b else a.a)
    print(f"[样本] A={len(A)} 局 B={len(B)} 局")

    if not a.skip_det:
        same, msg = determinism(a.exe, a.state, a.det_seed, a.det_n, a.det_k, a.workdir)
        print(f"[确定性] 同 exe 同 env 同种子跑两遍（B 臂 {a.det_n} 局，K={a.det_k}）：{msg}")

    inert = [s for s in A if A[s]["overridden"] == 0]
    print(f"[CRN 自检用料] A 臂里『兜底一次都没改写 top-1』的局 = {len(inert)}/{len(A)}"
          + ("  ⇒ 这项自检**空转**，没有可比局；两臂同批初始局只能靠上面的确定性证"
             if not inert else f"，其中与 B 不同的 {sum(1 for s in inert if A[s]['cp_log'] != B[s]['cp_log'])} 个"))

    # A 臂内剂量
    if len(A) >= 8:
        med = st.median([r["overridden"] for r in A.values()])
        lo = [r for r in A.values() if r["overridden"] <= med]
        hi = [r for r in A.values() if r["overridden"] > med]
        print(f"\n[A 臂内剂量] 改写次数中位 {med:.1f}：")
        for nm, grp in (("少改（≤中位）", lo), ("多改（>中位）", hi)):
            if not grp:
                continue
            rate = 100 * sum(cp_great(r, 2) for r in grp) / len(grp)
            five = st.mean(r["five_sum"] for r in grp)
            stat = st.mean(r["stat_score"] for r in grp)
            print(f"   {nm:<14} n={len(grp):<4} 改写均值={st.mean(r['overridden'] for r in grp):5.1f} "
                  f"CP2 大成功={rate:5.1f}%  五维和={five:7.0f}  五维分={stat:8.0f}")
        print("   ⚠ 同臂内部关联，混杂「这局本来就有几格没起来」，**不是**因果读数。")

    # B 臂的三分量分布（给「面板档 vs 基策档」留个对照锚）
    if B and all(r["arm"] == "B" for r in B.values()):
        print(f"\n[B 臂终值] 五维和 {st.mean(r['five_sum'] for r in B.values()):.0f}"
              f" ± {st.stdev([r['five_sum'] for r in B.values()]):.0f}"
              f" / 五维分 {st.mean(r['stat_score'] for r in B.values()):.0f}"
              f" / PT {st.mean(r['pt'] for r in B.values()):.0f}"
              f" / 达 85,600 的局数 "
              f"{sum(1 for r in B.values() if r['stat_score'] + 1.0746 * round(r['pt'] * 2.61) >= 85600)}"
              f"/{len(B)}"
              f"（real 口径＝五维分 + 1.0746×round(PT×2.61)，与 dist 的 real_score 同式）")
    else:
        print("\n[B 臂终值] 没给 --b，跳过")
    print(f"\n[注] 若『面板档 vs 基策档』要对照，用 MC_POLICY=base（已与 matsuri-dist 逐局核过三分量全等）。")


if __name__ == "__main__":
    main()
