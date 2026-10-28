#!/usr/bin/env python3
"""把黑板 harvest 到的 58 组真实剧情事件选项（out/event_choices.json）解码成
引擎 events.json 同构的 EventData 列表，供 scenario_matsuri.json 的 scenario_events 字段使用。

display_id 语义（已定案，见项目记忆 / tools/decode_choices2.py）：
  display_id=1  v0∈1-5 = 五维属性（1速2耐3力4根5智，v1=数值）→ status_pt[v0-1]
  display_id=1  v0=10  = 体力（v1）→ vital
  display_id=1  v0=20  = 干劲（v1）→ motivation
  display_id=1  v0=30  = 技能点（v1）→ status_pt[5]
  display_id=2         = 技能点（v1）→ status_pt[5]
  display_id=6         = 技能 hint（v0=技能id, v1=等级）→ 跳过（引擎无特定技能 hint 建模）
  display_id=4         = 事件/分支 id → 跳过
  其它（5/7/8/9/11/12/13/15/16/19/21/37/40/61/63）→ 跳过（覆盖 92% 主量）

输出结构（EventData 同构）：
  {"id", "name", "trigger": {"type":"Random","max_time":1}, "player_select": true,
   "choices": [[{"result":0,"prob":100,"value":{...}}], ...]}   # 每个选项=1组含1个必中结果
"""
import json
import os

SRC = os.path.join(os.path.dirname(__file__), "..", "..", "jidianbeiAI0.22", "out", "event_choices.json")
OUT = os.path.join(os.path.dirname(__file__), "..", "out", "scenario_events.json")

STAT_AXIS = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}  # 速耐力根智 -> status_pt 下标


def decode_gains(gains):
    """一组 gains -> ActionValue（status_pt[6]/vital/motivation 三件套，其余置 0）"""
    status = [0, 0, 0, 0, 0, 0]
    vital = 0
    motivation = 0
    for g in gains:
        d = g.get("display_id")
        v0 = g.get("v0") or 0
        v1 = g.get("v1") or 0
        if d == 1:
            if v0 in STAT_AXIS:
                status[STAT_AXIS[v0]] += v1
            elif v0 == 10:
                vital += v1
            elif v0 == 20:
                motivation += v1
            elif v0 == 30:
                status[5] += v1
            # v0=11 未知，跳过
        elif d == 2:
            status[5] += v1
        # d==6 hint / d==4 分支 / 其它：跳过
    return {
        "status_pt": status,
        "vital": vital,
        "max_vital": 0,
        "motivation": motivation,
        "hint_level": 0,
        "friendship": 0,
    }


def build():
    with open(SRC, encoding="utf-8") as f:
        raw = json.load(f)
    events = raw["events"]

    out = []
    skipped_all_empty = 0
    for i, ev in enumerate(events):
        eid = 9001 + i
        groups = []
        for opt in ev["options"]:
            value = decode_gains(opt["gains"])
            groups.append([{"result": 0, "prob": 100, "value": value}])
        # 全部选项都是空效果（被跳过的 display_id）→ 这组没有可建模收益，跳过
        def _empty(grp):
            v = grp[0]["value"]
            return (all(v["status_pt"][k] == 0 for k in range(6))
                    and v["vital"] == 0 and v["motivation"] == 0)
        if all(_empty(grp) for grp in groups):
            skipped_all_empty += 1
            continue
        out.append({
            "id": eid,
            "name": f"剧本事件{eid}",
            "trigger": {"type": "Random", "max_time": 1},
            "player_select": True,
            "choices": groups,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"生成 {len(out)} 个事件（跳过 {skipped_all_empty} 个全空组）→ {OUT}")
    return out


if __name__ == "__main__":
    build()
