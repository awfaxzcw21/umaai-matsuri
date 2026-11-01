# 测试一览（骏大祭 / matsuri）

> **前期验证以对话核对为主**：机制是否成立由用户按 [`plan/intake.md`](plan/intake.md) 的确认清单逐条核对，
> 自动化测试随业务落地逐步补齐，不用测试代替机制确认。
> 分工：单元（内联，私有细节 / 纯函数，`assert`）/ 机制集成（`tests/`，打印 + `Result`）/
> 冒烟（`cli/tests/`）/ 基线（`baselines/`，自动判定）。
> 测试 cwd 在各 crate 目录；路径统一经 `matsuri_core::testing` 取仓根。

## 1. 分 crate

| crate | 测试面 | 现状 |
|---|---|---|
| `matsuri-core` | 内联单测 + `tests/mechanics.rs`（机制集成） | 内联单测已有；机制集成待补 |
| `matsuri-sim` | `tests/baseline.rs`（固定种子基线） | 待补 |
| `matsuri-cli` | `tests/state_t30.rs`（t30 参考局回归）/ `tests/smoke.rs`（一局冒烟 + 可复现） | 已有 t30 回归；冒烟待补 |
| `matsuri-ai` / `matsuri-tools` | 骨架，暂无 | — |

## 2. 单元测试（内联）

| 文件 | 用例 | 守门内容 |
|---|---|---|
| `core/src/data.rs` | `parse_sample_ok` / `reject_wrong_training_count` / `reject_bad_row_width` | 资产解析 / 5 训练 / 7 值行 |
| `core/src/config.rs` | `parse_base_ok` / `overlay_replaces_list` / `unknown_key_rejected` / `out_of_range_index_rejected` | 默认层 / 覆盖层 / 未知键 / 下标 |
| `core/src/game/stage.rs` | `next_chain` / `stage_ids_are_stable` | 阶段链 / 阶段号稳定 |
| `core/src/trainer.rs` | `preferred_training_wraps` | 样例训练员轮转 |

## 3. 机制集成（`core/tests/mechanics.rs`）

> 每条机制至少一个用例；机制未确认前不写「按猜测实现」的测试。

| 用例 | 守门内容 | 对应机制文档 |
|---|---|---|
| `<待补>` | 演目 / gauge / 稽古 / 教え / 合宿 / 発表会 | `mechanism.md` §1–§6 |

## 3.5 参考局回归（`cli/tests/state_t30.rs`）

> fixture = `cli/tests/data/state_t30.json`（面板 `_state_t30.json` 快照）。
> 守门的是「引擎训练值 == 游戏自己的预测值」，不是任何拟合系数。

