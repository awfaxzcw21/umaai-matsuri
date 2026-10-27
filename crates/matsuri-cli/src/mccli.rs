//! 面板后端：读 `_mc_state.json` → 官方 MatsuriGame + FlatSearch → 写 `_mc_result.json`
//!
//! 用法：matsuri-mccli <state.json> <result.json> [search_n]
//!
//! 状态字段与黑板现有的 `_mc_state.json` 对齐（turn/vital/stats/stat_caps/skill_point/acts）。

use std::collections::HashMap;
use std::sync::Arc;

use anyhow::{Context, Result};
use rand::rngs::StdRng;
use rand::SeedableRng;
use serde_json::{json, Value};

use matsuri_core::game::Action;
use matsuri_core::{build_game, MatsuriData};
use umaai::Config;
use umaai_core::Data;
use umasim::game::Game;
use umasim::search::{FlatSearch, FlatSearchGame, SearchConfig};
use umasim::trainer::RandomTrainer;

const DATA_ROOT: &str = "data";

/// 动作 → 面板用的中文标签（与黑板规则引擎同口径）
fn label_of(a: &Action) -> String {
    const TRAIN: [&str; 5] = ["速度", "耐力", "力量", "根性", "智力"];
    const ACT: [&str; 5] = ["流鏑馬", "神旗争奪戦", "納舞", "大曳神輿", "飛駆"];
    match a {
        Action::Train(i) => format!("训练 {}", TRAIN[(*i as usize).min(4)]),
        Action::Keiko(i) => format!("稽古 {}", ACT[(*i as usize).min(4)]),
        Action::Outing => "出行".to_string(),
        Action::Rest => "休息".to_string(),
    }
}

fn main() {
    if let Err(e) = run() {
        eprintln!("错误: {e:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let state_path = args.get(1).cloned().unwrap_or_else(|| "out/_mc_state.json".into());
    let result_path = args.get(2).cloned().unwrap_or_else(|| "out/_mc_result.json".into());
    let search_n: usize = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(200);

    let raw = std::fs::read_to_string(&state_path).context("读状态失败")?;
    let v: Value = serde_json::from_str(&raw).context("解析状态失败")?;

    let cfg = Config::load_with_overlay_file(DATA_ROOT, ".").context("载入配置失败")?;
    Data::init(DATA_ROOT)?;
    let data = Arc::new(MatsuriData::load(DATA_ROOT).context("载入剧本数据失败")?);
    let mut game = build_game(&cfg, data).context("装配对局失败")?;

    // ---- 用面板状态覆盖局面 ----
    let turn = v.get("turn").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
    game.base.turn = turn.clamp(0, 77);
    let uma = game.base.uma.clone();
    let stats: HashMap<String, i64> = v
        .get("stats")
        .and_then(|x| serde_json::from_value(x.clone()).ok())
        .unwrap_or_default();
    let caps: HashMap<String, i64> = v
        .get("stat_caps")
        .and_then(|x| serde_json::from_value(x.clone()).ok())
        .unwrap_or_default();
    let names = ["速度", "耐力", "力量", "根性", "智力"];
    {
        let u = game.uma_mut();
        for (i, n) in names.iter().enumerate() {
            u.five_status[i] = stats.get(*n).copied().unwrap_or(0) as i32;
            u.five_status_limit[i] = caps.get(*n).copied().unwrap_or(1200) as i32;
        }
        u.vital = v.get("vital").and_then(|x| x.as_i64()).unwrap_or(uma.vital as i64) as i32;
        u.max_vital = v.get("max_vital").and_then(|x| x.as_i64()).unwrap_or(uma.max_vital as i64) as i32;
        u.skill_pt = v.get("skill_point").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
        u.motivation = match v.get("motivation").and_then(|x| x.as_str()).unwrap_or("") {
            "绝好调" => 5,
            "好调" => 4,
            "普通" => 3,
            "不调" => 2,
            "绝不调" => 1,
            _ => 3,
        };
    }
    if let Some(acts) = v.get("acts").and_then(|x| x.as_array()) {
        for a in acts {
            let at = a.get("act_type").and_then(|x| x.as_i64()).unwrap_or(0);
            if (1..=5).contains(&at) {
                let i = (at - 1) as usize;
                game.act_level[i] = a.get("level").and_then(|x| x.as_i64()).unwrap_or(1) as i32;
                game.act_gauge[i] = a.get("gauge").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
            }
        }
    }

    let actions = game.list_actions()?;
    let mut cfg_search = SearchConfig::default();
    cfg_search.search_n = search_n.clamp(50, 800);
    cfg_search.max_depth = 0;
    let search = FlatSearch::<matsuri_core::game::MatsuriGame>::new(cfg_search);
    let mut rng = StdRng::seed_from_u64(20261028);

    let out = search.search_with(&game, &actions, &mut rng, |g, a, seed| {
        let mut g = g.fork_for_rollout(seed);
        let mut r = StdRng::seed_from_u64(seed ^ 0x9E37_79B9_7F4A_7C15);
        g.apply_root_action(a, &mut r)?;
        while g.next() {
            let mut rr = StdRng::seed_from_u64(seed ^ 0x51ED_2701);
            g.run_stage(&RandomTrainer, &mut rr)?;
        }
        Ok(g.search_score(1.0)?)
    })?;

    let mut rows: Vec<(String, f64, f64, u32)> = out
        .actions
        .iter()
        .zip(out.action_results.iter())
        .map(|(a, ar)| {
            let n = ar.count();
            let mean = ar.mean();
            let se = if n > 1 { ar.stdev() / (n as f64).sqrt() } else { 0.0 };
            (label_of(a), mean, se, n)
        })
        .collect();
    rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let ranked: Vec<Value> = rows
        .iter()
        .map(|(label, mean, se, n)| {
            json!({"label": label, "action": ["matsuri", label], "score": mean, "se": se, "n": n})
        })
        .collect();
    let result = json!({
        "turn": turn,
        "ok": true,
        "engine": "umaai-matsuri-official",
        "ranked": ranked,
        "rollouts": out.action_results.iter().map(|a| a.count()).sum::<u32>()
    });
    std::fs::write(&result_path, serde_json::to_string(&result)?)?;
    Ok(())
}
