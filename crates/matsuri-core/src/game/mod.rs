//! 剧本局面（`MatsuriGame`）、资源快照与装配
//!
//! 状态底座复用引擎 [`BaseGame`]（回合 / 马娘 / 卡组 / 继承 / 规则流），剧本只加自己的
//! 阶段机、人头与剧本资产。契约 impl 集中在 [`traits`]，本文件只放 inherent 方法。

pub mod action;
pub mod stage;
pub mod traits;

use std::ops::{Deref, DerefMut};
use std::sync::Arc;

use anyhow::Result;
use rand::Rng;
use rand::rngs::StdRng;
use serde::{Deserialize, Serialize};
use umaai::Config;
use umaai_core::Data;
use umaai_core::gamedata::ActionValue;
use umaai_core::rng::{StrategyRng, StreamTag, derive_seed};
use umasim::game::base::{BaseGame, BasePerson, RuleStreams};
use umasim::game::{Game, InheritInfo, Trainer, TrainingValueSource};

pub use action::Action;
pub use stage::Stage;

use crate::data::{ActLevelEffect, MatsuriData};

/// 剧本资源快照（给前端的纯数据 View）
///
/// 形态 A 五条纪律（见引擎仓 `docs/scenario/contracts.md`）：**单一取数口** / 只放引擎不知道的字段 /
/// 快照语义（幂等、无副作用）/ 可演进（`non_exhaustive`）/ **不进契约 trait**。
/// S1 留一个占位字段；S2 后替换为 5 条演目 gauge / 5 个演目 Lv / 教え槽位等。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct MatsuriView {
    /// S1 占位：剧本自持资源；S2 后替换为演目账本
    pub sample_resource: i32,
    /// 演目 gauge（0..5）
    pub act_gauge: [i32; 5],
    /// 演目等级（1..10）
    pub act_level: [i32; 5],
}

/// 剧本局面
///
/// 状态底座复用引擎的 [`BaseGame`]（回合 / 马娘 / 卡组 / 继承 / 规则流），剧本只加自己的
/// 阶段机、人头、策略流与剧本资产。
#[derive(Debug, Clone, PartialEq)]
pub struct MatsuriGame {
    /// 引擎状态底座（经 `Deref` 取用；底座方法名不得与契约方法同名，见 AGENTS.md）
    pub base: BaseGame,
    /// 人头（当前直接复用引擎参考实现 `BasePerson`；后续实现可定义自己的 `Person`）
    pub persons: Vec<BasePerson>,
    /// 剧本自持阶段机
    pub stage: Stage,
    /// 剧本自持策略流（动作结算与剧本机制随机；从规则主种子派生）
    pub strategy: Option<StrategyRng>,
    /// 剧本自持资产（宿主载入后注入；训练表等数值都取自它）
    pub data: Arc<MatsuriData>,
    /// 演目 gauge（0..5）
    pub act_gauge: [i32; 5],
    /// 演目等级（1..10）
    pub act_level: [i32; 5],
    /// 每个演目挂的教え id（0 = 未分配）
    pub advice_id: [i32; 5],
    /// 教え等级（用演目等级近似）
    pub advice_level: [i32; 5],
    /// 発表会累计的永久收益加成（0.2 = +20%）
    pub gain_pct: f64,
}

impl Deref for MatsuriGame {
    type Target = BaseGame;

    fn deref(&self) -> &Self::Target {
        &self.base
    }
}

impl DerefMut for MatsuriGame {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.base
    }
}

impl MatsuriGame {
    /// 建一局（要求宿主已初始化静态数据：`umaai_core::Data::init`）
    ///
    /// `data` 是剧本自持资产（宿主载入后注入）：训练基础值表等数值都取自它。
    pub fn newgame(
        uma_id: u32,
        deck_ids: &[u32],
        inherit: InheritInfo,
        race_grades: &[i32],
        data: Arc<MatsuriData>,
    ) -> Result<Self> {
        let mut game = Self {
            base: BaseGame::new(
                uma_id,
                deck_ids,
                inherit,
                Data::get()?.consts.five_status_limit_base,
                race_grades,
            )?,
            act_gauge: [0; 5],
            act_level: [1; 5],
            advice_id: [0; 5],
            advice_level: [1; 5],
            gain_pct: 0.0,
            persons: Vec::new(),
            stage: Stage::Begin,
            strategy: None,
            data,
        };
        game.init_persons()?;
        Ok(game)
    }

    /// 追加一个人头（按当前人数分配人头序号）
    pub fn add_person(&mut self, mut person: BasePerson) {
        person.person_index = self.persons.len() as i32;
        self.persons.push(person);
    }

    /// 取剧本资源快照（**单一取数口**；何时取由宿主决定）
    #[must_use]
    pub fn view(&self) -> MatsuriView {
        MatsuriView {
            sample_resource: self.data.sample_resource,
            act_gauge: self.act_gauge,
            act_level: self.act_level,
        }
    }

