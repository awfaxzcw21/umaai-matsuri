#!/usr/bin/env python3
"""开局继承（蓝因子）普查：引擎唯一的「种马」入口是 `data/ai_config.toml` 写死的
`blue_count = [15, 3, 0, 0, 0]` / `extra_count = [0, 30, 0, 0, 30, 30]`（unasim `InheritInfo`，
开局属性＝蓝因子×7，`umaai_next/crates/umasim/src/game/inherit.rs:18`）。
面板 state JSON 和面板仓里都没有蓝因子字段 ⇒ 引擎从来没按真机那局的实际继承装配过。
本脚本只用**一手报文 + master.mdb**把「那局实际是多少」量出来，不接线、不改 `data/`。

    python tools/blue_factor_census.py --probe [--limit N]   # 先看盘上有哪些开局侧字段
    python tools/blue_factor_census.py                        # 全量普查（要跑 ~1 分钟：报文解码两遍）
    python tools/blue_factor_census.py --all-frames            # 逐框打印，不按 (卡, 开局五轴) 去重

口径（由 --probe 定下来，不是猜的）：
- 开局框 = `single_mode_start_common`，取 `chara_info` 里 `scenario_id=15 且 turn=1` 的框；
  五轴字段是**具名**的（speed/stamina/power/guts/wiz），不存在轴序歧义。
- 真机那局的继承源 = `chara_info.succession_trained_chara_id_1/_2`（父母两格），
  完整记录（带 `factor_info_array`）从全量报文的 `trained_chara` / `succession_trained_chara_data`
  / `user_trained_chara_array` / `add_trained_chara_array` 里按 (viewer_id, trained_chara_id) 取**因子最多**的那次观测（同 #62 纪律）。
- 蓝因子 = master.mdb `succession_factor.factor_type=1`，只有 15 个 id：组别 1..5 × 稀有度 1..3。
  组别序按 speed/stamina/power/guts/wiz 的第 i 轴假设，由【4】的残差同号检验判它站不站得住。
- 反推路（开局五轴 ÷ 引擎公式）**要求 base 是对的**：【6】逐框验 `真机−卡组 ≥ base`，
  缺轴就当场报「这条路的参照不成立」，不拿它出结论。
- 不改引擎、不写 `data/`、不新增常数；接线要过配对 CRN 三分量并单独征得同意。
"""

import argparse
import collections
import glob
import os
import sqlite3
import sys

OWN_VIEWER = "238107205"
SCEN = 15
PACKET_DIR = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
AXES = ("speed", "stamina", "power", "guts", "wiz")
# 想看的开局侧字段（probe 用；命中什么就照什么定口径）
WANT = ("single_mode_start_common", "generate_succession_chara", "add_trained_chara_array",
        "succession_trained_chara_array", "init_status_array", "inherit_factor_array",
        "blue_factor_array", "factor_info_array", "trained_chara")


