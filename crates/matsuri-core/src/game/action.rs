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
}

impl Display for Action {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Train(idx) => write!(f, "训练{idx}"),
            Self::Keiko(idx) => write!(f, "稽古{idx}"),
            Self::Outing => write!(f, "出行"),
            Self::Rest => write!(f, "休息"),
        }
    }
}

impl ActionEnum for Action {
    type Game = MatsuriGame;

    fn apply(&self, game: &mut Self::Game, rng: &mut impl Rng) -> Result<()> {
        match self {
            Self::Train(idx) => game.do_train(*idx as usize, rng),
            Self::Keiko(idx) => game.do_keiko(*idx as usize, rng),
            Self::Outing => game.do_outing(rng),
            Self::Rest => {
                // S1 占位：休息恢复体力（后续实现按自己的回复规则改）
                let _ = rng;
                let uma = game.uma_mut();
                uma.vital = (uma.vital + 20).min(uma.max_vital);
                Ok(())
            }
        }
    }
}
