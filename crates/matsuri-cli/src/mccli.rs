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
use matsuri_core::trainer::MatsuriRolloutTrainer;

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

    // ★用面板报文里的真实卡组（卡号 + 突破数），而不是配置里的示例卡组
    // SupportCard::new 接受 idrank = cardId*10 + 突破等级；
    // 卡表里某些卡只有 N 档，突破等级要按 cardValue 长度夹取，否则引擎越界
    let carddb_raw = std::fs::read_to_string(format!("{DATA_ROOT}/cardDB.json")).ok();
    let carddb: Option<serde_json::Map<String, Value>> = carddb_raw
        .as_deref()
        .and_then(|t| serde_json::from_str::<Value>(t).ok())
        .and_then(|v| match v {
            Value::Object(m) => Some(m),
            _ => None,
        });
    let mut deck_ids: Vec<u32> = Vec::new();
    if let Some(cards) = v.get("support_cards").and_then(|x| x.as_array()) {
        for c in cards {
            if let Some(id) = c.get("card_id").and_then(|x| x.as_i64()) {
                let id = id as u32;
                let mut rank = c
                    .get("limit_break")
                    .or_else(|| c.get("limit"))
                    .and_then(|x| x.as_i64())
                    .unwrap_or(4)
                    .clamp(0, 4) as u32;
                if let Some(map) = &carddb {
                    if let Some(entry) = map.get(&id.to_string()) {
                        if let Some(len) = entry
                            .get("cardValue")
                            .and_then(|cv| cv.as_array())
                            .map(|a| a.len())
                        {
                            rank = rank.min(len.saturating_sub(1) as u32);
                        }
                    }
                }
                deck_ids.push(id * 10 + rank);
            }
        }
    }
    // 卡表可能缺新卡：先把卡表里不存在的卡剔掉，再装配
    if !deck_ids.is_empty() {
        if let Ok(raw) = std::fs::read_to_string(format!("{DATA_ROOT}/cardDB.json")) {
            if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&raw) {
                let before = deck_ids.len();
                deck_ids.retain(|idr| map.contains_key(&(idr / 10).to_string()));
                if deck_ids.len() != before {
                    eprintln!(
                        "卡表缺失 {} 张卡（{:?}），已从卡组剔除",
                        before - deck_ids.len(),
                        before
                    );
                }
            }
        }
    }
    let mut deck_fallback = false;
    let mut game = build_game(&cfg, data).context("装配对局失败")?;

    // 用报文里的真实支援卡替换引擎默认卡组（缺卡的槽位跳过）
    {
        use umasim::game::{SupportCard, Trainer as _};
        let mut real: Vec<SupportCard> = Vec::new();
        for id in &deck_ids {
            match SupportCard::new(*id) {
                Ok(c) => real.push(c),
                Err(e) => eprintln!("卡 {id} 无法装配（跳过）: {e}"),
            }
        }
        if real.len() >= 5 {
            game.base.card_type_count = [0; 7];
            for c in &real {
                game.base.card_type_count[(c.card_type.clamp(0, 6)) as usize] += 1;
            }
            game.base.deck = real;
            game.persons.clear();
            game.init_persons().context("重建人头失败")?;
            eprintln!("已使用报文卡组 {} 张", game.base.deck.len());
        } else {
            deck_fallback = true;
        }
    // 用报文里的人和站位/羁绊校准模型，否则 MCTS 看不见友情/提示（会得出与实测相反的结论）
    {
        const TRAINS: [&str; 5] = ["速度", "耐力", "力量", "根性", "智力"];
        let bonds = v.get("bonds").and_then(|b| b.as_object());
        if let Some(cmds) = v.get("commands").and_then(|x| x.as_array()) {
            for c in cmds {
                let Some(name) = c.get("train").and_then(|x| x.as_str()) else { continue };
                let Some(idx) = TRAINS.iter().position(|t| *t == name) else { continue };
                if let Some(ids) = c.get("partner_ids").and_then(|x| x.as_array()) {
                    for id in ids {
                        let Some(idv) = id.as_i64() else { continue };
                        for p in game.persons.iter_mut() {
                            if p.chara_id as i64 == idv {
                                p.train_type = idx as i32;
                                if let Some(bs) = bonds {
                                    if let Some(f) = bs.get(&idv.to_string()).and_then(|x| x.as_i64()) {
                                        p.friendship = f as i32;
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    }

    // ---- 用面板状态覆盖局面 ----
    let turn = v.get("turn").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
    // 面板 turn 是 1 基（chara_info.turn），引擎是 0 基
    let panel_turn = turn;
    game.base.turn = (turn - 1).clamp(0, 77);
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
                // 教え：报文里带 advice_id 就回填，否则保持 0
                if let Some(aid) = a.get("advice_id").and_then(|x| x.as_i64()) {
                    game.advice_id[i] = aid as i32;
                    game.advice_level[i] = game.act_level[i].clamp(1, 10);
                }
            }
        }
    }

    // 人头分布（count_training_persons 依赖它，空 vec 会按 0 人结算）
    game.base.distribution = (0..5usize)
        .map(|t| {
            game.persons
                .iter()
                .enumerate()
                .filter(|(_, p)| p.train_type == t as i32)
                .map(|(i, _)| i as i32)
                .collect::<Vec<i32>>()
        })
        .collect();

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
            g.run_stage(&MatsuriRolloutTrainer, &mut rr)?;
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
    rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    // 発表会兜底（S2 排程规则）：临近检查点且演目未达标时，
    // 优先推「最接近达标」的那个演目的训练/稽古（不是改搜索，只是候选排序约束）
    let mut rows = rows;
    if let Some(cp) = game
        .data
        .check_points
        .iter()
        .find(|c| c.turn >= game.turn)
        .cloned()
    {
        let deadline = cp.turn - game.turn;
        let deficit: Vec<(usize, i32, i32)> = (0..5usize)
            .filter(|i| game.act_level[*i] < cp.need)
            .map(|i| (i, cp.need - game.act_level[i], game.act_gauge[i]))
            .collect();
        if deadline <= 15 && !deficit.is_empty() {
            let idx = deficit
                .iter()
                .min_by_key(|(_, gap, gauge)| (*gap, -*gauge))
                .map(|(i, _, _)| *i)
                .unwrap_or(0);
            const NAMES: [&str; 5] = ["速度", "耐力", "力量", "根性", "智力"];
            let targets = [format!("训练 {}", NAMES[idx]), format!("稽古 {}", NAMES[idx])];
            if let Some(pos) = rows.iter().position(|(label, _, _, _)| targets.contains(label)) {
                let pick = rows.remove(pos);
                rows.insert(0, pick);
            }
        }
    }
    let ranked: Vec<Value> = rows
        .iter()
        .map(|(label, mean, se, n)| {
            json!({"label": label, "action": ["matsuri", label], "score": mean, "se": se, "n": n})
        })
        .collect();
    // 即时收益（报文实测）：与隔壁 AI 的“净收益”同口径。
    // 「这一下点哪个」以实测为准；MCTS 只负责未来推演，避免模型失真盖过真实数据。
    const NAMES5: [&str; 5] = ["速度", "耐力", "力量", "根性", "智力"];
    const W5: [f64; 5] = [1.25, 0.90, 1.00, 0.85, 1.10];
    let mut live: Vec<(String, f64)> = Vec::new();
    if let Some(cmds) = v.get("commands").and_then(|x| x.as_array()) {
        for c in cmds {
            if !c.get("enable").and_then(|x| x.as_bool()).unwrap_or(true) {
                continue;
            }
            let Some(name) = c.get("train").and_then(|x| x.as_str()) else { continue };
            let gains = c.get("stat_gains").and_then(|g| g.as_object());
            let mut val = 0.0;
            for (i, nm) in NAMES5.iter().enumerate() {
                let g = gains
                    .and_then(|m| m.get(*nm))
                    .and_then(|x| x.as_f64())
                    .unwrap_or(0.0);
                val += g * W5[i];
            }
            let pt = c.get("skill_point").and_then(|x| x.as_f64()).unwrap_or(0.0);
            let vital = c.get("vital_delta").and_then(|x| x.as_f64()).unwrap_or(0.0);
            let fail = c.get("failure_rate").and_then(|x| x.as_f64()).unwrap_or(0.0);
            val += pt * 2.46 + vital * 0.35 - fail * 0.8;
            live.push((format!("训练 {name}"), val));
        }
        live.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    }

    let result = json!({
        "turn": turn,
        "ok": true,
        "engine": "umaai-matsuri-official",
        "deck_fallback": deck_fallback,
        "ranked": ranked,
        "live": live.iter().map(|(l, sc)| json!({"label": l, "score": sc})).collect::<Vec<_>>(),
        "rollouts": out.action_results.iter().map(|a| a.count()).sum::<u32>()
    });
    std::fs::write(&result_path, serde_json::to_string(&result)?)?;
    Ok(())
}
