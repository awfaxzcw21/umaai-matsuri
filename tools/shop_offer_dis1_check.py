#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shop_offer_dis1_check.py —— 一手验证「disable_singlemode=1 的技能吃不吃技能 PT」

背景（为什么要查这一条）
    `docs/技能腿背包余量_20261106.md` 把 370 匹高分马的技能清单按 master.mdb
    `skill_data.disable_singlemode` 劈成两半（dis=0 ⇒ 可 PT 直购 60%，dis=1 ⇒ 40%），
    而「dis=1 不能用技能 PT 直购」此前只有连接统计（`docs/ref/skill_grade_value_20261105.md`
    §7.2），属二手推断。本脚本用一手报文一票裁决。

要回答的唯一问题
    一手报文里「商店发牌的技能候选池」＝ `single_mode_gain_skills_request_common`
    请求体的 `gain_skill_info_array`，里面有没有出现过 `disable_singlemode=1` 的 skill id？

四个阶段（全部只读；不写姊妹仓、不写 data/、不碰 Rust、不跑 cargo）
    P1  字节级 grep 报文目录 → 解出所有 `single_mode_gain_skills_request_common`
        包（顺带扫「只带 gain_skill_info_array、不带该请求名」的包，防漏）。
    P2  每包 join master.mdb，报出候选条目里 dis=1 / dis=0 / 查无此 id 的分布。
        ⇒ 这就是字面问题的答案。
    P3  同会话配对的 response 里 `data/chara_info`：终局 `skill_array`、
        `disable_skill_id_array`、`skill_point`，做集合关系与「表价 − 实扣」拆分——
        用来判断 P2 里那些 dis=1 到底是「发牌可购」还是「已持有被回带」。
    P4  全语料抽样 `disable_skill_id_array`（发牌侧显式拉黑的技能列表），
        逐 id join master，看它是不是 100% 落在 dis=1 —— dis_singlemode 语义的一手对照。

用法
    python tools/shop_offer_dis1_check.py [--sample 700] [--skip-sample]
产物
    out/shop_offer_dis1_check.json（中间产物，gitignore 工作区）
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import sqlite3
import struct
import sys
from collections import Counter

RAW_DIR = r"E:\HJM\jidianbeiAI0.22\out\hachimi_raw"        # 只读
MASTER = r"E:\HJM\_ref01\matsuri-ref-01\data\master.mdb"   # 只读 uri=ro
OUT_DIR = r"E:\HJM\umaai-matsuri\out"
OUT_JSON = os.path.join(OUT_DIR, "shop_offer_dis1_check.json")
HIGHSCORE = os.path.join(OUT_DIR, "_lf_ref_highscore.json")  # 只读，370 匹一手终局
NEEDLE_REQ = b"single_mode_gain_skills_request_common"
NEEDLE_ARR = b"gain_skill_info_array"
REQ_KEY = NEEDLE_REQ.decode()
ARR_KEY = NEEDLE_ARR.decode()


