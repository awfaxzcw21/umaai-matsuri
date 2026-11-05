//! 契约 impl（`Game` / `TrainingValueSource` / `FlatSearchGame`）
//!
//! 契约 trait 的 impl 必须整体落在同一文件；可拆的只有 inherent `impl`（见 AGENTS.md）。

use anyhow::{Result, anyhow};
use rand::Rng;
use rand::rngs::StdRng;
use umaai_core::gamedata::EventData;
use umasim::game::base::BasePerson;
use umasim::game::{
    Game, StageId, SupportCard, Trainer, TrainingBasicTable, TrainingValueSource, Uma,
};
use umasim::search::FlatSearchGame;

use super::{ACT_GAUGE_MAX, Action, MatsuriGame, Stage};
use crate::MAX_TURN;

impl TrainingValueSource for MatsuriGame {
    /// 剧本资产里的训练表（后续实现换成自己的数值表）
    fn training_basic_table(&self) -> &TrainingBasicTable {
        &self.data.training_basic_value
    }
}

impl Game for MatsuriGame {
    type Person = BasePerson;
    type Action = Action;

    fn turn(&self) -> i32 {
        self.base.turn
    }

    fn max_turn(&self) -> i32 {
        MAX_TURN
    }

    fn rule_master(&self) -> Option<u64> {
        self.base.master()
    }

    fn set_rule_master(&mut self, master: u64) {
        // 引擎两轴 + 剧本自持的策略轴一起重置（底座入口叫 init_rule_streams，刻意不与契约同名）
        self.base.init_rule_streams(master);
        self.reset_strategy_stream();
    }

    fn stage(&self) -> StageId {
        self.stage.into()
    }

    fn next(&mut self) -> bool {
        if let Some(stage) = self.stage.next() {
            self.stage = stage;
        } else if self.turn < self.max_turn() {
            self.turn += 1;
            self.stage = Stage::Begin;
            // 新回合：两条引擎流与自持的策略流一起按 `(master, 新回合)` 重新派生（CRN 配对来源）
            self.base.reset_rule_streams();
            self.reset_strategy_stream();
        } else {
            return false;
        }
        true
    }

    fn run_stage<T: Trainer<Self>>(&mut self, trainer: &T, decision: &mut StdRng) -> Result<()> {
        // 三条轴各就各位：局面流 / 事件流从底座取出，策略流本局面自持，决策流只给 Trainer。
        // 取出-放回成对执行（中途出错也先放回再传播），避免推进过的流状态被丢掉。
        let mut streams = self.base.take_rule_streams()?;
        let Some(mut strategy) = self.strategy else {
            return Err(anyhow!(
                "策略流缺失：主种子已注入但策略流未派生，应先调用 set_rule_master"
            ));
        };
        let res = self.stage_body(trainer, decision, &mut streams, &mut strategy);
        self.base.put_rule_streams(streams);
        self.strategy = Some(strategy);
        res
    }

    fn list_actions(&self) -> Result<Vec<Self::Action>> {
        // URA 三场那三回合（引擎 73/75/77 = 面板 74/76/78）**不给训练/稽古/休息**：
        // 报文侧全库 13 局在这三回合 0 条 `command_result` ⇒ 真机在这里不结算任何手。
        // 不收窄的话收尾期 perk 会从真机的 3 个可出手回合（引擎 72/74/76）涨到 6 个，
        // 实测这一条占三条虚高净效应的 77%（五维和 −87.8、五维分 −994、PT −99）。
        // 见 docs/URA收尾期窗口报文核对_20261104.md §6 与 #52-B 的 CRN 台账。
        if Self::ura_turns().contains(&self.turn) {
            // 只留 Rest：这三回合不结算任何训练 ⇒ perk 拿不到，也不会多发一次演目 Lv。
            // （给空动作集会卡住决策循环，且我在 #52-B 实测的正是 Rest 这一版。）
            return Ok(vec![Action::Rest]);
        }
        // S1：五个训练 + 休息；后续实现在这里列自己的动作（比赛 / 出行 / 剧本机制）
        // 合宿窗口：只有训练（引擎里训练=演目稽古），没有出行/休息
        if self.is_camp_turn() {
            return Ok((0..5u8).map(Action::Train).collect());
        }
        let mut actions: Vec<Action> = Vec::new();
        // 演目亮起（槽满且未封顶）→ 该训练位只能做演目稽古，不能普通训练（memo §2.3）
        let mut lit = [false; 5];
        for idx in 0..5u8 {
            // 演目槽满 → 稽古作为可选动作（官方：gauge 满才亮）
            if self.act_gauge[idx as usize] >= ACT_GAUGE_MAX && self.act_level[idx as usize] < 10 {
                lit[idx as usize] = true;
                actions.push(Action::Keiko(idx));
            }
        }
        actions.extend((0..5u8).filter(|i| !lit[*i as usize]).map(Action::Train));
        // 出行：第一/二年、未满 5 次、且体力 < 40% 才放进候选（与隔壁手写策略同口径）
        let vital_ratio = self.uma().vital as f64 / self.uma().max_vital.max(1) as f64;
        if self.turn >= 21 && self.turn <= 60 && self.outing_count < 5 && vital_ratio < 0.40 {
            actions.push(Action::Outing);
        }
        actions.push(Action::Rest);
        // 自选比赛（目標外）：只在该回合有窗口时给出（面板回合 45 / 69 ⇒ 引擎 44 / 68）。
        // 放在**末尾**：训练位下标不能因多一个候选而位移（策略按动作下标选训练）。
        if self.free_race_window(self.turn).is_some() {
            actions.push(Action::Race);
        }
        Ok(actions)
    }

