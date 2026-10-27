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
use umasim::trainer::RandomTrainer;

use super::{Action, MatsuriGame, Stage};
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
        // S1：五个训练 + 休息；后续实现在这里列自己的动作（比赛 / 出行 / 剧本机制）
        // 合宿窗口：只有训练（引擎里训练=演目稽古），没有出行/休息
        if self.is_camp_turn() {
            return Ok((0..5u8).map(Action::Train).collect());
        }
        let mut actions: Vec<Action> = Vec::new();
        // 演目槽满 → 稽古作为可选动作（官方：gauge 满才亮）
        for idx in 0..5u8 {
            if self.act_gauge[idx as usize] >= 5 && self.act_level[idx as usize] < 10 {
                actions.push(Action::Keiko(idx));
            }
        }
        actions.extend((0..5u8).map(Action::Train));
        // 出行：第一/二年、未满 5 次、且体力 < 40% 才放进候选（与隔壁手写策略同口径）
        let vital_ratio = self.uma().vital as f64 / self.uma().max_vital.max(1) as f64;
        if self.turn >= 21 && self.turn <= 60 && self.outing_count < 5 && vital_ratio < 0.40 {
            actions.push(Action::Outing);
        }
        actions.push(Action::Rest);
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
                if let Some(event) = self.random_select_event(&data.events.uma_events, rng) {
                    events.push(event);
                }
            }
            Ok(2) => {
                if self.turn >= 12 {
                    if let Ok(event) = data.system_event("drop_motivation") {
                        events.push(event.clone());
                    }
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

    fn deyilv(&mut self, _person_index: i32) -> f32 {
        // S1 占位：后续实现按自己的卡组效果算「人头得意率」
        1.0
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
}
