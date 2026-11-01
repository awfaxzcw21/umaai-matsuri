//! 面板后端（终局分布）：读 `_mc_state.json` → 官方 MatsuriGame → 换种子跑满整局 → 写 `_dist_result.json`
//!
//! 用途（C 线「方差 / 运气分」）：从当前局面估计**终局评分分布**与「冲上 US8 的概率」。
//!
//! 用法：matsuri-dist <state.json> <result.json>
//!
//! 与 `matsuri-mccli` 共用 `state::assemble_game` 装配；策略用引擎自带的 `MatsuriRolloutTrainer`
//! 基策（rollout 基策，非决策者），随机性只来自 `fork_for_rollout(seed)` 派生的局面/事件流。
//!
//! 输出：
//!   - `score`：引擎原生 `search_score(1.0)`（衰减五维表 + PT×2.0 + 技能分）。排序口径，绝对值偏低。
//!   - `real_score`：**面板显示用**（对齐黑板面板）——
//!     `real = Σ status_final_score(revise(五维)) + 1.0746 × round(skill_pt × 2.35)`
//!     五维分走游戏客户端 `revise`（>1200 减半）再查同一张表；技能分用剧本 15 拟合系数
//!     `1.0746` × `grade_est`（grade/pt 取 2.35，见 `GRADE_PER_PT_DEFAULT`，`MC_GRADE_PER_PT` 可覆盖）。
//!     名次沿用引擎原生 `rank_names`（301 档，US8=72,900）。
//!   - `gate`：**三分量门禁**（2026-10-31 起）。`real_score` 里混了一个**为凑 72,142 反解出来的
//!     拟合系数 2.35**（见 `GRADE_PER_PT_DEFAULT` 注释），拿它判机制改动会被这个系数搅混。
//!     所以门禁改为三个**可独立核对**的分量各自对照真实局：
//!     `five_sum`（五维和，直接加）、`stat_score`（五维分，游戏原生表、无拟合）、`pt`（直接计数）。
//!     `grade_est` 一并输出，但它**只是折算估计、不是实测**——真实 Σgrade 得从游戏里数技能清单。

use std::collections::HashMap;

use anyhow::{Context, Result};
use rand::SeedableRng;
use rand::rngs::StdRng;
use serde_json::{Value, json};

use matsuri_core::trainer::MatsuriRolloutTrainer;
use umaai_core::Data;
use umaai_core::gamedata::constants::GameConstants;
use umasim::game::Game;
use umasim::search::FlatSearchGame;

use matsuri_cli::state;

/// 剧本 15 技能系数（黑板 `score.SCENARIO_SCALES[15] = (4.7848, 1.0746)` 的技能项）。
const SKILL_SCALE: f64 = 1.0746;

/// 技能分折算：`grade_est = round(skill_pt × GRADE_PER_PT)`。
///
/// 2.61 是黑板 `skill_plan.REAL_GRADES_PER_PT` 的口径（137 匹练成马娘 24,114 grade / 9,240 pt），
/// 但那是**全样本聚合**；本剧本可核的那一局（card 114101，t78 五维 2244/1750/1845/1349/1296、
/// PT 8,450，官方 `rank_score` 72,142）反推出来只有 `18,297 / 8,450 = 2.17`，
/// 直接量到的 Σgrade 19,492 则给 `2.31`。取中间值 2.35，使 72,142 的残差落在 1% 内。
/// 换值不必重编译：`MC_GRADE_PER_PT` 覆盖。
///
/// ⚠ **这个数是拟合出来的，不是量出来的机制**（2026-10-31 标注）。它只是为了让 `real_score`
/// 对上 72,142；**不要拿它当门禁**。门禁请看 `gate` 字段的三分量（`five_sum` / `stat_score` / `pt`），
/// 真实 Σgrade 要从游戏技能清单直接数。
///
/// ⚠ 另一个未定的口径问题：2.35 该除「总得到 PT」还是「实付 PT」没有定论。本机收的一局
/// （PT 8,339 / 实付 7,649 / Σgrade 17,022）按总 PT 是 2.04、按实付是 2.23，两个都不到 2.35。
const GRADE_PER_PT_DEFAULT: f64 = 2.35;

fn grade_per_pt() -> f64 {
    std::env::var("MC_GRADE_PER_PT")
        .ok()
        .and_then(|s| s.parse::<f64>().ok())
        .filter(|v| *v > 0.0)
        .unwrap_or(GRADE_PER_PT_DEFAULT)
}

/// 游戏客户端 `ScoreUtils`：>1200 的属性按 `(v+1200)/2` 减半计分；
/// `revise` 是它的逆（`2v-1200`），把原始属性还原成「等效未衰减值」再去查表。
fn revise(v: i32) -> i32 {
    if v <= 1200 { v } else { 2 * v - 1200 }
}

fn main() {
    if let Err(e) = run() {
        eprintln!("错误: {e:#}");
        std::process::exit(1);
    }
}

