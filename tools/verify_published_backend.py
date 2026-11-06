#!/usr/bin/env python3
"""发布后验收：用**面板的真实调用口径**（MC_PLY=2 MC_K=1024 MC_SEED=0，cwd=rust/）
跑刚发布的 matsuri-mccli.exe，和发布前备份的那个并排比 ranking。
它回答的是一个具体问题：面板同学点下去以后，建议变了没有、变了多少。

    python tools/verify_published_backend.py
    python tools/verify_published_backend.py --k 1024 --states a.json b.json
"""
import argparse
import json
import os
import subprocess
import sys

PANEL = r"E:/HJM/jidianbeiAI0.22"
DEFAULT_STATES = [
    r"E:/HJM/umaai-matsuri/_state_t30.json",
    r"E:/HJM/umaai-matsuri/out/gate98/state_t40.json",
]


def run(exe, cwd, state, k, ply, seed, n, tmp):
    env = dict(os.environ, MC_K=str(k), MC_PLY=str(ply), MC_SEED=str(seed))
    r = subprocess.run([exe, state, tmp, str(n)], cwd=cwd, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, f"rc={r.returncode} {r.stderr.strip()[:200]}"
    try:
        d = json.load(open(tmp, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return None, f"读结果失败 {e}"
    return d, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=PANEL)
    ap.add_argument("--states", nargs="*", default=DEFAULT_STATES)
    ap.add_argument("--k", type=int, default=1024, help="面板默认 1024")
    ap.add_argument("--ply", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n", type=int, default=200, help="rollout 数（面板 turn>=45 时也是 200）")
    a = ap.parse_args()

    rust = os.path.join(a.panel, "rust")
    new = os.path.join(rust, "matsuri-mccli.exe")
    bak = [f for f in os.listdir(rust) if f.startswith("matsuri-mccli.exe.bak")]
    if not os.path.isfile(new):
        sys.exit(f"找不到发布件：{new}")
    old = os.path.join(rust, sorted(bak)[-1]) if bak else None
    bj = json.load(open(os.path.join(rust, "backend_build.json"), encoding="utf-8"))
    print(f"[发布件] {new}\n   commit={bj['git_commit'][:7]} built_at={bj['built_at']}")
    print(f"[对照件] {old or '（无备份，只能看新件自己）'}")

    tmp = os.path.join(rust, "_verify_tmp.json")
    for state in a.states:
        print(f"\n=== 状态 {os.path.basename(state)} ===")
        res = {}
        for tag, exe in (("new", new), (("old", old) if old else ("old", None))):
            if not exe:
                continue
            d, err = run(exe, rust, state, a.k, a.ply, a.seed, a.n, tmp)
            if err:
                print(f"  {tag}: 失败 {err}")
                continue
            rows = d.get("ranked") or []
            res[tag] = rows
            top = rows[0]["label"] if rows else "(空)"
            print(f"  {tag}: ok={d.get('ok')} 行数={len(rows)} top1={top} "
                  f"engine={str(d.get('engine'))[:40]}")
            for i, r in enumerate(rows[:5]):
                print(f"      {i} {r['label']:<14} score={r['score']:.1f} se={r.get('se', 0):.1f}")
        if "new" in res and "old" in res:
            same = [r["label"] for r in res["new"]] == [r["label"] for r in res["old"]]
            print(f"  排序{'完全相同（这次发布对此局面无影响）' if same else '有变化 <<<<'}")
            if not same:
                for i, (x, y) in enumerate(zip(res["new"], res["old"])):
                    if x["label"] != y["label"]:
                        print(f"      位次 {i}: 旧={y['label']}({y['score']:.1f}) → 新={x['label']}({x['score']:.1f})")
    if os.path.exists(tmp):
        os.remove(tmp)


if __name__ == "__main__":
    main()