    fn generate_events(&self, rng: &mut impl Rng) -> Vec<EventData> {
        // 与 BasicGame 同口径：剧本事件优先，否则按事件分布抽卡事件 / 马娘事件 / 掉心情
        let Ok(data) = umaai_core::Data::get() else {
            return vec![];
        };
        let mut events = vec![];
        let story = self.list_turn_events(&data.events.story_events);
        if !story.is_empty() {
            return story;
        }
        if data.consts.no_event_turns.contains(&self.turn) {
            return vec![];
        }
        let dist = data.consts.get_event_distribution();
        match umasim::game::sample_bucket(&dist, rng) {
            Ok(0) => {
                let card_event_times: Vec<u32> = [8001, 8002, 8003]
                    .iter()
                    .map(|x| *self.base.events.get(x).unwrap_or(&0))
                    .collect();
                let mut available: Vec<usize> = Vec::new();
                if card_event_times[0] < 5 {
                    available.push(0);
                }
                if card_event_times[1] < card_event_times[0] {
                    available.push(1);
                }
                if card_event_times[2] < card_event_times[1] {
                    available.push(2);
                }
                if !available.is_empty() {
                    let idx = available[rng.random_range(0..available.len())];
                    let mut event = data.events.card_events[idx].clone();
                    let mut p = rng.random_range(0..6);
                    while !self.person_is_available(p) {
                        p = rng.random_range(0..6);
                    }
                    event.person_index = Some(p as i32);
                    events.push(event);
                }
            }
            Ok(1) => {
                // 通用马娘事件 + 剧本专属剧情事件（scenario_events）合并抽签：
                // 剧本专属事件带真实选项（属性/体力/干劲/PT 组合），max_time=1 每局至多一次。
                if self.data.scenario_events.is_empty() {
                    if let Some(event) = self.random_select_event(&data.events.uma_events, rng) {
                        events.push(event);
                    }
                } else {
                    let mut pool: Vec<EventData> = data.events.uma_events.clone();
                    pool.extend(self.data.scenario_events.iter().cloned());
                    if let Some(event) = self.random_select_event(&pool, rng) {
                        events.push(event);
                    }
                }
            }
            Ok(2) => {
                if self.turn >= 12
                    && let Ok(event) = data.system_event("drop_motivation")
                {
                    events.push(event.clone());
                }
            }
            _ => {}
        }
        events
    }

    fn apply_event(&mut self, event: &EventData, choice: usize, rng: &mut impl Rng) -> Result<()> {
        // 引擎底座负责通用事件结算；剧本自己的特殊事件在这里补分支
        let _ = self.base.apply_event(event, choice, rng);
        Ok(())
    }

    fn persons(&self) -> &[Self::Person] {
        &self.persons
    }

    fn persons_mut(&mut self) -> &mut [Self::Person] {
        &mut self.persons
    }

    fn init_persons(&mut self) -> Result<()> {
        // S1：卡组人头 + 理事长；后续实现按自己的卡池与友人规则构造
        let persons: Vec<BasePerson> = self
            .base
            .deck
            .iter()
            .map(BasePerson::try_from)
            .collect::<Result<Vec<_>>>()?;
        for person in persons {
            self.add_person(person);
        }
        self.add_person(BasePerson::yayoi());
        Ok(())
    }

    fn distribution(&self) -> &Vec<Vec<i32>> {
        &self.base.distribution
    }

    fn distribution_mut(&mut self) -> &mut Vec<Vec<i32>> {
        &mut self.base.distribution
    }

