"""#42 前置：把「玩家爱取人头最满格」拆成 **真偏好** vs **演目需求的影子**。

#40（`docs/训练腿预告对账_20261104.md` §3）量到真机落子八成落在盘面人头最多的格、
落子站位/最满位 = 0.95，而引擎只有 0.56，于是按对数把人头缺口的 **65% 记在「选位策略」**。
本页查这 0.95 里有多少不是「爱人头」、而是影子：本剧本支援卡按担当演目站位，
「要补的那个演目」那一格本来就容易堆人头。两种结论把 #42 指向相反修法
（给策略加「取最满」偏置 vs 把演目需求接进选位）。

四张表（都是**条件化**判据，不看边际）：
  表1 需求格集合 ∩ 人头最满格集合 的重合率 + **同板置换**零假设（人头与演目无关时该重合多少）
      另给「需求格人头 − 同板其余格」的配对差 = 影子机制本身在不在
  表2 只在 **需求集合与人头集合不相交** 的手上（干净对照）看玩家偏向哪边，带 n、Wilson、
      Clopper–Pearson 精确区间，并与「按集合大小的随机期望」比
  表3 排除重合层后 `P(落该格)` 随 `heads_own` 的条件曲线 + **McFadden 条件 logit**
      （手内似然，截距自动消掉）：同时放 人头 与 需求缺口 两个解释变量 ⇒ 各自净效应
  表4 把 #40 的比值口径直接拆开：`R_player`/`R_need`/`R_rand` = 落子(或需求格/随机)人头 ÷ 最满格人头
      ⇒ 「影子」成立要求 R_player ≈ R_need；差值按局 bootstrap

口径（照 #40，不自创分母）：窗口 `30 <= turn <= 78`、run 可用 = 同 `(run,start)` 段内 >= 20 手，
  候选集 = 同 `(file, turn, kind)` 的板上条目 —— 照 `tools/train_leg_gap_split.py` 的
  `real_side()` / `real_board()`。⚠ 稽古层候选数**不恒为 5**（未点亮演目在报文里就没有 510x 条目），
  单格手在 #40 口径下**必然**算「取到最满」，本页单列拆开报。

需求侧一手来源（存在性已核：窗口内 180/180 块板全有 5 条，见文档 §2）：
  板报文 `festa_data_set.act_info_array[]` 的 `{act_type, level, act_gauge, need_act_gauge}`；
  下一检查点场次 = `len(festa_data_set.check_point_result_array) + 1`（第 4 场不写进该数组）；
  need = master.mdb `single_mode_15_schedule.need_act_level`（1/2/3 场 = 3/5/6，见
  `tools/cp_tier_check.py` 的 `NEED_TBL`）+ 第 4 场 = `docs/发表会档位一手_20261103.md` §2
  夹逼出的 6。**读出来的，没有拟合系数。**

位↔演目映射：稽古 510x 直接 = `cid-5100`（与 `tools/keiko_curve.py` 同口径）⇒ 1:1 干净；
  普通训练位**没有** 1:1 演目身份（引擎的演目槽落在担当本人的演目、不是训练轴，
  `crates/matsuri-core/src/game/mod.rs:1211`），这里只能按「该位中位主轴 → 同轴演目」近似
  ⇒ 普通层的数带假设，主判断看稽古层。

    python tools/train_slot_confound.py                    # heads_own（#40 那句 0.95 的口径）
    python tools/train_slot_confound.py --heads deck       # heads_deck（#40 §3「取最满」列的口径）
    python tools/train_slot_confound.py --need-key level   # 需求只按 Lv 缺口，不看升级槽进度

结论见 `docs/选位共因检验_20261104.md`。
"""

import argparse
import collections
import csv
import math
import os
import random
import statistics
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
import msgpack_lite as ml  # noqa: E402