/// 采样数：`MC_N` 覆盖（默认 4000，实测约 0.3~0.6 秒）。
/// 种子基：`MC_SEED` 覆盖（默认 0），换一次即可检验「结论是不是这套抽样序列碰巧跑出来的」。
fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let state_path = args
        .get(1)
        .cloned()
        .unwrap_or_else(|| "out/_mc_state.json".into());
    let result_path = args
        .get(2)
        .cloned()
        .unwrap_or_else(|| "out/_dist_result.json".into());
    let n: usize = std::env::var("MC_N")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(4000);
    let seed_base: u64 = std::env::var("MC_SEED")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(0);

    let raw = std::fs::read_to_string(&state_path).context("读状态失败")?;
    let loaded = state::assemble_game(&raw)?;
    let game = loaded.game;
    let turn = loaded.panel_turn;

    let data = Data::get()?;
    let consts = &data.consts;
    let grade_pp = grade_per_pt();

    let mut native_scores: Vec<i32> = Vec::with_capacity(n);
    let mut real_scores: Vec<i32> = Vec::with_capacity(n);
    let mut five_sums: Vec<i32> = Vec::with_capacity(n);
    let mut stat_scores: Vec<i32> = Vec::with_capacity(n);
    let mut pts: Vec<i32> = Vec::with_capacity(n);
    let mut grade_ests: Vec<i32> = Vec::with_capacity(n);

    // 逐样本明细（尾部诊断用）：`MC_DUMP=<path>` 时才落盘，避免默认输出变大。
    let dump_path = std::env::var("MC_DUMP").ok();
    let mut dump: Vec<Value> = Vec::new();
    for i in 0..n {
        // 与 mccli 2-ply 同族的种子散列：换 MC_SEED 不换抽样结构，只换基点
        let seed = seed_base
            .wrapping_mul(0x5DEE_CE66_D1CE_4A1F)
            .wrapping_add((i as u64).wrapping_mul(0xD1B5_4A32_D192_ED03));
        let mut g = game.fork_for_rollout(seed);
        let mut rng = StdRng::seed_from_u64(seed ^ 0xA1B2_C3D4_5E6F_7081);
        g.run_full_game(&MatsuriRolloutTrainer, &mut rng)?;

        native_scores.push(g.search_score(1.0)? as i32);

        // 真实标尺（对齐黑板面板）：
        //   五维分 = Σ 表[revise(属性)]（引擎 `score_parts` 少了 revise，这里补上）
        //   技能分 = 1.0746 × round(skill_pt × grade_pp)
        let uma = g.uma();
        let stat_part: i32 = uma
            .five_status
            .iter()
            .map(|&v| consts.status_final_score(revise(v)))
            .sum();
        let grade_est = (uma.skill_pt as f64 * grade_pp).round() as i32;
        let real = stat_part as f64 + SKILL_SCALE * grade_est as f64;
        real_scores.push(real as i32);
        five_sums.push(uma.five_status.iter().sum());
        stat_scores.push(stat_part);
        pts.push(uma.skill_pt);
        grade_ests.push(grade_est);

        if dump_path.is_some() {
            dump.push(json!({
                "i": i,
                "real": real as i32,
                "native": native_scores[native_scores.len() - 1],
                "five": uma.five_status.to_vec(),
                "sum": uma.five_status.iter().sum::<i32>(),
                "stat": stat_part,
                "pt": uma.skill_pt,
                "grade": grade_est,
                "act": g.act_level.to_vec(),
                "gauge": g.act_gauge.to_vec(),
                "gain": g.gain_pct,
            }));
        }
    }

    if let Some(path) = dump_path.as_deref() {
        let body: Vec<String> = dump.iter().map(|v| v.to_string()).collect();
        std::fs::write(path, body.join("\n")).context("写样本明细失败")?;
    }

    let result = json!({
        "turn": turn,
        "ok": true,
        "engine": "umaai-matsuri-official",
        "n": n,
        "deck_fallback": loaded.deck_fallback,
        "schedule": loaded.schedule_note,
        "score": summarize(consts, &native_scores),
        "real_score": summarize(consts, &real_scores),
        // 三分量门禁：各自可独立核对，不含拟合系数。
        "gate": {
            "note": "五维和 / 五维分 / PT 三项各自与真实局对照；grade_est 只是折算估计，真实 Σgrade 需从游戏技能清单直接数",
            "five_sum": describe(&five_sums),
            "stat_score": describe(&stat_scores),
            "pt": describe(&pts),
            "grade_est": describe(&grade_ests),
            "grade_per_pt_used": grade_pp,
        },
        "debug": {
            "five_sum_mean": if n == 0 { 0.0 } else { five_sums.iter().map(|&s| s as f64).sum::<f64>() / n as f64 },
            "five_sum_min": five_sums.iter().copied().min().unwrap_or(0),
            "five_sum_max": five_sums.iter().copied().max().unwrap_or(0),
            "pt_mean": if n == 0 { 0.0 } else { pts.iter().map(|&s| s as f64).sum::<f64>() / n as f64 },
            "pt_min": pts.iter().copied().min().unwrap_or(0),
            "pt_max": pts.iter().copied().max().unwrap_or(0),
        },
    });
    std::fs::write(&result_path, serde_json::to_string(&result)?)?;
    Ok(())
}

