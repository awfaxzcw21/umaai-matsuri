"""§3.3「下段」语义变体对照 / 残差分析（一次性诊断脚本，不入库）。

原料来自 matsuri-cli 的临时探针（_state_t30.json，面板 t30，绝好调，训练 Lv2）：
每条命令给出 memo 真值表行、引擎聚合 buff（彩圈按卡片天然得意判定）、
三种候选人数口径，以及面板真值 stat_gains / skill_point。

下层公式（引擎 default_calc_training_value 逐字）：
    floor((row[i]+bonus[i]) * (1+yq/100) * (1+bm/100*(1+gj/100)) * (1+xl/100)
          * (1+0.05N) * (1+growth[i]/100))
上层（§3.3 剧本加成）作为待检验的额外乘区 U。
"""

import math

GROWTH_ENGINE = [0, 20, 0, 0, 10, 0]  # 装配错人（默认特别周）时的成长率
GROWTH_REAL = [0, 0, 0, 20, 10, 0]  # umaDB 106301 生野狄杜斯，报文真身
BM = 20  # (motivation 5 - 3) * 10

# train, row(Lv2), 聚合 buff, 人数口径, 面板真值
#   n_map     = 引擎 distribution 里能对上的卡片数（state.rs 只按 chara_id 匹配，卡位号全丢）
#   n_ids     = 报文 persons 字段 = len(partner_ids)
#   n_distinct= partners_text 显示的人数 = 去重后的真实人数
#               （partner_ids 混了三套 id：卡位 1..6 / 嘉宾槽 101..115 / chara_id 四位数，
#                 同一个人会同时以「嘉宾槽 + chara_id」出现 ⇒ persons 多算 1）
CMDS = [
    dict(t=0, name="速度", row=[13, 0, 2, 0, 0, 7], yq=89, gj=100, xl=45,
         bonus=[0, 0, 2, 0, 0, 4], n_map=2, n_ids=3, n_distinct=3,
         real={0: 57, 2: 17, 5: 48}),
    dict(t=1, name="耐力", row=[0, 11, 0, 4, 0, 7], yq=40, gj=40, xl=15,
         bonus=[0, 1, 0, 1, 0, 2], n_map=1, n_ids=1, n_distinct=1,
         real={1: 25, 3: 12, 5: 19}),
    dict(t=2, name="力量", row=[0, 6, 10, 0, 0, 7], yq=35, gj=90, xl=35,
         bonus=[0, 1, 0, 0, 3, 4], n_map=2, n_ids=5, n_distinct=4,
         real={2: 30, 1: 21, 5: 33}),
    dict(t=3, name="根性", row=[2, 0, 2, 13, 0, 7], yq=0, gj=25, xl=5,
         bonus=[0, 0, 0, 0, 0, 0], n_map=1, n_ids=2, n_distinct=2,
         real={3: 22, 0: 2, 2: 2, 5: 10}),
    dict(t=4, name="智力", row=[2, 0, 0, 0, 9, 6], yq=0, gj=0, xl=0,
         bonus=[0, 0, 0, 0, 0, 0], n_map=0, n_ids=3, n_distinct=2,
         real={4: 13, 0: 2, 5: 7}),
]

SLOT = ["速度", "耐力", "力量", "根性", "智力", "PT"]


def lower(c, i, n, growth):
    base = c["row"][i] + c["bonus"][i]
    if base <= 0:
        return 0
    g = 1 + growth[i] / 100
    return math.floor(
        base
        * (1 + c["yq"] / 100)
        * (1 + BM / 100 * (1 + c["gj"] / 100))
        * (1 + c["xl"] / 100)
        * (1 + 0.05 * n)
        * g
    )


def variant(nkey, growth):
    tot = 0
    rows = []
    for c in CMDS:
        n = c[nkey]
        for i, r in sorted(c["real"].items()):
            p = lower(c, i, n, growth)
            tot += abs(p - r)
            rows.append((c["name"], SLOT[i], p, r, p - r))
    return tot, rows


def main():
    print("=== 变体总残差（|预测-真值| 之和，16 个分量） ===")
    combos = []
    for nkey in ("n_map", "n_ids", "n_distinct"):
        for gname, g in (("engine", GROWTH_ENGINE), ("real", GROWTH_REAL)):
            tot, _ = variant(nkey, g)
            combos.append((tot, nkey, gname, g))
            print(f"  N={nkey:<10} growth={gname:<6}  残差={tot:4d}")
    combos.sort(key=lambda x: x[0])
    tot, nkey, gname, g = combos[0]
    print(f"\n=== 最优变体 N={nkey} growth={gname} 残差={tot} ===")
    _, rows = variant(nkey, g)
    print(f"{'训练':<6}{'分量':<6}{'预测':>6}{'真值':>6}{'差':>6}{'隐含U':>8}")
    for name, slot, p, r, d in rows:
        u = r / p if p else float("nan")
        print(f"{name:<6}{slot:<6}{p:>6}{r:>6}{d:>6}{u:>8.3f}")
    print("\n结论：隐含上层乘区 U 全为 1.000 ⇒ 普通训练的「下段」就是引擎")
    print("default_calc_training_value 原样，value_scale(2.22/7.35) 无机制依据。")
    print("注意：本快照 5 条命令的教えstat效果全未触发（唯一 ready 的演目1 是助力，")
    print("而速度训练是友情训练 ⇒ 助力按口径不生效），所以上层系数无法由此快照定标。")


if __name__ == "__main__":
    main()
