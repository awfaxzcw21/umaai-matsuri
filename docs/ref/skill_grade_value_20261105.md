# 技能价与 `grade_value`：表侧一手核（2026-11-05）

来源两条，**分开采信**：

- 探针脚本 8 个 + 一份 tsv ＝工作区 `E:/哈基米分裂/工具/uaviewer_probe/`、`data/viewer_text_extra_192.tsv`，
  由廉价会话「这是什么」（`e5f590b4`）产出，本会话**原样复制**进 `tools/uaviewer_probe/`（未改写内容）。
- 下面每一条数字都是**我自己重跑 master.mdb 得到的**，不是转述它给的数。

⚠ 「原样」指复制那一刻的字节。本仓 `core.autocrlf=true`，检出后可能是 CRLF ⇒
要核字节请用 `git show HEAD:tools/uaviewer_probe/<file> | sha1sum`，别拿工作区检出件比。

## 1. 已自证的表侧事实（`E:/HJM/_ref01/matsuri-ref-01/data/master.mdb`，只读）

| 事实 | 实测 |
|---|---|
| `skill_data.grade_value` 取值 | **22 个离散值**，近正负对称：`−500 −262 −174 −129 0 85 129 174 180 217 240 262 263 334 340 370 394 434 461 508 633 758` |
| 按稀有度分层均值 | rar1 `169.4`（n=880，min −500）／rar2 `494.7`（n=353）／rar3 `240.0`（n=22，恒）／rar4·5 `340.0`（n=22·254，恒）／rar6 `608.4`（n=692，max 758） |
| **技能 PT 价在表里** | `single_mode_skill_need_point`（1195 行，列 `id / need_skill_point / status_type / status_value / solvable_type`）与 `skill_data.id` **1195/1195 全join** ⇒ 每个技能有表侧原价。取值 `0 40 50 60 70 80 90 100 110 120 130 140 150 160 170 180 190 200 320 360`，众数 200(347)／180(243)／160(161) |
| 等级系数 | `skill_level_value`（331 行，`ability_type × level 1..10`）＝**万分比** `10000 10100 10200 10300 …` ⇒ Lv2 起 +1%/+2% 阶梯 |
| 升级经验 | `skill_exp`（10 行，`type/level/scale`）＝`50 100 150 200 …` |
| 剧本 15 无专属技能价 | `single_mode_15_*` 共 11 张（act/advice/fusion/check_point/schedule/outing/finals…）**均无技能价列** ⇒ 价表是全局的 |
| viewer 文本表 | `viewer_text_extra_192.tsv` ＝文本 cat `138` 的 191 条**活动名**，无数值、对技能价零贡献（它只证明「文本侧没有价」） |

## 2. 我这边新量到的一条（对技能汇率有直接含义）

**先说明什么不是新的**：`README.md` 的 Hachimi 段（本目录 `:195-197`）早已记下
`skill_data.grade_value` ＝「每个技能的评价点」（rar3→240 / rar5→340 / rar6→240…758），
并且已经写明它是**把拟合系数 2.35（技能Pt→Σgrade）换成真表的路线**，卡在「能否判定取得了哪些技能」。
本篇不重复这个判断，只补两件它没有的东西。

`grade_value / need_skill_point` **不是常数**：全体 n=1186，均值 1.600、中位 1.356、范围 −3.225..+4.367；
**按稀有度劈开**——rar1 比值 `1.017`（n=840）、rar2 比值 `3.016`（n=346）。

⇒ 我们 11-03 定的**全局技能汇率 2.61（带宽 2.31–2.62）正落在 rar1 与 rar2 之间**，
形态上是「稀有度混合池的平均」而不是机制常数。这只是**线索**，不构成改判：
比值口径本身没经过任何一局真机验证，且 `need_skill_point` 是否为客户端实际扣的 PT
（而非表定标价）**未核**——真机侧要回报文 `skill_point` 逐手对。

**第二条增量**：`single_mode_skill_need_point` 与 `skill_data` **1195/1195 全 join**（§1 已测），
即「价」这一列是表侧完整的，不依赖 `.br` 插件缓存、也不依赖稀有度分层——
Hachimi 段当年只能按 rarity 看恒值，现在能逐技能取价。

## 3. 未定性（保持对方原话的克制）

`grade_value` 是不是客户端**技能分**本身，**未定**。反例候选：它同样可能是继承评价权重或图鉴权重
（rar3/4/5 恒值 240/340 这种「分档常数」形态，既像强度分也像档位标签）。
**在它被一局真机对上之前，不许写进引擎当技能分。**

## 4. 可证伪的下一步（只有一条，报文侧，不跑引擎）

拿同一匹马、技能集已知的一局一手报文，把报文里的**技能侧贡献**与
`Σ grade_value × skill_level_value 万分比系数` 对表：

- 对上 ⇒ 才谈接线（且接线取表侧原值，不取拟合）。
- 对不上 ⇒ 只保留 `need_skill_point` 那部分（它是表里原始列，不依赖 `grade_value` 的语义），
  `grade_value` 降为参照、不进估值层。

## 5. 禁令（下游照此）

- 不许把 `grade_value` 当技能分接进 `search_score`／`score_parts`。
- 不许拿 `need_skill_point` 反推「汇率」后直接替换 2.61——先过第 4 节那一局。
- 第 2 节的比值口径若被引用，必须带上「未验证」标注。

## 6. 复现

```
node tools/uaviewer_probe/grade_value_probe.cjs    # grade_value 分布 + 稀有度分层
node tools/uaviewer_probe/export_pt_table.cjs      # 1195 行技能价表（名/价/grade_value/rarity/category/exp_type）
node tools/uaviewer_probe/skill_tables.cjs         # 相关表清点
node tools/uaviewer_probe/skill_probe.cjs
node tools/uaviewer_probe/grade_tier_check.cjs
node tools/uaviewer_probe/find_name_cat.cjs
node tools/uaviewer_probe/diff_text.cjs            # ⚠ 依赖 viewer 解包件，见下
node tools/uaviewer_probe/extract_json.cjs <zip解包输入> <输出目录> [minBytes=50000]
```

依赖（均**只读**，不在本仓）：

- `E:/HJM/_ref01/matsuri-ref-01/data/master.mdb`、同目录 `text_data_dict.json`（5,798,099 B）——前 6 条只吃这两个。
- `diff_text.cjs` 里**硬编码**的是临时路径 `C:/Users/PC/AppData/Local/Temp/uv3/out/blob_02_off1179180.json`。
  脚本按原样复制未改写，**Temp 一旦被清就断**；解包件已另存到
  `E:/HJM/_ref_uaviewer/uv3_out/`（blob_00/01/02，共 5.2 MB，11-05 06:25 那份）——要跑就把路径指过去。
- 上游源包（耐久）：`D:/Downloads/standalone构建，老旧架构.zip`（93,167,043 B）。
- 需 Node ≥22 的 `node:sqlite`（本机 v24.18.0 实测 `export_pt_table.cjs`／`grade_value_probe.cjs` 可跑）。
