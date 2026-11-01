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
use rand::seq::SliceRandom;
use serde::{Deserialize, Serialize};
use umaai::Config;
use umaai_core::Data;
use umaai_core::gamedata::ActionValue;
use umaai_core::rng::{EventRng, StrategyRng, StreamTag, derive_seed};
use umasim::game::CardTrainingEffect;
use umasim::game::base::{BaseGame, BasePerson, RuleStreams};
use umasim::game::{Game, InheritInfo, PersonType, Trainer, TrainingValueSource};

pub use action::Action;
pub use stage::Stage;

use crate::data::{ActLevelEffect, MatsuriData, RaceRewardDef};

/// 演目槽上限（真机 `NeedActGauge = 5`，见 `docs/mechanism.md` §2）
pub const ACT_GAUGE_MAX: i32 = 5;

/// 排练基础值切到「Classic 以后档」的回合（= Junior 进度汇报会 turn 23，见 memo §3.2）
pub const KEIKO_LATE_TURN: i32 = 23;

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
    /// 每个训练的真实面板五维收益快照（来自报文 `commands[].stat_gains`，5×5：训练 × 速度/耐力/力量/根性/智力）
    /// 全零 = 未注入 → 回退引擎训练值 `value.status_pt × mult`。
    /// 报文值是游戏对「这一下训练」的全加成真值（含训练等级/得意/人头/教え/発表会）。
    pub train_stat_anchor: [[i32; 5]; 5],
    /// 発表会累计的永久收益加成（0.2 = +20%）
    pub gain_pct: f64,
    /// 已用友人出行次数（官方：全剧本约 5 次）
    pub outing_count: i32,
    /// 装配时的起点回合（0 基，来自面板 turn−1）
    ///
    /// `newgame` 里为 0，宿主装配真实局面后覆盖。
    pub start_turn: i32,
    /// 起步时的彩圈人数快照（每训练位，仅作兜底）
    ///
    /// 锚点 `stat_gains` 已含起步彩圈，对全部彩圈再乘倍率会双重计算，故 `do_train`
    /// 只对「起步后新触发」的部分生效。`capture_initial_shining()` 在宿主
    /// 装配真实局面（distribution 重建）后调用一次。
    pub initial_shining: [i32; 5],
    /// 起步时每训练位的**友情加成合计%**（`calc_training_buff().youqing`）——彩圈增量的正确基线
    ///
    /// 2026-10-31 改口径：彩圈 = 该卡的真实友情加成，不再用固定 25% 的拍数
    /// （库兰指出「彩圈不是应该算友情加成吗，怎固定 25%」）。
    /// 依据 `docs/mechanism.md:126`「基础能力 = floor(下段 × (1+友情合计) × …)」，
    /// 引擎 `default_calc_training_buff` 已把**非彩圈卡的 youqing 归零**、彩圈的按
    /// `add()` 乘算组合，所以「当前 youqing − 本快照」正好等于起步后新长出来的友情加成%
    /// （含多张卡同时彩圈的组合结果）。拿不到卡组 buff 时才退回
    /// `initial_shining` + `shining_boost` 的旧兜底。
    pub initial_youqing: [f32; 5],
    /// レースボーナス（午式駿大祭 成功 +30% / 大成功 +100%，2026-10-31 真机「闘大祭ボーナス」表）
    pub race_bonus: f64,
    /// 「練習ベタ」状态：失败后获得，训练失败率 +22 个百分点（2026-10-31 真机：同体力 5 时
    /// 带上手○ 52% / 带ベタ 97%），保健室解除。
    pub practice_bad: bool,
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
            train_stat_anchor: [[0; 5]; 5],
            gain_pct: 0.0,
            outing_count: 0,
            start_turn: 0,
            initial_shining: [0; 5],
            initial_youqing: [0.0; 5],
            race_bonus: 0.0,
            practice_bad: false,
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
    /// 友人出行：体力 +51，随机演目 Lv+1（推友人事件）
    pub(crate) fn do_outing(&mut self, rng: &mut impl Rng) -> Result<()> {
        let uma = self.uma_mut();
        uma.vital = (uma.vital + 51).min(uma.max_vital);
        let idx = rng.random_range(0..5usize);
        self.act_level[idx] = (self.act_level[idx] + 1).min(10);
        self.outing_count += 1;
        Ok(())
    }

    /// 记录起步彩圈快照（宿主装配真实局面、distribution 重建后调用一次）
    ///
    /// 存两样：人数（兜底用）与**友情加成合计%**（主路径用，= `calc_training_buff().youqing`，
    /// 引擎已对非彩圈卡归零）。两者都是「起步后新长出的部分」的增量基线。
    pub fn capture_initial_shining(&mut self) {
        for t in 0..5usize {
            self.initial_shining[t] = self.shining_count(t) as i32;
            self.initial_youqing[t] = self.calc_training_buff(t).map(|b| b.youqing).unwrap_or(0.0);
        }
    }

    /// 合宿窗口（0 基）：12-15 / 36-39 / 60-63（官方 memo）
    pub(crate) fn is_camp_turn(&self) -> bool {
        matches!(self.turn, 12..=15 | 36..=39 | 60..=63)
    }

    /// 训练/稽古收益的「接近上限」衰减系数（防偏科）
    ///
    /// 引擎 `calc_training_value` 是静态表、不看当前属性与五维上限，会一直堆已高的属性。
    /// 真实训练收益随属性逼近上限而递减；这里用饱和式 `remain/(remain+K)` 近似：
    /// 剩余空间越大越接近 1.0，越贴上限越趋近 `cap_decay_floor`。
    /// `cap_decay <= 0`（未配置）时恒返回 1.0，保持原有行为。
    fn cap_decay_factor(&self, cur: i32, limit: i32) -> f64 {
        if self.data.cap_decay <= 0.0 || limit <= 0 {
            return 1.0;
        }
        let remain = (limit - cur).max(0) as f64;
        (remain / (remain + self.data.cap_decay)).max(self.data.cap_decay_floor)
    }

    /// 本回合生涯赛的赛后奖励基准档
    ///
    /// turn 73/75/77（面板 74/76/78）= URA 予選/準決勝/決勝；其余生涯回合 = 目標賽。
    /// 表未配 URA 档时回落目標賽档，不猜。
    fn race_reward_def(&self) -> RaceRewardDef {
        let idx = match self.base.turn {
            73 => 0,
            75 => 1,
            77 => 2,
            _ => return self.data.race_reward.target,
        };
        self.data
            .race_reward
            .ura
            .get(idx)
            .copied()
            .unwrap_or(self.data.race_reward.target)
    }

    /// 属性软上限：增量中**超出 `soft_cap` 的部分**减半（向下取整）
    ///
    /// 不是「超过就整条减半」——报文实证 pre=1195 / 满增量 30 时实得 17（= 5 + 25/2），
    /// pre≥1200 / 满增量 7 时实得 3。`soft_cap <= 0` 时关闭。
    fn soft_cap_gain(&self, pre: i32, gain: i32) -> i32 {
        soft_cap_gain(self.data.soft_cap, pre, gain)
    }

    /// 稽古用的成长率乘区（1.0 = 无加成）。
    ///
    /// 普通训练走 `default_calc_training_value`，那里已经乘过 `uma().five_status_bonus`
    /// （来自 `umaDB.json` 的 `fiveStatusBonus`，如 111401 迷人景致 = 速10/力10/智10）；
    /// 但演目稽古走剧本自己的 `keiko_basic_value` 表、不经过那条路径，所以这里单独补一次，
    /// 否则「同一匹马换个剧本练稽古」会少掉成长率那 10~20%。
    fn keiko_growth(&self, idx: usize) -> f64 {
        // 成长率是否作用于演目稽古**尚未实测**（普通训练那边由引擎自己乘 five_status_bonus，
        // 这条路不经过它）。memo 说稽古「下段 = 同一训练位的普通训练值」暗示应该乘，
        // 但没有真机数据时乘上去会把五维抬高 ~350 点，故先恒 1.0，等测到 Classic 稽古再开。
        let _ = idx;
        1.0
    }

    pub(crate) fn do_train(&mut self, train: usize, mut _rng: &mut impl Rng) -> Result<()> {
        // 合宿：所有训练都变成演目稽古（官方：合宿训练与演目稽古同一 ID）
        if self.is_camp_turn() {
            // 合宿：训练=稽古，但不升 Lv（官方：合宿结束统一 +1）
            return self.do_keiko_impl(train, _rng, false);
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
        // 失败判定（引擎公式：训练等级阈值 × 体力）
        // URA 收尾期（引擎 67..=76）失败率固定 -100%（memo §7 本番演出限定效果）
        let in_ura_finale = (67..=76).contains(&self.base.turn);
        let fail_rate = if in_ura_finale {
            0.0
        } else {
            self.calc_training_failure_rate(&CardTrainingEffect::default(), train)
                .unwrap_or(0.0)
        };
        // 不屈の教え（205）：体力达标时失败率 −100%（2026-10-31 截图）
        let fail_rate = {
            let vital = self.uma().vital;
            let drop = self.advice_fail_drop(vital);
            (fail_rate as f64 * (100.0 - drop) / 100.0).max(0.0) as f32
        };
        let roll = rand::Rng::random::<f32>(&mut _rng) * 100.0;
        let failed = roll < fail_rate;
        // 教え + 発表会加成
        let has_shining = self
            .base
            .distribution
            .get(train)
            .map(|d| {
                d.iter()
                    .any(|&pidx| pidx >= 0 && self.is_shining_at(pidx as usize, train))
            })
            .unwrap_or(false);
        let (mult, vital_delta, vital_pct) = self.advice_mods(train, has_shining);
        let vital_delta = vital_delta + (value.vital as f64 * vital_pct / 100.0) as i32;
        // 报文 `stat_gains` 是游戏对「这一下训练」的全加成真值（含训练等级/得意/人头/
        // 教え/発表会），直接用可绕过引擎训练值的缺口；未注入时才回退引擎训练值。
        let stat_anchor = self.train_stat_anchor[train];
        let stat = if stat_anchor.iter().any(|&x| x > 0) {
            let k = self.data.stat_anchor_scale;
            [
                (stat_anchor[0] as f64 * k) as i32,
                (stat_anchor[1] as f64 * k) as i32,
                (stat_anchor[2] as f64 * k) as i32,
                (stat_anchor[3] as f64 * k) as i32,
                (stat_anchor[4] as f64 * k) as i32,
            ]
        } else {
            [
                (value.status_pt[0] as f64 * mult) as i32,
                (value.status_pt[1] as f64 * mult) as i32,
                (value.status_pt[2] as f64 * mult) as i32,
                (value.status_pt[3] as f64 * mult) as i32,
                (value.status_pt[4] as f64 * mult) as i32,
            ]
        };
        // 彩圈增量：羁绊 ≥80 且得意的训练位，**按该卡真实友情加成%**结算
        // （2026-10-31 改口径，旧版是每多一个彩圈固定 +25%——库兰指出那不对）。
        // 主路径：`calc_training_buff().youqing` 已把非彩圈卡归零、彩圈的按 add() 组合，
        // 减掉起步快照即「起步后新长出来的友情加成%」，直接进 (1 + x/100) 乘区。
        // 兜底：拿不到卡组 buff 时退回旧的 人数 × shining_boost。
        let shining_mult = if self.data.shining_boost > 0.0 {
            match self.calc_training_buff(train) {
                Ok(b) => {
                    let delta = (b.youqing - self.initial_youqing[train]).max(0.0);
                    1.0 + delta as f64 / 100.0
                }
                Err(_) => {
                    let extra =
                        (self.shining_count(train) as i32 - self.initial_shining[train]).max(0);
                    1.0 + self.data.shining_boost * extra as f64
                }
            }
        } else {
            1.0
        };
        let stat = [
            (stat[0] as f64 * shining_mult) as i32,
            (stat[1] as f64 * shining_mult) as i32,
            (stat[2] as f64 * shining_mult) as i32,
            (stat[3] as f64 * shining_mult) as i32,
            (stat[4] as f64 * shining_mult) as i32,
        ];
        // 接近上限衰减：真实训练收益随属性逼近上限递减，引擎静态表无此机制
        let limits = self.uma().five_status_limit;
        let cur = self.uma().five_status;
        let stat = [
            (stat[0] as f64 * self.cap_decay_factor(cur[0], limits[0])) as i32,
            (stat[1] as f64 * self.cap_decay_factor(cur[1], limits[1])) as i32,
            (stat[2] as f64 * self.cap_decay_factor(cur[2], limits[2])) as i32,
            (stat[3] as f64 * self.cap_decay_factor(cur[3], limits[3])) as i32,
            (stat[4] as f64 * self.cap_decay_factor(cur[4], limits[4])) as i32,
        ];
        let scaled = ActionValue {
            status_pt: [
                stat[0],
                stat[1],
                stat[2],
                stat[3],
                stat[4],
                (value.status_pt[5] as f64 * mult) as i32,
            ],
            vital: value.vital + vital_delta,
            ..Default::default()
        };
        if failed {
            // 失败 → 固定事件「無茶は厳禁！」（2026-10-31 真机日志逐字核对）：
            // ① 无属性/PT ② **体力 +10（回复，不是扣除）** ③ 干劲 −1 级
            // ④ 主属性 −10 ⑤ **随机另外 2 项各 −10**（实测掉了 速/耐/根 三项各 10）
            // ⑥ 获得「練習ベタ」⑦「練習上手○」被顶掉
            let raws = [0.0_f32; 5];
            let mut picks: Vec<usize> = (0..5).filter(|i| *i != train).collect();
            picks.shuffle(_rng);
            let mut drop_idx = vec![train];
            drop_idx.extend(picks.into_iter().take(2));
            let _ = raws;
            let uma = self.uma_mut();
            uma.vital = (uma.vital + 10).min(uma.max_vital);
            uma.motivation = (uma.motivation - 1).max(1);
            for i in drop_idx {
                if i < uma.five_status.len() {
                    uma.five_status[i] = (uma.five_status[i] - 10).max(0);
                }
            }
            self.practice_bad = true;
        } else {
            self.uma_mut().add_value(&scaled);
        }
        self.base.train_level_count[train] += 1;
        // 羁绊成长：训练位上每个人头羁绊 +friend_gain；縛の教え（effect 16）另给全编成卡 +3（不叠加）
        let bond_bonus = self.advice_value(16) as i32;
        if self.data.friend_gain_per_train > 0 || bond_bonus > 0 {
            let gain = self.data.friend_gain_per_train;
            let dist = self
                .base
                .distribution
                .get(train)
                .cloned()
                .unwrap_or_default();
            let n = self.persons.len();
            {
                let bond_targets = self.bond_deck_indexes();
                let Self { base, persons, .. } = self;
                for pidx in dist {
                    if pidx >= 0 && (pidx as usize) < n {
                        base.add_friendship(persons, pidx as usize, gain);
                    }
                }
                if bond_bonus > 0 {
                    for pidx in bond_targets {
                        if pidx < persons.len() {
                            base.add_friendship(persons, pidx, bond_bonus);
                        }
                    }
                }
            }
        }
        // 演目槽入账（官方 2026-10-31 口径）：该训练位每位「演目担当」+1、闪彩担当再 +1，
        // 落点是**该担当对应的演目**，不是当前训练（原来只喂训练对口的演目，见 docs/mechanism.md §2）
        self.gain_act_gauge_from_train(train);
        // URA 收尾期（面板 t68–77 = 引擎 67..=76，memo §7 本番演出限定效果）：
        // 所选训练对应演目 +1 级；同时每次训练 体力 +30、干劲 +1。
        if (67..=76).contains(&self.base.turn) {
            self.act_level[train.clamp(0, 4)] = (self.act_level[train.clamp(0, 4)] + 1).min(10);
            let max_vital = self.uma().max_vital;
            let uma = self.uma_mut();
            uma.vital = (uma.vital + 30).min(max_vital.max(uma.vital));
            uma.add_motivation(1);
        }
        Ok(())
    }

    /// 某个演目上的教え修正：(收益倍率, 体力增减)
    fn advice_mods(&self, act: usize, has_shining: bool) -> (f64, i32, f64) {
        let aid = self.advice_id.get(act).copied().unwrap_or(0);
        if aid <= 0 {
            return (1.0, 0, 0.0);
        }
        let level = self
            .advice_level
            .get(act)
            .copied()
            .unwrap_or(1)
            .clamp(1, 10);
        let mut gain = 0.0;
        let vital = 0;
        let mut vital_pct = 0.0;
        for e in self
            .data
            .advice_effects
            .iter()
            .filter(|e| e.advice_id == aid && e.level == level)
        {
            match e.effect_type {
                // 13 主属性% / 21 副属性%：直接加训练效果
                13 | 21 => gain += e.value as f64 / 100.0,
                // 203「助力の教え」：**友情非発生時**才给训练效果（助力 20~30%），
                // 2026-10-31 截图：`友情非発生時にトレーニング効果アップ 20%`。
                // 原来无条件加，会在彩圈回合系统性高估。
                203 => {
                    if !has_shining {
                        gain += e.value as f64 / 100.0;
                    }
                }
                // 202 友情ボーナス%（協力の教え 1~10%）。`mechanism.md:129`
                // 「友情合计（**仅友情训练**）：…協力教え…」→ 只在彩圈回合生效；
                // 原来无条件加，会在非彩圈回合虚高。
                202 => {
                    if has_shining {
                        gain += e.value as f64 / 100.0;
                    }
                }
                3 => vital_pct += e.value as f64,
                _ => {}
            }
        }
        (1.0 + gain + self.gain_pct, vital, vital_pct)
    }

    /// 编成内支援卡（`card_id` 有值）的 persons 下标，用于「全编成卡羁绊 +N」类效果
    fn bond_deck_indexes(&self) -> Vec<usize> {
        self.persons
            .iter()
            .enumerate()
            .filter(|(_, p)| p.card_id.is_some())
            .map(|(i, _)| i)
            .collect()
    }

    /// 教え效果按类型取「不叠加」的最大值（同一类型可能 5 个演目都有；官方文案「効果は重複しない」）
    fn advice_value(&self, effect_type: i32) -> f64 {
        let mut best = 0.0f64;
        for act in 0..5 {
            let aid = self.advice_id.get(act).copied().unwrap_or(0);
            if aid <= 0 {
                continue;
            }
            let level = self
                .advice_level
                .get(act)
                .copied()
                .unwrap_or(1)
                .clamp(1, 10);
            for e in
                self.data.advice_effects.iter().filter(|e| {
                    e.advice_id == aid && e.level == level && e.effect_type == effect_type
                })
            {
                best = best.max(e.value as f64);
            }
        }
        best
    }

    /// 不屈の教え（effect 205）：体力 ≥ 阈值（Lv1 45 … Lv6 20，即 `50 - 5*level`）时失败率 −100%
    fn advice_fail_drop(&self, vital: i32) -> f64 {
        let mut drop = 0.0f64;
        for act in 0..5 {
            let aid = self.advice_id.get(act).copied().unwrap_or(0);
            if aid <= 0 {
                continue;
            }
            let level = self
                .advice_level
                .get(act)
                .copied()
                .unwrap_or(1)
                .clamp(1, 10);
            let has = self
                .data
                .advice_effects
                .iter()
                .any(|e| e.advice_id == aid && e.level == level && e.effect_type == 205);
            if has && vital >= 50 - 5 * level {
                let v = self
                    .data
                    .advice_effects
                    .iter()
                    .find(|e| e.advice_id == aid && e.level == level && e.effect_type == 205);
                drop = drop.max(v.map(|e| e.value as f64).unwrap_or(100.0));
            }
        }
        drop
    }

    /// 训练带来的演目槽入账（官方口径 2026-10-31，见 `docs/mechanism.md` §2）
    ///
    /// - 该训练位上**每位演目担当****+1**，落点是**本人担当的演目**（按 `train_type`
    ///   对齐 act 1..5），**不是当前训练**；
    /// - 担当包括：支援卡 / 编组外神驹 NPC / 团队卡（memo §2.2④、§2.3 U30）；
    ///   记者、理事长、剧本友人（吾妻）不是任何演目的担当，不推槽；
    /// - **闪彩担当**同场再多 +1（合计 +2），其他担当仍 +1；
    /// - 槽上限 [`ACT_GAUGE_MAX`]，满槽跳过（不溢出到其他演目）。
    pub(crate) fn gain_act_gauge_from_train(&mut self, train: usize) {
        let dist = self
            .base
            .distribution
            .get(train)
            .cloned()
            .unwrap_or_default();
        for pidx in dist {
            if pidx < 0 {
                continue;
            }
            let idx = pidx as usize;
            if idx >= self.persons.len() {
                continue;
            }
            if matches!(
                self.persons[idx].person_type,
                PersonType::Reporter
                    | PersonType::Yayoi
                    | PersonType::ScenarioCard
                    | PersonType::OtherFriend
            ) {
                continue; // 非担当（记者 / 理事长 / 友人）不推槽
            }
            let act = self.persons[idx].train_type.clamp(0, 4) as usize;
            let mut add = 1;
            if self.is_shining_at(idx, train) {
                add += 1; // 闪彩担当 +2
            }
            let gauge = &mut self.act_gauge[act];
            *gauge = (*gauge + add).min(ACT_GAUGE_MAX);
        }
    }

    /// 非训练来源的演目槽入账（官方口径 2026-10-31）
    ///
    /// 目标比赛 **+4**、休息 / 外出 / 非目标比赛 **+1**；每次入账的落点 =
    /// 「演目 Lv 最低 → 槽最低 → 速→耐→力→根→智」，满槽跳过。
    pub(crate) fn push_act_gauge(&mut self, times: i32) {
        for _ in 0..times {
            let mut best: Option<usize> = None;
            for i in 0..5 {
                if self.act_gauge[i] >= ACT_GAUGE_MAX {
                    continue;
                }
                let better = match best {
                    None => true,
                    Some(b) => {
                        (self.act_level[i], self.act_gauge[i])
                            < (self.act_level[b], self.act_gauge[b])
                    }
                };
                if better {
                    best = Some(i);
                }
            }
            match best {
                Some(b) => self.act_gauge[b] += 1,
                None => break,
            }
        }
    }

    /// 给「演目 Lv 最低 → 槽最低 → 速→耐→力→根→智」的那个演目 **+1 级**（不清槽）
    ///
    /// 用途（memo §2.4）：**吾妻外出**每段给最低演目 +1（一局 5 段）；URA 收尾期也复用同一落点。
    pub(crate) fn bump_lowest_act_level(&mut self) {
        let mut best = 0usize;
        for i in 1..5 {
            if (self.act_level[i], self.act_gauge[i]) < (self.act_level[best], self.act_gauge[best])
            {
                best = i;
            }
        }
        self.act_level[best] = (self.act_level[best] + 1).min(10);
    }

    /// 演目稽古：演目等级高 = 基础值按等级档 + 等级效果（官方 intake 3.2/3.3）
    pub(crate) fn do_keiko(&mut self, idx: usize, mut _rng: &mut impl Rng) -> Result<()> {
        self.do_keiko_impl(idx, _rng, true)
    }

    fn do_keiko_impl(&mut self, idx: usize, mut _rng: &mut impl Rng, level_up: bool) -> Result<()> {
        if idx >= 5 {
            return Err(anyhow::anyhow!("演目下标越界: {idx}"));
        }
        let level = self.act_level[idx].clamp(1, 10) as usize;
        // 排练基础值按时期两档（memo §3.2）：Junior 档 = 静态表 5101-5105；
        // Classic 以后档在 turn 23（Junior 进度汇报会后）切换，静态表里没有单列。
        let table = if self.turn >= KEIKO_LATE_TURN && !self.data.keiko_basic_value_late.is_empty()
        {
            &self.data.keiko_basic_value_late
        } else {
            &self.data.keiko_basic_value
        };
        let row = table.get(idx).copied().unwrap_or([0; 7]);
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
        let five_types = self.base.card_type_count.iter().filter(|c| **c > 0).count() >= 5;
        let sp_bonus = if five_types {
            (row[5] as f64 * (effect.sp_practice_pct as f64 + 100.0) / 100.0) as i32 - row[5]
        } else {
            0
        };
        // 排练 = 「同一训练位的普通训练下段」+ 演目基值加项（memo §3.3），
        // 所以卡组的训练效果/友情/干劲/人数/副属性/成长率**都要走同一套乘区**，
        // 与普通训练 `default_calc_training_value` 保持一致。
        let buffs = self.calc_training_buff(idx).unwrap_or_default();
        let person_count = self.count_training_persons(idx);
        let basic_motivation = ((self.uma().motivation - 3) * 10) as f32;
        let growth = self.uma().five_status_bonus;
        let keiko_chain = |base: i32, i: usize| -> i32 {
            if base <= 0 {
                return 0;
            }
            // 副属性（「〇〇ボーナス」）：只对本身有收益的维度按固定值相加
            let with_bonus = base
                + if i < 5 {
                    *buffs.bonus.get(i).unwrap_or(&0)
                } else {
                    0
                };
            (with_bonus as f32
                * (1.0 + 0.01 * (buffs.youqing + effect.friend_pct as f32))
                * (1.0 + 0.01 * basic_motivation * (1.0 + 0.01 * buffs.ganjing as f32))
                * (1.0 + 0.01 * buffs.xunlian as f32)
                * (1.0 + 0.05 * person_count as f32)
                * (1.0 + 0.01 * *growth.get(i).unwrap_or(&0) as f32))
                .floor() as i32
        };
        let value = ActionValue {
            status_pt: [
                keiko_chain(row[0] + effect.all_base, 0),
                keiko_chain(row[1] + effect.all_base, 1),
                keiko_chain(row[2] + effect.all_base, 2),
                keiko_chain(row[3] + effect.all_base, 3),
                keiko_chain(row[4] + effect.all_base, 4),
                keiko_chain(row[5] + sp_bonus, 5),
            ],
            vital: row[6],
            ..Default::default()
        };
        let fail_rate = self
            .calc_training_failure_rate(&CardTrainingEffect::default(), idx)
            .unwrap_or(0.0);
        // 不屈の教え（205）：体力达标时失败率 −100%（2026-10-31 截图）
        let fail_rate = {
            let vital = self.uma().vital;
            let drop = self.advice_fail_drop(vital);
            (fail_rate as f64 * (100.0 - drop) / 100.0).max(0.0) as f32
        };
        let roll = rand::Rng::random::<f32>(&mut _rng) * 100.0;
        let failed = roll < fail_rate;
        let (mult, vital_delta, vital_pct) = self.advice_mods(idx, false);
        let vital_delta = vital_delta + (value.vital as f64 * vital_pct / 100.0) as i32;
        // 接近上限衰减：稽古收益同样随属性逼近上限递减（PT 不衰减）
        let limits = self.uma().five_status_limit;
        let cur = self.uma().five_status;
        let scaled = ActionValue {
            status_pt: [
                (value.status_pt[0] as f64
                    * mult
                    * self.keiko_growth(0)
                    * self.cap_decay_factor(cur[0], limits[0])) as i32,
                (value.status_pt[1] as f64
                    * mult
                    * self.keiko_growth(1)
                    * self.cap_decay_factor(cur[1], limits[1])) as i32,
                (value.status_pt[2] as f64
                    * mult
                    * self.keiko_growth(2)
                    * self.cap_decay_factor(cur[2], limits[2])) as i32,
                (value.status_pt[3] as f64
                    * mult
                    * self.keiko_growth(3)
                    * self.cap_decay_factor(cur[3], limits[3])) as i32,
                (value.status_pt[4] as f64
                    * mult
                    * self.keiko_growth(4)
                    * self.cap_decay_factor(cur[4], limits[4])) as i32,
                (value.status_pt[5] as f64 * mult) as i32,
            ],
            vital: value.vital + vital_delta,
            ..Default::default()
        };
        if failed {
            // 稽古失败与普通训练失败同一套「無茶は厳禁！」口径（2026-10-31 真机）：
            // 体力 +10（回复）、干劲 −1、该演目维度 −10 + 随机 2 项各 −10、
            // 获得「練習ベタ」，且**不清槽不升级**（花落实测）。
            //   ⚠️ 与花落转达的视频证据冲突：视频说失败「不给属性但演目Lv仍+1」，待复核。
            let mut picks: Vec<usize> = (0..5).filter(|i| *i != idx).collect();
            picks.shuffle(_rng);
            let mut drop_idx = vec![idx];
            drop_idx.extend(picks.into_iter().take(2));
            let uma = self.uma_mut();
            uma.vital = (uma.vital + 10).min(uma.max_vital);
            uma.motivation = (uma.motivation - 1).max(1);
            for i in drop_idx {
                if i < uma.five_status.len() {
                    uma.five_status[i] = (uma.five_status[i] - 10).max(0);
                }
            }
            self.practice_bad = true;
        } else {
            self.uma_mut().add_value(&scaled);
        }
        if !failed {
            // 合宿（level_up=false）：训练=稽古但不升 Lv（官方：合宿结束全演目 +1）
            if level_up {
                // 真机口径（2026-10-31，报文 pre/post 对照 5 段 session）：
                // 稽古成功**只在「槽满→升级」那一下清槽**；槽未满的成功稽古不动槽；
                // 合宿期成功稽古也不清槽（合宿结束统一全演目 +1，槽数原样继承）。
                self.act_gauge[idx] = 0;
                self.act_level[idx] = (self.act_level[idx] + 1).min(10);
                // 演目Lv9/10 的「基礎能力上限 +20/+25」（act_effect 206）是**每级固定值**，
                // 只能在升级那一刻结算「新旧等级之差」一次；每次稽古都加会把上限无限抬高。
                let limit_at = |lv: i32| -> i32 {
                    self.data
                        .act_level_effects
                        .iter()
                        .find(|e| e.level == lv)
                        .map(|e| e.base_limit)
                        .unwrap_or(0)
                };
                let delta = (limit_at((level as i32 + 1).min(10)) - limit_at(level as i32)).max(0);
                if delta > 0 {
                    for s in self.uma_mut().five_status_limit.iter_mut() {
                        *s += delta;
                    }
                }
                // act_effect 109（スキルPt 上限 +10…+120）在我们的模型里没有 PT 上限概念，跳过。
            }
        }
        Ok(())
    }

    /// 派生本局面自持的策略流（注入点与回合边界调用）
    pub(crate) fn reset_strategy_stream(&mut self) {
        let turn = self.base.turn as u64;
        self.strategy = self.base.master().map(|master| {
            StrategyRng::new(derive_seed(master, &[turn, StreamTag::Strategy.tag()]))
        });
    }

    /// 比赛后隐藏事件（bwiki「隐藏事件一览」238 条，剧本 15 专属）
    ///
    /// 与 Python 模拟器同口径：生涯比赛回合后按 `HIDDEN_EVENT_RATE` 概率抽一条，
    /// 效果只结算属性 / 技能点 / 体力（hint 未建模，因 bwiki 抓取未落 numbers）。
    fn apply_hidden_event(&mut self, rng: &mut EventRng) {
        const HIDDEN_EVENT_RATE: f64 = 0.35;
        if self.data.hidden_events.is_empty() || !rng.random_bool(HIDDEN_EVENT_RATE) {
            return;
        }
        let (stat, pt, vital) = {
            let ev = &self.data.hidden_events[rng.random_range(0..self.data.hidden_events.len())];
            (ev.stat, ev.pt, ev.vital)
        };
        let limits = self.uma().five_status_limit;
        let uma = self.uma_mut();
        for i in 0..5 {
            uma.five_status[i] = (uma.five_status[i] + stat[i]).min(limits[i]);
        }
        uma.skill_pt += pt;
        uma.vital = (uma.vital + vital).clamp(0, uma.max_vital);
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
                let turn_events = self.generate_events(&mut streams.event);
                if self.turn == 77
                    && let Ok(ending) =
                        umaai_core::Data::get().and_then(|d| d.system_event("ending").cloned())
                {
                    self.base.unresolved_events.push(ending);
                }
                for event in &turn_events {
                    self.run_event(event, trainer, decision, &mut streams.event)?;
                }
                // 生涯比赛：引擎按回合生成比赛事件（与 BasicGame 同口径）
                if self.base.uma.is_race_turn(self.base.turn) {
                    // 赛后奖励 = 基准表 × 二段式乘区，两段各自 floor：
                    //   一段 = 支援卡「赛后」加成（`uma.race_bonus`）
                    //   二段 = 発表会#3 的 cp204 レースボーナス（`self.race_bonus`）
                    // 报文实证（2026-11-01）：cid168 后期目標賽 PT=216=floor(floor(72×1.5)×2)
                    // 而五维只有 14=floor(floor(5×1.5)×2)——单段式 floor(5×3.0)=15 被否掉。
                    let def = self.race_reward_def();
                    let m_card = (100 + self.base.uma.race_bonus) as f64 / 100.0;
                    let m_cp = 1.0 + self.race_bonus;
                    let full_stat = race_reward_two_stage(def.stat, m_card, m_cp);
                    let pt = race_reward_two_stage(def.pt, m_card, m_cp);
                    // 1200 软上限：按赛前值逐维算实得增量（PT 不受软上限影响）
                    let pre = self.uma().five_status;
                    let gains: [i32; 5] =
                        std::array::from_fn(|i| self.soft_cap_gain(pre[i], full_stat));
                    // 先走引擎入口拿到事件骨架与 `set_race`，再把面板值换成机制表算出的真值
                    self.base.push_race_event(1.0, 1.0)?;
                    if let Some(value) = self
                        .base
                        .unresolved_events
                        .last_mut()
                        .and_then(|e| e.choices.first_mut())
                        .and_then(|c| c.first_mut())
                        .map(|c| &mut c.value)
                    {
                        value.status_pt[..5].copy_from_slice(&gains);
                        value.status_pt[5] = pt;
                    }
                    // 演目槽：目标比赛 +4（官方口径 2026-10-31；落点 Lv 低→高、同级属性序）
                    self.push_act_gauge(4);
                }
                // 执行本回合事件（比赛事件也在其中）
                let events = std::mem::take(&mut self.base.unresolved_events);
                for event in &events {
                    self.run_event(event, trainer, decision, &mut streams.event)?;
                }
                // B1a：比赛后隐藏事件（bwiki 238 条，剧本 15 专属，按概率抽一条）
                if self.base.uma.is_race_turn(self.base.turn) {
                    self.apply_hidden_event(&mut streams.event);
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
                    && let Some(list) = self.data.advice_grant_order.get(idx)
                {
                    for (slot, aid) in list.iter().enumerate() {
                        if slot < 5 {
                            self.advice_id[slot] = *aid;
                            self.advice_level[slot] = self.act_level[slot].clamp(1, 10);
                        }
                    }
                }
                // 転成：第 2 次伝受时把两个初始教え当材料，吃融合效果
                if self.turn == 23 {
                    self.gain_pct += 0.25;
                }
                // 合宿开始：体力 +10、干劲 +1（2026-10-31 真机「1年目、駿大祭合宿開始！」）
                if matches!(self.turn, 12 | 36 | 60) {
                    let uma = self.uma_mut();
                    uma.vital = (uma.vital + 10).min(uma.max_vital);
                    uma.motivation = (uma.motivation + 1).min(5);
                }
                // 合宿结束：全演目 Lv +1（官方：合宿中演目 Lv 不升，结束统一 +1）
                if matches!(self.turn, 16 | 40 | 64) {
                    for i in 0..5 {
                        self.act_level[i] = (self.act_level[i] + 1).min(10);
                    }
                }
                // 発表会：全演目达标 → 永久收益加成
                if let Some(cp) = self
                    .data
                    .check_points
                    .iter()
                    .find(|c| c.turn == self.turn)
                    .cloned()
                    && self.act_level.iter().all(|lv| *lv >= cp.need)
                {
                    let great = self.act_level.iter().all(|lv| *lv > cp.need);
                    let buff = if great { cp.great } else { cp.success };
                    self.gain_pct += buff.gain_pct as f64 / 100.0;
                    // 午式駿大祭（第 3 场，cp.schedule == 3）：成功 +30% / 大成功 +100% レースボーナス
                    if cp.schedule == 3 {
                        self.race_bonus = if great { 1.00 } else { 0.30 };
                    }
                    // 达标实数奖励（官方 Wiki 文案，见 docs/plan/intake.md）
                    // t24 = 全能力+5 / PT+200 / トレLv+1
                    // t48 = 全能力+10 / PT+300 / トレLv+1
                    // t68 = 全能力+15 / PT+400 / 体力+30 / やる気+1
                    if cp.all_stat > 0 {
                        let limits = self.uma().five_status_limit;
                        let uma = self.uma_mut();
                        for (st, lim) in uma.five_status.iter_mut().zip(limits.iter()) {
                            *st = (*st + cp.all_stat).min(*lim);
                        }
                    }
                    if cp.pt > 0 {
                        self.uma_mut().skill_pt += cp.pt;
                    }
                    if cp.train_lv > 0 {
                        // 训练等级 = 1 + count/4，所以每 +1 级 = count +4
                        for t in 0..5 {
                            self.base.train_level_count[t] += cp.train_lv * 4;
                        }
                    }
                    if cp.vital > 0 {
                        let uma = self.uma_mut();
                        uma.vital = (uma.vital + cp.vital).min(uma.max_vital);
                    }
                    if cp.motivation > 0 {
                        self.uma_mut().add_motivation(cp.motivation);
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
            Stage::End => {
                // 终局：把剩余 PT 折算成已购技能评分（约 2.46/PT，上限按真实提示池曲线）
                if self.turn >= self.max_turn() {
                    let pt = self.base.uma.skill_pt as f64;
                    // 引擎 score_parts 已计 pt_score_rate(2.0)，补 0.46 到官方 2.46/点
                    let curve = 45000.0_f64.min(pt * 0.46);
                    self.base.uma.skill_score = curve as i32;
                }
            }
        }
        Ok(())
    }
}

/// 从引擎配置与剧本资产装配一局剧本局面（终端样例外壳与剧本侧测试共用）
///
/// 数据根与配置由宿主注入：本函数只读宿主给的 [`Config`] 与 [`MatsuriData`]，不碰任何路径。
pub fn build_game(cfg: &Config, data: Arc<MatsuriData>) -> Result<MatsuriGame> {
    build_game_for_uma(cfg, data, cfg.sim.uma)
}

/// 按指定马娘 id 装配一局（配置里的 `cfg.sim.uma` 只是示例马娘）
///
/// 宿主按真实局面装配时用本函数：面板报文里的 `chara_id` 才是这局的马娘，
/// 成长率 / 初始五维 / 生涯赛程都跟它走。
pub fn build_game_for_uma(
    cfg: &Config,
    data: Arc<MatsuriData>,
    uma_id: u32,
) -> Result<MatsuriGame> {
    MatsuriGame::newgame(
        uma_id,
        &cfg.sim.cards,
        cfg.inherit(),
        cfg.race_grades()?,
        data,
    )
}

/// 赛后奖励的二段式乘区：`floor(floor(base × m_card) × m_cp)`
///
/// 两段各自向下取整，**不能合并成一段**。报文实证（cid168 後期目標賽）：
/// PT 216 = floor(floor(72×1.5)×2)，五维只有 14 = floor(floor(5×1.5)×2)；
/// 若合并成单段 floor(5×3.0) 会得 15，与实测不符。
fn race_reward_two_stage(base: i32, m_card: f64, m_cp: f64) -> i32 {
    ((f64::from(base) * m_card).floor() * m_cp).floor() as i32
}

/// 属性软上限：增量中**超出 `cap` 的部分**减半（向下取整），不足 `cap` 的部分原样保留
///
/// 不是「超过就整条减半」。报文实证（`chara_info.default_max_*` 各局恒 1200）：
/// - pre=1195 / 满增量 30 → 5 + ⌊25/2⌋ = 17（cid168 t76 URA 準決勝 智力）
/// - pre≥1200 / 满增量 7 → ⌊7/2⌋ = 3（cid168 t54 大阪杯 速度）
///
/// `cap <= 0` 视为关闭。
fn soft_cap_gain(cap: i32, pre: i32, gain: i32) -> i32 {
    if cap <= 0 || gain <= 0 {
        return gain;
    }
    let below = gain.min((cap - pre).max(0));
    below + (gain - below) / 2
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_picks_distinct_rows_per_train_level() {
        // 步骤1验收：基础表填满 Lv1-5 后，引擎 `default_calc_training_value` 按
        // train_level 取到不同行（旧表每种训练只有两行相同行，Lv3-5 被 clamp 住不增长）。
        // 零卡组零人头零 buff：主属性 = floor(表值 × (1 + 成长率%))。
        let _ = Data::init(crate::testing::data_dir());
        let data = Arc::new(MatsuriData::load(crate::testing::data_dir()).unwrap());
        let mut game =
            MatsuriGame::newgame(106301, &[], InheritInfo::default(), &[], data).unwrap();
        let bonus = game.uma().five_status_bonus;
        for train in 0..5 {
            for lv in 1..=5 {
                game.base.train_level_count[train] = ((lv - 1) * 4) as i32;
                let v = Game::calc_training_value(&game, &CardTrainingEffect::default(), train)
                    .unwrap();
                let row = game.data.training_basic_value[train][lv - 1];
                let expected = (row[train] as f64 * (1.0 + 0.01 * bonus[train] as f64)).floor();
                assert_eq!(
                    v.status_pt[train] as f64, expected,
                    "训练 {train} Lv{lv} 应取到基础表第 {lv} 行"
                );
            }
        }
    }

    #[test]
    fn soft_cap_partial_halving() {
        // cid168 t76 URA 準決勝：智力 pre=1195，满增量 30 → 17
        assert_eq!(soft_cap_gain(1200, 1195, 30), 17);
        // cid170 t70 ジャパンC：智力 pre=1196，满增量 14 → 9
        assert_eq!(soft_cap_gain(1200, 1196, 14), 9);
        // cid179 t69 エ女王杯：根性 pre=1179，满增量 38 → 29
        assert_eq!(soft_cap_gain(1200, 1179, 38), 29);
    }

    #[test]
    fn soft_cap_above_and_below_cap() {
        // cid168 t54 大阪杯：速度 pre≥1200，满增量 7 → 3（排除 round：round(3.5)=4）
        assert_eq!(soft_cap_gain(1200, 1300, 7), 3);
        // 远低于上限：原样通过
        assert_eq!(soft_cap_gain(1200, 451, 7), 7);
        // 恰好压线：无超出部分
        assert_eq!(soft_cap_gain(1200, 1193, 7), 7);
        // 关闭
        assert_eq!(soft_cap_gain(0, 1195, 30), 30);
    }

    #[test]
    fn two_stage_floor_not_single_stage() {
        // cid168 後期目標賽（発表会#3 大成功 → m_cp=2.0，卡组赛后 m_card=1.5）
        assert_eq!(race_reward_two_stage(5, 1.5, 2.0), 14); // 单段会得 15
        assert_eq!(race_reward_two_stage(72, 1.5, 2.0), 216);
        // URA 予選
        assert_eq!(race_reward_two_stage(40, 1.5, 2.0), 120);
        // 无加成时保持基准值
        assert_eq!(race_reward_two_stage(72, 1.0, 1.0), 72);
    }
}