| 用例 | 守门内容 | 对应机制文档 |
|---|---|---|
| `t30_engine_value_matches_panel_truth` | 装配后 5 条训练命令的引擎值逐格等于面板真值（16 个非零分量）；同时钉住马娘 = 报文 `chara_id`（106301）、成长率 `[0,0,0,20,10]`、训练设施 Lv2、人数加成 = 支援卡 + 嘉宾（剧本 NPC 枠 101..115 不计） | `mechanism.md` §3.3 |
| `training_applies_soft_cap_above_1200`（`core/src/game/mod.rs`） | 训练通道的 1200 软上限接线：同一训练，赛前 500 时增量不打折、赛前 1300 时按「超出 1200 的部分减半」结算（断开 `soft_cap_gain` 会让本测失败） | `mechanism.md` § 赛后奖励（1200 软上限） |
| `check_point_buffs_are_cumulative_totals`（`core/src/game/mod.rs`） | 発表会的永久 buff 是**累计总量**：再结算一次「成功」档必须覆盖（0.20/0%/20%）而不是累加（0.60/10%/…） | `mechanism.md` §5.2 |
| `real_asset_check_point_buffs_match_master_mdb`（`core/src/data.rs`） | 配置里 3 场发表会的 得意率/友情ボーナス/ヒント発生率 六档，逐格等于 master.mdb `single_mode_15_check_point_effect` | `mechanism.md` §5.2 |
| `real_asset_advice_effects_match_master_mdb`（`core/src/data.rs`） | 配置里 153 行教诲效果（教诲 id / Lv / 条件类型 / 条件值 / 效果类型 / 效果值）逐格等于 master.mdb `single_mode_15_advice_effect` | `mechanism.md` §3.3 |
| `real_asset_fusion_effects_match_master_mdb`（`core/src/data.rs`） | 配置里 9 行转成固定档逐格等于 master.mdb `single_mode_15_fusion_effect` | `mechanism.md` §3.3 |
| `advice_scope_follows_memo_table`（`core/src/game/mod.rs`） | 教诲效果的三个作用域（训练域 / 排练域 / 全局域）逐条等于 memo §4.2 / §4.5 的口径 | `mechanism.md` §3.3 |
| `fusion_replaces_base_effect_with_fixed_tier`（`core/src/game/mod.rs`） | 融合后基础教诲的等级效果整条换成固定档（13→15），新教诲效果按绑定演目的 Lv 叠加，原教诲的羁绊效果不再单独生效 | `mechanism.md` §3.3 |
| `advice_only_applies_when_act_member_present`（`core/src/game/mod.rs`） | 教诲只在该演目的担当参与了该训练位时生效 | `mechanism.md` §3.3 |
| `advice_stacks_by_participant_count`（`core/src/game/mod.rs`） | 教诲效果按参与担当人数叠加（2 名担当 ⇒ 2 倍） | `mechanism.md` §3.3 |
| `gain_zone_does_not_multiply_skill_pt`（`core/src/game/mod.rs`） | 獲得量区（主/副属性获得量）**不乘技能Pt**（兼修の教え「スキルPtは対象外」） | `mechanism.md` §3.3 |
| `train_and_keiko_domains_do_not_leak`（`core/src/game/mod.rs`） | 排练域效果不外泄到普通训练、训练域效果不外泄到排练 | `mechanism.md` §3.3 |
| `assistance_only_when_no_friendship_training`（`core/src/game/mod.rs`） | 助力（条件 3 = 友情非发生时）只在非友情训练生效 | `mechanism.md` §3.3 |
| `extra_keiko_rate_follows_teach_level_and_not_headcount`（`core/src/game/mod.rs`） | 「追加の稽古」发生率取教诲绑定演目的 Lv 值，且**不按人数加算** | `mechanism.md` §3.4 |
| `extra_keiko_upper_and_lower_outcomes`（`core/src/game/mod.rs`） | 上档：主属性 +20 / PT +20 / 体力 −5 / 演目Lv 最低的演目槽 +1；下档：主属性 +5 / 体力 +5 | `mechanism.md` §3.4 |
| `assist_support_granted_by_keiko_and_consumed_by_training`（`core/src/game/mod.rs`） | 「助力の支え」由排练获得、训练消耗 1 次 | `mechanism.md` §3.4 |
| `free_race_window_matches_packet_evidence`（`core/src/game/mod.rs`） | 自选比赛窗口 = 引擎回合 44 / 68，各 3 场 G1（报文 34 局全一致） | `mechanism.md` §4 |
| `free_race_action_only_on_window_turns`（`core/src/game/mod.rs`） | `Action::Race` 只在窗口回合出现，且排在候选末尾（训练位下标不位移） | `mechanism.md` §4 |
| `free_race_pays_one_random_stat_and_pt`（`core/src/game/mod.rs`） | 目標外 GI = **1 随机项** +10 / PT +45；非生涯比赛给演目槽 +1 | `mechanism.md` §4 |
| `free_race_unknown_grade_is_not_target_row`（`core/src/game/mod.rs`） | GII 无样本 ⇒ 返回 `None`，不回落目標賽档（未知不猜） | `mechanism.md` §4 |
| `check_point_tiers_follow_memo`（`core/src/game/mod.rs`） | 発表会成功档 3/100、大成功档 5/200（memo L215-250） | `mechanism.md` §4 |
| `check_point_gives_chairman_bond_on_great_only`（`core/src/game/mod.rs`） | 大成功给理事長絆+15，成功档没有 | `mechanism.md` §4 |
| `check_point_all_stat_respects_soft_cap`（`core/src/game/mod.rs`） | 発表会的全能力奖励吃 1200 软上限（过线的那项减半） | `mechanism.md` §4 |
| `act_level_cap_is_six_unless_ssr_agatsuma`（`core/src/game/mod.rs`） | 演目 Lv 上限：不带/ R 吾妻 = 6、SSR 吾妻 = 10（报文 7 局实证） | `mechanism.md` §4 |
| `act_level_never_exceeds_cap`（`core/src/game/mod.rs`） | 演目 Lv 不得越过上限 | `mechanism.md` §4 |
| `camp_start_vitality_by_year`（`core/src/game/mod.rs`） | 合宿开始体力：1 年 +10 / 2 年 +15 / 3 年外推 +15 | `mechanism.md` §4 |
| `t30_advice_binding_matches_panel`（`cli/tests/state_t30.rs`） | fixture 报文给的教诲绑定 `[401, 501, 601, 301, 701]` 与各演目的担当人数（速度 2 / 根性 0 / 嘉宾 `train_type = -1` 不计担当） | `mechanism.md` §3.3 |

## 4. 冒烟与基线

| 项 | 内容 | 判定 |
|---|---|---|
| 冒烟 | 固定种子跑满回合、两次逐位一致 | `Result` / 非零退出 |
| 基线 | 一基线一 JSON（`input` 自持、`expect` 逐项容差、`meta` 不含路径） | 自动比对，失败返回 `Err` |

- 重录命令：`<cargo ... baseline --update>`（S9 落地）
- 数值占位替换后必须重录，并在 `glossary.md` 记影响面。

## 5. 对话核对记录（前期主验证方式）

| 日期 | 核对范围 | 参与人 | 结论 | 遗留 |
|---|---|---|---|---|
| 2026-10-26 | 建仓 / 代号 / master.mdb 表结构 | 用户 + Agent | 代号 `matsuri`、五 crate 建仓、11 张表结构登记 | 机制数值与回合坐标待收口 |
| 2026-10-26 | `single_mode_15_*` / `training_effect` / `text_data` | 用户 + Agent | 数值与效果编号已解码；回合统一 **0 基**（原始 1 基 -1） | gauge 归属 / 合宿延长 / 友人分身 / 双人头 / 事件回合 |
| 2026-10-26 | 合宿延长 / 合宿期支援卡事件 | 用户 + Agent | 每年保证 4 个非比赛合宿回合；正常合宿不触发支援卡事件 | 延长段连续事件 / Lv+1 结算点待测 |
