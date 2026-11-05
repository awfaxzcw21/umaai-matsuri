#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#83：把「进化进度计数器什么时候 +1」定位到具体那一次点击/比赛。

为什么这条能纯离线跑：`chara_info.skill_upgrade_info_array`
= `[{condition_id, current_count, total_count}]` 是**逐包实时更新**的（4,265 包含该字段）。
技能清单本身只在 t78  materialize（见 §1 的负结果），**但计数器不是** ⇒
找到 `current_count` 从 0→1 的那一刻，看它前后几个包是什么请求，触发面就露出来了。

两种预期结果：
  · 增量落在「比赛响应」附近 ⇒ 支持「技能在实战里发动过才计数」（timing_type=2/count_type=2 那一族）；
  · 增量落在「训练/事件/商店」附近 ⇒ 支持育成内计数，那我上一轮「满足却没进化」就是读错了门槛语义。

只读报文 + 只读 mdb；不写 data/、不动引擎。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shop_offer_dis1_check as D  # noqa: E402

RAW = D.RAW_DIR
GAP_S = 1800
CMD = "single_mode_exec_command_request_common"
EVT = "single_mode_check_event_request_common"

FAMILY = {**{i: "训练" for i in range(101, 107)},
          **{i: "稽古" for i in range(5101, 5106)}, 701: "比赛"}


def summarize(obj):
    """给一个包贴个「这是干什么的」标签，供增量附近的时间线用。"""
    kinds = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                continue
            kl = k.lower()
            if kl.endswith("_request_common") or kl.endswith("_request"):
                kinds.append(k)
    return kinds


def main():
    con = sqlite3.connect("file:%s?mode=ro" % D.MASTER.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    C = {r["id"]: r for r in con.execute("SELECT * FROM skill_upgrade_condition")}
    SPEC = {r["id"]: r["base_skill_id"] for r in con.execute(
        "SELECT id,base_skill_id FROM skill_upgrade_speciality WHERE scenario_id=15")}
    con.close()

    files = sorted(f for f in os.listdir(RAW) if f.endswith(".bin"))
    stream = []          # (file, ts, card, {cond:(cur,tot)}, cmds, turn)
    for nm in files:
        p = os.path.join(RAW, nm)
        b = open(p, "rb").read()
        if b"skill_upgrade_info_array" not in b:
            continue
        try:
            o = D.dpb(b)
        except Exception:
            continue
        ci = D.dig(o, "chara_info")[0]
        if not isinstance(ci, dict):
            continue
        up = {}
        for e in ci.get("skill_upgrade_info_array") or []:
            if isinstance(e, dict):
                cid = D.as_int(e.get("condition_id"))
                if cid:
                    up[cid] = (int(D.as_int(e.get("current_count")) or 0),
                               int(D.as_int(e.get("total_count")) or 0))
        if not up:
            continue
        cmd = []
        node = o.get(CMD) if isinstance(o, dict) else None
        if isinstance(node, dict):
            cid = D.as_int(node.get("command_id"))
            cmd.append("%s%s" % (FAMILY.get(cid, "命令%s" % cid), cid))
        node = o.get(EVT) if isinstance(o, dict) else None
        if isinstance(node, dict):
            cmd.append("事件%s" % D.as_int(node.get("event_id")))
        stream.append((nm, int(os.path.getmtime(p)), D.as_int(ci.get("card_id")),
                       up, cmd, D.as_int(node.get("current_turn") if isinstance(node, dict) else None)
                       if isinstance(o, dict) else None))
    print("带计数器的包：%d" % len(stream))

    # 按 (育成卡 + 时间连续) 切段
    segs, cur, last_ts, last_card = [], [], 0, None
    for rec in stream:
        if cur and (rec[1] - last_ts > GAP_S or (last_card and rec[2] != last_card)):
            segs.append(cur)
            cur = []
        cur.append(rec)
        last_ts, last_card = rec[1], rec[2]
    if cur:
        segs.append(cur)
    segs = [s for s in segs if len(s) >= 20]
    print("切成 %d 段（每段≥20 个带计数器的包）" % len(segs))

    n_inc = 0
    for seg in segs:
        card = seg[0][2]
        prev = {}
        for i, (nm, ts, _c, up, cmds, turn) in enumerate(seg):
            changed = [(cid, prev.get(cid), up[cid]) for cid in up
                       if cid in up and str(cid).startswith("415")
                       and prev.get(cid) and up[cid][0] > prev[cid][0]]
            for cid, old, new in changed:
                n_inc += 1
                print("\n◆ 卡 %s ｜条件 %s（链 %s→%s）current %s → %s / total %s"
                      % (card, cid, str(cid)[:6], SPEC.get(int(str(cid)[:6]), "?"),
                         old[0], new[0], new[1]))
                lo = max(0, i - 3)
                for j in range(lo, min(len(seg), i + 2)):
                    s = seg[j]
                    mark = "◀" if j == i else " "
                    print("   %s %-30s %s" % (mark, s[0],
                          "命令/事件：%s" % (",".join(s[4]) or "（response）")))
            prev = up
    print("\n== 定位到的增量共 %d 次 ==" % n_inc)
    if not n_inc:
        print("  零增量 ⇒ 我们抓到的局里计数器从没动过（那 §6.5 的『满足』来自初始值，不是过程量）"
              "⇒ 直接支持解释①：`current_count>=total_count` 不是进化门槛。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
