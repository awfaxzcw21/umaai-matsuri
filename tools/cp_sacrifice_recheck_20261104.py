"""主线集成核对：独立复算 #50 的 §4 点估计，不借用 cp_sacrifice_scan.py 的任何代码。

只读 out/_cp_sacrifice_hands.csv 重切窗口/对照（剔 camp，dt = T − turn），
产出 docs/真机兜底语义普查_20261104.md §11 那张表。判据口径按 #50 §1 原文，未改。

用法：python tools/cp_sacrifice_recheck_20261104.py
"""
import csv
import os
import sys

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "_cp_sacrifice_hands.csv")
TRUE = ("True", "1.0", "1")


def is_true(v):
    return v in TRUE


def split(rows, w):
    win, ctl = [], []
    for r in rows:
        if is_true(r["camp"]):
            continue
        dt = int(r["T"]) - int(r["turn"])
        if 1 <= dt <= w:
            win.append(r)
        elif dt > w:
            ctl.append(r)
    return win, ctl


def ratio(rows, den, val):
    """den=None ⇒ 全部手；val 既接受 1.0/0.0 标志列，也接受 kind=='keiko' 这种取值判断。"""
    pool = rows if den is None else [r for r in rows if is_true(r[den])]
    if val == "kind":
        hit = sum(1 for r in pool if r["kind"] == "keiko")
    else:
        hit = sum(1 for r in pool if is_true(r[val]))
    return len(pool), hit


METRICS = [
    ("O1", None, "o1_val"),
    ("O2", None, "o2_val"),
    ("M1", None, "kind"),
    ("M1c", "m1c_den", "m1c_val"),
    ("M3c", "m3c_den", "m3c_val"),
    ("M5", "m5_den", "m5_val"),
    ("M2", "m2_den", "m2_val"),
]


def pct(pair):
    n, k = pair
    return 100.0 * k / n if n else float("nan")


def main():
    if not os.path.exists(CSV):
        sys.exit(f"找不到 {CSV}（#50 的逐手留档，先跑 tools/cp_sacrifice_scan.py）")
    rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
    print(f"{len(rows)} 手 / {len({r['run'] for r in rows})} 局"
          f"（剔 camp 前的全量留档）")
    for w in (3, 5):
        win, ctl = split(rows, w)
        print(f"\n=== W={w}  窗口 {len(win)} 手 / 对照 {len(ctl)} 手")
        for lbl, den, val in METRICS:
            p_w, p_c = ratio(win, den, val), ratio(ctl, den, val)
            print(f"  {lbl:4s} 窗口 {p_w[1]:3d}/{p_w[0]:3d}={pct(p_w):5.1f}%   "
                  f"对照 {p_c[1]:3d}/{p_c[0]:3d}={pct(p_c):5.1f}%   "
                  f"差 {pct(p_w)-pct(p_c):+7.2f}pp")
        # DiD：分层是「需求格在场」(m3c_den) 对「稽古格在场但无需求格」(m5_den)，
        # 判据原文见 #50 §1——两层各算取稽古率，窗口减对照后再作差。
        def take(rs, den):
            return ratio([r for r in rs if is_true(r[den])], None, "kind")
        wa, wn, ca, cn = take(win, "m3c_den"), take(win, "m5_den"), take(ctl, "m3c_den"), take(ctl, "m5_den")
        did = (pct(wa) - pct(wn)) - (pct(ca) - pct(cn))
        print(f"  DiD  窗口 有需求 {pct(wa):.1f}%/{wa[0]} 无需求 {pct(wn):.1f}%/{wn[0]} | "
              f"对照 有需求 {pct(ca):.1f}%/{ca[0]} 无需求 {pct(cn):.1f}%/{cn[0]} => {did:+.2f}pp")
        print(f"  nlow==0 窗口 {sum(1 for r in win if int(r['nlow']) == 0)}/{len(win)}")


if __name__ == "__main__":
    main()