    /// 训练结算（S1 版：按剧本资产里的表入账 + 设施计数；失败判定 / 羁绊 / hint 留给剧本作者）
    /// 合宿窗口（0 基）：12-15 / 36-39 / 60-63（官方 memo）
    fn is_camp_turn(&self) -> bool {
        matches!(self.turn, 12..=15 | 36..=39 | 60..=63)
    }

    pub(crate) fn do_train(&mut self, train: usize, _rng: &mut impl Rng) -> Result<()> {
        // 合宿：所有训练都变成演目稽古（官方：合宿训练与演目稽古同一 ID）
        if self.is_camp_turn() {
            return self.do_keiko(train, _rng);
        }
        let table = self.training_basic_table();
        let level = self.train_level(train).clamp(1, 5) - 1;
        // 当前表档位可少于 5：等级超出即取末档（后续实现给满 5 档）
        let row = table
            .get(train)
            .and_then(|levels| levels.get(level).or_else(|| levels.last()))
            .copied()
            .unwrap_or([0; 7]);
        // 用引擎默认训练结算：吃卡组 buff / 人头 / 等级（与拉面杯同一套口径）
        let value = match self
            .calc_training_buff(train)
            .and_then(|b| self.calc_training_value(&b, train))
        {
            Ok(v) => v,
            Err(_) => ActionValue {
                status_pt: [row[0], row[1], row[2], row[3], row[4], row[5]],
                vital: row[6],
                ..Default::default()
            },
        };
        // 教え + 発表会加成
        let (mult, vital_delta) = self.advice_mods(train);
        let scaled = ActionValue {
            status_pt: [
                (value.status_pt[0] as f64 * mult) as i32,
                (value.status_pt[1] as f64 * mult) as i32,
                (value.status_pt[2] as f64 * mult) as i32,
                (value.status_pt[3] as f64 * mult) as i32,
                (value.status_pt[4] as f64 * mult) as i32,
                (value.status_pt[5] as f64 * mult) as i32,
            ],
            vital: value.vital + vital_delta,
            ..Default::default()
        };
        self.uma_mut().add_value(&scaled);
        self.base.train_level_count[train] += 1;
        // 演目 gauge：有对应人头就涨（官方：有对应人头 +1、友情 +2）
        let persons = self.count_training_persons(train);
        let gain = if persons > 0 { 1 + persons.min(2) } else { 0 };
        if gain > 0 {
            self.act_gauge[train] = (self.act_gauge[train] + gain as i32).min(5);
        }
        Ok(())
    }

    /// 某个演目上的教え修正：(收益倍率, 体力增减)
    fn advice_mods(&self, act: usize) -> (f64, i32) {
        let aid = self.advice_id.get(act).copied().unwrap_or(0);
        if aid <= 0 {
            return (1.0, 0);
        }
        let level = self.advice_level.get(act).copied().unwrap_or(1).clamp(1, 10);
        let mut gain = 0.0;
        let mut vital = 0;
        for e in self
            .data
            .advice_effects
            .iter()
            .filter(|e| e.advice_id == aid && e.level == level)
        {
            match e.effect_type {
                13 | 21 | 202 | 203 => gain += e.value as f64 / 100.0,
                3 => vital += e.value,
                _ => {}
            }
        }
        (1.0 + gain + self.gain_pct, vital)
    }

    /// 演目稽古：演目等级高 = 基础值按等级档 + 等级效果（官方 intake 3.2/3.3）
    pub(crate) fn do_keiko(&mut self, idx: usize, _rng: &mut impl Rng) -> Result<()> {
        if idx >= 5 {
            return Err(anyhow::anyhow!("演目下标越界: {idx}"));
        }
        let level = self.act_level[idx].clamp(1, 10) as usize;
        let row = self
            .data
            .keiko_basic_value
            .get(idx)
            .copied()
            .unwrap_or([0; 7]);
        // 演目等级效果
        let effect = self
            .data
            .act_level_effects
            .iter()
            .find(|e| e.level == level as i32)
            .cloned()
            .unwrap_or(ActLevelEffect {
                level: level as i32,
                all_base: 0,
                friend_pct: 0,
                sp_practice_pct: 0,
                sp_limit: 0,
                base_limit: 0,
            });
        let five_types = self
            .base
            .card_type_count
            .iter()
            .filter(|c| **c > 0)
            .count()
            >= 5;
        let sp_bonus = if five_types {
            (row[5] as f64 * (effect.sp_practice_pct as f64 + 100.0) / 100.0) as i32 - row[5]
        } else {
            0
        };
        let value = ActionValue {
            status_pt: [
                row[0] + effect.all_base,
                row[1] + effect.all_base,
                row[2] + effect.all_base,
                row[3] + effect.all_base,
                row[4] + effect.all_base,
                row[5] + sp_bonus,
            ],
            vital: row[6],
            ..Default::default()
        };
        let (mult, vital_delta) = self.advice_mods(idx);
        let scaled = ActionValue {
            status_pt: [
                (value.status_pt[0] as f64 * mult) as i32,
                (value.status_pt[1] as f64 * mult) as i32,
                (value.status_pt[2] as f64 * mult) as i32,
                (value.status_pt[3] as f64 * mult) as i32,
                (value.status_pt[4] as f64 * mult) as i32,
                (value.status_pt[5] as f64 * mult) as i32,
            ],
            vital: value.vital + vital_delta,
            ..Default::default()
        };
        self.uma_mut().add_value(&scaled);
        if five_types && effect.sp_limit > 0 {
            for s in self.uma_mut().five_status_limit.iter_mut() {
                *s += effect.sp_limit;
            }
        }
        if effect.base_limit > 0 {
            for s in self.uma_mut().five_status_limit.iter_mut() {
                *s += effect.base_limit;
            }
        }
        self.act_gauge[idx] = 0;
        self.act_level[idx] = (self.act_level[idx] + 1).min(10);
        Ok(())
    }