RAW = os.environ.get("HJM_RAW", "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/")
WIN = (30, 78)
MIN_HANDS = 20
NEED = {1: 3, 2: 5, 3: 6, 4: 6}                        # 第 4 场 = 发表会一手 §2 夹逼
PLAIN_ACT = {101: 1, 105: 2, 102: 3, 103: 4, 106: 5}  # 位 → 主轴同序演目（preview 中位量出）
SEED = 20261104


# ---------------------------------------------------------------- 统计小件
def wilson(k, n, z=1.959963985):
    """Wilson score 区间：(p + z²/2n ± z√(pq/n + z²/4n²)) / (1 + z²/n)。"""
    if not n:
        return (float("nan"), float("nan"))
    p, d = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def binom_pmf(i, n, p):
    if p <= 0.0:
        return 1.0 if i == 0 else 0.0
    if p >= 1.0:
        return 1.0 if i == n else 0.0
    return math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                    + i * math.log(p) + (n - i) * math.log1p(-p))


def binom_cdf(k, n, p):
    return sum(binom_pmf(i, n, p) for i in range(k + 1))


def _solve_dec(f, lo, hi, it=200):
    """对 [lo,hi] 上单调**递减**的 f 求根（二分）。"""
    for _ in range(it):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper(k, n, a=0.05):
    """Clopper–Pearson：下界解 P(X<=k-1|p)=1-a/2、上界解 P(X<=k|p)=a/2（两边对 p 都递减）。"""
    if not n:
        return (float("nan"), float("nan"))
    lo = 0.0 if k == 0 else _solve_dec(lambda p: binom_cdf(k - 1, n, p) - (1 - a / 2), 0.0, 1.0)
    hi = 1.0 if k == n else _solve_dec(lambda p: binom_cdf(k, n, p) - a / 2, 0.0, 1.0)
    return (lo, hi)


def exact_two_sided(k, n):
    """精确二项双侧检验（H0: p=1/2）：把所有「不比观测更可能」的样本点概率相加。"""
    if not n:
        return float("nan")
    obs = binom_pmf(k, n, 0.5)
    return min(1.0, sum(binom_pmf(i, n, 0.5) for i in range(n + 1)
                        if binom_pmf(i, n, 0.5) <= obs * (1 + 1e-9)))


def cond_logit(hands, cols, it=80):
    """McFadden 条件 logit：ℓ = Σ_h [x_h,pick·β − log Σ_{c∈h} exp(x_c·β)]，牛顿法。
       手内似然 ⇒ 截距与「手」本身被消掉，只有格间差异起作用。
       hands = [(候选特征向量列表, 中选下标)]；返回 (β, SE)；不收敛/奇异返回 nan。"""
    p = len(cols)
    data = [(xs, i) for xs, i in hands if len(xs) >= 2]
    if not data:
        return [float("nan")] * p, [float("nan")] * p
    b = [0.0] * p
    converged = False
    for _ in range(it):
        grad = [0.0] * p                       # score = ∂ℓ/∂β
        Hess = [[0.0] * p for _ in range(p)]   # 负定的 ∂²ℓ
        for xs, i in data:
            lin = [sum(bb * x for bb, x in zip(b, xv)) for xv in xs]
            shift = max(lin)                   # log-sum-exp 平移，防溢出
            es = [math.exp(v - shift) for v in lin]
            tot = sum(es)
            if tot <= 0 or not math.isfinite(tot):
                return b, [float("nan")] * p
            pr = [e / tot for e in es]
            mean = [sum(pk * xk[j] for pk, xk in zip(pr, xs)) for j in range(p)]
            for j in range(p):
                grad[j] += xs[i][j] - mean[j]
                for q in range(p):
                    Hess[j][q] -= (sum(pk * xk[j] * xk[q] for pk, xk in zip(pr, xs))
                                   - mean[j] * mean[q])
        try:
            step = _solve_lin(Hess, grad)      # = H⁻¹·score
        except ZeroDivisionError:
            return b, [float("nan")] * p
        if not all(math.isfinite(s) for s in step) or max(abs(s) for s in step) > 50:
            return b, [float("nan")] * p       # 发散（完全分离）⇒ 不报，不换定义硬凑
        b = [bb - s for bb, s in zip(b, step)]  # 牛顿上升：β ← β − H⁻¹·score（H 为负）
        if max(abs(s) for s in step) < 1e-9:
            converged = True
            break
    if not converged:
        return b, [float("nan")] * p
    try:
        cov = _inv(Hess)                       # 对角即各参数的方差（负定 ⇒ 取 −v）
    except ZeroDivisionError:
        return b, [float("nan")] * p
    # Hess 是负 Hessian ⇒ 协方差 = −Hess⁻¹
    return b, [math.sqrt(-v) if v is not None and v < 0 else float("nan") for v in cov]


