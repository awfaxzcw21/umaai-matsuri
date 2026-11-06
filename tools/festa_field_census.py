#!/usr/bin/env python3
"""#92 第 0 步：报文里「演目 Lv」与「卡组构成」到底在哪个路径下。

只读 `hachimi_raw`（一手），不改任何东西、不开测量壳。
对若干条报文把 dict/list 路径压成 `data.chara_info.act_info_array[].level` 这种形式，
按出现次数排序，并留一个样例值 ⇒ 先定住字段名，再谈逐手重算 `pt_festa`。

    python tools/festa_field_census.py            # 默认扫 900 条
    python tools/festa_field_census.py --n 3000 --grep "act|card|support|festa"
"""
import argparse
import collections
import os
import re
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
from msgpack_lite import decode_file  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"


def walk(node, path, out, depth=0, maxdepth=6):
    if depth > maxdepth:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            kp = f"{path}.{k}" if path else str(k)
            if isinstance(v, (dict, list)):
                walk(v, kp, out, depth + 1, maxdepth)
            else:
                out[kp][0] += 1
                if out[kp][1] is None and v not in (None, ""):
                    out[kp][1] = v
    elif isinstance(node, list):
        if not node:
            out[path + "[]"][0] += 1
            out[path + "[]"][1] = out[path + "[]"][1]
            return
        for v in node[:3]:
            walk(v, path + "[]", out, depth + 1, maxdepth)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=900, help="扫多少条报文")
    ap.add_argument("--grep", default=r"act|card|support|festa|skill_point|sp",
                    help="路径正则（只统计命中的键）")
    ap.add_argument("--limit-print", type=int, default=70)
    a = ap.parse_args()

    rx = re.compile(a.grep, re.I)
    files = sorted(f for f in os.listdir(RAW) if f.endswith(".bin"))
    files = files[-a.n:]
    hits = collections.defaultdict(lambda: [0, None])
    n_ok = 0
    for f in files:
        try:
            d = decode_file(RAW + f)
        except Exception:
            continue
        n_ok += 1
        out = collections.defaultdict(lambda: [0, None])
        walk(d, "", out)
        for k, v in out.items():
            if rx.search(k.split(".")[-1]) or rx.search(k):
                hits[k][0] += v[0]
                if hits[k][1] is None:
                    hits[k][1] = v[1]
    print(f"扫了 {len(files)} 条（成功解出 {n_ok} 条），命中路径 {len(hits)} 个\n")
    rows = sorted(hits.items(), key=lambda kv: -kv[1][0])
    print(f"{'出现次数':>9}  路径  ──  样例值")
    shown = 0
    for k, (cnt, sample) in rows:
        if cnt < 3:
            continue
        s = repr(sample)
        if len(s) > 90:
            s = s[:90] + "…"
        print(f"{cnt:>9}  {k}  ──  {s}")
        shown += 1
        if shown >= a.limit_print:
            break


if __name__ == "__main__":
    main()