# ---------------------------------------------------------------- msgpack
# 自带零依赖解码器（本机 Python 无 msgpack 包；也不 import 姊妹仓，
# 免得在只读的 jidianbeiAI0.22 里落 __pycache__）。
class _R:
    __slots__ = ("b", "p", "n")

    def __init__(self, buf: bytes) -> None:
        self.b, self.p, self.n = buf, 0, len(buf)

    def t(self, k: int) -> bytes:
        e = self.p + k
        if e > self.n:
            raise ValueError(f"truncated: want {k} have {self.n - self.p}")
        v = self.b[self.p:e]
        self.p = e
        return v

    def u(self, fmt: str):
        return struct.unpack(fmt, self.t(struct.calcsize(fmt)))[0]

    def rd(self):
        b = self.t(1)[0]
        if b <= 0x7F:
            return b
        if 0x80 <= b <= 0x8F:
            return self.map_n(b & 0x0F)
        if 0x90 <= b <= 0x9F:
            return self.arr_n(b & 0x0F)
        if 0xA0 <= b <= 0xBF:
            return self.t(b & 0x1F).decode("utf-8", "replace")
        if 0xE0 <= b <= 0xFF:
            return b - 256
        if b == 0xC0:
            return None
        if b in (0xC2, 0xC3):
            return b == 0xC3
        if b == 0xC4:
            return self.t(self.u(">B"))
        if b == 0xC5:
            return self.t(self.u(">H"))
        if b == 0xC6:
            return self.t(self.u(">I"))
        if b == 0xCA:
            return self.u(">f")
        if b == 0xCB:
            return self.u(">d")
        if b == 0xCC:
            return self.u(">B")
        if b == 0xCD:
            return self.u(">H")
        if b == 0xCE:
            return self.u(">I")
        if b == 0xCF:
            return self.u(">Q")
        if b == 0xD0:
            return self.u(">b")
        if b == 0xD1:
            return self.u(">h")
        if b == 0xD2:
            return self.u(">i")
        if b == 0xD3:
            return self.u(">q")
        if 0xD4 <= b <= 0xD8:
            ln = (1, 2, 4, 8, 16)[b - 0xD4]
            return ("ext", self.t(1)[0], self.t(ln))
        if b == 0xD9:
            return self.t(self.u(">B")).decode("utf-8", "replace")
        if b == 0xDA:
            return self.t(self.u(">H")).decode("utf-8", "replace")
        if b == 0xDB:
            return self.t(self.u(">I")).decode("utf-8", "replace")
        if b == 0xDC:
            return self.arr_n(self.u(">H"))
        if b == 0xDD:
            return self.arr_n(self.u(">I"))
        if b == 0xDE:
            return self.map_n(self.u(">H"))
        if b == 0xDF:
            return self.map_n(self.u(">I"))
        if b in (0xC7, 0xC8, 0xC9):            # ext8/16/32
            ln = {0xC7: self.u(">B"), 0xC8: self.u(">H"), 0xC9: self.u(">I")}[b]
            return ("ext", self.t(1)[0], self.t(ln))
        raise ValueError(f"unknown opcode 0x{b:02X} at {self.p - 1}")

    def arr_n(self, k: int):
        return [self.rd() for _ in range(k)]

    def map_n(self, k: int):
        return {self.rd(): self.rd() for _ in range(k)}


def dpb(buf: bytes):
    return _R(buf).rd()