    fn absent_rate_drop(&self) -> i32 {
        self.base.absent_rate_drop
    }

    fn deyilv(&mut self, person_index: i32) -> f32 {
        // 剧本 15 的落位规则：**支援卡按卡种站位**（自己卡种的格优先）。
        //
        // 引擎底座拿 `deyilv` 当「自身格权重」用（`weights[train_type] += deyilv`），
        // 且 `deyilv` **不进增益计算**（`calc_training_value` 只吃 buffs.bonus / youqing /
        // ganjing / xunlian / 人数），所以改它只动排列、不动收益公式。
        //
        // 这里不用卡牌的 `deYiLv` 数值：真机报文显示它与落位无剂量关系（见
        // `SELF_SLOT_P` 的注释），偏置是「按卡种站位」的分类效应。
        if person_index < 0 || person_index as usize >= self.base.deck.len() {
            return 0.0; // 非卡人头（理事长/记者）：与引擎底座一致 ⇒ 均匀分布
        }
        super::self_slot_weight()
    }

    fn has_group_buff(&self) -> bool {
        self.base.friend.group_buff_turn > 0
    }

    fn train_level(&self, train: usize) -> usize {
        (self.base.train_level_count[train] as usize / 4 + 1).clamp(1, 5)
    }

    fn uma(&self) -> &Uma {
        &self.base.uma
    }

    fn uma_mut(&mut self) -> &mut Uma {
        &mut self.base.uma
    }

    fn deck(&self) -> &Vec<SupportCard> {
        &self.base.deck
    }
}

impl FlatSearchGame for MatsuriGame {
    /// rollout 基策（S2）：机制性走子
    type RolloutTrainer = crate::trainer::MatsuriRolloutTrainer;

    /// 没有 leaf 估值器时声明 `false`：搜索入口会直接拒绝 `max_depth > 0`
    const SUPPORTS_TRUNCATED_LEAF: bool = false;

    fn default_rollout_trainer() -> Self::RolloutTrainer {
        crate::trainer::MatsuriRolloutTrainer
    }

    fn apply_root_action(&mut self, action: &Self::Action, rng: &mut StdRng) -> Result<()> {
        // 必须与 `run_stage` 内执行动作的方式逐字一致，否则被评估的动作会用上另一条流
        self.apply_action(action, rng)
    }

    fn fork_for_rollout(&self, rollout_seed: u64) -> Self {
        // 分支 = 克隆 + 注入 rollout 主种子；任何不由传入 rng 驱动的流都必须在这里重置
        let mut game = self.clone();
        game.set_rule_master(rollout_seed);
        game
    }

    /// 搜索排序口径：五维腿按**官方算法**过 `revise` 再查表，其余两腿沿用 `score_parts()`。
    ///
    /// 上游默认实现（`umasim/src/search/searchable.rs:71` → `game/uma.rs:233`）拿**原值**直接查表，
    /// 而一手官方分是 `Σ five_status_final_score[revise(v)]`——395 张终局快照反解技能系数时用的就是
    /// 这个口径（中位 1.0705、±0.6% 对得上 `rank_score`，见 `docs/ref/skill_grade_value_20261105.md` §8）。
    /// 差多少：属性 2328 一档，原值查表 10,328、`revise` 后查表 **20,481** ⇒ 不补这层，
    /// 搜索会把五维系统性低估近一倍，排序偏向攒 PT／技能而不是练属性。
    /// 显示层 `matsuri-cli/src/dist.rs:125` 早就在替搜索层兜这个错（注释原话「引擎 `score_parts`
    /// 少了 revise，这里补上」），两边口径不一致已存在很久。
    fn search_score(&self, pt_favor_rate: f64) -> Result<f64> {
        let parts = self.uma().score_parts()?;
        let pt = (parts.pt as f32 * pt_favor_rate as f32) as i32;
        let consts = &umaai_core::Data::get()?.consts;
        let five: i32 = self
            .uma()
            .five_status
            .iter()
            .zip(self.uma().five_status_limit.iter())
            .map(|(&status, &limit)| consts.status_final_score(revise(status.min(limit))))
            .sum();
        Ok((parts.skill + pt + five) as f64)
    }
}

/// 游戏客户端 `ScoreUtils` 的逆：>1200 的属性按 `(v+1200)/2` 减半计分，
/// 查终局评分表前先还原成「等效未衰减值」`2v-1200`。与 `dist.rs` 同名函数逐字一致。
fn revise(v: i32) -> i32 {
    if v <= 1200 { v } else { 2 * v - 1200 }
}
