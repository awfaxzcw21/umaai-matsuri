#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""#53 集成复核：主线**独立谓词**重算「合宿开始 vs 教诲改派」的落点。

只读 `out/_camp_axis_pkt.json`（#53 的逐包摊平缓存，3,844 帧）+ 原始报文目录，
不改 tracked 文件、不跑引擎。判据全部自己写，不复用 `camp_axis_scan.py` 的判定函数——
#52-A 那次教训是**对方的表口径没问题、我的谓词会错**，所以这里把每个谓词的
「可判/不可判」都显式分出来，不把「判不了」计成违例。

跑法（仓根）：python tools/camp_axis_recheck_20261104.py
"""
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "out", "_camp_axis_pkt.json")
RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"

CAMP_PANEL = {13, 37, 61}          # 合宿首回合（= 引擎 12/36/60）
REASSIGN_PANEL = {3, 12, 24, 36, 48, 60}   # 教诲改派窗（表侧 turn_num）
STORY = {215016: (13, 10), 215017: (37, 15), 215018: (61, 15)}


def frames(pkts):
    """按 (run, 文件序号) 排成逐局时间线。fs 是 #53 给的文件序号；缺了就用文件名前缀。"""
    by_run = defaultdict(list)
    for p in pkts:
        seq = p.get("fs")
        if seq is None:
            seq = int(str(p["file"]).split("_")[0])
        by_run[p["run"]].append((seq, p))
    for k in by_run:
        by_run[k].sort(key=lambda x: x[0])
    return by_run


def want(name, got, exp):
    flag = "✓" if got == exp else "✗"
    print("  [%s] %-46s got=%s want=%s" % (flag, name, got, exp))