# ---------------------------------------------------------------- helpers
def as_int(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def dig(obj, name):
    """递归找出所有 key==name 的值（list 或标量），返回第一个 / 全部。"""
    first = None
    alls = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k == name:
                    alls.append(v)
                    if first is None:
                        first = v
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return first, alls


def ids_of(arr):
    """把 skill_array / gain_skill_info_array 这类 [{skill_id,level}] 抽成 id 列表。"""
    out = []
    for e in arr or []:
        if isinstance(e, dict):
            sid = as_int(e.get("skill_id"))
            if sid is not None:
                out.append(sid)
        else:
            sid = as_int(e)
            if sid is not None:
                out.append(sid)
    return out


def key_paths(obj):
    found = set()
    stack = [(obj, "")]
    while stack:
        cur, p = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                kp = f"{p}/{k}" if isinstance(k, str) else f"{p}/<noname>"
                if isinstance(k, str):
                    found.add(kp)
                stack.append((v, kp))
        elif isinstance(cur, list):
            for v in cur:
                stack.append((v, p + "[]"))
    return sorted(found)


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=700,
                    help="P4 抽样 response 包数（0＝跳过 P4）")
    ap.add_argument("--skip-sample", action="store_true", help="等价 --sample 0")
    args = ap.parse_args()

    if not os.path.isdir(RAW_DIR):
        print(f"[FATAL] 报文目录不存在: {RAW_DIR}", file=sys.stderr)
        return 2
    if not os.path.isfile(MASTER):
        print(f"[FATAL] master.mdb 不存在: {MASTER}", file=sys.stderr)
        return 2
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- 表侧
    con = sqlite3.connect(f"file:{MASTER}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sk = {r["id"]: dict(r) for r in con.execute(
        "SELECT id, rarity, grade_value, disable_singlemode, disable_count_condition "
        "FROM skill_data WHERE disable_singlemode IS NOT NULL")}
    price = {r["id"]: r["need_skill_point"] for r in con.execute(
        "SELECT id, need_skill_point FROM single_mode_skill_need_point")}
    n_dis0 = sum(1 for v in sk.values() if v["disable_singlemode"] == 0)
    n_dis1 = sum(1 for v in sk.values() if v["disable_singlemode"] == 1)
    print(f"[mdb ] skill_data {len(sk)} 行（dis=0 共 {n_dis0}／dis=1 共 {n_dis1}）；"
          f"价目表 {len(price)} 行")

    def dis(sid):
        r = sk.get(sid)
        return None if r is None else r["disable_singlemode"]

    # ---- P1 扫包
    bins = [f for f in sorted(os.listdir(RAW_DIR)) if f.endswith(".bin")]
    hits_req, hits_arr_only, fails = [], [], []
    for nm in bins:
        p = os.path.join(RAW_DIR, nm)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "rb") as f:
                buf = f.read()
        except OSError as e:
            fails.append((nm, f"io: {e}"))
            continue
        if NEEDLE_REQ in buf:
            hits_req.append((nm, buf))
        elif NEEDLE_ARR in buf:
            hits_arr_only.append((nm, buf))
    print(f"[P1  ] 报文 {len(bins)} 个；带 {REQ_KEY} 的 {len(hits_req)} 包；"
          f"只带 {ARR_KEY} 而不带请求名的 {len(hits_arr_only)} 包"
          + ("（一并核查）" if hits_arr_only else ""))

    # ---- P2 候选池逐包
    packets = []
    all_dis1, all_unknown = Counter(), Counter()
    for nm, buf in hits_req + hits_arr_only:
        try:
            obj = dpb(buf)
        except Exception as e:                                # noqa: BLE001
            print(f"[decode FAIL] {nm}: {e}")
            fails.append((nm, f"decode: {e}"))
            continue
        req, _ = dig(obj, REQ_KEY)
        req = req if isinstance(req, dict) else obj
        arrays = [v for v in dig(req, ARR_KEY)[1] if isinstance(v, list)]
        turn = as_int(dig(req, "current_turn")[0])
        viewer = as_int(dig(obj, "viewer_id")[0])
        cand = []
        for arr in arrays:
            for item in arr:
                sid = as_int(item.get("skill_id")) if isinstance(item, dict) else None
                if sid is None:
                    continue
                r = sk.get(sid)
                cand.append({
                    "skill_id": sid,
                    "level": as_int(item.get("level")) if isinstance(item, dict) else None,
                    "disable_singlemode": None if r is None else r["disable_singlemode"],
                    "rarity": None if r is None else r["rarity"],
                    "grade_value": None if r is None else r["grade_value"],
                    "need_skill_point": price.get(sid),
                })
        ids = [c["skill_id"] for c in cand]
        d1 = [c for c in cand if c["disable_singlemode"] == 1]
        d0 = [c for c in cand if c["disable_singlemode"] == 0]
        unk = [c for c in cand if c["disable_singlemode"] is None]
        all_dis1.update(c["skill_id"] for c in d1)
        all_unknown.update(c["skill_id"] for c in unk)
        priced = sum(c["need_skill_point"] or 0 for c in cand)
        pk = {
            "file": nm, "current_turn": turn, "viewer_id": viewer,
            "skill_set_deck_id": as_int(dig(req, "skill_set_deck_id")[0]),
            "exec_auto_play_plan_id": as_int(dig(req, "exec_auto_play_plan_id")[0]),
            "n_slots": len(cand), "n_unique": len(set(ids)),
            "n_dis0": len(d0), "n_dis1": len(d1), "n_not_in_skill_data": len(unk),
            "pool_price_sum": priced,
            "dis1_ids": sorted({c["skill_id"] for c in d1}),
            "request_key_paths": [k for k in key_paths(obj) if ARR_KEY in k or "turn" in k],
            "candidates": cand,
        }
        packets.append(pk)
        print(f"[P2  ] {nm} turn={turn} viewer={viewer} deck={pk['skill_set_deck_id']} "
              f"候选 {pk['n_slots']} 条（去重 {pk['n_unique']}）"
              f" dis0={len(d0)} dis1={len(d1)} 查无={len(unk)} 表价合计={priced}")

    total_slots = sum(p["n_slots"] for p in packets)
    total_d1 = sum(p["n_dis1"] for p in packets)
    print(f"\n[P2  ] 字面判定：{total_slots} 条候选里 dis=1 出现 {total_d1} 条，"
          f"去重 {len(all_dis1)} 个 id")
    for sid, n in sorted(all_dis1.items()):
        r = sk[sid]
        print(f"        {sid} x{n} dis={r['disable_singlemode']} "
              f"rarity={r['rarity']} grade={r['grade_value']} "
              f"need_point={price.get(sid)}")
    if all_unknown:
        print("        skill_data 查无的 id：" + ", ".join(map(str, sorted(all_unknown))))

    # ---- P3 配对 response
    seqmap = {nm: i for i, nm in enumerate(bins)}
    resp_cache = {}

    def resp_obj(nm):
        if nm in resp_cache:
            return resp_cache[nm]
        o = None
        try:
            with open(os.path.join(RAW_DIR, nm), "rb") as f:
                o = dpb(f.read())
        except Exception:                                     # noqa: BLE001
            o = None
        resp_cache[nm] = o
        return o

    print("\n[P3  ] 配对 response（同会话下一号包）→ 终局列表 / disable_skill_id_array / PT")
    for pk in packets:
        nm = pk["file"]
        i = seqmap.get(nm)
        pair = None
        if i is not None:
            want_seq = int(nm.split("_")[1]) + 1
            for j in range(i + 1, min(i + 8, len(bins))):
                f2 = bins[j]
                if f2.endswith("_notify_response.bin") and \
                        int(f2.split("_")[1]) == want_seq:
                    pair = f2
                    break
        ci = None
        if pair:
            o = resp_obj(pair)
            if o is not None:
                ci = dig(o, "chara_info")[0]
        if not isinstance(ci, dict):
            print(f"        {nm}: 配不到带 chara_info 的 response（{pair}）")
            pk["response"] = None
            continue
        fin = ids_of(ci.get("skill_array"))
        disarr = ids_of(ci.get("disable_skill_id_array"))
        pool = {c["skill_id"] for c in pk["candidates"]}
        F, P = set(fin), pool
        extras = P - F                       # 池里有、终局没有 ⇒ 真正「没买成的发牌项」
        row = {
            "response_file": pair,
            "single_mode_chara_id": as_int(ci.get("single_mode_chara_id")),
            "card_id": as_int(ci.get("card_id")),
            "stats": tuple(as_int(ci.get(k)) for k in
                           ("speed", "stamina", "power", "wiz", "guts")),
            "skill_point_after": as_int(ci.get("skill_point")),
            "n_final": len(F),
            "final_price_sum": sum(price.get(i) or 0 for i in F),
            "final_dis1_price_sum": sum(price.get(i) or 0 for i in F if dis(i) == 1),
            "final_dis0_price_sum": sum(price.get(i) or 0 for i in F if dis(i) == 0),
            "n_final_dis1": sum(1 for i in F if dis(i) == 1),
            "pool_minus_final": len(extras),
            "pool_minus_final_dis1": sum(1 for i in extras if dis(i) == 1),
            "pool_minus_final_dis0": sum(1 for i in extras if dis(i) == 0),
            "pool_minus_final_unknown": sum(1 for i in extras if dis(i) is None),
            "final_minus_pool": sorted(F - P),
            "disable_skill_id_array": sorted(disarr),
            "disable_array_all_dis1": (None if not disarr else
                                       all(dis(i) == 1 for i in disarr)),
            "disable_array_in_pool": sorted(set(disarr) & P),
            "disable_array_in_final": sorted(set(disarr) & F),
        }
        pk["response"] = row
        print(f"        {nm} ⇐ {pair} 终局 {row['n_final']} 条"
              f"（dis1 {row['n_final_dis1']} 条／表价 {row['final_price_sum']}，"
              f"其中 dis=1 占 {row['final_dis1_price_sum']}、dis=0 占 {row['final_dis0_price_sum']}）"
              f" PT剩={row['skill_point_after']}")
        print(f"            池−终局 {row['pool_minus_final']} 条 ⇒ "
              f"dis1={row['pool_minus_final_dis1']} dis0={row['pool_minus_final_dis0']} "
              f"查无={row['pool_minus_final_unknown']}；终局−池={row['final_minus_pool']}")
        print(f"            disable_skill_id_array={row['disable_skill_id_array']} "
              f"全为 dis=1? {row['disable_array_all_dis1']}（None＝本包该数组为空）"
              f" 出现在池里? {row['disable_array_in_pool']}")

    # ---- P4 全语料抽样 disable_skill_id_array ⇒ dis_singlemode 语义一手对照
    p4 = None
    sample_n = 0 if args.skip_sample else args.sample
    if sample_n:
        random.seed(20261106)
        rfiles = [f for f in bins if f.endswith("_notify_response.bin")]
        chosen = random.sample(rfiles, min(sample_n, len(rfiles)))
        cnt = Counter()
        n_entries = n_files_hit = n_unknown = 0
        for nm in chosen:
            try:
                with open(os.path.join(RAW_DIR, nm), "rb") as f:
                    o = dpb(f.read())
            except Exception:                                 # noqa: BLE001
                continue
            _, arrs = dig(o, "disable_skill_id_array")
            arrs = [a for a in arrs if isinstance(a, list)]
            if not arrs:
                continue
            n_files_hit += 1
            for a in arrs:
                for sid in ids_of(a):
                    n_entries += 1
                    r = sk.get(sid)
                    if r is None:
                        n_unknown += 1
                        continue
                    cnt[(r["disable_singlemode"], r["disable_count_condition"])] += 1
        tot = sum(cnt.values())
        p4 = {"sampled_files": len(chosen), "files_with_field": n_files_hit,
              "entries": n_entries, "not_in_skill_data": n_unknown,
              "flag_dist": {f"dis_singlemode={k[0]},dis_count_cond={k[1]}": v
                            for k, v in sorted(cnt.items())},
              "frac_dis1": (None if not tot else cnt[(1, 0)] / tot)}
        print(f"\n[P4  ] 抽样 {p4['sampled_files']} 个 response，{n_files_hit} 个带 "
              f"disable_skill_id_array，共 {n_entries} 条 id（查无 {n_unknown}）")
        for k, v in sorted(cnt.items()):
            print(f"        (disable_singlemode,disable_count_condition)={k} → {v} 条")
        print(f"        其中 disable_singlemode=1 占比 "
              f"{'—' if p4['frac_dis1'] is None else format(p4['frac_dis1'], '.4f')}")

    # ---- P5 能不能对上某匹终局马
    match = {}
    if os.path.isfile(HIGHSCORE):
        try:
            with open(HIGHSCORE, encoding="utf-8") as f:
                hs = json.load(f)
        except Exception:                                     # noqa: BLE001
            hs = []
        recs = []
        for r in hs if isinstance(hs, list) else []:
            sa = r.get("skill_array")
            if isinstance(sa, str):
                try:
                    sa = ast.literal_eval(sa)
                except Exception:                             # noqa: BLE001
                    sa = []
            recs.append((r, {x for x in ids_of(sa)}))
        for pk in packets:
            row = pk.get("response") or {}
            pool = {c["skill_id"] for c in pk["candidates"]}
            best = None
            for r, S in recs:
                if not S:
                    continue
                st = tuple(r.get(k) for k in
                           ("speed", "stamina", "power", "wiz", "guts"))
                key_ok = (r.get("single_mode_chara_id") == row.get("single_mode_chara_id")
                          and r.get("card_id") == row.get("card_id")
                          and st == tuple(row.get("stats") or ()))
                j = len(pool & S) / max(1, len(pool | S))
                score = (j, key_ok, r.get("viewer_id") == pk["viewer_id"])
                if best is None or score > best[0]:
                    best = (score, r.get("viewer_id"), len(S), j, key_ok,
                            sorted(i for i in (pool & S) if dis(i) == 1))
            if best:
                _, vid, nS, j, key_ok, ov1 = best
                match[pk["file"]] = {
                    "best_jaccard": round(j, 4), "rec_viewer_id": vid,
                    "rec_n_skill": nS, "same_viewer_id": vid == pk["viewer_id"],
                    "same_chara_and_card": key_ok, "overlap_dis1_ids": ov1}
                print(f"[P5  ] {pk['file']}: 高分库里最像的一条 jaccard={j:.3f} "
                      f"viewer={vid}（==报文 viewer? {match[pk['file']]['same_viewer_id']}）"
                      f"卡+马同号? {key_ok}")
            else:
                match[pk["file"]] = None
        print(f"[P5  ] 高分库记录 {len(recs)} 条（{HIGHSCORE}）")

    verdict = {
        "literal_dis1_in_array_slots": total_d1,
        "literal_dis1_unique_ids": sorted(all_dis1),
        "pool_minus_final_dis1_slots": sum(
            (pk.get("response") or {}).get("pool_minus_final_dis1", 0) for pk in packets),
        "disable_skill_id_array_all_dis1": (None if p4 is None else p4["frac_dis1"]),
    }
    print("\n" + "=" * 72)
    print(f"字面：候选数组里 dis=1 共 {total_d1} 条／{len(all_dis1)} 个去重 id")
    print(f"关键：池里「终局没有」的那批（＝真正没买成的发牌项）里 dis=1 = "
          f"{verdict['pool_minus_final_dis1_slots']} 条")
    print(f"对照：response 侧 disable_skill_id_array 全落在 dis=1 = "
          f"{verdict['disable_skill_id_array_all_dis1']}")

    out = {
        "raw_dir": RAW_DIR, "master": MASTER,
        "question": "gain_skill_info_array 里有没有 disable_singlemode=1 的 skill id",
        "verdict_summary": verdict,
        "dis1_unique_with_flags": {
            str(sid): {"count": n, **{k: sk[sid][k] for k in
                                      ("disable_singlemode", "rarity", "grade_value")},
                       "need_skill_point": price.get(sid)}
            for sid, n in sorted(all_dis1.items())},
        "not_in_skill_data": {str(k): v for k, v in sorted(all_unknown.items())},
        "packets": packets,
        "p4_disable_skill_id_array_sample": p4,
        "p5_highscore_match": match,
        "failures": fails[:20],
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[out ] {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