def _solve_lin(A, g):
    """2x2/3x3 线性方程组（高斯消元，小规模够用）。"""
    n = len(A)
    M = [list(A[i]) + [g[i]] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-14:
            raise ZeroDivisionError
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _inv(A):
    n = len(A)
    I = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    M = [list(A[i]) + I[i] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-14:
            raise ZeroDivisionError
        M[c], M[piv] = M[piv], M[c]
        d = M[c][c]
        M[c] = [v / d for v in M[c]]
        for r in range(n):
            if r != c:
                f = M[r][c]
                M[r] = [v - f * w for v, w in zip(M[r], M[c])]
    return [M[i][n + i] for i in range(n)]


def bootstrap_diff(pairs, reps=2000):
    """按手 bootstrap 两个同分母均值的差：pairs = [(a, b)]；返回 (diff, lo, hi)。"""
    if len(pairs) < 2:
        return (float("nan"),) * 3
    rng = random.Random(SEED)
    n = len(pairs)
    diffs = []
    for _ in range(reps):
        s = [pairs[rng.randrange(n)] for _ in range(n)]
        diffs.append(statistics.fmean(x[0] for x in s) - statistics.fmean(x[1] for x in s))
    diffs.sort()
    return (statistics.fmean(a - b for a, b in pairs),
            diffs[int(0.025 * reps)], diffs[int(0.975 * reps)])


# ---------------------------------------------------------------- 装载与建观测
def load_hands(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    inwin = [r for r in rows if WIN[0] <= int(r["turn"]) <= WIN[1]]
    per_run = collections.Counter((r["run"], r["start"]) for r in inwin)
    keep = {k for k, v in per_run.items() if v >= MIN_HANDS}
    return rows, [r for r in inwin if (r["run"], r["start"]) in keep], per_run, keep


def load_board(path, files):
    b = collections.defaultdict(list)
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        if r["file"] in files:
            b[(r["file"], r["turn"], r["kind"])].append({
                "cid": int(r["cid"]), "own": int(r["heads_own"]),
                "deck": int(r["heads_deck"]), "sub": int(r["heads_sub"])})
    return b


def act_info(fname):
    """板报文 → ({act_type: {...}}, 已结算场次数)。所需列为 None 就抛，不静默兜底。"""
    d = ml.decode_file(os.path.join(RAW, fname))
    fs = (d.get("data") or {}).get("festa_data_set") or {}
    out = {}
    for a in fs.get("act_info_array") or []:
        for k in ("act_type", "level", "act_gauge", "need_act_gauge"):
            if a.get(k) is None:
                raise KeyError("%s 的 act_info_array 缺 %s" % (fname, k))
        out[a["act_type"]] = {"lv": a["level"], "gauge": a["act_gauge"],
                              "need_gauge": a["need_act_gauge"], "state": a.get("act_training_state")}
    return out, len(fs.get("check_point_result_array") or [])


def decorate(cands, acts, need):
    """给每格补上 演目/缺口/升级进度；对不上演目返回 None（调用方计数后丢弃）。"""
    for c in cands:
        at = c["cid"] - 5100 if c["cid"] >= 5101 else PLAIN_ACT.get(c["cid"])
        a = acts.get(at) if at else None
        if a is None:
            return None
        c["act"], c["lv"], c["state"] = at, a["lv"], a["state"]
        c["gap"] = need - a["lv"]                       # >0 = 该演目还没到下一检查点的线
        c["marks"] = a["need_gauge"] - a["gauge"]       # 距升 1 级还差几格
    return cands


def sets_of(cands, heads_key, need_key):
    """人头最满格集合 + 需求格集合（都允许并列，不硬挑一个）。"""
    hmax = max(c[heads_key] for c in cands)
    hset = {c["cid"] for c in cands if c[heads_key] == hmax}
    key = (lambda c: (c["gap"],)) if need_key == "level" else \
          (lambda c: (c["gap"], -c["marks"]))           # noqa: E731 平手取「达标记数最少」=最接近升级
    best = max(key(c) for c in cands)
    nset = {c["cid"] for c in cands if key(c) == best}
    return hset, hmax, nset


def build(chosen, boards, heads_key, need_key):
    obs, drop = [], collections.Counter()
    for r in chosen:
        cands = boards.get((r["file"], r["turn"], r["kind"]), [])
        if not cands:
            drop["无同 kind 板"] += 1
            continue
        try:
            acts, n_settled = act_info(r["file"])
        except Exception:  # noqa: BLE001  解码失败/缺列：计数后跳过，不猜
            drop["报文缺列或解不开"] += 1
            continue
        need = NEED[min(n_settled + 1, 4)]
        if decorate(cands, acts, need) is None:
            drop["位对不上演目"] += 1
            continue
        pick = int(r["cid"])
        if pick not in {c["cid"] for c in cands}:
            drop["落子不在候选集"] += 1
            continue
        mine = next(c for c in cands if c["cid"] == pick)
        if mine[heads_key] != int(r["heads_" + heads_key]):
            drop["落子人头与板行不符"] += 1
        hset, hmax, nset = sets_of(cands, heads_key, need_key)
        # 全板（跨 kind）备选：只做敏感性，不改主口径
        full = [c for c in (boards.get((r["file"], r["turn"], "plain"), [])
                            + boards.get((r["file"], r["turn"], "keiko"), []))]
        decorate(full, acts, need)
        fset = sets_of(full, heads_key, need_key) if full and not any(
            c.get("gap") is None for c in full) else None
        obs.append({"run": r["run"], "start": r["start"], "turn": int(r["turn"]), "kind": r["kind"],
                    "camp": r["camp"], "need": need, "settled": n_settled, "cands": cands,
                    "k": len(cands), "hset": hset, "hmax": hmax, "nset": nset, "pick": pick,
                    "pick_h": mine[heads_key], "full": full, "fsets": fsets_safe(fset)})
    return obs, drop


def fsets_safe(f):
    if not f:
        return None
    hset, hmax, nset = f
    return {"hset": hset, "hmax": hmax, "nset": nset}


# ---------------------------------------------------------------- 表
def perm_null(full, hits, reps=2000):
    """同板置换：把每手的人头向量在候选格间打乱（保住这板的人头多重集），
       重算需求格是否仍落在最满格集合里 = 「人头与演目无关」下的期望重合。

       返回 `(期望重合, p_上侧, p_下侧)`。判「观测低于基线」只能看**下侧** p，
       上侧 p 在观测低于基线时必然≈1，拿它当显著性会把方向说反。"""
    rng = random.Random(SEED)
    tot = ge = le = 0
    for _ in range(reps):
        c = 0
        for h in full:
            hv = [x["h"] for x in h["cands"]]
            rng.shuffle(hv)
            mx = max(hv)
            hs = {x["cid"] for x, v in zip(h["cands"], hv) if v == mx}
            c += 1 if h["nset"] & hs else 0
        tot += c
        ge += 1 if c >= hits else 0
        le += 1 if c <= hits else 0
    return tot / reps, (ge + 1) / (reps + 1), (le + 1) / (reps + 1)


def table1(hands, heads_key):
    full = [h for h in hands if h["k"] >= 2]
    for h in hands:
        for c in h["cands"]:
            c["h"] = c[heads_key]
    print("  可判手（候选 >=2 格）n=%d；单格手（无从选择）n=%d" % (len(full), len(hands) - len(full)))
    # 复算 #40 的「取最满%」并把它拆开：单格手在 #40 口径下必然算取到最满
    def rate(sel):
        return (sum(1 for h in sel if h["pick"] in h["hset"]), len(sel))
    a1, n1 = rate(hands)
    a2, n2 = rate(full)
    camp_split = [(c, "合宿" if c == "1" else "非合宿") for c in ("0", "1")]
    print("  取最满率：含单格手 %d/%d = %.1f%%｜去单格手 %d/%d = %.1f%%（%s 口径）"
          % (a1, n1, 100.0 * a1 / max(n1, 1), a2, n2, 100.0 * a2 / max(n2, 1), heads_key))
    for c, tag in camp_split:
        s = [h for h in hands if h["camp"] == c]
        if s:
            x, y = rate(s)
            print("        %s n=%d 含单格 %.1f%%" % (tag, y, 100.0 * x / y), end="")
            f = [h for h in s if h["k"] >= 2]
            if f:
                x2, y2 = rate(f)
                print("｜去单格 %d/%d = %.1f%%" % (x2, y2, 100.0 * x2 / y2), end="")
            print()
    if not full:
        return full
    hit = sum(1 for h in full if h["nset"] & h["hset"])
    w = wilson(hit, len(full))
    print("  重合率 P(需求格集合 ∩ 最满格集合 ≠ ∅) = %d/%d = %.1f%%  Wilson95 [%.1f%%, %.1f%%]"
          % (hit, len(full), 100.0 * hit / len(full), 100 * w[0], 100 * w[1]))
    nm, p_hi, p_lo = perm_null(full, hit)
    print("  同板置换零假设（人头与演目无关）：期望重合 %.1f%%，下侧 p=%.4f｜上侧 p=%.4f（2000 次，种子 %d）"
          % (100.0 * nm / len(full), p_lo, p_hi, SEED))
    one = [h for h in full if len(h["nset"]) == 1 and len(h["hset"]) == 1]
    if one:
        u = sum(1 for h in one if h["nset"] == h["hset"])
        w2 = wilson(u, len(one))
        print("  强版（两边都唯一）P(需求格 = 最满格) = %d/%d = %.1f%% Wilson95 [%.1f%%, %.1f%%]"
              % (u, len(one), 100.0 * u / len(one), 100 * w2[0], 100 * w2[1]))
    dif = []
    dif_lv = []
    for h in full:
        if len(h["nset"]) != 1:
            continue
        nid = next(iter(h["nset"]))
        on = [c["h"] for c in h["cands"] if c["cid"] == nid]
        rest = [c["h"] for c in h["cands"] if c["cid"] != nid]
        if on and rest:
            dif.append(on[0] - statistics.fmean(rest))
        lmax = max(c["lv"] for c in h["cands"])
        lset = [c["h"] for c in h["cands"] if c["lv"] == lmax]
        loth = [c["h"] for c in h["cands"] if c["lv"] != lmax]
        if lset and loth:
            dif_lv.append(statistics.fmean(lset) - statistics.fmean(loth))
    for name, d in (("需求格(缺)", dif), ("最高 Lv 格(强)", dif_lv)):
        if len(d) > 1:
            m = statistics.fmean(d)
            se = statistics.stdev(d) / math.sqrt(len(d))
            print("  影子机制直测：%s人头 − 同板其余格 = %+.3f，95%% [%+.3f, %+.3f]（n=%d 手配对）"
                  % (name, m, m - 1.96 * se, m + 1.96 * se, len(d)))
    print("     正 = 卡确实往那格堆（影子成立）；负 = 相反。需求格为负 ⇒ 缺的演目恰恰人头最少")
    return full


def table2(hands):
    sub = [h for h in hands if h["k"] >= 2 and not (h["nset"] & h["hset"])]
    n = len(sub)
    print("  干净对照（需求集合与人头集合**不相交**）n=%d" % n)
    if not n:
        print("  !! 无样本 ⇒ 该层无法分辨")
        return sub
    pn = sum(1 for h in sub if h["pick"] in h["nset"])
    ph = sum(1 for h in sub if h["pick"] in h["hset"])
    print("  落需求集合 %d/%d = %.1f%%｜落最满集合 %d/%d = %.1f%%｜落两者之外 %d"
          % (pn, n, 100.0 * pn / n, ph, n, 100.0 * ph / n, n - pn - ph))
    k, nn = ph, pn + ph
    if nn:
        w, c = wilson(k, nn), clopper(k, nn)
        exp = statistics.fmean(len(h["hset"]) / (len(h["hset"]) + len(h["nset"])) for h in sub
                               if h["pick"] in h["nset"] or h["pick"] in h["hset"])
        print("  二选一（落点命中两者之一的手，n=%d）：选最满 %d = %.1f%%" % (nn, k, 100.0 * k / nn))
        print("        Wilson95 [%.1f%%, %.1f%%]｜Clopper–Pearson 精确95 [%.1f%%, %.1f%%]｜"
              "对「五五开」精确二项 p=%.4f" % (100 * w[0], 100 * w[1], 100 * c[0], 100 * c[1],
                                               exact_two_sided(k, nn)))
        print("        随机期望（按两边集合大小 |最满|/(|最满|+|需求|) 逐手平均）= %.1f%% ⇒ "
              "偏好判据要比这条基线，不是比 50%%" % (100 * exp,))
    return sub


def table3(hands, heads_key):
    sub = [h for h in hands if h["k"] >= 2 and len(h["nset"]) == 1 and not (h["nset"] & h["hset"])]
    print("  人头条件曲线：子集 = 需求格唯一且不重合，n=%d（需求格已从曲线剔除）" % len(sub))
    buck = collections.defaultdict(lambda: [0, 0])
    for h in sub:
        nid = next(iter(h["nset"]))
        for c in h["cands"]:
            if c["cid"] == nid:
                continue
            b = min(c["h"], 3)
            buck[b][0] += 1
            buck[b][1] += 1 if c["cid"] == h["pick"] else 0
    print("        %-12s %6s %6s %24s" % ("非需求格人头", "格数", "被落", "P(落该格) Wilson95"))
    for b in sorted(buck):
        tot, hit = buck[b]
        w = wilson(hit, tot)
        print("        %-12s %6d %6d   [%.1f%%, %.1f%%] = %.1f%%"
              % (">=3" if b == 3 else b, tot, hit, 100 * w[0], 100 * w[1], 100.0 * hit / tot))

    def curve(field, name):
        g = collections.defaultdict(lambda: [0, 0])
        for h in hands:
            if h["k"] < 2:
                continue
            for c in h["cands"]:
                b = c[field]
                g[b][0] += 1
                g[b][1] += 1 if c["cid"] == h["pick"] else 0
        print("        %s 条件落子率：%s" % (name, " ".join(
            "%s:%d/%d=%.0f%%" % (k, v[1], v[0], 100.0 * v[1] / v[0]) for k, v in sorted(g.items()))))

    curve("h", "人头")
    curve("gap", "Lv 缺口(need-Lv)")
    def model(fields):
        d = [([tuple(c[f] for f in fields) for c in h["cands"]],
              next(i for i, c in enumerate(h["cands"]) if c["cid"] == h["pick"]))
             for h in hands if h["k"] >= 2]
        return cond_logit(d, fields)

    print("  条件 logit（McFadden 手内似然 ⇒ 截距与「手」被消掉，只用格间差异；n_手=%d）"
          % sum(1 for h in hands if h["k"] >= 2))
    for fields, tag in ((("h",), "只人头"), (("gap",), "只 Lv 缺口"), (("h", "gap"), "两者互相控制")):
        b, se = model(fields)
        if any(s != s for s in se):
            print("        %-14s 拟合退化（分离/共线）⇒ 不报" % tag)
            continue
        cells = " ".join("β(%s)=%+.3f(SE %.3f, z %.2f)" % (f, bb, s, bb / s if s else float("nan"))
                         for f, bb, s in zip(fields, b, se))
        print("        %-14s %s ⇒ 每 +1 单位赔率 %s" % (
            tag, cells, " ".join("×%.2f" % math.exp(bb) for bb in b)))
    print("        两个系数同时进模型 = **互相控制后**的净效应 ⇒ 谁还显著，缺口就归谁")
    return sub


def table4(hands, heads_key, full):
    """#40 的比值口径直接拆开：落子 / 需求格 / 随机 各除以最满格人头。"""
    if not full:
        return None
    den = statistics.fmean(h["hmax"] for h in full)
    r_player = statistics.fmean(h["pick_h"] for h in full) / den
    r_need = statistics.fmean(statistics.fmean(c[heads_key] for c in h["cands"] if c["cid"] in h["nset"])
                              for h in full) / den
    r_rand = statistics.fmean(statistics.fmean(c[heads_key] for c in h["cands"]) for h in full) / den
    r_lvmax = statistics.fmean(
        statistics.fmean(c[heads_key] for c in h["cands"] if c["lv"] == max(x["lv"] for x in h["cands"]))
        for h in full) / den
    print("  分母 = 最满格人头均值 %.3f（n=%d 手，去单格手）" % (den, len(full)))
    print("  R_player = %.3f｜R_need（取需求格可得）= %.3f｜R_rand（随机取位）= %.3f｜"
          "R_lvmax（取 Lv 最高的格可得）= %.3f" % (r_player, r_need, r_rand, r_lvmax))
    d1 = bootstrap_diff([(h["pick_h"], statistics.fmean(
        c[heads_key] for c in h["cands"] if c["cid"] in h["nset"])) for h in full])
    d2 = bootstrap_diff([(statistics.fmean(c[heads_key] for c in h["cands"] if c["cid"] in h["nset"]),
                          statistics.fmean(c[heads_key] for c in h["cands"])) for h in full])
    print("  落子 − 需求格 = %+.3f [%.3f, %.3f]（按手 bootstrap 2000 次）⇒ 正 = 需求解释不完" % d1)
    print("  需求格 − 全体均值 = %+.3f [%.3f, %.3f] ⇒ 负 = 缺的演目反而人头少（影子方向反了）" % d2)
    fb = [h for h in hands if h["fsets"] and len(h["full"]) >= 2]
    if fb:
        fden = statistics.fmean(h["fsets"]["hmax"] for h in fb)
        fr_player = statistics.fmean(h["pick_h"] for h in fb) / fden
        fr_need = statistics.fmean(
            statistics.fmean(c[heads_key] for c in h["full"] if c["cid"] in h["fsets"]["nset"])
            for h in fb) / fden
        print("  敏感性（**跨 kind 全板**备选口径，n=%d）：R_player = %.3f｜R_need = %.3f"
              % (len(fb), fr_player, fr_need))
    return r_player, r_need, r_rand, len(full), r_lvmax


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chosen", default="out/train_leg_chosen_20261103.csv")
    ap.add_argument("--preview", default="out/train_leg_preview_20261103.csv")
    ap.add_argument("--heads", choices=("own", "deck"), default="own",
                    help="own=站位人头（#40 那句 0.95 的口径）；deck=参与人头（#40 §3「取最满」列口径）")
    ap.add_argument("--need-key", choices=("both", "level"), default="both",
                    help="both=先按 need-Lv 缺口、平手取距升级差几格最少的那格；level=只按 Lv 缺口")
    a = ap.parse_args()

    all_rows, chosen, per_run, keep = load_hands(a.chosen)
    boards = load_board(a.preview, {r["file"] for r in chosen})
    obs, drop = build(chosen, boards, a.heads, a.need_key)

    print("########## 0. 样本流与字段可得性 ##########")
    print("口径：heads=%s｜需求键=%s｜窗口 t%d-%d｜run 需段内 >=%d 手"
          % (a.heads, a.need_key, WIN[0], WIN[1], MIN_HANDS))
    print("执行腿配对 %d 行｜窗口内且 run 可用 %d 手｜可判定 %d 手" % (len(all_rows), len(chosen), len(obs)))
    print("可用 run = %s（%d 个）；剔除 %s" % (
        sorted({k[0] for k in keep}), len(keep),
        sorted("%s:%d手" % (k[0], v) for k, v in per_run.items() if v < MIN_HANDS)))
    if drop:
        print("丢弃明细：%s" % dict(drop))
    print("分层(kind,camp)：%s" % dict(sorted(collections.Counter(
        (h["kind"], h["camp"]) for h in obs).items())))
    print("候选格数分布：全体 %s｜稽古 %s" % (
        dict(sorted(collections.Counter(h["k"] for h in obs).items())),
        dict(sorted(collections.Counter(h["k"] for h in obs if h["kind"] == "keiko").items()))))
    print("下一检查点 need：%s｜已结算场次 %s（need 3/5/6 来自 master.mdb 表，第 4 场 = 夹逼 6）" % (
        dict(sorted(collections.Counter(h["need"] for h in obs).items())),
        dict(sorted(collections.Counter(h["settled"] for h in obs).items()))))
    print("已结算场次 × 回合段（校验「下一场 = len(check_point_result_array)+1」随回合单调）：%s" % dict(sorted(
        collections.Counter(("t%d-%d" % ((h["turn"] // 10) * 10 + 1, (h["turn"] // 10) * 10 + 10),
                               h["settled"]) for h in obs).items())))
    print("格数口径提醒：候选只 1 格的手不算「选择了最满」；#40 的 `real_board` 会把它们计入取最满")

    for label, sel in (("全体", obs),
                       ("稽古层（位↔演目 1:1 ⇒ 主判据）", [h for h in obs if h["kind"] == "keiko"]),
                       ("普通层（位↔演目 按主轴近似 ⇒ 带假设）", [h for h in obs if h["kind"] == "plain"])):
        print("\n########## %s（n=%d）##########" % (label, len(sel)))
        if not sel:
            print("  无样本")
            continue
        print("\n-- 表1 需求格 vs 人头最满格：重合率 --")
        full = table1(sel, a.heads)
        print("\n-- 表2 干净对照（需求集合 ∩ 人头集合 = ∅）--")
        table2(sel)
        print("\n-- 表3 排除重合后的残余斜率（条件曲线 + 条件 logit）--")
        table3(sel, a.heads)
        print("\n-- 表4 #40 比值口径拆解：R_player / R_need / R_rand --")
        got = table4(sel, a.heads, full)
        if label.startswith("稽古") and got:
            print("  引用值（#40 §3，非本页重测）：落子站位/最满位 真机 0.95 vs 引擎 0.56")
            print("  本页稽古层：R_player=%.3f ⇒ 若真机 = 纯「取需求格」，上限只到 R_need=%.3f；"
                  "随机取位 = %.3f" % (got[0], got[1], got[2]))
            if got[1] < got[2] and got[0] > got[2]:
                print("  ⇒ 落子在人头轴上**高于随机**、需求轴上**低于随机**："
                      "「取最满」不是演目需求的影子，方向是反的")


if __name__ == "__main__":
    main()