def walk(node, key, trail=()):
    """把任意深度、任意键名的节点挖出来，带路径（数组下标用数字）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield trail + (k,), v
            yield from walk(v, key, trail + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, key, trail + (i,))


def brief(o, cap=1400):
    """紧凑打印：字典只列前若干键，数组只给长度＋首元素。"""
    if isinstance(o, dict):
        s = "{" + ", ".join("%s:%s" % (k, brief(v)) for k, v in list(o.items())[:16]) + "}"
    elif isinstance(o, list):
        s = "[%d]{%s}" % (len(o), brief(o[0]) if o else "")
    elif isinstance(o, str) and len(o) > 28:
        s = o[:28] + "…"
    else:
        s = str(o)
    return s[:cap]


def probe(limit=0):
    if "E:/HJM/jidianbeiAI0.22" not in sys.path:
        sys.path.insert(1, "E:/HJM/jidianbeiAI0.22")
    import msgpack_lite as ml  # noqa: PLC0415

    files = sorted(glob.glob(os.path.join(PACKET_DIR, "*.bin")))
    if limit:
        files = files[:limit]
    hits = collections.defaultdict(list)      # 字段名 -> [(文件, 顶层路径, 值)]
    nfail = 0
    for p in files:
        try:
            d = ml.decode_file(p)
        except Exception:
            nfail += 1
            continue
        for key in WANT:
            for tr, v in walk(d, key):
                if len(hits[key]) < 6:
                    hits[key].append((os.path.basename(p), "/".join(map(str, tr)), v))
    print("报文 %d 个（解码失败 %d）" % (len(files) - nfail, nfail))
    for key in WANT:
        lst = hits.get(key) or []
        print("\n### %s ｜ 命中 %d 处（最多留 6 个样本）" % (key, len(lst)))
        for fn, tr, v in lst[:3]:
            print("  %s  路径 %s" % (fn, tr))
            print("    %s" % brief(v))
    return hits


RECORD_KEYS = ("trained_chara", "succession_trained_chara_data",
               "user_trained_chara_array", "add_trained_chara_array")


def factor_meta():
    """master.mdb 因子表：factor_id → (组别, 稀有度, 种类)，种类 1 = 青い因子。

    顺带取两条换算口径：`succession_initial_factor`（开局加算）与
    `succession_factor_effect`（同组 n 条 → value_1），都只按表原样打印，不合成新常数。
    """
    db = sqlite3.connect("file:%s?mode=ro" % MDB, uri=True)
    meta = {r[0]: (r[1], r[2], r[3]) for r in db.execute(
        "select factor_id, factor_group_id, rarity, factor_type from succession_factor")}
    init = [(r[0], r[1], r[2]) for r in db.execute(
        "select value_1, value_2, add_point from succession_initial_factor "
        "where factor_type=1 order by value_2, value_1")]
    step = sorted({(r[0], r[1]) for r in db.execute(
        "select effect_id, value_1 from succession_factor_effect "
        "where target_type between 1 and 5 and target_type=factor_group_id")})
    n_type = collections.Counter(r[0] for r in db.execute(
        "select factor_type from succession_factor"))
    return meta, init, step, n_type


def blues(fa, meta):
    """一条马娘记录的 `factor_info_array` → 逐轴蓝因子条数 / 逐轴蓝因子稀有度合计。"""
    cnt = [0] * 5
    rar = [0] * 5
    ncol = collections.Counter()
    unknown = 0
    for x in fa or []:
        m = meta.get(x.get("factor_id"))
        if m is None:
            unknown += 1
            continue
        grp, r, ftype = m
        ncol[ftype] += 1
        if ftype == 1 and 1 <= grp <= 5:
            cnt[grp - 1] += 1
            rar[grp - 1] += r
    return cnt, rar, ncol, unknown


def files_of(limit=0):
    fs = sorted(glob.glob(os.path.join(PACKET_DIR, "*.bin")))
    return fs[:limit] if limit else fs


def build_index(fs):
    """(viewer_id, trained_chara_id) → 最完整的一条马娘记录（因子最多的那次观测，同 #62 纪律）。"""
    idx = {}
    for p in fs:
        try:
            d = probe_decode(p)
        except Exception:
            continue
        for key in RECORD_KEYS:
            for node in _gather(d, key):
                for e in node if isinstance(node, list) else []:
                    if not isinstance(e, dict) or "trained_chara_id" not in e:
                        continue
                    k = (str(e.get("viewer_id")), e.get("trained_chara_id"))
                    fa = e.get("factor_info_array") or []
                    if len(fa) > len(idx.get(k, {}).get("factor_info_array") or []):
                        idx[k] = e
    return idx


def probe_decode(p):
    if "E:/HJM/jidianbeiAI0.22" not in sys.path:
        sys.path.insert(1, "E:/HJM/jidianbeiAI0.22")
    import msgpack_lite as ml  # noqa: PLC0415
    return ml.decode_file(p)


def _gather(node, key, out=None):
    """深度收集某个键的**所有**取值（含 list 形态的字段本身）。"""
    if out is None:
        out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.append(v)
            _gather(v, key, out)
    elif isinstance(node, list):
        for v in node:
            _gather(v, key, out)
    return out


def start_frames(fs):
    """自家 scen15 的 t1 开局框：[(文件, chara_info, 单_mode 节点)]。"""
    out = []
    for p in fs:
        try:
            d = probe_decode(p)
        except Exception:
            continue
        for n in _gather(d, "single_mode_start_common"):
            if not isinstance(n, dict):
                continue
            ci = n.get("chara_info")
            if not isinstance(ci, dict) or ci.get("scenario_id") != SCEN or ci.get("turn") != 1:
                continue
            par = n.get("add_trained_chara_array") or ci.get("add_trained_chara_array") or []
            own = str(n.get("viewer_id") or d.get("viewer_id") or "")
            if own != OWN_VIEWER and par:
                own = str(par[0].get("viewer_id"))
            if own != OWN_VIEWER:
                continue
            out.append((os.path.basename(p), ci, n))
    return out


