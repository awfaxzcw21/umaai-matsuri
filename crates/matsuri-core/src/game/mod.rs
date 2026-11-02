//! 剧本局面（`MatsuriGame`）、资源快照与装配
//!
//! 状态底座复用引擎 [`BaseGame`]（回合 / 马娘 / 卡组 / 继承 / 规则流），剧本只加自己的
//! 阶段机、人头与剧本资产。契约 impl 集中在 [`traits`]，本文件只放 inherent 方法。

pub mod action;
pub mod stage;
pub mod traits;

use std::ops::{Deref, DerefMut};
use std::sync::Arc;

use anyhow::{Result, anyhow};
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
use umasim::game::{Game, InheritInfo, PersonType, SupportCard, Trainer, TrainingValueSource};

pub use action::Action;
pub use stage::Stage;

use crate::data::{ActLevelEffect, CheckPointDef, FreeRaceDef, MatsuriData, RaceRewardDef};

/// 演目槽上限（真机 `NeedActGauge = 5`，见 `docs/mechanism.md` §2）
pub const ACT_GAUGE_MAX: i32 = 5;

/// 排练基础值切到「Classic 以后档」的回合（= Junior 进度汇报会 turn 23，见 memo §3.2）
pub const KEIKO_LATE_TURN: i32 = 23;

/// 支援卡站「自己卡种」那一格的概率（剧本 15 的落位规则）
///
/// 引擎底座把 `deyilv` 当「自身格权重」用（`weights[train_type] += deyilv`），
/// 而真机报文证明落位**与卡牌 `deYiLv` 的数值无关**（2026-11-01 实测 413 条训练命令：
/// `deYiLv` = 10/43/60/75 的卡参与「自己类型」训练的比例 58.0 / 65.8 / 61.5 / 47.1%，无单调性）。
/// 偏置来自「按卡种站位」这一分类效应，所以这里给**与卡种无关**的固定权重。
///
/// 取值由报文实测的可观测量反解：**卡参与「自己类型」训练 = 73.1%**（零假设 35.6%，z=+15.9）。
/// 引擎权重公式 `P(自身格) = (100 + w) / (500 + w)`（分母是 5 格权重之和，不是 400）。
///
/// ⚠️ 已知口径差（2026-11-01，未闭合）：
/// - 报文**没有逐格布局**（角色在 `training_partner_array` 里的位置是分散的），
///   所以「卡在某一格」不可直接测，只能测「训练 t 的参与者里有没有 t 类卡」。
/// - 同一批报文里逐卡算「参与自己类型 / 参与总次数」只有 47~66%，与 73.1% 的差距来自
///   ①本仓卡组没有耐力卡（该格恒 0）②引擎 9.1% 不在率 ③真实玩家会优先选有同类型卡的那格
///   （我们的训练员不做这件事）。这三条都记在 `docs/mechanism.md`，未闭合前本值只算「对标值」。
pub const SELF_SLOT_P: f64 = 0.977;

/// 引擎权重公式 `P(自身格) = (100 + w) / (500 + w)` 的反解
pub(crate) fn self_slot_weight() -> f32 {
    ((500.0 * SELF_SLOT_P - 100.0) / (1.0 - SELF_SLOT_P)) as f32
}

/// 一条教诲效果行（已解析到「教诲 id + 效果类型 + 值 + 条件」）
#[derive(Debug, Clone, Copy)]
struct AdviceRow {
    /// 效果归属的教诲 id（转成固定档用被融合的基础教诲 id）
    advice_id: i32,
    effect_type: i32,
    value: i32,
    /// 条件类型（master.mdb `condition_type`）：0 = 无条件，2 = 体力阈值，3 = 友情非发生
    condition_type: i32,
    /// 条件值（体力阈值等）
    condition_value: i32,
    /// 该行来自转成固定档（作用域按被融合的基础教诲查）
    fused: bool,
}

/// 教诲效果的分区结果（memo §3.3 的乘区口径）
///
/// 基础能力 = floor(下段 × (1+友情合計) × (1+訓練効果合計) × (1+獲得量合計))
/// 技能Pt   = floor(下段 × (1+友情合計) × (1+訓練効果合計 + SP訓練効果))
#[derive(Debug, Clone, Copy, Default)]
struct AdviceMods {
    /// 主属性获得量加成 %（獲得量区：只乘主属性）
    main_pct: f64,
    /// 副属性获得量加成 %（獲得量区：只乘副属性，技能Pt 除外）
    sub_pct: f64,
    /// 训练/排练效果加成 %（訓練効果区：主属性 + 副属性 + 技能Pt）
    effect_pct: f64,
    /// 友情加成 %（友情区：主属性 + 副属性 + 技能Pt）
    friend_pct: f64,
    /// 体力消费下降 %（排练域）
    vital_pct: f64,
}

/// 是不是 5 种**基础教诲**之一（101 / 201 / 301 / 401 / 501）
fn is_base_advice(advice_id: i32) -> bool {
    matches!(advice_id, 101 | 201 | 301 | 401 | 501)
}

/// 该维度用哪个教诲倍率：主属性吃「主属性獲得量区」，其余维度吃「副属性獲得量区」
///
/// 训练位 / 演目的主属性就是它自己那一位（速度↔演目 1 …），其余四维是副属性。
fn stat_mult(main_stat: usize, stat: usize, main_mult: f64, sub_mult: f64) -> f64 {
    if stat == main_stat {
        main_mult
    } else {
        sub_mult
    }
}

/// 教诲效果的作用域：`(训练域, 排练域)`
///
/// memo §4.2（十种教诲逐级效果）与 §4.5（转成效果）的逐条口径：
///
/// | 教诲 | 效果 | 域 |
/// |---|---|---|
/// | 101 絆 | 13 主属性获得量 | 排练（「演目稽古でメイン基礎能力の獲得量アップ」）|
/// | 101 絆 | 16 羁绊 +3 | 训练（「トレーニング時に全編成サポカの絆ゲージ+3」）|
/// | 201 得意 | 4 得意率 | 全局（无模型，只记录）|
/// | 301 ヒント | 2 ヒント発生率 | 全局（无模型，只记录）|
/// | 401 助力 | 203 训练效果（条件 3 = 友情非发生）| 训练 |
/// | 401 助力 | 20 助力の支え | 排练获得（未实现）|
/// | 501 体力 | 3 体力消费 | 排练 |
/// | 601 特化 | 13 主属性获得量 | 训练 + 排练 |
/// | 601 特化 | 206 基础上限 | 全局 |
/// | 701 研钻 | 8 追加稽古发生率 | 训练 + 排练（事件未实现）|
/// | 801 協力 | 202 友情ボーナス | 训练（仅友情训练）|
/// | 801 協力 | 18 额外登场 | 训练（未实现）|
/// | 901 不屈 | 205 失败率 | 训练 |
/// | 901 不屈 | 203 排练效果 | 排练 |
/// | 1001 兼修 | 21 副属性获得量（技能Pt 対象外）| 训练 + 排练 |
/// | 1001 兼修 | 203 排练效果 | 排练 |
///
/// 转成固定档按**被融合的基础教诲**查域：101→13 排练 / 101→17 全局 / 201→4 全局 /
/// 201→202 排练 / 301→2 全局 / 301→19 排练 / 401→203 训练 / 401→20 排练 / 501→3 排练。
fn advice_scope(row: &AdviceRow) -> (bool, bool) {
    if row.fused {
        return match (row.advice_id, row.effect_type) {
            (101, 13) => (false, true),
            (101, 17) => (false, false),
            (201, 4) => (false, false),
            (201, 202) => (false, true),
            (301, 2) => (false, false),
            (301, 19) => (false, true),
            (401, 203) => (true, false),
            (401, 20) => (false, true),
            (501, 3) => (false, true),
            _ => (false, false),
        };
    }
    match (row.advice_id, row.effect_type) {
        (101, 13) => (false, true),
        (101, 16) => (true, false),
        (201, _) => (false, false),
        (301, _) => (false, false),
        (401, 203) => (true, false),
        (401, 20) => (false, true),
        (501, 3) => (false, true),
        (601, 13) => (true, true),
        (601, 206) => (false, false),
        (701, 8) => (true, true),
        (801, 202) => (true, false),
        (801, 18) => (true, true),
        (901, 205) => (true, false),
        (901, 203) => (false, true),
        (1001, 21) => (true, true),
        (1001, 203) => (false, true),
        _ => (false, false),
    }
}

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
    /// 每个演目槽上的**基础教诲** id（101/201/301/401/501，0 = 未分配）
    pub advice_base: [i32; 5],
    /// 每个演目槽上的**融合教诲** id（601/701/801/901/1001，0 = 未融合）
    ///
    /// 融合（転成）后基础教诲的等级效果整条换成 `fusion_effects` 的固定档，
    /// 新教诲的效果再叠加其上（memo §4.5）。
    pub advice_fusion: [i32; 5],
    /// 每个训练的真实面板五维收益快照（来自报文 `commands[].stat_gains`，5×5：训练 × 速度/耐力/力量/根性/智力）
    /// 全零 = 未注入 → 回退引擎训练值 `value.status_pt × mult`。
    /// 报文值是游戏对「这一下训练」的全加成真值（含训练等级/得意/人头/教え/発表会）。
    pub train_stat_anchor: [[i32; 5]; 5],
    /// 発表会累计的**得意率**（type 4）：master.mdb 给的是累计总量 ⇒ 设值不累加。
    /// 引擎没有落位概率模型，暂按「训练效果%」近似（近似，不是结论）。
    pub gain_pct: f64,
    /// 発表会累计的**友情ボーナス**（type 202，%）：作用于全部友情训练。
    pub check_friend_pct: f64,
    /// 発表会累计的**ヒント発生率**（type 2，%）：引擎无提示/技能模型 ⇒ 只记录。
    pub check_hint_pct: i32,
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
    /// 「助力の支え」剩余次数（0..3）：排练成功时获得，3 次内的训练（含友情训练与排练）
    /// 训练效果 +5%；再次获得时剩余次数重置回 3（memo §4.2 / master.mdb type 20）。
    pub assist_support: i32,
    /// 演目 Lv 上限（面板 `festa_info.max_act_level`）
    ///
    /// 真值（CN memo §2.4）：**不带吾妻 / R 吾妻 = 6，SSR 吾妻 = 10**（0 凸即可，凸数无关）。
    /// 友人「吾妻」SSR = 卡 30323、R = 卡 10149（两者 `chara_id` 同为 9052，靠稀有度区分）。
    /// 面板直接给这个值 ⇒ 宿主装配真实局面时以面板为准（`state.rs` 注入）。
    pub act_level_cap: i32,
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

