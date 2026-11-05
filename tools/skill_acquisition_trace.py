#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#82 第 1 步：技能到底是**哪一下**到手的——报文逐回合轨迹。

为什么先跑这个：`docs/技能腿背包余量_20261106.md` 量到技能分占 LG15 总分 31.7%、其中 40% 不吃 PT，
而引擎里根本没有「技能清单」这个对象（`mod.rs:1717` 只是把存量 PT 过曲线）。
但 370 匹高分马只有终局快照 ⇒ 组内量不出任何斜率（卡组 −7…+5 条，见 #82）。
要判断「产量能不能规划」，只能看**我们自己客户端的逐回合报文**：
每次 `chara_info.skill_array` 变长，那一刻屏幕上正在发生什么。

三张表（都是实测，不引入任何常数）：
  T-A 每条**新到手**的技能，归因到当回合的动作族（训练 101–106／稽古 5101–5105／比赛 701／
      外出／事件 check_event／商店结算 gain_skills／其他）或事件号；
  T-B **金技能（dis=1）与固有（无价目）**到底从哪一族来——这是 #81 第 2 步要的答案；
  T-C 局级：我们打的局最终几条、和 370 匹（med 106）差多少、差在哪个族。

只读 `hachimi_raw` ＋ 只读 master.mdb ＋ 只读终局 corpus；解码器复用同目录
`shop_offer_dis1_check.py` 的零依赖 msgpack 实现（不 import 姊妹仓）。
中间产物落 `out/skill_trace_packets.jsonl`，重跑不重复解码。
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shop_offer_dis1_check as D  # noqa: E402  复用解码器 + 路径常量

RAW = D.RAW_DIR
MASTER = D.MASTER
TRACE = os.path.join(D.OUT_DIR, "skill_trace_packets.jsonl")

CMD = b"single_mode_exec_command_request_common"
EVT = b"single_mode_check_event_request_common"
GAIN = b"single_mode_gain_skills_request_common"
CHARA = b"chara_info"

# 命令号族（一手，见 docs/命令号两族更正_20261106.md）
FAMILY = {
    **{i: "训练" for i in range(101, 107)},
    **{i: "稽古" for i in range(5101, 5106)},
    701: "比赛",
}
OTHER_CMD = {301: "外出?", 304: "外出?", 390: "其他?", 401: "休息?", 801: "分身/追加?"}


def g(path):
    with open(path, "rb") as f:
        return D.dpb(f.read())


def turn_of(obj):
    for key in (CMD, EVT, GAIN):
        node = obj.get(key.decode()) if isinstance(obj, dict) else None
        if isinstance(node, dict):
            t = D.as_int(node.get("current_turn"))
            if t is not None:
                return t
    ci, _ = D.dig(obj, "current_turn")
    return D.as_int(ci)


def extract(nm):
    """单包 → 一行记录（None＝与本调查无关）。"""
    p = os.path.join(RAW, nm)
    try:
        with open(p, "rb") as f:
            buf = f.read()
    except OSError:
        return None
    if not any(k in buf for k in (CMD, EVT, GAIN, CHARA)):
        return None
    try:
        obj = D.dpb(buf)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    kind = None
    rec = {"file": nm, "turn": turn_of(obj), "viewer": D.as_int(obj.get("viewer_id")),
           "ts": int(os.path.getmtime(p))}
    node = obj.get(CMD.decode())
    if isinstance(node, dict):
        kind = "cmd"
        rec["command_id"] = D.as_int(node.get("command_id"))
        rec["command_group_id"] = D.as_int(node.get("command_group_id"))
        rec["select_id"] = D.as_int(node.get("select_id"))
        rec["vital"] = D.as_int(node.get("current_vital"))
    node = obj.get(EVT.decode())
    if isinstance(node, dict):
        kind = "evt" if kind is None else kind + "+evt"
        rec["event_id"] = D.as_int(node.get("event_id"))
        rec["evt_chara_id"] = D.as_int(node.get("chara_id"))
        rec["choice"] = D.as_int(node.get("choice_number"))
    node = obj.get(GAIN.decode())
    if isinstance(node, dict):
        kind = "gain"
        rec["pool"] = D.ids_of(node.get("gain_skill_info_array"))
    ci, _ = D.dig(obj, "chara_info")
    if isinstance(ci, dict):
        if kind is None:
            kind = "resp"
        rec["skills"] = D.ids_of(ci.get("skill_array"))
        rec["pt"] = D.as_int(ci.get("skill_point")) or D.as_int(ci.get("total_skill_point"))
        rec["mode_chara"] = D.as_int(ci.get("single_mode_chara_id"))
        rec["card_id"] = D.as_int(ci.get("card_id"))
        rec["vital_now"] = D.as_int(ci.get("vital"))
    if kind is None:
        return None
    rec["kind"] = kind
    return rec


