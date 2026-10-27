"""从 bwiki（官方 wiki，B站托管）补一张缺失的支援卡进 cardDB。

数据来源：https://wiki.biligame.com/umamusume/【いとどめでたき、まつりかな】吾妻
把页面渲染后的「支援效果」逐级数值（Lv1..Lv50）抄下来，按
  rank0..4 = 卡等级 30 / 35 / 40 / 45 / 50
折算成 cardDB 的 cardValue 结构。

注意：这个等级映射是按社区资料推定的（SSR 突破等级对应的等级上限），
已在脚本里注明；如果后续拿到官方 cardDB 生成器，应以其为准。
"""

import json
import os

CARD_DB = r"E:\HJM\umaai-matsuri\data\cardDB.json"
CARD_ID = "30323"

# 页面「支援效果」逐级值，顺序 = Lv1,5,10,15,20,25,30,35,40,45,50
CURVES = {
    "ganJing": [10, 10, 10, 10, 15, 20, 20, 20, 20, 20, 20],
    "xunLian": [1, 1, 1, 1, 1, 1, 1, 3, 5, 7, 10],
    "initialJiBan": [10, 12, 14, 17, 19, 22, 25, 26, 27, 28, 30],
    "saiHou": [1, 1, 1, 1, 1, 1, 1, 5, 5, 5, 5],
    "eventRecoveryAmountUp": [10, 14, 19, 24, 29, 34, 40, 45, 50, 55, 60],
    "eventEffectUp": [1, 5, 10, 13, 16, 20, 20, 22, 25, 27, 30],
    "failRateDrop": [5, 5, 5, 5, 5, 5, 5, 5, 5, 10, 20],
    "vitalCostDrop": [1, 2, 5, 7, 10, 12, 15, 17, 20, 22, 25],
    "initialPt": [5, 5, 5, 5, 5, 5, 5, 5, 5, 30, 60],
}

# Lv1,5,...,50 的下标
LEVELS = [1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
RANK_LEVELS = [30, 35, 40, 45, 50]


def at(curve, level):
    return curve[LEVELS.index(level)]


def card_value(rank):
    lv = RANK_LEVELS[rank]
    hint_bonus = [0, 0, 0, 0, 0, 0]
    return {
        "filled": True,
        "bonus": [0, 0, 0, 0, 0, 0],
        "initialBonus": [0, 0, 0, 0, 0, at(CURVES["initialPt"], lv)],
        "hintBonus": hint_bonus,
        "hintLevel": 0,
        "xunLian": at(CURVES["xunLian"], lv),
        "initialJiBan": at(CURVES["initialJiBan"], lv),
        "saiHou": at(CURVES["saiHou"], lv),
        "eventRecoveryAmountUp": at(CURVES["eventRecoveryAmountUp"], lv),
        "eventEffectUp": at(CURVES["eventEffectUp"], lv),
        "failRateDrop": at(CURVES["failRateDrop"], lv),
        "vitalCostDrop": at(CURVES["vitalCostDrop"], lv),
        "ganJing": at(CURVES["ganJing"], lv),
    }


def main():
    db = json.load(open(CARD_DB, encoding="utf-8"))
    if CARD_ID in db:
        print("already present")
        return
    entry = {
        "cardId": int(CARD_ID),
        "charaId": 9052,
        "cardName": "[友]吾妻",
        "fullName": "【いとどめでたき、まつりかな】吾妻",
        "rarity": 3,
        "cardType": 5,
        "cardValue": [card_value(r) for r in range(5)],
        "uniqueEffectType": 1,
        "uniqueEffectSummary": "羁绊条件值 60；速度加成 +1；耐力加成 +1",
        # [触发id, 羁绊阈值, effect_type, value, effect_type2, value2]
        # 3=速度加成 4=耐力加成（引擎 add_effect_line 口径）
        "uniqueEffectParam": [101, 60, 3, 1, 4, 1],
    }
    db[CARD_ID] = entry
    backup = CARD_DB + ".bak_before_" + CARD_ID
    if not os.path.exists(backup):
        os.replace(CARD_DB, backup)
        print("backup ->", backup)
    json.dump(db, open(CARD_DB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("added", CARD_ID, "cards now:", len(db))


if __name__ == "__main__":
    main()