def main():
    d = json.load(open(CACHE, encoding="utf-8"))
    pkts = d["pkts"]
    runs = frames(pkts)
    print("缓存：%d 帧 / %d 局（files=%d）" % (len(pkts), len(runs), d["files"]))

    # ── R1 落点：camp_left 由 0 变 4 的那一条包落在哪个面板回合 ──────────────
    # 口径：只认 `check_event` 包。同回合的 `exec_command` 结算包也会把 camp_left 读成 4
    # （它带的 Δvital 是「当手 + 合宿 +15」的混账，@202/@203 面板 37 各一条），
    # 把它当 onset 会既多算条数、又把 Δmot 判成 0 违例——那是**我的口径错**，不是真机违例。
    onset = []
    for run, seq in runs.items():
        prev = 0
        for _, p in seq:
            cl = p.get("camp_left") or 0
            if prev == 0 and cl == 4 and p.get("req") == "check_event":
                onset.append((run, p))
            prev = cl
    print("\n=== R1 camp_left 0→4 的标记包：%d 条，回合分布 %s ==="
          % (len(onset), dict(sorted(Counter(p["turn"] for _, p in onset).items()))))
    off = set(p["turn"] for _, p in onset) - CAMP_PANEL
    want("标记包只在面板 13/37/61", off, set())

    # ── R2 台阶：标记包相对上一条包的体力增量，按年次分组 ────────────────────
    steps = defaultdict(list)
    mot = defaultdict(list)
    capped = []
    ev_of_onset = Counter()
    req_of_onset = Counter()
    for run, seq in runs.items():
        idx = {p["file"]: i for i, (_, p) in enumerate(seq)}
        for r2, p in [x for x in onset if x[0] == run]:
            i = idx[p["file"]]
            if i == 0:
                continue
            q = seq[i - 1][1]
            dv = (p.get("vital") or 0) - (q.get("vital") or 0)
            dm = (p.get("mot") or 0) - (q.get("mot") or 0)
            steps[p["turn"]].append((run, dv, p.get("event_id"), p.get("max_vital")))
            mot[p["turn"]].append((run, dm, p.get("mot"), q.get("mot")))
            ev_of_onset[p.get("event_id")] += 1
            req_of_onset[p.get("req")] += 1
            if p.get("max_vital") is not None and p["vital"] == p["max_vital"]:
                capped.append((run, p["turn"], dv, p["vital"], p["max_vital"]))
    print("\n=== R2 标记包 Δvital（面板回合 → 逐局增量）===")
    for t in sorted(steps):
        c = Counter(x[1] for x in steps[t])
        print("  面板 %2d（引擎 %2d）: n=%2d  Δvital 分布 %s" % (t, t - 1, len(steps[t]), dict(sorted(c.items()))))
    print("  onset 包 event_id 分布：%s" % dict(ev_of_onset))
    print("  onset 包 req 分布：%s" % dict(req_of_onset))
    for ev, (t, gain) in STORY.items():
        if ev not in ev_of_onset:
            print("  [!] event_id %d 未出现在 onset 包上" % ev)
    # 未钳位样本的众数（钳位的单独看，别污染幅度结论）
    print("  钳位候选（onset 时 vital == max_vital）：%d 条" % len(capped))
    for x in capped:
        print("    %s 面板%d Δv=%+d v=%d max=%d" % x)

    # ── R3 干劲：+1 是否成立；「已顶格」不算违例 ─────────────────────────────
    print("\n=== R3 标记包 Δmot ===")
    for t in sorted(mot):
        rows = mot[t]
        judgeable = [x for x in rows if x[3] is not None and x[3] < 5]
        c = Counter(x[1] for x in judgeable)
        bad = [x for x in judgeable if x[1] != 1]
        print("  面板 %2d: 可判 %d/%d  Δmot 分布 %s  违例 %d %s"
              % (t, len(judgeable), len(rows), dict(sorted(c.items())), len(bad),
                 [(b[0].split("@")[1], b[2], b[3]) for b in bad]))

    # ── R4 改派：请求只在教诲窗提交，且这些包不发体力/干劲 ───────────────────
    ar = [p for p in pkts if p.get("req") and "advice" in str(p["req"])]
    turns = Counter(p.get("req_turn") or p["turn"] for p in ar)
    print("\n=== R4 改派请求：n=%d，current_turn 分布 %s ===" % (len(ar), dict(sorted(turns.items()))))
    want("改派只在教诲窗那一格提交", set(turns) - REASSIGN_PANEL, set())
    # 改派包本身（及其紧邻下一条）有没有抬体力：与 onset 判据同族，但轴不同
    gain_on_reassign = []
    for run, seq in runs.items():
        idx = {p["file"]: i for i, (_, p) in enumerate(seq)}
        for p in [x[1] for x in seq if x[1].get("req") and "advice" in str(x[1]["req"])]:
            i = idx[p["file"]]
            if i + 1 >= len(seq):
                continue
            n = seq[i + 1][1]
            if (n.get("vital") or 0) > (p.get("vital") or 0) and n.get("event_id") is None:
                gain_on_reassign.append((run.split("@")[1], p["turn"], p["vital"],
                                         n["req"], n["turn"], n["vital"]))
    # 这些都落在 `exec_command`（玩家那一手自己的结算）上，不是改派包在发东西 ⇒ 不推翻 §0
    print("  紧邻改派之后、无 event_id 的体力上涨：%d 次 %s" % (len(gain_on_reassign), gain_on_reassign))

    # ── R5 独立解码抽查：不吃 #53 的字段抽取，自己走 msgpack ─────────────────
    print("\n=== R5 原始报文独立解码抽查（每合宿年 1 条）===")
    sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
    from msgpack_lite import decode_file  # noqa: E402
    picked = {}
    for run, p in onset:
        picked.setdefault(p["turn"], (run, p))
    for t in sorted(picked):
        run, p = picked[t]
        path = os.path.join(RAW, p["file"])   # 报文目录是平的：NNNNNN_IIII_name.bin
        if not os.path.exists(path):
            print("  面板 %d: 找不到原始文件 %s（跳过，登记未复核）" % (t, path))
            continue
        obj = decode_file(path)
        hits = []

        def walk(x, key=None):
            if isinstance(x, dict):
                for k, v in x.items():
                    kk = k.decode() if isinstance(k, bytes) else str(k)
                    walk(v, kk)
            elif isinstance(x, (list, tuple)):
                for v in x:
                    walk(v, key)
            else:
                if key and "summer_camp" in key:
                    hits.append((key, x))

        walk(obj)
        camp = [v for k, v in hits if "remain" in k]
        print("  面板 %d（引擎 %d）：expect camp_left=4 → 自解码 %s；缓存值=%s event_id=%s"
              % (t, t - 1, camp[:3], p["camp_left"], p["event_id"]))

    print("\n判读：R1/R4 决定「+10/+15 与改派是不是同一格」；R2/R3 决定幅度与干劲；R5 只验字段抽取没跑偏。")


if __name__ == "__main__":
    main()
