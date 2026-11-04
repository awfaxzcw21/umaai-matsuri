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

---

## 7. 同日第二批交接（`e5f590b4`，06:4x）：三条「期望结果」逐条裁决

对方要求：① 把比赛 grade×名次 PT 表接进非训练 PT 来源、重跑 −29.1% 缺口；
② 用 `disable_singlemode` 砍掉「金技能可 PT 直购」的搜索分支；③ `grade_value` 维持未证。
**裁决＝① 驳回（一手报文否证）／② 无可执行动作（该分支不存在）／③ 照办。**
以下全部自己重算，未采信对方数字。

### 7.1 `single_mode_free_win_point` 不是剧本 15 的赛后 PT 腿 ⇒ ① 驳回

表本身逐行核对通过：66 行 `id / race_group_id / grade / order_min / order_max / point_num`，
grade 100→`100/60/40/20/10`、200→`80/48/32/16/8`、300→`60/36/24/12/6`、400→`40/24/16/8/4`、
500/600/700→`20/12/8/4/2`、800→`10,其余0`、900→`10/6/4/2/1`，`race_group_id` 40001–40003 的
grade 0 档为 `10/8/6/4/3/2/1/0`（名次 1..≥14）。**表值无误，但接不进我们这条腿**，三条一手否证：

| 判据 | 一手数据 | 实测 |
|---|---|---|
| 比赛结算包有没有 PT | `E:/HJM/_pttrace/race_reward_ledger.csv` 282 场 | **`pt_at_race` 全为 0**（282/282），PT 落在紧随的**赛后事件**上 |
| 比赛窗口 PT 量级 | `out/_free_race_scan.csv` 281 行 | `d_pt=0` 占 **255/281**，非零最大 30，**从未接近** 100/80/60 |
| 表的名次／等级形状 | 同一台账按 `grade×rank` 分组 | G2 名次1 与 G3 名次1 实测**同为 55**（表 80 vs 60）；grade 700 名次1 实测 **45**（表 20） |

另两条结构线索：`race_group_id` 40001–40003 经 `single_mode_race_group` 指到
`race_program_id` 2301+（团队战／练习赛族），grade 0 档名次段一直开到 14 名 ⇒ 形态更像
**团队战按名次计点**表，而非单场赛后技能Pt。它与 #33 早已钉死的
「按名次分档的 `single_mode_reward_set` 是**マニー**（`reward_type=1, item_category=91, item_id=59`），
且全部 `race_end` 报文零 PT 差分」同族。

**下游禁令**：不许把它接进非训练 PT 来源，不许据它重跑 PT 缺口（−29.1%）；
若再有人拿「按名次分档的比赛表」来当 PT，先过上面这三条。

### 7.2 `disable_singlemode` 约束自证通过，但 ② 没有分支可砍

join 实测（`single_mode_skill_need_point` ⋈ `skill_data`）：
`disable=0,rarity=1` 451 行（价 70–320）／`disable=1,rarity=1` 398 行／`disable=1,rarity=2` 346 行（110–360），
合计 **1195/1195**。⇒「`disable_singlemode=0` 的行全为 rarity=1」「rarity=2 一律 disable=1」**两条都对**。

但**引擎里没有「PT 直购技能」这条分支**：

- 本仓 `crates/`（排除 tests）搜 `need_skill_point`／`buy`／`purchase`／`skill_price` **零命中**；
- `crates/matsuri-core/src/game/mod.rs` 里 `skill_pt` 只有**累加**（`:891` `:1112` `:1566`），
  `:1717` 把存量 PT 过曲线折算成 `skill_score` —— 从不花 PT 换具体技能；
- 上游 `E:/HJM/umaai_next`（排除 tests）搜 `skill_point|SkillPoint|buy_skill|purchase_skill|need_point` **零命中**。

⇒ 该约束的**真实用处不在搜索分支，而在估值层**：rarity=2（金技能）不吃 PT 直购、只能靠提示／事件拿，
所以「存量 PT 可换任意技能」这个隐含假设在金技能上不成立。
这条**留给 #72**（技能侧贡献对表），不进 #24，也不改任何常数。

### 7.3 `grade_value` 维持未证 ⇒ ③ 照办

第 3、5 节的禁令一字不改：不得当技能分接进 `search_score`／`score_parts`；
`single_mode_scenario` id=15 的 `max_skill_point=99999`／`gimmick_discount_rate=5000`／`hint_rate=0`
属**配置行**，同样不构成 `grade_value` 的语义证据。

### 7.4 两张此前没登记的剧本 15 表（真增量，本轮实测）

| 表 | 行数／内容 | 对我方的含义 |
|---|---|---|
| `single_mode_15_schedule` | 3 行：`turn_num` **24/48/68**、`need_act_num` **5**、`need_act_level` **3/5/6** | 与我方 `data/scenario_matsuri.json` 的 `check_points`（`turn` 23/47/67、`need` 3/5/6）**逐档咬合**，回合差 1＝面板 vs 引擎口径 ⇒ `need` 的真实身份＝**`need_act_level`**；我方第 4 场（turn 77、need 6）表里没有。表多出的 `need_act_num=5` 我方配置未带，**是否为参与演目数要求未判** |
| `single_mode_15_outing_effect` | 10 行：`support_card_id × story_step 1..5 → add_level=1`，**只挂两张卡** `30323`（rarity 3）与 `10149`（rarity 1），且两张同属 **chara 9052** | 外出加等级**有表侧出处、但按卡条件触发**，与 `crates/matsuri-core/src/game/mod.rs:385` 的 `rng.random_range(0..5)` 无条件随机 Lv+1 不符 ⇒ 给 **#58** 加一条表侧约束。**不接线**，外出动几格的报文口径仍以 #58 为准 |

⚠ 这张外出表**不改变**我方 #69 第 (c) 条的负结果：它没有 event／story 键，
结构上与那 27 个未归因跳无关；它是**外出通道**的出处，不是**按号收发干劲**的出处。

### 7.5 本轮边界

只读 SQLite／CSV／grep，未写 `data/`、未动常数、未跑引擎、未改他人持有的 tracked 文件、无出网写；
本篇追加由我方提交，他方文档不动。