/// 纯数值描述统计（不带名次直方图）——门禁三分量用。
fn describe(values: &[i32]) -> Value {
    let n = values.len();
    let mean = if n == 0 {
        0.0
    } else {
        values.iter().map(|&s| s as f64).sum::<f64>() / n as f64
    };
    let std = if n == 0 {
        0.0
    } else {
        let var = values
            .iter()
            .map(|&s| {
                let d = s as f64 - mean;
                d * d
            })
            .sum::<f64>()
            / n as f64;
        var.sqrt()
    };
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    let pct = |p: f64| -> f64 {
        if n == 0 {
            0.0
        } else {
            let idx = ((n as f64 - 1.0) * p).round() as usize;
            sorted[idx.min(n - 1)] as f64
        }
    };
    let median = if n == 0 {
        0.0
    } else if n % 2 == 1 {
        sorted[n / 2] as f64
    } else {
        (sorted[n / 2 - 1] as f64 + sorted[n / 2] as f64) / 2.0
    };
    json!({
        "mean": mean,
        "median": median,
        "std": std,
        "p10": pct(0.10),
        "p90": pct(0.90),
        "min": sorted.first().copied().unwrap_or(0),
        "max": sorted.last().copied().unwrap_or(0),
    })
}

/// 一组分数的描述统计 + 名次直方图 + 关键档达标概率，统一塞进一个 JSON 对象。
fn summarize(consts: &GameConstants, scores: &[i32]) -> Value {
    let n = scores.len();

    let mean = if n == 0 {
        0.0
    } else {
        scores.iter().map(|&s| s as f64).sum::<f64>() / n as f64
    };
    let std = if n == 0 {
        0.0
    } else {
        let var = scores
            .iter()
            .map(|&s| {
                let d = s as f64 - mean;
                d * d
            })
            .sum::<f64>()
            / n as f64;
        var.sqrt()
    };
    let mut sorted = scores.to_vec();
    sorted.sort_unstable();
    let pct = |p: f64| -> f64 {
        if n == 0 {
            0.0
        } else {
            let idx = ((n as f64 - 1.0) * p).round() as usize;
            sorted[idx.min(n - 1)] as f64
        }
    };
    let median = if n == 0 {
        0.0
    } else if n % 2 == 1 {
        sorted[n / 2] as f64
    } else {
        (sorted[n / 2 - 1] as f64 + sorted[n / 2] as f64) / 2.0
    };

    // 名次直方图：按分数线从高到低排（LS 在前），只保留出现过的档
    let mut hist: HashMap<String, u32> = HashMap::new();
    for &s in scores {
        *hist.entry(consts.get_rank_name(s).to_string()).or_insert(0) += 1;
    }
    let mut rank_rows: Vec<(i32, String, u32)> = hist
        .into_iter()
        .map(|(name, count)| {
            let thresh = consts
                .rank_names
                .iter()
                .position(|r| *r == name)
                .and_then(|i| consts.rank_scores.get(i).copied())
                .unwrap_or(0);
            (thresh, name, count)
        })
        .collect();
    rank_rows.sort_by_key(|a| std::cmp::Reverse(a.0));
    let rank_hist: Vec<Value> = rank_rows
        .iter()
        .map(|(thresh, name, count)| {
            json!({
                "rank": name,
                "threshold": thresh,
                "count": count,
                "pct": if n == 0 { 0.0 } else { (*count as f64 / n as f64) * 100.0 },
            })
        })
        .collect();

    // 关键档达标概率：P(评分 ≥ 某档分数线)
    let targets = ["US7", "US8", "US9", "LG", "LF", "LE"];
    let p_at_least = |rank: &str| -> Option<Value> {
        let i = consts.rank_names.iter().position(|r| r == rank)?;
        let thresh = consts.rank_scores.get(i).copied()?;
        let p = if n == 0 {
            0.0
        } else {
            scores.iter().filter(|&&s| s >= thresh).count() as f64 / n as f64
        };
        Some(json!({"rank": rank, "threshold": thresh, "p": p, "pct": p * 100.0}))
    };
    let targets_json: Vec<Value> = targets.iter().filter_map(|t| p_at_least(t)).collect();

    json!({
        "mean": mean,
        "median": median,
        "std": std,
        "p10": pct(0.10),
        "p90": pct(0.90),
        "min": sorted.first().copied().unwrap_or(0),
        "max": sorted.last().copied().unwrap_or(0),
        "rank_hist": rank_hist,
        "targets": targets_json,
    })
}
