//! 样例训练员（`MatsuriTrainer`）
//!
//! 只演示「训练员落在 core」的接线：按剧本配置的 `sample_order` 轮转选训练，事件选项取首项。
//! 后续实现在这里替换为自己的手写策略 / 搜索训练员；动作与决策的随机纪律由引擎契约约束。

use anyhow::{Result, anyhow};
use rand::rngs::StdRng;
use umaai_core::gamedata::EventChoice;
use umasim::game::{Game, Trainer};

use crate::MatsuriConfig;
use crate::game::{Action, MatsuriGame};

/// 样例训练员：按配置顺序轮转训练
pub struct MatsuriTrainer {
    /// 剧本配置（训练优先顺序）
    config: MatsuriConfig,
}

impl MatsuriTrainer {
    /// 用剧本配置构造训练员
    pub fn new(config: MatsuriConfig) -> Self {
        Self { config }
    }

    /// 取当前回合偏好的训练下标（0-4）
    fn preferred_training(&self, turn: i32) -> usize {
        let len = self.config.sample_order.len();
        self.config.sample_order[turn.max(0) as usize % len]
    }
}

impl Trainer<MatsuriGame> for MatsuriTrainer {
    fn select_action(
        &self,
        game: &MatsuriGame,
        actions: &[Action],
        _rng: &mut StdRng,
    ) -> Result<usize> {
        let train = self.preferred_training(game.turn());
        actions
            .iter()
            .position(|action| matches!(action, Action::Train(idx) if usize::from(*idx) == train))
            .ok_or_else(|| anyhow!("候选动作缺少训练 {train}"))
    }

    fn select_choice(
        &self,
        _game: &MatsuriGame,
        _choices: &[Vec<EventChoice>],
        _rng: &mut StdRng,
    ) -> Result<usize> {
        // S1 占位：后续实现在这里按事件内容选择（可用 `select_event_choice` 拿完整事件）
        Ok(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preferred_training_wraps() {
        let trainer = MatsuriTrainer::new(MatsuriConfig {
            sample_order: vec![0, 2, 4],
        });
        assert_eq!(trainer.preferred_training(0), 0);
        assert_eq!(trainer.preferred_training(1), 2);
        assert_eq!(trainer.preferred_training(2), 4);
        assert_eq!(trainer.preferred_training(3), 0);
    }
}

/// rollout 基策（S2）：机制性走子，不做搜索决策。
///
/// 规则（只读局面）：
/// 1. 演目槽满 → 稽古（等级最低的那个）
/// 2. 体力 < 45% → 休息
/// 3. 発表会前若有演目未达标 → 优先练那个演目的训练
/// 4. 否则 → 补缺口最大的训练
#[derive(Debug, Clone, Default)]
pub struct MatsuriRolloutTrainer;

impl MatsuriRolloutTrainer {
    fn desired(game: &MatsuriGame) -> Action {
        // 0) 合宿：候选只有 5 个训练（=稽古、不升 Lv），练「等级最低」的演目把属性摊开，
        //    避免落空后 fallback 到 Train(0) 死磕第一个演目（实测 act0 被合宿 3 回合
        //    反复稽古而 act3/4 长期 Lv4）。
        if game.is_camp_turn() {
            let idx = (0..5usize)
                .filter(|i| game.act_level[*i] < 10)
                .min_by_key(|i| (game.act_level[*i], -game.act_gauge[*i], *i))
                .unwrap_or(0);
            return Action::Train(idx as u8);
        }
        // 1) 低体力先休息。**必须排在稽古之前**：稽古失败时体力照扣（-12）但
        //    gauge 不清零、Lv 不升，低体力下会锁进「必败稽古 → 体力 0」的死循环
        //    （实测 t37–t39 连续三次失败后 vital 恒 0，整局再没恢复）。
        let ratio = game.uma().vital as f64 / game.uma().max_vital.max(1) as f64;
        if ratio < 0.45 {
            return Action::Rest;
        }
        // 2) 稽古
        if let Some(idx) = (0..5usize)
            .filter(|i| game.act_gauge[*i] >= 5 && game.act_level[*i] < 10)
            .min_by_key(|i| (game.act_level[*i], *i))
        {
            return Action::Keiko(idx as u8);
        }
        // 3) 発表会截止：练未达标的演目（等级低优先，同等级优先 gauge 高的，更接近稽古）
        let need = game
            .data
            .check_points
            .iter()
            .find(|c| c.turn >= game.turn)
            .cloned();
        if let Some(cp) = need {
            if cp.turn - game.turn <= 15 {
                if let Some(idx) = (0..5usize)
                    .filter(|i| game.act_level[*i] < cp.need)
                    .min_by_key(|i| (game.act_level[*i], -game.act_gauge[*i], *i))
                {
                    return Action::Train(idx as u8);
                }
            }
        }
        // 4) 平衡演目：练「等级最低」的演目（把 gauge 填满 → 下一步稽古升 Lv）。
        //    祭典杯的核心循环是 训练(涨槽) → 稽古(升 Lv)，5 个演目等级要齐头并进
        //    才过得了発表会（need 3/5/6）。按「五维缺口最大」练会把某个演目练到
        //    封顶而长期忽略其余（实测 act_level 终局 [8.4,5.9,5.5,5.1,5.0]，
        //    発表会 0 达标）。同等级里优先 gauge 高（更接近稽古）的。
        let best = (0..5usize)
            .filter(|i| game.act_level[*i] < 10)
            .min_by_key(|i| (game.act_level[*i], -game.act_gauge[*i], *i));
        Action::Train(best.unwrap_or(0) as u8)
    }
}

impl Trainer<MatsuriGame> for MatsuriRolloutTrainer {
    fn select_action(
        &self,
        game: &MatsuriGame,
        actions: &[Action],
        _rng: &mut StdRng,
    ) -> Result<usize> {
        let want = Self::desired(game);
        if let Some(i) = actions.iter().position(|a| *a == want) {
            return Ok(i);
        }
        Ok(0)
    }

    fn select_choice(
        &self,
        _game: &MatsuriGame,
        choices: &[Vec<EventChoice>],
        _rng: &mut StdRng,
    ) -> Result<usize> {
        // rollout 基策选「综合价值最高」的选项（终局评分同向）：
        //   五维 1 点 ≈ 属性分，PT 1 点 ≈ 技能分，体力能换训练、干劲影响训练收益。
        // 每组取首个结果（剧本事件每组 1 个 prob=100 的结果，通用事件同构）。
        if choices.len() <= 1 {
            return Ok(0);
        }
        let best = (0..choices.len())
            .max_by_key(|&i| {
                choices[i].first().map_or(i32::MIN, |c| {
                    let v = &c.value;
                    let stat: i32 = v.status_pt.iter().take(5).sum();
                    stat + v.status_pt[5] * 2 + v.vital * 2 + v.motivation * 20
                })
            })
            .unwrap_or(0);
        Ok(best)
    }
}
