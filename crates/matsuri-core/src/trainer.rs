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
        // 1) 稽古
        if let Some(idx) = (0..5usize)
            .filter(|i| game.act_gauge[*i] >= 5 && game.act_level[*i] < 10)
            .min_by_key(|i| (game.act_level[*i], *i))
        {
            return Action::Keiko(idx as u8);
        }
        // 2) 低体力
        let ratio = game.uma().vital as f64 / game.uma().max_vital.max(1) as f64;
        if ratio < 0.45 {
            return Action::Rest;
        }
        // 3) 発表会截止：练未达标的演目
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
                    .min_by_key(|i| game.act_level[*i])
                {
                    return Action::Train(idx as u8);
                }
            }
        }
        // 4) 缺口最大
        let mut best = (0usize, f64::MAX);
        for i in 0..5 {
            let gap = (game.uma().five_status_limit[i] - game.uma().five_status[i]) as f64
                / game.uma().five_status_limit[i].max(1) as f64;
            if gap < best.1 {
                best = (i, gap);
            }
        }
        Action::Train(best.0 as u8)
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
        Ok(if choices.is_empty() { 0 } else { 0 })
    }
}