def build_trace():
    if os.path.exists(TRACE):
        rows = [json.loads(l) for l in open(TRACE, encoding="utf-8") if l.strip()]
        if rows and all("ts" in r for r in rows[:50]):
            print("[缓存] %s：%d 行" % (os.path.basename(TRACE), len(rows)))
            return rows
        print("[缓存] 旧格式（无 ts），重扫全语料")
    files = sorted(f for f in os.listdir(RAW) if f.endswith(".bin"))
    rows = []
    for i, nm in enumerate(files, 1):
        r = extract(nm)
        if r:
            rows.append(r)
        if i % 2000 == 0:
            print("  解码 %d/%d（留 %d）" % (i, len(files), len(rows)))
    with open(TRACE, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("[落盘] %s：%d 行（全语料 %d 包）" % (TRACE, len(rows), len(files)))
    return rows


def order_and_inherit(rows):
    """按 (mtime, 文件名) 排序；response 侧没有 `current_turn`（实测 0/4,222 带），
    所以回合号只能**从前一个带 turn 的包继承**——同一次点击的 request→response 相邻，
    继承是安全的。继承来的打上 `turn_inherited`，读数时能分清。"""
    rows = sorted(rows, key=lambda r: (r.get("ts") or 0, r["file"]))
    last = None
    last_mc, last_card = None, None
    for r in rows:
        if r.get("turn") is not None:
            last = r["turn"]
            r["turn_inherited"] = False
        elif last is not None:
            r["turn"] = last
            r["turn_inherited"] = True
        # request 侧不带 `single_mode_chara_id`／`card_id`（实测 0/3,149 带）
        # ⇒ 局身份只能从前一个 response 继承，否则一局会被切成十几个碎片（第一版就踩了）。
        if r.get("mode_chara") is not None:
            last_mc = r["mode_chara"]
            r["card_id_owner"] = r.get("card_id")
        elif last_mc is not None:
            r["mode_chara"] = last_mc
            r["card_id"] = last_card
            r["id_inherited"] = True
        if r.get("card_id") is not None:
            last_card = r["card_id"]
    return rows


def split_games(rows, gap_s=1800):
    """切局：回合回落 / 育成马换了 / 时间断档 >gap_s ⇒ 新的一局。"""
    games, cur, last_t, last_ts, last_mc = [], [], -1, 0, None
    for r in rows:
        t = r.get("turn")
        if t is None:
            continue
        mc = r.get("mode_chara")
        new = (t + 3 < last_t) or (last_ts and (r["ts"] - last_ts) > gap_s) \
            or (mc is not None and last_mc is not None and mc != last_mc)
        if new and cur:
            games.append(cur)
            cur = []
        cur.append(r)
        last_t, last_ts = t, r["ts"]
        if mc is not None:
            last_mc = mc
    if cur:
        games.append(cur)
    return [gp for gp in games if len({x["turn"] for x in gp}) >= 10]


def main():
    con = sqlite3.connect("file:%s?mode=ro" % MASTER.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    sk = {r["id"]: r for r in con.execute(
        "SELECT id,grade_value,rarity,disable_singlemode FROM skill_data")}
    price = {r["id"]: r["need_skill_point"] for r in con.execute(
        "SELECT id,need_skill_point FROM single_mode_skill_need_point")}
    con.close()

    def cls(sid):
        r = sk.get(sid)
        if r is None:
            return "无表条目"
        p = price.get(sid)
        if p is None:
            return "无价目(固有系)"
        return "可直购" if int(r["disable_singlemode"]) == 0 else "金(dis=1)"

    rows = order_and_inherit(build_trace())
    inh = sum(1 for r in rows if r.get("turn_inherited"))
    print("\n== 覆盖 ==")
    print("  留用包 %d（其中 response 侧回合号靠继承得到 %d 个；带 turn 的原始包 %d）"
          % (len(rows), inh, len(rows) - inh))
    games = split_games(rows)
    print("  可用轨迹局数（覆盖 ≥10 个回合）：%d" % len(games))

    # 逐局：把每回合的「动作族集合」和 skill_array 长度对齐
    gain_by_family = Counter()
    gain_grade = defaultdict(int)
    gain_by_event = Counter()
    gain_when = Counter()
    gain_when_grade = Counter()
    finals = []
    n_traced = 0
    for gp in games:
        by_turn = defaultdict(lambda: {"fams": set(), "evts": set(), "skills": None})
        for r in gp:
            b = by_turn[r["turn"]]
            cid = r.get("command_id")
            if cid is not None:
                b["fams"].add(FAMILY.get(cid) or OTHER_CMD.get(cid) or ("命令%s" % cid))
            if r.get("event_id") is not None:
                b["evts"].add(r["event_id"])
            if r.get("kind") == "gain":
                b["fams"].add("商店结算")
            if r.get("skills") is not None:
                b["skills"] = r["skills"]
        prev = set()
        n_traced += 1
        seen_all = set()
        for t in sorted(by_turn):
            cur = set(by_turn[t]["skills"] or [])
            if not cur:
                continue
            seen_all |= cur
            new = cur - prev
            if new:
                b = by_turn[t]
                fam = "+".join(sorted(b["fams"])) if b["fams"] else (
                    "事件" if b["evts"] else "无动作可归")
                for sid in new:
                    gain_by_family[fam] += 1
                    gain_grade[cls(sid)] += int(sk[sid]["grade_value"] or 0) if sid in sk else 0
                    when = "结算段(t≥78)" if t >= 78 else "中途(t<78)"
                    gain_when[(cls(sid), when)] += 1
                    gain_when_grade[(cls(sid), when)] += (
                        int(sk[sid]["grade_value"] or 0) if sid in sk else 0)
                    for e in (b["evts"] or {None}):
                        if e is not None:
                            gain_by_event[(fam, e)] += 1
            prev = cur
        finals.append((gp[0]["file"], len(seen_all), max(by_turn), gp[0].get("card_id")))

    print("\n== T-A 新到手的技能归因到哪一族（全语料累计）==")
    tot = sum(gain_by_family.values())
    for fam, c in gain_by_family.most_common(14):
        print("  %-22s %6d 条  %5.1f%%" % (fam, c, 100.0 * c / max(1, tot)))

    print("\n== T-B 按类别拆（金/固有 到底从哪来）==")
    for k, v in sorted(gain_grade.items(), key=lambda kv: -kv[1]):
        print("  %-14s 累计 Σgrade %10s" % (k, f"{v:,}"))
    # 按族列出该族下最高频的事件号
    ev_by_fam = defaultdict(Counter)
    for (fam, e), c in gain_by_event.items():
        ev_by_fam[fam][e] += c
    print("\n  %-22s %s" % ("族", "该族下 top 事件号（次数）"))
    for fam, _ in gain_by_family.most_common(6):
        top = ev_by_fam.get(fam, Counter()).most_common(4)
        print("  %-22s %s" % (fam, " ".join("%s:%d" % (e, c) for e, c in top) or "—"))

    print("\n== T-D 到手时点：中途 vs t78 结算段（这是「能不能规划」的分水岭）==")
    print("  %-14s %-16s %8s %12s" % ("类别", "时点", "条数", "Σgrade"))
    for (cat, when), c in sorted(gain_when.items()):
        print("  %-14s %-16s %8d %12s" % (cat, when, c, f"{gain_when_grade[(cat, when)]:,}"))

    print("\n== T-C 我们自己的局最终几条（对照 370 匹 med 106）==")
    if finals:
        n = [f[1] for f in finals]
        print("  局数 %d ｜ 条数 med %s ｜ min %d max %d ｜ 覆盖回合中位 %s"
              % (len(n), f"{statistics.median(n):,.0f}", min(n), max(n),
                 statistics.median([f[2] for f in finals])))
        print("  最差 5 局 / 最好 5 局：")
        for f in sorted(finals, key=lambda x: x[1])[:5]:
            print("    %-24s %3d 条（末回合 %d，育成卡 %s）" % (f[0], f[1], f[2], f[3]))
        for f in sorted(finals, key=lambda x: -x[1])[:5]:
            print("    %-24s %3d 条（末回合 %d，育成卡 %s）" % (f[0], f[1], f[2], f[3]))
    else:
        print("  没切出完整局——检查 turn 回落判据")
    print("\n[边界] n_traced=%d；技能集合按**单调增长**处理（清单里消失的条目不计减少），"
          "进化替换会算成一次新增。" % n_traced)
    return 0


if __name__ == "__main__":
    sys.exit(main())
