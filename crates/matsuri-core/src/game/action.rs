//! 剧本动作（`Action`）与结算入口
//!
//! `apply` 只负责把动作落到局面上，**取哪条随机流由 `run_stage` 决定**（策略流）。

use std::fmt::Display;

use anyhow::Result;
use rand::Rng;
use umasim::game::{ActionEnum, Game};

use super::MatsuriGame;

/// 剧本动作
#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    /// 训练（0-4：速 / 耐 / 力 / 根 / 智）
    Train(u8),
    /// 演目稽古（演目下标 0-4）
    Keiko(u8),
    /// 友人出行
    Outing,
    /// 休息
    Rest,
    /// 保健室（练习ベタ 时出现：体力 +20 并解除 debuff）
    Clinic,
}

impl Display for Action {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Train(idx) => write!(f, "训练{idx}"),
            Self::Keiko(idx) => write!(f, "稽古{idx}"),
            Self::Outing => write!(f, "出行"),
            Self::Rest => write!(f, "休息"),
            Self::Clinic => write!(f, "保健室"),
        }
    }
}

impl ActionEnum for Action {
    type Game = MatsuriGame;

    fn apply(&self, game: &mut Self::Game, rng: &mut impl Rng) -> Result<()> {
        match self {
            Self::Train(idx) => game.do_train(*idx as usize, rng),
            Self::Keiko(idx) => game.do_keiko(*idx as usize, rng),
            Self::Outing => {
                game.do_outing(rng)?;
                // 演目槽：外出 +1（官方口径 2026-10-31，落点 Lv 最低→槽最低→属性序）
                game.push_act_gauge(1);
                // 吾妻外出：每段再给「最低演目」+1 级（memo §2.4，一局 5 段）
                game.bump_lowest_act_level();
                Ok(())
            }
            Self::Rest => {
                // S1 占位：休息恢复体力（后续实现按自己的回复规则改）
                let _ = rng;
                let uma = game.uma_mut();
                // 2026-10-31 真机：休息一次回 **50** 体力（旧占位写 20）
                uma.vital = (uma.vital + 50).min(uma.max_vital);
                // 演目槽：休息 +1（官方口径 2026-10-31）
                game.push_act_gauge(1);
                Ok(())
            }
            Self::Clinic => {
                // 2026-10-31 真机：保健室「体力 +20、練習ベタ が治った」
                let _ = rng;
                let uma = game.uma_mut();
                uma.vital = (uma.vital + 20).min(uma.max_vital);
                game.practice_bad = false;
                Ok(())
            }
        }
    }
}