    /// 派生本局面自持的策略流（注入点与回合边界调用）
    pub(crate) fn reset_strategy_stream(&mut self) {
        let turn = self.base.turn as u64;
        self.strategy = self.base.master().map(|master| {
            StrategyRng::new(derive_seed(master, &[turn, StreamTag::Strategy.tag()]))
        });
    }

    /// 单阶段推进（S1 版阶段机；后续实现按自己的回合结构改）
    pub(crate) fn stage_body<T: Trainer<Self>>(
        &mut self,
        trainer: &T,
        decision: &mut StdRng,
        streams: &mut RuleStreams,
        strategy: &mut StrategyRng,
    ) -> Result<()> {
        match self.stage {
            Stage::Begin => {
                // 回合事件（与 BasicGame 同口径）
                let mut turn_events = self.generate_events(&mut streams.event);
                if self.turn == 77 {
                    if let Ok(ending) = umaai_core::Data::get().and_then(|d| d.system_event("ending").cloned()) {
                        self.base.unresolved_events.push(ending);
                    }
                }
                for event in &turn_events {
                    self.run_event(event, trainer, decision, &mut streams.event)?;
                }
                // 生涯比赛：引擎按回合生成比赛事件（与 BasicGame 同口径）
                if self.base.uma.is_race_turn(self.base.turn) {
                    let scale = (100 + self.base.uma.race_bonus) as f64 / 100.0;
                    self.base.push_race_event(scale, 1.0)?;
                }
                // 执行本回合事件（比赛事件也在其中）
                let events = std::mem::take(&mut self.base.unresolved_events);
                for event in &events {
                    self.run_event(event, trainer, decision, &mut streams.event)?;
                }
                // 人头分配 + 提示（hint）：走回合固定流（seed 可复现）
                self.distribute_all(&mut streams.turn_fixed)?;
                self.distribute_hint(&mut streams.turn_fixed)?;
                // 伝受：按回合把教え分配到演目
                if let Some(idx) = self
                    .data
                    .advice_grant_turns
                    .iter()
                    .position(|t| *t == self.turn)
                {
                    if let Some(list) = self.data.advice_grant_order.get(idx) {
                        for (slot, aid) in list.iter().enumerate() {
                            if slot < 5 {
                                self.advice_id[slot] = *aid;
                                self.advice_level[slot] = self.act_level[slot].clamp(1, 10);
                            }
                        }
                    }
                }
                // 転成：第 2 次伝受时把两个初始教え当材料，吃融合效果
                if self.turn == 23 {
                    self.gain_pct += 0.25;
                }
                // 発表会：全演目达标 → 永久收益加成
                if let Some(cp) = self
                    .data
                    .check_points
                    .iter()
                    .find(|c| c.turn == self.turn)
                    .cloned()
                {
                    if self.act_level.iter().all(|lv| *lv >= cp.need) {
                        let great = self.act_level.iter().all(|lv| *lv > cp.need);
                        let buff = if great { cp.great } else { cp.success };
                        self.gain_pct += buff.gain_pct as f64 / 100.0;
                    }
                }
                // 事件链留待后续（`streams.event`）
            }
            Stage::Train => {
                let actions = self.list_actions()?;
                let selection = trainer.select_action(self, &actions, decision)?;
                // 动作结算走**策略流**：与决策抽签分开，改决策流消耗不会位移结算随机
                self.apply_action(&actions[selection], strategy)?;
            }
            Stage::End => {}
        }
        Ok(())
    }
}

/// 从引擎配置与剧本资产装配一局剧本局面（终端样例外壳与剧本侧测试共用）
///
/// 数据根与配置由宿主注入：本函数只读宿主给的 [`Config`] 与 [`MatsuriData`]，不碰任何路径。
pub fn build_game(cfg: &Config, data: Arc<MatsuriData>) -> Result<MatsuriGame> {
    MatsuriGame::newgame(
        cfg.sim.uma,
        &cfg.sim.cards,
        cfg.inherit(),
        cfg.race_grades()?,
        data,
    )
}
