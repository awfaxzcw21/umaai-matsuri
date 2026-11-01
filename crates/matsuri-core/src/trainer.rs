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
            // 演目亮起（槽满）时该训练位只出 `Keiko`，普通 `Train` 不在候选里（memo §2.3），
            // 样例训练员同样接受稽古，避免开箱即崩。
            .or_else(|| {
                actions.iter().position(
                    |action| matches!(action, Action::Keiko(idx) if usize::from(*idx) == train),
                )
            })
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
/// 3. 凹羁绊窗口内 → 优先练有「支援卡未满 80」的训练位（羁绊最低优先）
/// 4. 発表会前若有演目未达标 → 优先练那个演目的训练
/// 5. 否则 → 补缺口最大的训练
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
        // 3) 凹羁绊窗口：前 friend_window 回合优先练「有支援卡且羁绊未满」的训练，
        //    模拟真人前期把关键卡凹到 80 闪彩圈（彩圈收益见 do_train 的 shining_boost）。
        //    只认 card_id 非空的支援卡人头，理事长/记者/友人（card_id=None）不参与凹。
        //    （放在発表会截止之前：前期凹羁绊是长期投资，発表会前 15 回合才紧张）
        if game.data.friend_window > 0 && game.turn < game.data.friend_window {
            let near_shining = |t: usize| -> bool {
                game.distribution().get(t).is_some_and(|d| {
                    d.iter().any(|p| {
                        *p >= 0
                            && game.persons.get(*p as usize).is_some_and(|person| {
                                person.card_id.is_some() && person.friendship < 80
                            })
                    })
                })
            };
            if let Some(idx) = (0..5usize).filter(|&i| near_shining(i)).min_by_key(|&i| {
                // 优先羁绊最低的支援卡所在的训练（凹最需要的那张）
                let min_bond = game
                    .distribution()
                    .get(i)
                    .map(|d| {
                        d.iter()
                            .filter(|p| **p >= 0)
                            .filter_map(|p| game.persons.get(*p as usize))
                            .filter(|person| person.card_id.is_some())
                            .map(|person| person.friendship)
                            .min()
                            .unwrap_or(100)
                    })
                    .unwrap_or(100);
                (min_bond, i)
            }) {
                return Action::Train(idx as u8);
            }
        }
        // 4) 発表会截止：练未达标的演目（等级低优先，同等级优先 gauge 高的，更接近稽古）
        let need = game
            .data
            .check_points
            .iter()
            .find(|c| c.turn >= game.turn)
            .cloned();
        if let Some(cp) = need
            && cp.turn - game.turn <= 15
            && let Some(idx) = (0..5usize)
                .filter(|i| game.act_level[*i] < cp.need)
                .min_by_key(|i| (game.act_level[*i], -game.act_gauge[*i], *i))
        {
            return Action::Train(idx as u8);
        }
        // 5) 平衡五维：练「离上限最远」的训练（相对缺口 cur/limit 最小 = 最缺），
        //    配合 do_train/do_keiko 的 `cap_decay` 衰减，接近上限的维度收益递减，
        //    自然把五个属性都拉起来、不再死堆已高的属性。
        //    （旧「练等级最低演目」会把演目等级摊平，但五维仍偏——实测速度溢出、
        //    耐力偏低；発表会达标仍由上方规则 3 在截止前 15 回合兜底。）
        //
        // **自选比赛（`Action::Race`）不选**：报名门槛是 12,000 粉（引擎没有粉丝系统），
        // 体力代价也未建模（真机 −15 / −20）；实测 34 局玩家一次都没报名，
        // 这里按同一行为默认不报名，机制本身留了口子给别的策略用。
        let best = (0..5usize).min_by_key(|i| {
            let cur = game.uma().five_status[*i] as i64;
            let lim = game.uma().five_status_limit[*i].max(1) as i64;
            // 千分位相对缺口（0..1000，越小越缺）；同缺口优先 gauge 高（更接近稽古）
            let gap_permille = cur * 1000 / lim;
            (gap_permille, -game.act_gauge[*i], *i)
        });
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