/// 合宿开始（= 教えの付け替え 同一个事件）给的体力
///
/// 真机日志：「1年目、駿大祭合宿開始！」= **+10**；「2年目、駿大祭合宿開始！」= **+15**
/// （docs/机制对照表 §6）。第 3 年**没有样本** ⇒ 取与第 2 年同值（⚠ 外推，待采样后修正）。
fn camp_start_vitality(turn: i32) -> i32 {
    match turn {
        12 => 10,
        36 | 60 => 15,
        _ => 0,
    }
}

/// 由卡组推导演目 Lv 上限：带 **SSR 吾妻**（卡 30323 / 稀有度 3）= 10，否则 6
///
/// CN memo §2.4：「不带 / R 吾妻 **6**；SSR 吾妻 **10**（实测 `max_act_level = 10`，0 凸即可）」。
/// 面板本身带这个值，真实局面以面板为准；这里只给**纯模拟**（无面板）用。
fn act_level_cap_for(deck: &[SupportCard]) -> i32 {
    let ssr_agatsuma = deck
        .iter()
        .any(|c| c.data.chara_id == AGATSUMA_CHARA_ID && c.data.rarity >= 3);
    if ssr_agatsuma { 10 } else { 6 }
}

/// 友人「吾妻」的角色 id（`cardDB.json` 的 `charaId`；R 卡 10149 与 SSR 卡 30323 同值）
const AGATSUMA_CHARA_ID: u32 = 9052;

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
            advice_base: [0; 5],
            advice_fusion: [0; 5],
            train_stat_anchor: [[0; 5]; 5],
            gain_pct: 0.0,
            check_friend_pct: 0.0,
            check_hint_pct: 0,
            outing_count: 0,
            start_turn: 0,
            initial_shining: [0; 5],
            initial_youqing: [0.0; 5],
            race_bonus: 0.0,
            practice_bad: false,
            assist_support: 0,
            act_level_cap: 6,
            persons: Vec::new(),
            stage: Stage::Begin,
            strategy: None,
            data,
        };
        game.init_persons()?;
        game.act_level_cap = act_level_cap_for(&game.base.deck);
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
        let cap = self.act_level_cap;
        self.act_level[idx] = (self.act_level[idx] + 1).min(cap);
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
    ///
    /// **已停用（2026-11-01 步骤 3）**：`cap_decay` 是**没有报文依据的拟合参数**，
    /// 与有报文依据的 1200 软上限（`soft_cap_gain`）是同一现象的两种建模，两者并用会压低五维过头。
    /// 配置里 `cap_decay` 已置 0 ⇒ 本函数恒返回 1.0。字段与路径留着（便于 A/B），
    /// 其它机制落地后再决定是否删。
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

    /// 该回合的自选比赛窗口（`None` = 这回合不能报名）
    fn free_race_window(&self, turn: i32) -> Option<&FreeRaceDef> {
        self.data.free_races.iter().find(|fr| fr.turn == turn)
    }

    /// 自选比赛（目標外）的赛后奖励基准档：按等级取 `race_reward.non_target`
    ///
    /// 等级语义与引擎 `race_grades` 一致（1 = G1 … 4 = OP）。
    /// 档位是 `null`（GII 无样本）时返回 `None` —— **未知不猜**，不回落目標賽档。
    fn free_race_reward(&self, grade: i32) -> Option<RaceRewardDef> {
        self.data
            .race_reward
            .non_target
            .get((grade - 1).max(0) as usize)
            .copied()
            .flatten()
    }

    /// 自选比赛（目標外比赛）结算
    ///
    /// 2026-11-01 报文 + master.mdb 实证：窗口在面板回合 45 / 69（引擎 44 / 68），
    /// 每窗口 3 场、全部 G1、需 12,000 粉；奖励 = **1 随机项** +10 / PT +45，
    /// 与目標賽一样吃二段式乘区（支援卡「赛后」× 発表会 cp204），各自 floor。
    ///
    /// **未建模**：体力代价（真机 −15 / −20 两例）与报名门槛（12,000 粉）——
    /// 引擎没有粉丝系统，连目標賽的体力代价也没建模，不在这里造数值。
    pub(crate) fn do_free_race(&mut self, rng: &mut impl Rng) -> Result<()> {
        let turn = self.base.turn;
        let Some(window) = self.free_race_window(turn).copied() else {
            return Err(anyhow!("回合 {turn} 没有自选比赛窗口"));
        };
        let Some(reward) = self.free_race_reward(window.grade) else {
            return Err(anyhow!(
                "自选比赛等级 {} 的奖励未配置（未知不猜）",
                window.grade
            ));
        };
        let m_card = (100 + self.base.uma.race_bonus) as f64 / 100.0;
        let m_cp = 1.0 + self.race_bonus;
        let stat_gain = race_reward_two_stage(reward.stat, m_card, m_cp);
        let pt = race_reward_two_stage(reward.pt, m_card, m_cp);
        // 「1 ステ」= 随机一项：两个真机样本分别落在耐力与智力 ⇒ 随机，不固定
        let idx = rng.random_range(0..5usize);
        let pre = self.uma().five_status[idx];
        let gain = self.soft_cap_gain(pre, stat_gain);
        let mut status_pt = [0i32; 6];
        status_pt[idx] = gain;
        status_pt[5] = pt;
        self.uma_mut().add_value(&ActionValue {
            status_pt,
            ..Default::default()
        });
        // 演目槽：非生涯比赛 +1（memo §8.2；落点 Lv 低 → 槽低 → 属性序）
        self.push_act_gauge(1);
        Ok(())
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

    /// 普通训练**成功结算**的那条腿（表值 → 教诲分乘区 → 彩圈/友情 → 软上限）。
    ///
    /// `do_train` 用它；稽古的 home 腿也用它——报文两腿恒等式实测
    /// `实到 = 同训练位普通训练 + 稽古板自报演目加项`（229 手执行稽古里 227 手五轴全中，
    /// 见 `docs/festa平摊加项_20261103.md`），两处必须是同一条公式，不各写一遍。
    /// 不含失败分支、不含报文锚点（锚点在 `do_train` 里替换 `raw` 那一段）。
    fn training_leg(&self, train: usize) -> TrainingLeg {
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
        let has_shining = self
            .base
            .distribution
            .get(train)
            .map(|d| {
                d.iter()
                    .any(|&pidx| pidx >= 0 && self.is_shining_at(pidx as usize, train))
            })
            .unwrap_or(false);
        // 分乘区（memo §3.3）：主属性吃「獲得量 + 訓練効果」，副属性吃「副属性獲得量 + 訓練効果」，
        // 技能Pt 只吃「訓練効果 + 友情」（獲得量区不含技能Pt —— 兼修の教え明写「スキルPtは対象外」）。
        let mods = self.advice_mods_train(train, has_shining);
        // 「助力の支え」：3 次内的训练效果 +5%（memo §4.2），本次训练消耗 1 次
        let assist_bonus = if self.assist_support > 0 { 5.0 } else { 0.0 };
        let effect_mult = 1.0 + (mods.effect_pct + assist_bonus) / 100.0;
        let main_mult = (1.0 + mods.main_pct / 100.0) * effect_mult;
        let sub_mult = (1.0 + mods.sub_pct / 100.0) * effect_mult;
        let pt_mult = effect_mult;
        let raw = [
            (value.status_pt[0] as f64 * stat_mult(train, 0, main_mult, sub_mult)) as i32,
            (value.status_pt[1] as f64 * stat_mult(train, 1, main_mult, sub_mult)) as i32,
            (value.status_pt[2] as f64 * stat_mult(train, 2, main_mult, sub_mult)) as i32,
            (value.status_pt[3] as f64 * stat_mult(train, 3, main_mult, sub_mult)) as i32,
            (value.status_pt[4] as f64 * stat_mult(train, 4, main_mult, sub_mult)) as i32,
        ];
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
        let friend_mult = shining_mult * (1.0 + mods.friend_pct / 100.0);
        let with_friend = [
            (raw[0] as f64 * friend_mult) as i32,
            (raw[1] as f64 * friend_mult) as i32,
            (raw[2] as f64 * friend_mult) as i32,
            (raw[3] as f64 * friend_mult) as i32,
            (raw[4] as f64 * friend_mult) as i32,
        ];
        // 接近上限衰减：拟合曲线 `cap_decay` 已置 0 停用（2026-11-01 步骤 3），
        // 接近上限的打折改由下面那条有报文依据的 1200 软上限承担
        let limits = self.uma().five_status_limit;
        let cur = self.uma().five_status;
        let decayed = [
            (with_friend[0] as f64 * self.cap_decay_factor(cur[0], limits[0])) as i32,
            (with_friend[1] as f64 * self.cap_decay_factor(cur[1], limits[1])) as i32,
            (with_friend[2] as f64 * self.cap_decay_factor(cur[2], limits[2])) as i32,
            (with_friend[3] as f64 * self.cap_decay_factor(cur[3], limits[3])) as i32,
            (with_friend[4] as f64 * self.cap_decay_factor(cur[4], limits[4])) as i32,
        ];
        // 1200 软上限：训练增量同样按「超出 1200 的部分减半」
        // （2026-11-01 步骤 3 报文实证：同一训练同一格，名义 10 时 pre=1195 得 7、pre=1300 得 5）
        let five = [
            self.soft_cap_gain(cur[0], decayed[0]),
            self.soft_cap_gain(cur[1], decayed[1]),
            self.soft_cap_gain(cur[2], decayed[2]),
            self.soft_cap_gain(cur[3], decayed[3]),
            self.soft_cap_gain(cur[4], decayed[4]),
        ];
        TrainingLeg {
            five,
            friend_mult,
            pt: (value.status_pt[5] as f64 * pt_mult * friend_mult) as i32,
            vital: value.vital,
        }
    }

    pub(crate) fn do_train(&mut self, train: usize, mut _rng: &mut impl Rng) -> Result<()> {
        // 合宿：所有训练都变成演目稽古（官方：合宿训练与演目稽古同一 ID）
        if self.is_camp_turn() {
            // 合宿：训练=稽古，但不升 Lv（官方：合宿结束统一 +1）
            return self.do_keiko_impl(train, _rng, false);
        }
        // 成功结算的公式在 `training_leg`（稽古的 home 腿共用同一条，不各写一遍）
        let leg = self.training_leg(train);
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
            let drop = self.advice_fail_drop(train, vital, None);
            (fail_rate as f64 * (100.0 - drop) / 100.0).max(0.0) as f32
        };
        let roll = rand::Rng::random::<f32>(&mut _rng) * 100.0;
        let failed = roll < fail_rate;
        // 「追加の稽古」（研鑽の教え type 8）：该演目的担当参与了本次训练才可能发生，
        // 且**只在训练成功时结算**（memo §4.4）；发生率不按人数加算。
        let acts = self.participating_acts(train, None);
        let extra_keiko = {
            let rate = self.extra_keiko_rate(&acts);
            rate > 0 && rand::Rng::random::<f32>(&mut _rng) * 100.0 < rate as f32
        };
        // 报文 `stat_gains` 是游戏对「这一下训练」的全加成真值（含训练等级/得意/人头/
        // 教え/発表会），直接用可绕过引擎训练值的缺口；未注入时才用 `training_leg`。
        // 锚点分支只替换「表值 × 教诲乘区」那一段，彩圈/友情与接近上限衰减仍接在后面；
        // 1200 软上限跳过——锚点是真机**实得**值，已含这条打折，再折算会重复扣减。
        let stat_anchor = self.train_stat_anchor[train];
        let anchored = stat_anchor.iter().any(|&x| x > 0);
        let stat = if anchored {
            let k = self.data.stat_anchor_scale;
            let raw = [
                (stat_anchor[0] as f64 * k) as i32,
                (stat_anchor[1] as f64 * k) as i32,
                (stat_anchor[2] as f64 * k) as i32,
                (stat_anchor[3] as f64 * k) as i32,
                (stat_anchor[4] as f64 * k) as i32,
            ];
            let with_friend = [
                (raw[0] as f64 * leg.friend_mult) as i32,
                (raw[1] as f64 * leg.friend_mult) as i32,
                (raw[2] as f64 * leg.friend_mult) as i32,
                (raw[3] as f64 * leg.friend_mult) as i32,
                (raw[4] as f64 * leg.friend_mult) as i32,
            ];
            let limits = self.uma().five_status_limit;
            let cur = self.uma().five_status;
            [
                (with_friend[0] as f64 * self.cap_decay_factor(cur[0], limits[0])) as i32,
                (with_friend[1] as f64 * self.cap_decay_factor(cur[1], limits[1])) as i32,
                (with_friend[2] as f64 * self.cap_decay_factor(cur[2], limits[2])) as i32,
                (with_friend[3] as f64 * self.cap_decay_factor(cur[3], limits[3])) as i32,
                (with_friend[4] as f64 * self.cap_decay_factor(cur[4], limits[4])) as i32,
            ]
        } else {
            leg.five
        };
        let scaled = ActionValue {
            status_pt: [
                stat[0], stat[1], stat[2], stat[3], stat[4],
                // 技能Pt：吃「訓練効果」与「友情」两区，**不吃獲得量区**
                leg.pt,
            ],
            vital: leg.vital,
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
            if extra_keiko {
                let upper = rand::Rng::random::<f32>(&mut _rng) < 0.5;
                self.apply_extra_keiko(train, upper);
            }
        }
        if self.assist_support > 0 {
            self.assist_support -= 1;
        }
        self.base.train_level_count[train] += 1;
        // 羁绊成长：训练位上每个人头羁绊 +friend_gain；縛の教え（effect 16）另给全编成卡 +3（不叠加）
        let bond_bonus = self.advice_value(16, train) as i32;
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
            let cap = self.act_level_cap;
            self.act_level[train.clamp(0, 4)] = (self.act_level[train.clamp(0, 4)] + 1).min(cap);
            let max_vital = self.uma().max_vital;
            let uma = self.uma_mut();
            uma.vital = (uma.vital + 30).min(max_vital.max(uma.vital));
            uma.add_motivation(1);
        }
        Ok(())
    }

    /// 発表会结算：永久 buff + 达标实数奖励
    ///
    /// **永久 buff 是累计总量，不是增量**（master.mdb `single_mode_15_check_point_effect`：
    /// 同一 schedule 的「成功」档 = 上一场「大成功」档的值，如 schedule 2 成功 = 5/40/50
    /// 与 schedule 1 大成功同值）⇒ 设值不累加。
    /// effect_type：4 得意率 / 202 友情ボーナス / 2 ヒント発生率 / 204 比赛加成。
    fn apply_check_point(&mut self, cp: &CheckPointDef, great: bool) {
        let buff = if great {
            cp.great.clone()
        } else {
            cp.success.clone()
        };
        self.gain_pct = buff.gain_pct as f64 / 100.0;
        self.check_friend_pct = buff.friend_pct as f64 / 100.0;
        self.check_hint_pct = buff.hint_pct;
        // 午式駿大祭（第 3 场，cp.schedule == 3）：成功 +30% / 大成功 +100% レースボーナス
        if cp.schedule == 3 {
            self.race_bonus = if great { 1.00 } else { 0.30 };
        }
        // 达标实数奖励（memo L215-250；官方 Wiki 文案，见 docs/plan/intake.md）
        //   大成功：t24 = 5ステ+5 / SP+200 / トレLv+1；t48 = 5ステ+10 / SP+300 / トレLv+1；
        //           t68 = 5ステ+15 / SP+400 / 体力+30 / やる気+1
        //   成功  ：t24 = 5ステ+3 / SP+100；t48 = 5ステ+5 / SP+150；t68 = 5ステ+10 / SP+200
        // （成功档没有 トレLv+1，也没有体力 / 干劲）
        let (all_stat, pt, train_lv, vital, motivation, chairman) = if great {
            (
                cp.all_stat,
                cp.pt,
                cp.train_lv,
                cp.vital,
                cp.motivation,
                cp.chairman_bond,
            )
        } else {
            (cp.success_all_stat, cp.success_pt, 0, 0, 0, 0)
        };
        if all_stat > 0 {
            let limits = self.uma().five_status_limit;
            let pre = self.uma().five_status;
            let soft_cap = self.data.soft_cap;
            let uma = self.uma_mut();
            for (i, (st, lim)) in uma.five_status.iter_mut().zip(limits.iter()).enumerate() {
                // 全能力奖励吃 1200 软上限：清单里那条「#2 速+5 耐+10 … 非均匀」就是
                // 均匀的 5ステ+10 被软上限砍掉过线的那一项（不是「非均匀设计」）。
                let gain = soft_cap_gain(soft_cap, pre[i], all_stat);
                *st = (*st + gain).min(*lim);
            }
        }
        if pt > 0 {
            self.uma_mut().skill_pt += pt;
        }
        if train_lv > 0 {
            // 训练等级 = 1 + count/4，所以每 +1 级 = count +4
            for t in 0..5 {
                self.base.train_level_count[t] += train_lv * 4;
            }
        }
        if vital > 0 {
            let uma = self.uma_mut();
            uma.vital = (uma.vital + vital).min(uma.max_vital);
        }
        if motivation > 0 {
            self.uma_mut().add_motivation(motivation);
        }
        if chairman > 0 {
            // 理事长羁绊（大成功档；memo L219 / L232）。理事长是人头里的 `PersonType::Yayoi`。
            let idx = self
                .persons
                .iter()
                .position(|p| p.person_type == PersonType::Yayoi);
            if let Some(idx) = idx {
                let Self { base, persons, .. } = self;
                base.add_friendship(persons, idx, chairman);
            }
        }
    }

    /// 某演目槽上的教诲效果行（已按「融合 ⇒ 固定档」解析）
    ///
    /// 融合后基础教诲的等级效果整条换成 `fusion_effects` 的**固定档**（与演目 Lv 无关，
    /// memo §4.5），所绑定的新教诲效果再叠加其上；未融合的取该教诲在
    /// **绑定演目当前 Lv** 的行（memo §4.3 规则 1：按绑定的演目取 Lv）。
    fn slot_advice_rows(&self, slot: usize) -> Vec<AdviceRow> {
        let base = self.advice_base.get(slot).copied().unwrap_or(0);
        if base <= 0 {
            return Vec::new();
        }
        let level = self.act_level.get(slot).copied().unwrap_or(1).clamp(1, 10);
        let fusion = self.advice_fusion.get(slot).copied().unwrap_or(0);
        let mut rows = Vec::new();
        if fusion > 0 {
            for e in self
                .data
                .fusion_effects
                .iter()
                .filter(|e| e.advice_id == base)
            {
                rows.push(AdviceRow {
                    advice_id: base,
                    effect_type: e.effect_type,
                    value: e.value,
                    condition_type: e.condition_type,
                    condition_value: e.condition_value,
                    fused: true,
                });
            }
            for e in self
                .data
                .advice_effects
                .iter()
                .filter(|e| e.advice_id == fusion && e.level == level)
            {
                rows.push(AdviceRow {
                    advice_id: fusion,
                    effect_type: e.effect_type,
                    value: e.value,
                    condition_type: e.condition_type,
                    condition_value: e.condition_value,
                    fused: false,
                });
            }
        } else {
            for e in self
                .data
                .advice_effects
                .iter()
                .filter(|e| e.advice_id == base && e.level == level)
            {
                rows.push(AdviceRow {
                    advice_id: base,
                    effect_type: e.effect_type,
                    value: e.value,
                    condition_type: e.condition_type,
                    condition_value: e.condition_value,
                    fused: false,
                });
            }
        }
        rows
    }

    /// 普通训练侧的教诲效果（训练域）
    ///
    /// 生效口径（memo §4.3）：教诲只在该演目的**担当参与了该训练位**时生效，
    /// 且**按参与人数叠加**。排练域的效果（絆的主属性、体力消费等）在普通训练里不吃。
    fn advice_mods_train(&self, slot: usize, has_shining: bool) -> AdviceMods {
        let mut mods = AdviceMods::default();
        for act in 0..5 {
            let members = self.slot_act_members(slot, act);
            if members <= 0 {
                continue;
            }
            for row in self.slot_advice_rows(act) {
                let (train_scope, _) = advice_scope(&row);
                if !train_scope {
                    continue;
                }
                let value = row.value as f64 * members as f64;
                match row.effect_type {
                    // 13 主属性获得量 %（特化）
                    13 => mods.main_pct += value,
                    // 21 副属性获得量 %（兼修；**技能Pt 対象外**）
                    21 => mods.sub_pct += value,
                    // 203 训练效果 %：条件 3 = 「友情非発生時」（助力），无条件的直接生效
                    203 => {
                        if row.condition_type != 3 || !has_shining {
                            mods.effect_pct += value;
                        }
                    }
                    // 202 友情ボーナス %（協力）：只在友情训练（彩圈）时进友情区
                    202 if has_shining => mods.friend_pct += value,
                    _ => {}
                }
            }
        }
        // 発表会の永久 buff：得意率（近似：当训练效果%用）与友情ボーナス（仅友情训练）
        mods.effect_pct += self.gain_pct;
        if has_shining {
            mods.friend_pct += self.check_friend_pct;
        }
        mods
    }

    /// 演目稽古侧的教诲效果（排练域）
    ///
    /// 排练 = 该演目自己的稽古，绑定的教诲必然生效，人数按该演目的参加人数叠加。
    fn advice_mods_keiko(&self, act: usize) -> AdviceMods {
        let mut mods = AdviceMods::default();
        let members = self.act_roster(act).max(1);
        for row in self.slot_advice_rows(act) {
            let (_, keiko_scope) = advice_scope(&row);
            if !keiko_scope {
                continue;
            }
            let value = row.value as f64 * members as f64;
            match row.effect_type {
                // 13 主属性获得量 %（絆 / 特化）
                13 => mods.main_pct += value,
                // 21 副属性获得量 %（兼修；技能Pt 対象外）
                21 => mods.sub_pct += value,
                // 203 排练效果 %（不屈 Lv8+ / 兼修）
                203 => mods.effect_pct += value,
                // 202 排练友情ボーナス %（転成 201）
                202 => mods.friend_pct += value,
                // 3 体力消费下降 %（体力 / 転成 501）
                3 => mods.vital_pct += value,
                _ => {}
            }
        }
        mods
    }

    /// 本次行动里「参与了」的演目
    ///
    /// 普通训练 = 该训练位上有担当的演目；排练 = 该演目自己（排练的参加者就是它的担当）。
    fn participating_acts(&self, slot: usize, keiko_act: Option<usize>) -> Vec<usize> {
        match keiko_act {
            Some(act) => vec![act],
            None => (0..5)
                .filter(|&a| self.slot_act_members(slot, a) > 0)
                .collect(),
        }
    }

    /// 这些演目上「追加の稽古」的发生率 %（master.mdb type 8 = 研鑽の教え）
    ///
    /// 发生率取教诲**绑定的演目的 Lv** 对应的值，**不按人数加算**
    /// （一手 memo：`確率は人数分加算しない`）；多个演目同时挂着研鑽时取最大。
    fn extra_keiko_rate(&self, acts: &[usize]) -> i32 {
        let mut rate = 0;
        for &act in acts {
            for row in self.slot_advice_rows(act) {
                if row.effect_type == 8 {
                    rate = rate.max(row.value);
                }
            }
        }
        rate
    }

    /// 这些演目上能否拿到「助力の支え」（master.mdb type 20 = 助力の教え）
    fn can_obtain_assist_support(&self, acts: &[usize]) -> bool {
        acts.iter().any(|&act| {
            self.slot_advice_rows(act)
                .iter()
                .any(|row| row.effect_type == 20)
        })
    }

    /// 「追加の稽古」结算（memo §4.4 的上 / 下两档）
    ///
    /// 上：理事长羁绊 +5、体力 -5、主属性 +20、技能Pt +20、**演目Lv 最低的演目**槽 +1；
    /// 下：体力 +5、主属性 +5。上/下 权重 memo 未给（U40），先按 50/50。
    /// 「随机解除一个 debuff」引擎没有异常状态系统 ⇒ 不实现。
    fn apply_extra_keiko(&mut self, main_stat: usize, upper: bool) {
        if upper {
            if let Some(idx) = self
                .persons
                .iter()
                .position(|p| p.person_type == PersonType::Yayoi)
            {
                let Self { base, persons, .. } = self;
                base.add_friendship(persons, idx, 5);
            }
            let act = (0..5).min_by_key(|&i| self.act_level[i]).unwrap_or(0);
            self.act_gauge[act] = (self.act_gauge[act] + 1).min(ACT_GAUGE_MAX);
            let uma = self.uma_mut();
            uma.vital = (uma.vital - 5).max(0);
            let limit = uma.five_status_limit[main_stat];
            uma.five_status[main_stat] = (uma.five_status[main_stat] + 20).min(limit);
            uma.skill_pt += 20;
        } else {
            let uma = self.uma_mut();
            let max_vital = uma.max_vital;
            uma.vital = (uma.vital + 5).min(max_vital);
            let limit = uma.five_status_limit[main_stat];
            uma.five_status[main_stat] = (uma.five_status[main_stat] + 5).min(limit);
        }
    }

    /// 某演目的担当人数（支援卡 + 编组外神驹 + 团队卡；不含理事长 / 记者 / 友人）
    ///
    /// `train_type = -1` = 没有演目归属（剧本友人），不算任何演目的担当。
    pub fn act_roster(&self, act: usize) -> i32 {
        self.persons
            .iter()
            .filter(|p| {
                p.train_type >= 0
                    && p.train_type as usize == act
                    && matches!(
                        p.person_type,
                        PersonType::Card | PersonType::Npc | PersonType::TeamCard
                    )
            })
            .count() as i32
    }

    /// 某训练位上「属于该演目的担当」的人数
    pub fn slot_act_members(&self, slot: usize, act: usize) -> i32 {
        let Some(dist) = self.base.distribution.get(slot) else {
            return 0;
        };
        dist.iter()
            .filter(|&&p| p >= 0)
            .filter_map(|&p| self.persons.get(p as usize))
            .filter(|p| {
                p.train_type >= 0
                    && p.train_type as usize == act
                    && matches!(
                        p.person_type,
                        PersonType::Card | PersonType::Npc | PersonType::TeamCard
                    )
            })
            .count() as i32
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
    ///
    /// 只统计**训练域**且该演目的担当参与了该训练位的效果。
    fn advice_value(&self, effect_type: i32, slot: usize) -> f64 {
        let mut best = 0.0f64;
        for act in 0..5 {
            if self.slot_act_members(slot, act) <= 0 {
                continue;
            }
            for row in self.slot_advice_rows(act) {
                let (train_scope, _) = advice_scope(&row);
                if train_scope && row.effect_type == effect_type {
                    best = best.max(row.value as f64);
                }
            }
        }
        best
    }

    /// 不屈の教え（effect 205）：体力 ≥ 阈值时失败率 −100%
    ///
    /// 阈值取表里的 `condition_value`（Lv1 45 … Lv9 5，Lv10 = 0 = 无条件）。
    /// 训练侧按「该演目的担当参与了该训练位」判定（memo §4.3）；
    /// 排练侧按该演目自己（`keiko_act`）。
    fn advice_fail_drop(&self, slot: usize, vital: i32, keiko_act: Option<usize>) -> f64 {
        let mut drop = 0.0f64;
        for act in 0..5 {
            let active = match keiko_act {
                Some(a) => a == act,
                None => self.slot_act_members(slot, act) > 0,
            };
            if !active {
                continue;
            }
            for row in self.slot_advice_rows(act) {
                if row.effect_type != 205 {
                    continue;
                }
                let threshold = if row.condition_type == 2 {
                    row.condition_value
                } else {
                    0
                };
                if vital >= threshold {
                    drop = drop.max(row.value as f64);
                }
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
        let cap = self.act_level_cap;
        self.act_level[best] = (self.act_level[best] + 1).min(cap);
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
        // 排练 = 「同一训练位的普通训练下段」（memo §3.3），所以卡组的训练效果/友情/
        // 干劲/人数/副属性/成长率**都要走同一套乘区**，与普通训练
        // `default_calc_training_value` 保持一致。
        // 演目加项 `effect_type 105` 的平摊值**不在这里**——报文预览显示它不进乘区，
        // 见下面 `flat_festa_add`。
        let buffs = self.calc_training_buff(idx).unwrap_or_default();
        let person_count = self.count_training_persons(idx);
        let basic_motivation = ((self.uma().motivation - 3) * 10) as f32;
        let growth = self.uma().five_status_bonus;
        // 排练域的教诲效果（memo §4.2：絆的主属性、不屈/兼修的排练效果、体力消费、
        // 転成 201 的排练友情ボーナス）。分乘区口径同普通训练。
        let mods = self.advice_mods_keiko(idx);
        // 「助力の支え」：3 次内的训练效果 +5%（memo §4.2），本次排练消耗 1 次
        let assist_bonus = if self.assist_support > 0 { 5.0 } else { 0.0 };
        let effect_mult = 1.0 + (mods.effect_pct + assist_bonus) / 100.0;
        let main_mult = (1.0 + mods.main_pct / 100.0) * effect_mult;
        let sub_mult = (1.0 + mods.sub_pct / 100.0) * effect_mult;
        let pt_mult = effect_mult;
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
                * (1.0
                    + 0.01 * (buffs.youqing + effect.friend_pct as f32 + mods.friend_pct as f32))
                * (1.0 + 0.01 * basic_motivation * (1.0 + 0.01 * buffs.ganjing as f32))
                * (1.0 + 0.01 * buffs.xunlian as f32)
                * (1.0 + 0.05 * person_count as f32)
                * (1.0 + 0.01 * *growth.get(i).unwrap_or(&0) as f32))
                .floor() as i32
        };
        let value = ActionValue {
            status_pt: [
                keiko_chain(row[0], 0),
                keiko_chain(row[1], 1),
                keiko_chain(row[2], 2),
                keiko_chain(row[3], 3),
                keiko_chain(row[4], 4),
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
            let drop = self.advice_fail_drop(idx, vital, Some(idx));
            (fail_rate as f64 * (100.0 - drop) / 100.0).max(0.0) as f32
        };
        let roll = rand::Rng::random::<f32>(&mut _rng) * 100.0;
        let failed = roll < fail_rate;
        // 「追加の稽古」：排练也触发，同样只在该演目的担当在场且排练成功时结算
        let acts = self.participating_acts(idx, Some(idx));
        let extra_keiko = {
            let rate = self.extra_keiko_rate(&acts);
            rate > 0 && rand::Rng::random::<f32>(&mut _rng) * 100.0 < rate as f32
        };
        let vital_delta = (value.vital as f64 * mods.vital_pct / 100.0) as i32;
        // 接近上限衰减：拟合曲线 `cap_decay` 已置 0 停用（2026-11-01 步骤 3）；
        // 打折改由下面那条 1200 软上限承担（PT 不打折）
        let limits = self.uma().five_status_limit;
        let cur = self.uma().five_status;
        // 演目加项（`single_mode_15_act_effect` 105）：非主属性的**平摊固定值**，
        // 在乘区之后相加、再进 1200 软上限（报文预览：pre 1192→9、1195→7、≥1199→5
        // 恰等于 `soft_cap_gain(1200, pre, 10)`）。主属性不吃这一项。
        let flat_festa_add = |i: usize| -> i32 { if i == idx { 0 } else { effect.all_base } };
        let festa_axis = |i: usize| -> i32 {
            (value.status_pt[i] as f64
                * stat_mult(idx, i, main_mult, sub_mult)
                * self.keiko_growth(i)
                * self.cap_decay_factor(cur[i], limits[i])) as i32
                + flat_festa_add(i)
        };
        // 稽古是**两条腿**（一手报文逐轴复现，229 执行手里 227 手五轴全中，
        // 合宿期 80/81、非合宿 147/148 ⇒ 无条件成立）：
        //   实到[axis] = home 腿[axis]（同训练位普通训练）+ festa 腿[axis]（稽古板自报值）
        // 两腿在报文里**各自已套过 1200 软上限**（pre=1902 那腿显示 5、pre=1195 那腿显示 7），
        // 所以折也是分腿折，不能对两腿之和套。见 `docs/festa平摊加项_20261103.md`。
        let festa = [
            self.soft_cap_gain(cur[0], festa_axis(0)),
            self.soft_cap_gain(cur[1], festa_axis(1)),
            self.soft_cap_gain(cur[2], festa_axis(2)),
            self.soft_cap_gain(cur[3], festa_axis(3)),
            self.soft_cap_gain(cur[4], festa_axis(4)),
        ];
        let home = self.training_leg(idx);
        let gains = [
            home.five[0] + festa[0],
            home.five[1] + festa[1],
            home.five[2] + festa[2],
            home.five[3] + festa[3],
            home.five[4] + festa[4],
        ];
        let scaled = ActionValue {
            status_pt: [
                gains[0],
                gains[1],
                gains[2],
                gains[3],
                gains[4],
                // 技能Pt 同样是两条腿：227/229 手 `d_sp == home[30] + p[30]`
                home.pt + (value.status_pt[5] as f64 * pt_mult) as i32,
            ],
            // 体力**不叠第二条腿**：报文里 159/229 手的稽古板 `p[10]` 为 0，实到体力 =
            // `home[10]` ⇒ 稽古表 `row[6]` 那列本身就是普通训练的体力消费，再加一次会翻倍。
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
            if extra_keiko {
                let upper = rand::Rng::random::<f32>(&mut _rng) < 0.5;
                self.apply_extra_keiko(idx, upper);
            }
        }
        if self.assist_support > 0 {
            self.assist_support -= 1;
        }
        if !failed {
            // 「助力の支え」：排练成功时获得（剩余次数重置为 3）
            if self.can_obtain_assist_support(&acts) {
                self.assist_support = 3;
            }
            // 合宿（level_up=false）：训练=稽古但不升 Lv（官方：合宿结束全演目 +1）
            if level_up {
                // 真机口径（2026-10-31，报文 pre/post 对照 5 段 session）：
                // 稽古成功**只在「槽满→升级」那一下清槽**；槽未满的成功稽古不动槽；
                // 合宿期成功稽古也不清槽（合宿结束统一全演目 +1，槽数原样继承）。
                self.act_gauge[idx] = 0;
                let cap = self.act_level_cap;
                self.act_level[idx] = (self.act_level[idx] + 1).min(cap);
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
                let cap = self.act_level_cap;
                let delta = (limit_at((level as i32 + 1).min(cap)) - limit_at(level as i32)).max(0);
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
                // 伝受：turn 2 把 5 个基础教诲分给 5 个演目（memo §4.1：初始必须全部分配）
                if let Some(idx) = self
                    .data
                    .advice_grant_turns
                    .iter()
                    .position(|t| *t == self.turn)
                    && let Some(list) = self.data.advice_grant_order.get(idx)
                {
                    for (slot, aid) in list.iter().enumerate() {
                        if slot < 5 {
                            self.advice_base[slot] = *aid;
                            self.advice_fusion[slot] = 0;
                        }
                    }
                }
                // 転成（融合）：turn 23 / 47 把基础教诲与新教诲配对融合（memo §4.1/§4.5）
                // —— 融合后基础教诲的等级效果换成 `fusion_effects` 的固定档，
                // 所绑定的新教诲效果叠加其上（`slot_advice_rows` 负责解析）。
                let turn = self.turn;
                let grants: Vec<i32> = self
                    .data
                    .fusion_grants
                    .iter()
                    .filter(|g| g.turn == turn)
                    .map(|g| g.fusion_id)
                    .collect();
                if !grants.is_empty() {
                    // 可融合的槽位 = 挂着**基础教诲**且还没被融合过的槽（按槽位顺序）
                    let slots: Vec<usize> = (0..5)
                        .filter(|&s| {
                            is_base_advice(self.advice_base[s]) && self.advice_fusion[s] == 0
                        })
                        .collect();
                    for (i, fusion_id) in grants.iter().enumerate() {
                        if let Some(&slot) = slots.get(i) {
                            self.advice_fusion[slot] = *fusion_id;
                        }
                    }
                }
                // 合宿开始（与「教えの付け替え」是同一个事件：memo L319「付け替えは合宿直前」；
                // JP memo 的「7月前半行動前」就是本事件）。
                // 体力：**1 年目 +10**（真机日志「1年目、駿大祭合宿開始！」）
                //      **2 年目 +15**（真机日志「2年目、駿大祭合宿開始！」，docs/机制对照表 §6）
                //      **3 年目 +15**（⚠ 外推：第 3 年没有样本，取与 2 年目同值，待采样后修正）
                // 干劲 +1（两年样本一致）
                let camp_vital = camp_start_vitality(self.turn);
                if camp_vital > 0 {
                    let uma = self.uma_mut();
                    uma.vital = (uma.vital + camp_vital).min(uma.max_vital);
                    uma.motivation = (uma.motivation + 1).min(5);
                }
                // 合宿结束：全演目 Lv +1（官方：合宿中演目 Lv 不升，结束统一 +1）
                if matches!(self.turn, 16 | 40 | 64) {
                    let cap = self.act_level_cap;
                    for i in 0..5 {
                        self.act_level[i] = (self.act_level[i] + 1).min(cap);
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
                    self.apply_check_point(&cp, great);
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

/// [`MatsuriGame::training_leg`] 的返回值：普通训练成功结算的一条腿
struct TrainingLeg {
    /// 五轴增量，**已各自套过 1200 软上限**
    five: [i32; 5],
    /// 彩圈 + 教诲友情乘区，供 `do_train` 的报文锚点分支接着用
    friend_mult: f64,
    /// 技能 Pt（不吃 1200 软上限）
    pt: i32,
    /// 体力消费
    vital: i32,
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::SeedableRng;

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
    fn training_applies_soft_cap_above_1200() {
        // 步骤 3：训练通道同样走 1200 软上限（报文：cid168/5101 耐力位 pre=1195 → 7、
        // pre=1300 → 5，名义 10）。同一局同一训练，只改赛前属性值，验证增量按超额减半。
        let _ = Data::init(crate::testing::data_dir());
        let data = Arc::new(MatsuriData::load(crate::testing::data_dir()).unwrap());
        let run = |pre: i32, seed: u64| -> i32 {
            let mut game =
                MatsuriGame::newgame(106301, &[], InheritInfo::default(), &[], data.clone())
                    .unwrap();
            game.uma_mut().five_status = [pre, 500, 500, 500, 500];
            let before = game.uma().five_status[0];
            let mut rng = StdRng::seed_from_u64(seed);
            game.do_train(0, &mut rng).unwrap();
            game.uma().five_status[0] - before
        };
        // 500 起步：增量完全落在 1200 以下，不打折
        let low = run(500, 20261101);
        assert!(low > 0, "训练应有增量");
        // 1300 起步：超额部分减半
        let high = run(1300, 20261101);
        assert_eq!(high, soft_cap_gain(1200, 1300, low));
        assert!(high < low, "越过 1200 的增量应被打折");
    }

    #[test]
    fn check_point_buffs_are_cumulative_totals() {
        // master.mdb `single_mode_15_check_point_effect`：每档给的是**该场之后的累计总量**
        // （「成功」档 = 上一场「大成功」档的值）⇒ 结算必须**覆盖**而不是累加。
        let _ = Data::init(crate::testing::data_dir());
        let data = Arc::new(MatsuriData::load(crate::testing::data_dir()).unwrap());
        let mut game =
            MatsuriGame::newgame(106301, &[], InheritInfo::default(), &[], data).unwrap();
        let cp = game.data.check_points[0].clone();
        game.apply_check_point(&cp, true);
        assert_eq!(game.gain_pct, 0.40, "第 1 回大成功：得意率累计 40%");
        assert_eq!(game.check_friend_pct, 0.05, "友情ボーナス累计 5%");
        assert_eq!(game.check_hint_pct, 50, "ヒント発生率累计 50%");
        // 再按「成功」结算一次：应是覆盖（0.20 / 0%），不是累加（0.60 / 10%）
        game.apply_check_point(&cp, false);
        assert_eq!(game.gain_pct, 0.20);
        assert_eq!(game.check_friend_pct, 0.0);
        assert_eq!(game.check_hint_pct, 20);
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

    /// 造一局：把每个演目的担当按 `[(演目, 人数)]` 放到训练位 0
    fn game_with_roster(roster: &[(i32, usize)]) -> MatsuriGame {
        game_with_data(
            Arc::new(MatsuriData::load(crate::testing::data_dir()).unwrap()),
            roster,
        )
    }

    fn game_with_data(data: Arc<MatsuriData>, roster: &[(i32, usize)]) -> MatsuriGame {
        let _ = Data::init(crate::testing::data_dir());
        let mut game =
            MatsuriGame::newgame(106301, &[], InheritInfo::default(), &[], data).unwrap();
        let mut dist = Vec::new();
        for (act, count) in roster {
            for _ in 0..*count {
                let idx = game.persons.len() as i32;
                game.add_person(BasePerson {
                    person_index: idx,
                    person_type: PersonType::Npc,
                    train_type: *act,
                    chara_id: 9001,
                    friendship: 0,
                    is_hint: false,
                    card_id: None,
                });
                dist.push(idx);
            }
        }
        game.base.distribution = vec![dist, vec![], vec![], vec![], vec![]];
        game
    }

    #[test]
    fn advice_scope_follows_memo_table() {
        // 作用域表（memo §4.2 / §4.5）：训练域 / 排练域
        let row = |id: i32, effect_type: i32| AdviceRow {
            advice_id: id,
            effect_type,
            value: 0,
            condition_type: 0,
            condition_value: 0,
            fused: false,
        };
        assert_eq!(
            advice_scope(&row(101, 13)),
            (false, true),
            "絆的主属性只在排练"
        );
        assert_eq!(
            advice_scope(&row(101, 16)),
            (true, false),
            "絆的羁绊只在训练"
        );
        assert_eq!(
            advice_scope(&row(401, 203)),
            (true, false),
            "助力的训练效果在训练域"
        );
        assert_eq!(
            advice_scope(&row(901, 203)),
            (false, true),
            "不屈的排练效果在排练域"
        );
        assert_eq!(advice_scope(&row(601, 13)), (true, true), "特化两域都吃");
        assert_eq!(advice_scope(&row(1001, 21)), (true, true), "兼修两域都吃");
        assert_eq!(
            advice_scope(&row(501, 3)),
            (false, true),
            "体力消费只在排练"
        );
        // 转成固定档按被融合的基础教诲查域
        let fused = AdviceRow {
            advice_id: 101,
            effect_type: 13,
            value: 15,
            condition_type: 0,
            condition_value: 0,
            fused: true,
        };
        assert_eq!(advice_scope(&fused), (false, true));
    }

    #[test]
    fn fusion_replaces_base_effect_with_fixed_tier() {
        // 转成（転成）：基础教诲的等级效果整条换成 `fusion_effects` 的固定档，
        // 所绑定的新教诲效果叠加其上（memo §4.5）。
        let mut game = game_with_roster(&[]);
        game.advice_base[0] = 101;
        game.act_level[0] = 3;
        let rows = game.slot_advice_rows(0);
        // 未融合：101 在 Lv3 = 13→7、16→3
        assert!(
            rows.iter()
                .any(|r| r.effect_type == 13 && r.value == 7 && !r.fused)
        );
        // 融合 601（特化）：101 的 13/16 换成固定档（13→15），601 的 13/206 按 Lv3 取
        game.advice_fusion[0] = 601;
        let rows = game.slot_advice_rows(0);
        assert!(
            rows.iter()
                .any(|r| r.effect_type == 13 && r.value == 15 && r.fused)
        );
        assert!(
            rows.iter()
                .any(|r| r.effect_type == 13 && r.value == 7 && !r.fused),
            "601 的 13 应按绑定演目的 Lv3 取 7"
        );
        assert!(rows.iter().any(|r| r.effect_type == 206 && r.value == 7));
        assert!(
            !rows.iter().any(|r| r.effect_type == 16),
            "融合后 101 的羁绊 +3 不再单独生效"
        );
    }

    #[test]
    fn advice_only_applies_when_act_member_present() {
        // memo §4.3：教诲只在该演目的**担当参与了该训练位**时生效。
        let mut game = game_with_roster(&[(1, 1)]); // 只有演目 2（耐力）的担当在训练位 0
        game.advice_base[0] = 601; // 特化：主属性获得量 %（训练 + 排练）
        game.act_level[0] = 5;
        let mods = game.advice_mods_train(0, false);
        assert_eq!(mods.main_pct, 0.0, "演目 1 的担当不在场上 ⇒ 特化不生效");
        // 换成演目 1 的担当
        let mut game = game_with_roster(&[(0, 1)]);
        game.advice_base[0] = 601;
        game.act_level[0] = 5;
        let mods = game.advice_mods_train(0, false);
        assert_eq!(mods.main_pct, 9.0, "担当在场上 ⇒ 特化 Lv5 的 +9% 生效");
    }

    #[test]
    fn advice_stacks_by_participant_count() {
        // memo §4.3：按担当人数分加算。
        let mut game = game_with_roster(&[(0, 2)]); // 两个担当
        game.advice_base[0] = 601;
        game.act_level[0] = 5;
        let mods = game.advice_mods_train(0, false);
        assert_eq!(mods.main_pct, 18.0, "2 名担当 ⇒ +9% × 2");
    }

    #[test]
    fn gain_zone_does_not_multiply_skill_pt() {
        // memo §3.3：技能Pt = floor(下段 × (1+友情合計) × (1+訓練効果合計 + SP訓練効果))
        // —— 獲得量区（主/副属性获得量）**不含技能Pt**（兼修の教え明写「スキルPtは対象外」）。
        let mut game = game_with_roster(&[(0, 1)]);
        game.advice_base[0] = 1001; // 兼修：副属性获得量 %（21）
        game.act_level[0] = 10;
        let mods = game.advice_mods_train(0, false);
        assert_eq!(mods.sub_pct, 25.0, "兼修 Lv10 的副属性 +25%");
        assert_eq!(
            mods.effect_pct, 0.0,
            "兼修的 21 不进訓練効果区 ⇒ 不乘技能Pt"
        );
    }

    #[test]
    fn keiko_festa_flat_add_bypasses_multipliers_and_skips_primary() {
        // 一手证据：festa 板报文的逐轴预览值（1,740 手，`single_mode_15_act_effect`
        // `effect_type 105 v4 = 10`）。基础值为 0 的非主属性轴预览**恒等于 10**，
        // 不随演目 Lv(1-10)、step、人头、干劲、卡组友情变化；主属性 = 基础表走乘区、
        // **不吃这 10**（例：`('1',1,5105)` 14 手预览 智=12 恰等于表值 12、速=10）。
        // 挨近 1200 时预览按 `soft_cap_gain(1200, pre, 10)` 退化（pre 1192→9、1195→7、
        // ≥1199→5），说明 10 是**平摊常数**、在乘区之后相加、再进软上限。
        let run = |with_105: bool| -> [i32; 5] {
            let mut data = MatsuriData::load(crate::testing::data_dir()).unwrap();
            if !with_105 {
                for e in data.act_level_effects.iter_mut() {
                    e.all_base = 0;
                }
            }
            // 3 名担当同训练位 + 干劲絶好調 ⇒ 乘区明显 >1，才分得出「10 进乘区」和「10 平摊」
            let mut game = game_with_data(Arc::new(data), &[(0, 3)]);
            game.act_level[0] = 5;
            game.uma_mut().motivation = 5;
            game.uma_mut().vital = 100;
            let pre = game.uma().five_status;
            game.do_keiko(0, &mut rand::rngs::StdRng::seed_from_u64(7))
                .expect("稽古应可结算");
            std::array::from_fn(|i| game.uma().five_status[i] - pre[i])
        };
        let gain = run(true);
        let bare = run(false);
        // 速度稽古基础表 [16,0,3,0,0,…] ⇒ 耐/根/智三轴表值为 0
        for i in [1usize, 3, 4] {
            assert_eq!(gain[i], 10, "表值为 0 的轴 {i} 应拿到平摊 10、不吃乘区");
            assert_eq!(bare[i], 0, "关掉 105 后该轴应无收益");
        }
        assert_eq!(gain[0], bare[0], "主属性不该吃 105 的平摊值");
        assert!(
            gain[0] > 16,
            "主属性应吃到人头/干劲乘区，实测 {}/16",
            gain[0]
        );
    }

    #[test]
    fn keiko_flat_ten_boundary_table_matches_packet() {
        // 一手：基础表为 0 的轴（=纯平摊 10）的预览随赛前值的退化表，逐档括号内是该 pre
        // 值的手数（`E:/HJM/_festa/festa_rows2.csv`，1,740 手）：
        //   1192→9(2) / 1194→8(3) / 1195→7(7) / 1196→7(1) / 1199→5(1) / ≥1201→5(21)
        // ⇒ 平摊值与普通训练同走一条 1200 软上限（余量原样 + 超出部分 floor 减半），
        //   不是「超过就整条减半」。
        // ⚠️ 唯一对不上的一档：run168 turn73 根性 pre=1191 预览 10（本公式给 9）。只此 1 手，
        //   同局另 5 档全吻合 ⇒ 不改公式，留档待复测（怀疑该轴当时软上限并非正好 1200）。
        assert_eq!(soft_cap_gain(1200, 1183, 10), 10);
        assert_eq!(soft_cap_gain(1200, 1190, 10), 10);
        assert_eq!(soft_cap_gain(1200, 1192, 10), 9);
        assert_eq!(soft_cap_gain(1200, 1194, 10), 8);
        assert_eq!(soft_cap_gain(1200, 1195, 10), 7);
        assert_eq!(soft_cap_gain(1200, 1196, 10), 7);
        assert_eq!(soft_cap_gain(1200, 1199, 10), 5);
        assert_eq!(soft_cap_gain(1200, 1614, 10), 5);
    }

    #[test]
    fn keiko_pays_home_leg_plus_festa_leg_with_per_leg_soft_cap() {
        // 一手判据（`E:/HJM/_festa/festa_rows2.csv`，229 执行手）：
        // `实到[axis] = home_pred[axis] + p[axis]` —— 227/229 手五轴全中
        // （合宿 80/81、非合宿 147/148，不中的 2 手是稽古失败手），PT 同样 227/229。
        // 报文里两腿**各自已套过** 1200 软上限（pre=1902 那条显示 5、pre=1195 显示 7），
        // 所以是「分腿折再相加」，不是「对总和折」。
        // 这里用三条独立路径交叉验证同一个等式：普通训练腿(do_train) + 只留稽古腿的臂 = 稽古实到。
        let load = || MatsuriData::load(crate::testing::data_dir()).unwrap();
        // 「只留稽古腿」臂：把普通训练那张表整块清零，剩下的增量就是稽古板自己那条腿
        let mut festa_only_data = load();
        for levels in festa_only_data.training_basic_value.iter_mut() {
            for row in levels.iter_mut() {
                *row = [0; 7];
            }
        }
        let setup = |data: MatsuriData, cur_speed: i32| -> MatsuriGame {
            let mut game = game_with_data(Arc::new(data), &[]);
            game.act_level[0] = 1;
            game.uma_mut().motivation = 3; // 干劲乘区 = 1.0
            game.uma_mut().vital = 100;
            game.uma_mut().five_status[0] = cur_speed;
            game.uma_mut().five_status_limit[0] = 2500; // 别撞硬上限，否则折不到那条腿
            game
        };
        let seed = || StdRng::seed_from_u64(7);

        let mut g = setup(load(), 0);
        let pre = g.uma().five_status;
        let pre_pt = g.uma().skill_pt;
        let mut r = seed();
        g.do_keiko(0, &mut r).unwrap();
        let both: [i32; 5] = std::array::from_fn(|i| g.uma().five_status[i] - pre[i]);
        let both_pt = g.uma().skill_pt - pre_pt;

        let mut g = setup(festa_only_data, 0);
        let pre = g.uma().five_status;
        let pre_pt = g.uma().skill_pt;
        let mut r = seed();
        g.do_keiko(0, &mut r).unwrap();
        let festa: [i32; 5] = std::array::from_fn(|i| g.uma().five_status[i] - pre[i]);
        let festa_pt = g.uma().skill_pt - pre_pt;

        let mut g = setup(load(), 0);
        let pre = g.uma().five_status;
        let pre_pt = g.uma().skill_pt;
        let mut r = seed();
        g.do_train(0, &mut r).unwrap();
        let home: [i32; 5] = std::array::from_fn(|i| g.uma().five_status[i] - pre[i]);
        let home_pt = g.uma().skill_pt - pre_pt;

        assert!(
            home[0] > 0 && festa[0] > 0,
            "两腿都要有量：home={home:?} festa={festa:?}"
        );
        for i in 0..5 {
            assert_eq!(
                both[i],
                home[i] + festa[i],
                "轴 {i}：稽古实到 {} 应 = home {} + festa {}",
                both[i],
                home[i],
                festa[i]
            );
        }
        assert_eq!(both_pt, home_pt + festa_pt, "技能Pt 同样是两条腿相加");

        // 分腿折：把速度做到 pre=1195，两腿各自折后相加（12→8、16→10）
        // 若改回「对总和折」会得到 16 ⇒ 这条断言会红
        let mut g = setup(load(), 1195);
        let pre = g.uma().five_status;
        let mut r = seed();
        g.do_keiko(0, &mut r).unwrap();
        let capped = g.uma().five_status[0] - pre[0];
        assert_eq!(
            capped,
            soft_cap_gain(1200, 1195, home[0]) + soft_cap_gain(1200, 1195, festa[0]),
            "1200 软上限是分腿各套再相加"
        );
        assert_ne!(
            capped,
            soft_cap_gain(1200, 1195, home[0] + festa[0]),
            "不许改成对两腿之和套软上限"
        );

        // 体力**只一条腿**：报文 159/229 手的稽古板 `p[10]` 为 0、实到体力 = `home[10]`
        // ⇒ 稽古表 `row[6]` 那列本身就是普通训练的体力消费，不能再叠第二次
        let keiko_row = load().keiko_basic_value[0];
        let mut g = setup(load(), 0);
        let pre_vital = g.uma().vital;
        let mut r = seed();
        g.do_keiko(0, &mut r).unwrap();
        assert_eq!(
            g.uma().vital - pre_vital,
            keiko_row[6],
            "稽古体力消费只结算一次（叠两条腿会翻倍）"
        );
    }

    #[test]
    fn train_and_keiko_domains_do_not_leak() {
        // 絆の教え（101）：13 = 排练域的主属性获得量，16 = 训练域的羁绊 +3
        let mut game = game_with_roster(&[(0, 1)]);
        game.advice_base[0] = 101;
        game.act_level[0] = 10;
        let train = game.advice_mods_train(0, false);
        assert_eq!(train.main_pct, 0.0, "絆的主属性不在训练域");
        let keiko = game.advice_mods_keiko(0);
        assert_eq!(keiko.main_pct, 15.0, "絆的主属性在排练域（Lv10 = 15%）");
    }

    #[test]
    fn assistance_only_when_no_friendship_training() {
        // 助力の教え（401）：条件 3 = 「友情非発生時」才有训练效果（master.mdb condition_type=3）。
        let mut game = game_with_roster(&[(0, 1)]);
        game.advice_base[0] = 401;
        game.act_level[0] = 10;
        let no_friendship = game.advice_mods_train(0, false);
        assert_eq!(no_friendship.effect_pct, 40.0, "非友情训练 ⇒ 助力 +40%");
        let friendship = game.advice_mods_train(0, true);
        assert_eq!(friendship.effect_pct, 0.0, "友情训练 ⇒ 助力不生效");
    }

    #[test]
    fn extra_keiko_rate_follows_teach_level_and_not_headcount() {
        // 研鑽の教え（type 8）：发生率取教诲绑定演目的 Lv 值，且**不按人数加算**
        // （一手 memo：`確率は人数分加算しない`）。
        let mut game = game_with_roster(&[(0, 3)]); // 三个担当同属演目 1
        game.advice_base[0] = 701;
        game.act_level[0] = 4; // type 8 Lv4 = 27%
        let acts = game.participating_acts(0, None);
        assert_eq!(game.extra_keiko_rate(&acts), 27);
        // 换成 Lv10 = 80%
        game.act_level[0] = 10;
        assert_eq!(game.extra_keiko_rate(&game.participating_acts(0, None)), 80);
        // 没有人参与 ⇒ 不触发
        let empty = game.participating_acts(1, None);
        assert_eq!(game.extra_keiko_rate(&empty), 0);
    }

    #[test]
    fn extra_keiko_upper_and_lower_outcomes() {
        // memo §4.4：上 = 体力 -5 / 主属性 +20 / PT +20 / 演目槽 +1；下 = 体力 +5 / 主属性 +5
        let mut game = game_with_roster(&[(0, 1)]);
        game.uma_mut().vital = 50;
        game.act_level = [5, 3, 4, 4, 4]; // 演目 2 的 Lv 最低 → 槽 +1 落在它
        game.act_gauge[1] = 2;
        let stat0 = game.uma().five_status[0];
        let pt0 = game.uma().skill_pt;
        game.apply_extra_keiko(0, true);
        assert_eq!(game.uma().five_status[0], stat0 + 20, "上档：主属性 +20");
        assert_eq!(game.uma().skill_pt, pt0 + 20, "上档：技能Pt +20");
        assert_eq!(game.uma().vital, 45, "上档：体力 -5");
        assert_eq!(game.act_gauge[1], 3, "上档：演目Lv 最低的演目槽 +1");
        let mut game = game_with_roster(&[(0, 1)]);
        game.uma_mut().vital = 50;
        let stat0 = game.uma().five_status[0];
        let pt0 = game.uma().skill_pt;
        game.apply_extra_keiko(0, false);
        assert_eq!(game.uma().five_status[0], stat0 + 5, "下档：主属性 +5");
        assert_eq!(game.uma().vital, 55, "下档：体力 +5");
        assert_eq!(game.uma().skill_pt, pt0, "下档没有技能Pt");
    }

    #[test]
    fn assist_support_granted_by_keiko_and_consumed_by_training() {
        // 助力の支え（type 20）：排练成功时获得（3 次），3 次内的训练效果 +5%；
        // 再次获得时剩余次数重置回 3（memo §4.2）。
        let mut game = game_with_roster(&[(0, 1)]);
        game.advice_base[0] = 401; // 助力
        game.act_level[0] = 5;
        assert!(game.can_obtain_assist_support(&game.participating_acts(0, Some(0))));
        game.assist_support = 3;
        // 训练消费一次
        let mut rng = StdRng::seed_from_u64(20261101);
        game.do_train(0, &mut rng).unwrap();
        assert_eq!(game.assist_support, 2, "训练消耗一次「助力の支え」");
    }

    #[test]
    fn check_point_tiers_follow_memo() {
        // memo L215-250：成功档 3/100・5/150・10/200；大成功档 5/200・10/300・15/400
        let _ = Data::init(crate::testing::data_dir());
        let data = Arc::new(MatsuriData::load(crate::testing::data_dir()).unwrap());
        let cp = data.check_points[0].clone();
        let mut game =
            MatsuriGame::newgame(106301, &[], InheritInfo::default(), &[], data).unwrap();
        let before = game.uma().five_status;
        let pt_before = game.uma().skill_pt;
        game.apply_check_point(&cp, false);
        assert_eq!(game.uma().five_status[0] - before[0], 3, "成功档 = 5ステ+3");
        assert_eq!(game.uma().skill_pt - pt_before, 100, "成功档 = SP+100");
        let before = game.uma().five_status;
        let pt_before = game.uma().skill_pt;
        game.apply_check_point(&cp, true);
        assert_eq!(
            game.uma().five_status[0] - before[0],
            5,
            "大成功档 = 5ステ+5"
        );
        assert_eq!(game.uma().skill_pt - pt_before, 200, "大成功档 = SP+200");
    }

    #[test]
    fn check_point_gives_chairman_bond_on_great_only() {
        // memo L219：第 1 回発表会 大成功「理事長絆+15」；成功档没有
        let mut game = game_with_roster(&[(0, 1)]);
        let cp = game.data.check_points[0].clone();
        let chairman = game
            .persons
            .iter()
            .position(|p| p.person_type == PersonType::Yayoi)
            .expect("人头里应有理事長");
        game.apply_check_point(&cp, false);
        assert_eq!(game.persons[chairman].friendship, 0, "成功档没有理事长羁绊");
        game.apply_check_point(&cp, true);
        assert_eq!(
            game.persons[chairman].friendship, 15,
            "大成功 = 理事長絆+15"
        );
    }

    #[test]
    fn check_point_all_stat_respects_soft_cap() {
        // 清单里的「#2 速+5 耐+10 … 非均匀」= 均匀的 5ステ+10 被 1200 软上限砍半
        let mut game = game_with_roster(&[(0, 1)]);
        let cp = game.data.check_points[1].clone(); // all_stat = 10
        let uma = game.uma_mut();
        uma.five_status[0] = 1300; // 速已过 1200
        uma.five_status[1] = 800; // 耐没过
        game.apply_check_point(&cp, true);
        assert_eq!(game.uma().five_status[0] - 1300, 5, "过线的项减半");
        assert_eq!(game.uma().five_status[1] - 800, 10, "没过线的项照给");
    }

    #[test]
    fn act_level_cap_is_six_unless_ssr_agatsuma() {
        // 报文 `festa_info.max_act_level`：R 吾妻（10149）/不带 = 6，SSR 吾妻（30323）= 10
        let _ = Data::init(crate::testing::data_dir());
        let data = Arc::new(MatsuriData::load(crate::testing::data_dir()).unwrap());
        let plain =
            MatsuriGame::newgame(106301, &[], InheritInfo::default(), &[], data.clone()).unwrap();
        assert_eq!(plain.act_level_cap, 6, "不带吾妻 ⇒ 上限 6");
        // 引擎的卡组 id 口径是 `卡号 × 10 + 凸数`（`SupportCard::new`）⇒ SSR 吾妻 = 303230
        let ssr =
            MatsuriGame::newgame(106301, &[303230], InheritInfo::default(), &[], data).unwrap();
        assert_eq!(ssr.act_level_cap, 10, "SSR 吾妻 ⇒ 上限 10");
    }

    #[test]
    fn act_level_never_exceeds_cap() {
        let mut game = game_with_roster(&[(0, 1)]);
        assert_eq!(game.act_level_cap, 6);
        for _ in 0..20 {
            game.bump_lowest_act_level();
        }
        assert!(
            game.act_level.iter().all(|l| *l <= 6),
            "演目 Lv 不得越过上限：{:?}",
            game.act_level
        );
    }

    #[test]
    fn camp_start_vitality_by_year() {
        // 真机日志：1年目「駿大祭合宿開始！」体力 +10；2年目同事件 +15（3 年目为外推）
        assert_eq!(camp_start_vitality(12), 10, "1 年目 +10");
        assert_eq!(camp_start_vitality(36), 15, "2 年目 +15");
        assert_eq!(camp_start_vitality(60), 15, "3 年目：外推，同 2 年目");
        assert_eq!(camp_start_vitality(13), 0, "其它回合没有");
    }

    #[test]
    fn free_race_window_matches_packet_evidence() {
        // 2026-11-01 报文（34 局 `race_random_program_array` 全一致）：可报名窗口在
        // 面板回合 45 / 69（引擎 0 基 44 / 68），每窗口 3 场、全部 G1。
        let _ = Data::init(crate::testing::data_dir());
        let data = Arc::new(MatsuriData::load(crate::testing::data_dir()).unwrap());
        let windows: Vec<(i32, i32, i32)> = data
            .free_races
            .iter()
            .map(|fr| (fr.turn, fr.count, fr.grade))
            .collect();
        assert_eq!(
            windows,
            vec![(44, 3, 1), (68, 3, 1)],
            "自选比赛窗口应是引擎回合 44 / 68，各 3 场 G1"
        );
    }

    #[test]
    fn free_race_action_only_on_window_turns() {
        let mut game = game_with_roster(&[(0, 1)]);
        for (turn, expected) in [
            (43, false),
            (44, true),
            (45, false),
            (67, false),
            (68, true),
        ] {
            game.turn = turn;
            let actions = game.list_actions().unwrap();
            let has = actions.contains(&Action::Race);
            assert_eq!(has, expected, "回合 {turn} 的自选比赛动作应为 {expected}");
            if has {
                assert_eq!(
                    actions.last(),
                    Some(&Action::Race),
                    "自选比赛必须排在候选末尾（训练位下标不能位移）"
                );
            }
        }
    }

    #[test]
    fn free_race_pays_one_random_stat_and_pt() {
        // memo §8.1：目標外 GI = 1 ステ +10 / PT +45（未乘加成；乘区与目標賽同口径）
        let mut game = game_with_roster(&[(0, 1)]);
        game.turn = 44;
        let before = game.uma().five_status;
        let pt_before = game.uma().skill_pt;
        let mut rng = StdRng::seed_from_u64(20261101);
        game.do_free_race(&mut rng).unwrap();
        let after = game.uma().five_status;
        let gains: Vec<i32> = (0..5).map(|i| after[i] - before[i]).collect();
        let hit: Vec<i32> = gains.iter().copied().filter(|g| *g > 0).collect();
        assert_eq!(hit.len(), 1, "「1 ステ」= 只加一项，实际增益 {gains:?}");
        assert_eq!(hit[0], 10, "G1 目标外 = 随机一项 +10");
        assert_eq!(game.uma().skill_pt - pt_before, 45, "G1 目标外 = PT +45");
        // 演目槽：非生涯比赛 +1（memo §8.2）
        let gauge_before = game.act_gauge.iter().sum::<i32>();
        game.turn = 68;
        let mut rng2 = StdRng::seed_from_u64(20261101);
        game.do_free_race(&mut rng2).unwrap();
        assert_eq!(
            game.act_gauge.iter().sum::<i32>() - gauge_before,
            1,
            "非生涯比赛给演目槽 +1"
        );
    }

    #[test]
    fn free_race_unknown_grade_is_not_target_row() {
        // GII 没有样本（`non_target[1] = null`）⇒ 未知不猜：不回落目標賽档。
        let game = game_with_roster(&[(0, 1)]);
        assert!(game.free_race_reward(1).is_some(), "G1 有真值");
        assert!(game.free_race_reward(3).is_some(), "GIII 有真值");
        assert!(
            game.free_race_reward(2).is_none(),
            "GII 无样本 ⇒ 返回 None，不猜"
        );
        assert_ne!(
            game.free_race_reward(1).unwrap().stat,
            game.data.race_reward.target.stat,
            "目標外的五维增量不是「各 +5」"
        );
    }
}
