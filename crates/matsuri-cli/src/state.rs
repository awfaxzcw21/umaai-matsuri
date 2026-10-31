//! 共享局面装配：把面板的 `_mc_state.json` 文本转成引擎 `MatsuriGame`。
//!
//! `matsuri-mccli`（动作搜索）与 `matsuri-dist`（终局分布）共用同一套装配，
//! 保证两个后端对「当前局面」的理解逐字一致。任何对报文→引擎映射的修正只改这一处。

use std::collections::HashMap;
use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use matsuri_core::game::MatsuriGame;
use matsuri_core::{build_game, MatsuriData};
use umaai::Config;
use umaai_core::{Data, RaceMask};
use umasim::game::Game;

const DATA_ROOT: &str = "data";

/// 装配结果：引擎局面 + 面板 1 基回合号 + 兜底/校准备注。
pub struct LoadedState {
    pub game: MatsuriGame,
    /// 面板报文里的 `turn`（1 基）；引擎内部是 0 基（`game.base.turn`）。
    pub panel_turn: i32,
    /// 报文卡组不足 5 张 / 装配失败 → 退回示例卡组。
    pub deck_fallback: bool,
    /// 赛程校准说明（`{before, after}`），未校准为 `Null`。
    pub schedule_note: Value,
}

/// 面板赛程（`races[].turn`，1 基）→ 引擎比赛回合（0 基，只留 RaceMask 窗口内的）。
fn schedule_turns(v: &Value) -> Vec<i32> {
    v.get("races")
        .and_then(|x| x.as_array())
        .map(|arr| {
            let mut turns: Vec<i32> = arr
                .iter()
                .filter_map(|r| r.get("turn").and_then(|t| t.as_i64()))
                .map(|t| t as i32 - 1)
                .filter(|t| (RaceMask::MIN_TURN..=RaceMask::MAX_TURN).contains(t))
                .collect();
            turns.sort_unstable();
            turns
        })
        .unwrap_or_default()
}

/// 读 `_mc_state.json` 文本 → 装配引擎局面（卡组/人头/五维/体力/干劲/赛程/演目/教え）。
///
/// 与 `matsuri-mccli` 原 `run()` 内的装配逐字一致，抽出供两个 bin 共享。
pub fn assemble_game(raw: &str) -> Result<LoadedState> {
    let v: Value = serde_json::from_str(raw).context("解析状态失败")?;

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
                // 先收集缺失的卡号再剔除：之前这条日志把「卡组张数」当成卡号打出来了，
                // 看到「缺失 1 张卡（6）」根本不知道是哪张，白查一轮。
                let missing: Vec<u32> = deck_ids
                    .iter()
                    .filter(|idr| !map.contains_key(&(**idr / 10).to_string()))
                    .map(|idr| idr / 10)
                    .collect();
                deck_ids.retain(|idr| map.contains_key(&(idr / 10).to_string()));
                if !missing.is_empty() {
                    eprintln!("卡表缺失 {} 张卡，已从卡组剔除：{:?}", missing.len(), missing);
                }
            }
        }
    }
    let mut deck_fallback = false;
    let mut game = build_game(&cfg, data).context("装配对局失败")?;

    // 用报文里的真实支援卡替换引擎默认卡组（缺卡的槽位跳过）
    {
        use umasim::game::SupportCard;
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
                // B2 分源：真实训练 PT 收益快照（面板 skill_point，含得意/等级/教え/発表会全加成）
                if let Some(sp) = c.get("skill_point").and_then(|x| x.as_i64()) {
                    game.train_pt_anchor[idx] = sp as i32;
                }
                // 3b：真实训练五维收益快照（面板 stat_gains，同样是全加成真值）
                if let Some(sg) = c.get("stat_gains").and_then(|x| x.as_object()) {
                    for (i, name) in TRAINS.iter().enumerate() {
                        if let Some(val) = sg.get(*name).and_then(|x| x.as_i64()) {
                            game.train_stat_anchor[idx][i] = val as i32;
                        }
                    }
                }
                if let Some(ids) = c.get("partner_ids").and_then(|x| x.as_array()) {
                    for id in ids {
                        let Some(idv) = id.as_i64() else { continue };
                        for p in game.persons.iter_mut() {
                            if p.chara_id as i64 == idv {
                                p.train_type = idx as i32;
                            }
                        }
                    }
                }
            }
        }
        // 羁绊快照 → 人头 → 卡组。
        // 报文里的 training_partner_id 有两套 id：支援卡给的是「卡位」（1..=6），
        // 剧本追加人头给的是 chara_id（4 位数）。之前只按 chara_id 匹配，支援卡一条都对不上，
        // 导致 deck[].friendship 一直是卡面 initial_jiban —— 支援卡固有（unique_effect_type 1|2）
        // 的「羁绊 ≥ 80 触发词条」永远不生效（t38 那套卡 4 张卡阈值都是 80，快照全是 100）。
        if let Some(bs) = bonds {
            const DECK_SLOTS: i64 = 6;
            let chara_ids: Vec<i64> = game.persons.iter().map(|p| p.chara_id as i64).collect();
            for (k, val) in bs.iter() {
                let Some(idv) = k.parse::<i64>().ok() else { continue };
                let Some(f) = val.as_i64() else { continue };
                let idx = if (1..=DECK_SLOTS).contains(&idv) {
                    (idv - 1) as usize
                } else if let Some(i) = chara_ids.iter().position(|c| *c == idv) {
                    i
                } else {
                    continue;
                };
                if idx < game.persons.len() {
                    game.persons[idx].friendship = f as i32;
                }
            }
            let n = game.base.deck.len().min(game.persons.len());
            for i in 0..n {
                game.base.deck[i].friendship = game.persons[i].friendship;
            }
        }
    }
    }

    // ---- 用面板状态覆盖局面 ----
    let turn = v.get("turn").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
    // 面板 turn 是 1 基（chara_info.turn），引擎是 0 基
    let panel_turn = turn;
    game.base.turn = (turn - 1).clamp(0, 77);
    // 记录起点回合：`early_pt_scale` 只在早局（面板 t30 前）启用
    game.start_turn = game.base.turn;
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
    // ---- 赛程校准：用报文里的真实赛程覆盖生涯比赛回合 ----
    // 祭典杯赛程因马娘而异（single_mode_route_race 按 race_set 区分），umaDB 未必覆盖；
    // 窗口（11..=74）外的终盘比赛仍由 is_race_turn 的 73/75/77 兜底。
    let want_turns = schedule_turns(&v);
    let mut schedule_note = Value::Null;
    if !want_turns.is_empty() {
        if let Ok(mask) = RaceMask::from_turns(&want_turns) {
            let have: Vec<i32> = (RaceMask::MIN_TURN..=RaceMask::MAX_TURN)
                .filter(|t| uma.career_races.contains_turn(*t))
                .collect();
            if have != want_turns {
                game.uma_mut().career_races = mask;
                eprintln!("赛程校准（报了 {} 场）：{have:?} → {want_turns:?}", want_turns.len());
                schedule_note = json!({"before": have, "after": want_turns});
            }
        }
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

    // 起步彩圈快照：报文的 stat_gains 已含起步彩圈，涨羁绊/新增彩圈只能给「增量」加成，
    // 否则对起步就彩圈的卡双重计算（见 do_train 的 shining_mult：按真实 youqing% 增量结算）。
    game.capture_initial_shining();

    Ok(LoadedState {
        game,
        panel_turn,
        deck_fallback,
        schedule_note,
    })
}