def deck_bonus(node, ci, carddb):
    """卡组逐轴初始加成：cardDB `cardValue[完凸].initialBonus` 前五格求和（六号位是技能 pt）。"""
    tot = [0] * 5
    miss = []
    arr = node.get("support_card_array") or ci.get("support_card_array") or []
    for s in arr:
        cv = carddb.get(str(s.get("support_card_id")))
        if not cv or not cv.get("cardValue"):
            miss.append(s.get("support_card_id"))
            continue
        lv = min(int(s.get("limit_break_count") or 0), len(cv["cardValue"]) - 1)
        ib = cv["cardValue"][lv].get("initialBonus") or [0] * 6
        for j in range(5):
            tot[j] += int(ib[j])
    return tot, len(arr), miss


def census(args):
    for p in ("data/umaDB.json", "data/cardDB.json", "data/ai_config.toml"):
        if not os.path.exists(p):
            raise SystemExit("缺 %s：本脚本要从仓根跑" % p)
    import json  # noqa: PLC0415
    import tomllib  # noqa: PLC0415

    uma = json.load(open("data/umaDB.json", encoding="utf-8"))
    card = json.load(open("data/cardDB.json", encoding="utf-8"))
    cfg = tomllib.load(open("data/ai_config.toml", "rb"))
    meta, init_step, ladder, n_type = factor_meta()

    fs = files_of(args.limit)
    frames = start_frames(fs)
    idx = build_index(fs)

    print("=== 开局蓝因子普查 ===")
    print("报文 %d 个（--limit %s）｜自家 scen15 t1 开局框 %d 个｜马娘记录索引 %d 条"
          % (len(fs), args.limit or "全量", len(frames), len(idx)))
    print("引擎写死的继承输入：blue_count=%s extra_count=%s（开局属性＝蓝因子×7，"
          "见 unasim `inherit.rs:18`）" % (cfg["sim"]["blue_count"], cfg["sim"]["extra_count"]))
    print("master.mdb 因子种类分布：%s" % dict(sorted(n_type.items())))

    rows = []
    for fn, ci, node in frames:
        cid = ci.get("card_id")
        real = [int(ci.get(a) or 0) for a in AXES]
        deck, nd, miss = deck_bonus(node, ci, card)
        u = uma.get(str(cid)) or {}
        base = [int(x) for x in (u.get("fiveStatusInitial") or [0] * 5)]
        resid = [real[j] - base[j] - deck[j] for j in range(5)]
        par_ids = (ci.get("succession_trained_chara_id_1"), ci.get("succession_trained_chara_id_2"))
        ps = []
        for pid in par_ids:
            rec = idx.get((OWN_VIEWER, pid))
            if rec is None:
                ps.append(None)
                continue
            cnt, rar, ncol, unk = blues(rec.get("factor_info_array"), meta)
            ps.append((pid, rec.get("card_id"), rec.get("rarity"), cnt, rar, len(ncol), unk))
        # 对照组：本框自带那 3 条「新增副本」记录（租借来的父母本体）的蓝因子逐轴和
        addg = [0] * 5
        addn = 0
        for e in (node.get("add_trained_chara_array") or ci.get("add_trained_chara_array") or []):
            if not isinstance(e, dict):
                continue
            addn += 1
            c, _, _, _ = blues(e.get("factor_info_array"), meta)
            addg = [a + b for a, b in zip(addg, c)]
        rows.append(dict(fn=fn, cid=cid, grade=ci.get("chara_grade"), real=real, base=base,
                         deck=deck, nd=nd, miss=miss, resid=resid, par_ids=par_ids, ps=ps,
                         addg=addg, addn=addn,
                         cap=[int(ci.get("max_" + a) or 0) for a in AXES],
                         dcap=[int(ci.get("default_max_" + a) or 0) for a in AXES]))

    # ---- 【1】逐框：真机开局 vs 引擎可核对的三段（base + 卡组 + 残差）----
    print("\n【1】逐框（真机开局＝一手报文；base＝umaDB fiveStatusInitial；卡组＝cardDB initialBonus）")
    print("文件      卡      开局五轴(速耐力量根智)      base                卡组              残差＝真机−base−卡组")
    shown = rows if args.all_frames else []
    if not shown:
        seen = set()
        for r in rows:
            k = (r["cid"], tuple(r["real"]))
            if k in seen:
                continue
            seen.add(k)
            shown.append(r)
    for r in shown:
        print("%s %-7s %-24s %-18s %-14s %s" % (
            r["fn"][:6], r["cid"], r["real"], r["base"], r["deck"], r["resid"]))
    print("（同键去重后 %d/%d 框；重取同一局只留一条）" % (len(shown), len(rows)))
    if any(r["miss"] for r in rows):
        print("⚠ 卡组里 cardDB 查不到的卡：%s" % sorted({m for r in rows for m in r["miss"]}))

    # ---- 【2】父母两条蓝因子（一手记录 × master.mdb 因子表）----
    print("\n【2】继承源：`chara_info.succession_trained_chara_id_1/_2` → 索引里的完整记录 → 数蓝因子")
    print("卡      开局五轴和  父1(组别:条数@稀有度)      父2(组别:条数@稀有度)      Σblue  残差")
    nres = sum(1 for r in shown if all(r["ps"]))
    for r in shown:
        def fmt_rec(x):
            if x is None:
                return "索引缺"
            pid, card_id, rar, cnt, bluer, ncol, unk = x
            hits = "/".join("g%d:%d@%d" % (j + 1, cnt[j], bluer[j]) for j in range(5) if cnt[j])
            return "tc%s(卡%s r%s) %s%s" % (pid, card_id, rar, hits or "无蓝", " 未匹配%d" % unk if unk else "")
        tot = sum(sum(x[3]) for x in r["ps"] if x)
        print("%-7s %-9d %-24s %-24s %5d  %s" % (
            r["cid"], sum(r["real"]), fmt_rec(r["ps"][0]), fmt_rec(r["ps"][1]), tot, r["resid"]))
    print("父母两格都在索引里的框：%d/%d ⇒ 缺格的那几行 Σblue 只是下界" % (nres, len(shown)))

    # ---- 【3】条数对照：引擎的 blue_count 是不是一个真实可达的量 ----
    print("\n【3】逐轴蓝因子条数：引擎写死 vs 真机（父母两条之和）")
    eng = [int(x) for x in cfg["sim"]["blue_count"]]
    obs = [0] * 5
    per_game = []
    for r in shown:
        g = [0] * 5
        for x in r["ps"]:
            if x:
                for j in range(5):
                    g[j] += x[3][j]
        obs = [a + b for a, b in zip(obs, g)]
        per_game.append(g)
    nfull = len(per_game) or 1
    print("轴        %s" % "  ".join("%-6s" % a for a in AXES))
    print("引擎     %s  Σ=%d" % ("  ".join("%-6d" % v for v in eng), sum(eng)))
    print("真机均值 %s  Σ=%.1f" % ("  ".join("%-6.1f" % (v / nfull) for v in obs), sum(obs) / nfull))
    print("真机最大 %s" % ("  ".join("%-6d" % max([g[j] for g in per_game], default=0) for j in range(5)))
          if per_game else "真机最大 ——（这批样本没解析出父母记录）")
    add_obs = [sum(r["addg"][j] for r in shown) for j in range(5)]
    print("对照：本框 3 条新增副本记录（租借父母本体在内，%d 条）逐轴和 %s  Σ=%d"
          % (sum(r["addn"] for r in shown), add_obs, sum(add_obs)))
    per_parent = [sum(x[3]) for r in shown for x in r["ps"] if x]
    print("读法：单匹马记录里蓝因子最多 %d 条（父母两条之和 ⇒ 每轴上限 2）；"
          "引擎输入 Σ=%d 条不是『真机某局的档位』，而是把 ×7 乘子塞进条数里的等效值。"
          % (max(per_parent, default=0), sum(eng)))
    print("轴序由配置注释自证：ai_config.toml:30 写「种马蓝因子个数 [速度, 耐力, 力量, 根性, 智力]」。")

    # ---- 【3b】引擎开局 vs 真机开局（继承项换成引擎自己的 ×7 写死值）----
    print("\n【3b】引擎开局（base＋卡组＋蓝因子×7）vs 真机开局（同一框的一手读数）")
    print("卡      引擎开局                     真机开局                     引擎−真机                  和差")
    eng_tot = real_tot = 0
    for r in shown:
        e5 = [r["base"][j] + r["deck"][j] + eng[j] * 7 for j in range(5)]
        d5 = [e5[j] - r["real"][j] for j in range(5)]
        eng_tot += sum(e5)
        real_tot += sum(r["real"])
        print("%-7s %-26s %-26s %-26s %+d" % (
            r["cid"], e5, r["real"], d5, sum(e5) - sum(r["real"])))
    print("合计：引擎 %d vs 真机 %d（差 %+d，%.1f%%）" % (eng_tot, real_tot, eng_tot - real_tot,
                                                    100.0 * (eng_tot - real_tot) / (real_tot or 1)))
    eaxes = [AXES[j] for j in range(5) if eng[j] > 0]
    raxes = [AXES[j] for j in range(5) if obs[j] > 0]
    inter = [a for a in eaxes if a in raxes]
    print("引擎只给 %s（%s），真机这批落在 %s；交集 %s ⇒ 分布错位，总分对齐也不等于机制对。"
          % (eaxes, eng, raxes, inter or "空"))

    # ---- 【4】组别↔轴 标定（用残差同号检验，不靠猜序）----
    print("\n【4】因子组别 ↔ 五轴 序（假设 组别 i ↔ %s 的第 i 轴）" % "/".join(AXES))
    agree = disagree = silent = 0
    for r in shown:
        g = [0] * 5
        for x in r["ps"]:
            if x:
                for j in range(5):
                    g[j] += x[3][j]
        for j in range(5):
            if g[j] > 0 and r["resid"][j] > 0:
                agree += 1
            elif g[j] > 0 and r["resid"][j] <= 0:
                disagree += 1
            elif g[j] == 0 and r["resid"][j] > 0:
                silent += 1
    print("有蓝且残差为正 %d ｜ 有蓝但残差≤0 %d ｜ 无蓝却有正残差 %d" % (agree, disagree, silent))
    if agree and not disagree:
        print("⇒ %d 个『该轴有蓝因子』的格全部残差为正、0 例反证：组别序＝五轴序没被证伪。" % agree)
    elif agree or disagree:
        print("⇒ 组别序与五轴序在这批样本上对不上 %d 例，序未被钉死，别拿它换算。" % disagree)
    else:
        print("⇒ 这批样本没解析出蓝因子，本节无分辨力。")
    if silent:
        print("另有 %d 格无蓝却正残差 ⇒ 正残差不全来自蓝因子（base 参照本身缺轴，见【6】）。" % silent)

    # ---- 【5】换算口径：master.mdb 自己的两张表 vs 引擎的 ×7 ----
    print("\n【5】换算口径（照表原样打印，不合成新常数）")
    print("succession_initial_factor（factor_type=1）value_1/value_2 → add_point：%s"
          % " ".join("%d/%d→%d" % t for t in init_step))
    print("succession_factor_effect（target_type 1..5）同组 n 条 → value_1：%s"
          % " ".join("%d→%d" % t for t in ladder))
    print("引擎用 蓝因子×7 作开局加算（线性），与上两张表都不同形；本节只报差，不换算成结论。")

    # ---- 【6】base 参照是否闭合 ----
    print("\n【6】反推路能不能闭合：真机开局 − 卡组 必须 ≥ base（继承只会往上加）")
    bad = [r for r in shown if any(r["real"][j] - r["deck"][j] < r["base"][j] for j in range(5))]
    for r in bad:
        gaps = [(AXES[j], r["base"][j] + r["deck"][j] - r["real"][j]) for j in range(5)
                if r["real"][j] - r["deck"][j] < r["base"][j]]
        print("  卡 %s 有 %d 轴低于 base+卡组：%s ⇒ 该卡的 fiveStatusInitial 不是这局的开局基准"
              % (r["cid"], len(gaps), gaps))
    if not bad and shown:
        print("  这批样本没有缺轴：base 参照闭合，残差可当继承加算读数")
    if not shown:
        print("  这批样本没有开局框（--limit 按文件名顺序截断，开局框集中在后段 ⇒ 跑全量）")
    if bad:
        print("⇒ 反推路（开局五轴 ÷ 引擎公式）在上面这些框上不成立，只有直接数因子那条路可用。")

    print("\n【7】能 / 不能")
    gmax = max((sum(g) for g in per_game), default=0)
    print("能：一手数出这局父母蓝因子的组别与稀有度（本批每局 Σblue 实测最大 %d 条），"
          "量化引擎 Σblue=%d 与真机之间的量纲差。" % (gmax, sum(eng)))
    print("不能：把蓝因子条数换算成分数（#54 只管形状）；不能改 `data/ai_config.toml` 去贴锚点——"
          "接线要过配对 CRN 三分量并单独征得同意。")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true", help="只普查盘上有哪些开局侧字段")
    ap.add_argument("--limit", type=int, default=0, help="probe 只扫前 N 个报文")
    ap.add_argument("--all-frames", action="store_true", help="逐框打印（默认按 (卡, 开局五轴) 去重）")
    args = ap.parse_args(argv)
    if args.probe:
        probe(args.limit)
        return 0
    census(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
