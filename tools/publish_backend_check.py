#!/usr/bin/env python3
"""发布前/后面板侧一致性检查：rust/data 与主树 data/ 的逐文件指纹差，
以及 rust/backend_build.json 里记的 sha 是否等于磁盘上的实际文件。"""
import argparse
import hashlib
import json
import os
import posixpath

MAIN = r"E:/HJM/umaai-matsuri"
PANEL = r"E:/HJM/jidianbeiAI0.22"


def sha(path, limit=4_000_000):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read(limit)).hexdigest()


def snap(root):
    out = {}
    for dp, _dn, fn in os.walk(root):
        for name in fn:
            p = os.path.join(dp, name)
            rel = posixpath.join(*os.path.relpath(p, root).split(os.sep))
            out[rel] = (sha(p)[:16], os.path.getsize(p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=PANEL)
    ap.add_argument("--main", default=MAIN)
    a = ap.parse_args()

    main_data = snap(os.path.join(a.main, "data"))
    panel_data = snap(os.path.join(a.panel, "rust", "data"))
    keys = sorted(set(main_data) | set(panel_data))
    drift = [k for k in keys if main_data.get(k) != panel_data.get(k)]
    print(f"[data] 主树 {len(main_data)} 个文件 / 面板 rust/data {len(panel_data)} 个 / 指纹不同 {len(drift)}")
    for k in drift:
        print(f"   - {k}  main={main_data.get(k)}  panel={panel_data.get(k)}")

    bj_path = os.path.join(a.panel, "rust", "backend_build.json")
    if os.path.exists(bj_path):
        bj = json.load(open(bj_path, encoding="utf-8"))
        print(f"[build json] commit={bj.get('git_commit','')[:7]} branch={bj.get('git_branch')} built_at={bj.get('built_at')}")
        for name, meta in bj.items():
            if not isinstance(meta, dict) or "sha256" not in meta:
                continue
            p = os.path.join(a.panel, "rust", name)
            if not os.path.exists(p):
                print(f"   ! {name}: json 里有记录但磁盘上没有")
                continue
            actual = sha(p)
            size = os.path.getsize(p)
            ok = actual == meta["sha256"] and size == meta.get("size")
            print(f"   {'OK' if ok else 'MISMATCH'} {name} 记={meta['sha256'][:16]} 实={actual[:16]} 大小 {size}/{meta.get('size')}")
    else:
        print(f"[build json] 缺：{bj_path}")

    import subprocess
    head = subprocess.run(["git", "-C", a.main, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "-C", a.main, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", a.main, "status", "--porcelain", "--", "crates/"],
                           capture_output=True, text=True).stdout.strip().count("\n")
    print(f"[main tree] HEAD={head[:7]} branch={branch} crates/ 脏文件行数={dirty}")


if __name__ == "__main__":
    main()
