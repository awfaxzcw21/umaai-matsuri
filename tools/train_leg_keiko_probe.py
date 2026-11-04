"""稽古手的「实到 vs 板上预告」反查（#40 的口径修正）。

问题：合宿板同一 packet 里 101-106 和 5101-5105 **同时出现**，且 510x 的预告比同槽
10x 的预告平均高 +25 Pt（931 格里 928 格如此）。⇒ 板上 510x 那条到底是
「home 腿 + 演目腿 = 实到」，还是只报演目腿？这决定 #40 的对照该用哪一列。

判据用**实到**：`实到 = 下一块板的 skill_point − 本块板的 skill_point`（同一口径见
`tools/keiko_curve.py:19`）。逐手比三个数：510x 预告、同槽 10x 预告、实到。

    python tools/train_leg_keiko_probe.py
"""

import collections
import glob
import os
import statistics
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
AXES = ("speed", "stamina", "power", "guts", "wiz")
TT_PT = 30
PLAIN = {101, 102, 103, 104, 105, 106}
KEIKO = {5101, 5102, 5103, 5104, 5105}


def entry_pt(entry):
    for e in entry.get("params_inc_dec_info_array") or []:
        if e.get("target_type") == TT_PT:
            return e.get("value")
    return None


def five_sum(ci):
    return sum(int(ci.get(k) or 0) for k in ("speed", "stamina", "power", "guts", "wiz"))


def board_of(data):
    """一块板：(turn, sp, 五维和, {cid: Pt 预告})。缺字段返回 None。"""
    ci = data.get("chara_info")
    cia = (data.get("home_info") or {}).get("command_info_array")
    if not isinstance(ci, dict) or ci.get("turn") is None or not cia:
        return None
    sp = ci.get("skill_point")
    if not isinstance(sp, int):
        return None
    prev = {}
    for c in cia:
        cid = c.get("command_id")
        if cid in PLAIN or cid in KEIKO:
            v = entry_pt(c)
            if v is not None:
                prev[cid] = v
    return {"turn": ci["turn"], "run": ci.get("single_mode_chara_id"), "sp": sp,
            "five": five_sum(ci), "prev": prev}


def main():
    boards = []      # 最近一块带 510x 的板，等它的下一块
    rows = []
    stats = collections.Counter()
    pend = []        # 已配对请求，等实到
    for path in sorted(glob.glob(os.path.join(RAW, "*.bin")), key=PS.sort_key):
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if name.endswith("_notify_request.bin"):
            hit = next((k for k in d if k in PS.CHANNELS), None)
            if hit is None or PS.CHANNELS[hit] != "cmd":
                continue
            common = d[hit] or {}
            if common.get("command_type") != 1 or not boards:
                continue
            cid = common.get("command_id")
            b = boards[-1]
            if cid not in b["prev"]:
                continue
            pend.append(dict(b, cid=cid, used=b["prev"][cid]))
            stats["req"] += 1
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        b = board_of(data)
        if b is None:
            continue
        got = 0
        for p in pend:
            # 只认**同一回合**的下一块板：跨回合的 delta 会把事件/比赛 PT 混进来
            if p["run"] != b["run"] or b["turn"] != p["turn"]:
                continue
            rows.append(dict(p, d_sp=b["sp"] - p["sp"], d_five=b["five"] - p["five"],
                             post_turn=b["turn"]))
            got += 1
        stats["paired"] += got
        stats["dropped"] += len(pend) - got
        pend = []
        boards.append(b)

    kei = [r for r in rows if r["cid"] in KEIKO]
    pla = [r for r in rows if r["cid"] in PLAIN]
    print("训练手请求 %d｜同回合有后板的 %d｜因跨回合/换 run 丢弃 %d" % (
        stats["req"], stats["paired"], stats["dropped"]))
    print("配对成功：稽古手 %d，普通训练手 %d" % (len(kei), len(pla)))

    def rep(name, g):
        if not g:
            return
        # 买技能会让 d_sp 为负，先剔掉（只留非负的增量手）
        ok = [r for r in g if r["d_sp"] >= 0]
        print("\n== %s（d_sp>=0 的 %d/%d 手）==" % (name, len(ok), len(g)))
        for lbl, key in (("板上预告", "used"), ("实到 d_sp", "d_sp")):
            v = [r[key] for r in ok]
            print("  %-10s 均值 %7.2f  中位 %7.1f" % (lbl, statistics.mean(v), statistics.median(v)))
        dif = [r["used"] - r["d_sp"] for r in ok]
        eq = sum(1 for r in ok if r["used"] == r["d_sp"])
        print("  预告−实到 均值 %+.2f 中位 %+.1f｜逐手完全相等 %d/%d (%.1f%%)" % (
            statistics.mean(dif), statistics.median(dif), eq, len(ok), 100.0 * eq / len(ok)))
        # 逐手三种对照
        n_home = n_tot = 0
        for r in ok:
            alt = r["prev"].get((r["cid"] - 5000) if r["cid"] in KEIKO else (r["cid"] + 5000))
            if alt is None:
                continue
            n_tot += 1
            if abs(alt - r["d_sp"]) <= abs(r["used"] - r["d_sp"]):
                n_home += 1
        if n_tot:
            print("  在有同槽另一腿的 %d 手里，另一腿更接近实到的：%d (%.1f%%)" % (n_tot, n_home, 100.0 * n_home / n_tot))

    rep("稽古手 510x", kei)
    rep("普通训练手 10x", pla)

    # 稽古手：510x 预告 vs (10x 预告 + festa) 的分解
    print("\n== 稽古手逐手：510x 预告 − 同槽 10x 预告 ==")
    dd = []
    for r in kei:
        alt = r["prev"].get(r["cid"] - 5000)
        if alt is not None and r["d_sp"] >= 0:
            dd.append((r["used"], alt, r["d_sp"]))
    if dd:
        print("  n=%d  510x %.2f｜10x %.2f｜实到 %.2f｜(510x−10x) %.2f｜(实到−10x) %.2f" % (
            len(dd), statistics.mean(x[0] for x in dd), statistics.mean(x[1] for x in dd),
            statistics.mean(x[2] for x in dd),
            statistics.mean(x[0] - x[1] for x in dd), statistics.mean(x[2] - x[1] for x in dd)))
        hit5 = sum(1 for x in dd if x[0] == x[2])
        print("  510x 预告 == 实到 的手数：%d/%d" % (hit5, len(dd)))


if __name__ == "__main__":
    main()
