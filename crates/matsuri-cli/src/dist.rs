//! 面板后端（终局分布）：读 `_mc_state.json` → 官方 MatsuriGame → 换种子跑满整局 → 写 `_dist_result.json`
//!
//! 用途（C 线「方差 / 运气分」）：从当前局面估计**终局评分分布**与「冲上 US8 的概率」。
//!
//! 用法：matsuri-dist <state.json> <result.json>
//!
//! 与 `matsuri-mccli` 共用 `state::assemble_game` 装配；策略用引擎自带的 `MatsuriRolloutTrainer`
//! 基策（rollout 基策，非决策者），随机性只来自 `fork_for_rollout(seed)` 派生的局面/事件流。
//!
//! 输出两套分数：
//!   - `score`：引擎原生 `search_score(1.0)`（衰减五维表 + PT×2.0 + 技能分）。排序口径，绝对值偏低。
//!   - `real_score`：**真实标尺**（对齐黑板面板显示）——
//!       `real = Σ status_final_score(revise(五维)) + 1.0746 × round(skill_pt × 2.61)`
//!     五维分走游戏客户端 `revise`（>1200 减半）再查同一张表；技能分用剧本 15 拟合系数
//!     `1.0746` × `grade_est`（grade/pt 中位 2.61，黑板 `real_ppt` 同源）。
//!     名次沿用引擎原生 `rank_names`（301 档，US8=72,900）。

use std::collections::HashMap;

use anyhow::{Context, Result};
use rand::rngs::StdRng;
use rand::SeedableRng;
use serde_json::{json, Value};

use matsuri_core::trainer::MatsuriRolloutTrainer;
use umaai_core::gamedata::constants::GameConstants;
use umaai_core::Data;
use umasim::game::Game;
use umasim::search::FlatSearchGame;

mod state;

/// 剧本 15 技能系数（黑板 `score.SCENARIO_SCALES[15] = (4.7848, 1.0746)` 的技能项）。
const SKILL_SCALE: f64 = 1.0746;

/// 真实局「最终 Σgrade ÷ 实付 pt」中位 2.65，黑板 `real_ppt = 2.61`。
/// `grade_est = round(skill_pt × 2.61)`；黑板里 `min(清单Σgrade, …)` 的清单上限通常远大于此，不绑定。
const GRADE_PER_PT: f64 = 2.61;

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
    let state_path = args.get(1).cloned().unwrap_or_else(|| "out/_mc_state.json".into());
    let result_path = args.get(2).cloned().unwrap_or_else(|| "out/_dist_result.json".into());
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

    let mut native_scores: Vec<i32> = Vec::with_capacity(n);
    let mut real_scores: Vec<i32> = Vec::with_capacity(n);
    let mut five_sums: Vec<i32> = Vec::with_capacity(n);
    let mut pts: Vec<i32> = Vec::with_capacity(n);
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
        //   技能分 = 1.0746 × round(skill_pt × 2.61)
        let uma = g.uma();
        let stat_part: i32 = uma
            .five_status
            .iter()
            .map(|&v| consts.status_final_score(revise(v)))
            .sum();
        let grade_est = (uma.skill_pt as f64 * GRADE_PER_PT).round() as i32;
        let real = stat_part as f64 + SKILL_SCALE * grade_est as f64;
        real_scores.push(real as i32);
        five_sums.push(uma.five_status.iter().sum());
        pts.push(uma.skill_pt);
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
    rank_rows.sort_by(|a, b| b.0.cmp(&a.0));
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
