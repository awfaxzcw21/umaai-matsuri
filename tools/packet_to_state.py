"""把报文里的**决策板**转成面板形状的状态 JSON（#56 分身定价的前置）。

面板 `live_reader.pick_state()` 产出的形状就是引擎 `state::assemble_game` 吃的那份
（`_state_t30.json` 同源），所以离线复用它 = 零重新实现、口径与实机接缝一致。
决策板选择沿用 #51 的口径 1：同一 `(run,turn)` 里**最后一条
`chara_info.playing_state==1` 的板报文**，优先不带 `data.command_result` 的那条。

产物：`out/clone_states/{run}_t{turn}.json`（一块板一份）＋ `out/clone_states/_index.csv`。
只读报文、不写 `data/`、不改 tracked 源。

    python tools/packet_to_state.py                # 只要带分身的板
    python tools/packet_to_state.py --control 300  # 再补 N 块无分身的板作标定对照
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clone_partner_ids as cpi  # noqa: E402
import live_reader  # noqa: E402
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
OUT_DIR = "out/clone_states"
SCEN = 15


def boards_by_turn(chara):
    """(run,turn) -> 决策板报文（口径 1）。"""
    pk = {}
    idx = 0
    for f in sorted(os.listdir(RAW)):
        if not f.endswith(".bin"):
            continue
        idx += 1
        try:
            d = ml.decode_file(RAW + f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = data.get("chara_info")
        if not isinstance(ci, dict) or ci.get("scenario_id") != SCEN:
            continue
        b = cpi.read_board(data, chara)
        if not isinstance(b.get("turn"), int) or not b["entry"]:
            continue
        key = (b["run"], b["turn"])
        rec = (idx, f, ci.get("playing_state"), b, data)
        pk.setdefault(key, []).append(rec)
    out = {}
    for key, v in pk.items():
        ps1 = [x for x in v if x[2] == 1]
        cand = [x for x in ps1 if not isinstance(x[4].get("command_result"), dict)] or ps1 or v
        out[key] = (cand[-1], v)
    return out


def partner_ok(payload):
    """板得真带人头：缺人头的板引擎重建出来的分布是空的，残差没有可比性。"""
    cmds = [c for c in payload.get("commands") or [] if c.get("partner_ids") is not None]
    return bool(cmds) and all(c.get("partner_ids") for c in cmds)


def fill_from_siblings(data, frames):
    """面板 `_fill_partners` 的离线版：**只在本 `(run,turn)` 的报文里**找人头，绝不跨回合。

    有些响应只带训练预告（`params`）不带 `training_partner_array`，人头在相邻报文里。
    跨回合补会拿到几回合前的旧搭档（面板注释里记着这条实测教训）。
    """
    ci = (data.get("home_info") or {}).get("command_info_array") or []
    need = [c for c in ci
            if isinstance(c, dict) and c.get("command_type") == 1
            and not [p for p in (c.get("training_partner_array") or []) if isinstance(p, int)]]
    if not need:
        return 0
    src = {}
    for (_, _, _, b, _) in reversed(frames):
        for cid, c in b["entry"].items():
            if cid in src:
                continue
            if [p for p in (c.get("training_partner_array") or []) if isinstance(p, int)]:
                src[cid] = c
    filled = 0
    for c in need:
        s = src.get(c.get("command_id"))
        if not s:
            continue
        for k in ("training_partner_array", "sub_command_partner_array",
                  "tips_event_partner_array"):
            c[k] = s.get(k) or []
        filled += 1
    return filled


def attach_sub_partner_ids(payload, board):
    """把 `sub_command_partner_array` 的**卡组站位序号**原样挂进面板形状的板（#56 接线用）。

    面板 `pick_state` 只把分身渲染成**名字**（`sub_partners`），而引擎重建器要的是站位序号——
    它与 `partner_ids` 里 1..6 那一段是同一套 id（`chara_info.support_card_array.position`），
    可以直接当人头下标用，省掉「按名字反查卡组槽位」这步会撞名的映射（SSR/R 吾妻同名）。
    """
    entry = board.get("entry") or {}
    for c in payload.get("commands") or []:
        src = entry.get(c.get("command_id")) or {}
        c["sub_partner_ids"] = [int(x) for x in (src.get("sub_command_partner_array") or [])
                                if isinstance(x, (int, float))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--control", type=int, default=0, help="额外补几块无分身的板")
    args = ap.parse_args()
    tbl = cpi.load_tables("E:/HJM/_ref01/matsuri-ref-01/data/master.mdb")
    picks = boards_by_turn(tbl[2])
    picked = []
    dropped = 0
    filled_frames = 0
    for (run, turn), (rec, frames) in sorted(picks.items()):
        fill_from_siblings(rec[4], frames)
        payload = live_reader.pick_state({"data": rec[4]})
        if not isinstance(payload, dict) or not partner_ok(payload):
            dropped += 1
            continue
        attach_sub_partner_ids(payload, rec[3])
        n = sum(len(c.get("sub_partners") or []) for c in payload.get("commands") or [])
        picked.append(((run, turn), rec, payload, n))
    with_clone = [x for x in picked if x[3]]
    without = [x for x in picked if not x[3]]
    chosen = list(with_clone)
    if args.control:
        step = max(1, len(without) // args.control)
        chosen += without[::step][: args.control]
    os.makedirs(args.out, exist_ok=True)
    rows = []
    for (run, turn), rec, payload, n in chosen:
        path = os.path.join(args.out, f"{run}_t{turn}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        rows.append(dict(file=path, run=run, turn=turn, clones=n,
                         cells=len(payload.get("commands") or []),
                         pack=rec[1]))
    with open(os.path.join(args.out, "_index.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "run", "turn", "clones", "cells", "pack"])
        w.writeheader()
        w.writerows(rows)
    print(f"决策板 {len(picks)}｜可用 {len(picked)}（丢弃 {dropped}：人头缺失/面板不认）")
    print(f"带分身 {len(with_clone)} 块（槽合计 {sum(x[3] for x in with_clone)}）"
          f"｜无分身 {len(without)} 块｜写出 {len(rows)} 份 → {args.out}/")


if __name__ == "__main__":
    main()
