//! 共享局面装配：把面板的 `_mc_state.json` 文本转成引擎 `MatsuriGame`。
//!
//! `matsuri-mccli`（动作搜索）与 `matsuri-dist`（终局分布）共用同一套装配，
//! 保证两个后端对「当前局面」的理解逐字一致。任何对报文→引擎映射的修正只改这一处。

use std::collections::HashMap;
use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{Value, json};

use matsuri_core::game::MatsuriGame;
use matsuri_core::{MatsuriData, build_game_for_uma};
use umaai::Config;
use umaai_core::{Data, RaceMask};
use umasim::game::base::BasePerson;
use umasim::game::{Game, PersonType};

const DATA_ROOT: &str = "data";

/// 面板训练名 → 引擎训练下标（速 / 耐 / 力 / 根 / 智）
const TRAINS: [&str; 5] = ["速度", "耐力", "力量", "根性", "智力"];

/// 报文人头 id 里「嘉宾本人」的起点（4 位数角色 id）
///
/// 3 位数（101..115）是剧本 NPC 人头枠、1..6 是支援卡位，都不是嘉宾。
const GUEST_ID_MIN: i64 = 1000;

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
                if let Some(map) = &carddb
                    && let Some(entry) = map.get(&id.to_string())
                    && let Some(len) = entry
                        .get("cardValue")
                        .and_then(|cv| cv.as_array())
                        .map(|a| a.len())
                {
                    rank = rank.min(len.saturating_sub(1) as u32);
                }
                deck_ids.push(id * 10 + rank);
            }
        }
    }
    // 卡表可能缺新卡：先把卡表里不存在的卡剔掉，再装配
    if !deck_ids.is_empty()
        && let Ok(raw) = std::fs::read_to_string(format!("{DATA_ROOT}/cardDB.json"))
        && let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&raw)
    {
        // 先收集缺失的卡号再剔除：之前这条日志把「卡组张数」当成卡号打出来了，
        // 看到「缺失 1 张卡（6）」根本不知道是哪张，白查一轮。
        let missing: Vec<u32> = deck_ids
            .iter()
            .filter(|idr| !map.contains_key(&(**idr / 10).to_string()))
            .map(|idr| idr / 10)
            .collect();
        deck_ids.retain(|idr| map.contains_key(&(idr / 10).to_string()));
        if !missing.is_empty() {
            eprintln!(
                "卡表缺失 {} 张卡，已从卡组剔除：{:?}",
                missing.len(),
                missing
            );
        }
    }
    let mut deck_fallback = false;
    // ★马娘：报文里的 `chara_id` 才是这局的马娘（配置里的 `cfg.sim.uma` 只是示例马娘，
    // 成长率 / 初始五维 / 生涯赛程全跟它走，装错人整套数值都会错位）
    let mut game =
        build_game_for_uma(&cfg, data, panel_uma_id(&v, &cfg)).context("装配对局失败")?;

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
    }

    // ---- 人头与分布：按报文的 partner_id 重建 ----
    // 报文 `training_partner_array`（= 面板 `commands[].partner_ids`）混着三套 id：
    //   1..=6      支援卡位置（`persons` 前缀，与 `deck` 逐槽同序）
    //   101..=115  剧本 NPC 人头枠（9001..9050 池）：**不进人数加成**
    //   4 位数     嘉宾本人（角色 id 自身）：进人数加成、不提供卡加成
    // NPC 枠不进人数加成：t30 实证——力量位 5 个人头（2 卡 + NPC 枠 102 + 2 嘉宾），
    // 面板 stat_gains 对应的 N 是 4（NPC 枠 102/103 坐的是 9002 秋川理事长 / 9003 乙名史记者，
    // 引擎 `count_training_persons` 同样排除理事长与记者）。
    let cmds = v
        .get("commands")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default();
    let bonds = v
        .get("bonds")
        .and_then(|x| x.as_object())
        .cloned()
        .unwrap_or_default();
    let mut dist: Vec<Vec<i32>> = vec![Vec::new(); 5];
    let mut guests: HashMap<i64, usize> = HashMap::new();
    for c in &cmds {
        let Some(name) = c.get("train").and_then(|x| x.as_str()) else {
            continue;
        };
        let Some(t) = TRAINS.iter().position(|x| *x == name) else {
            continue;
        };
        // 训练设施等级 → train_level_count（引擎 `train_level = count/4 + 1`）
        if let Some(lv) = c.get("level").and_then(|x| x.as_i64()) {
            game.base.train_level_count[t] = ((lv - 1).max(0) * 4) as i32;
        }
        // 真实五维收益快照（面板 stat_gains：游戏算好的全加成真值）
        if let Some(sg) = c.get("stat_gains").and_then(|x| x.as_object()) {
            for (i, n) in TRAINS.iter().enumerate() {
                if let Some(val) = sg.get(*n).and_then(|x| x.as_i64()) {
                    game.train_stat_anchor[t][i] = val as i32;
                }
            }
        }
        // 人头：partner_id → persons 下标
        for id in c
            .get("partner_ids")
            .and_then(|x| x.as_array())
            .into_iter()
            .flatten()
        {
            let Some(idv) = id.as_i64() else { continue };
            let idx = if (1..=6).contains(&idv) {
                (idv - 1) as usize
            } else if idv >= GUEST_ID_MIN {
                // 嘉宾：同一角色只追加一次（追加到 persons 尾部 = 非卡人头区）
                *guests.entry(idv).or_insert_with(|| {
                    let bond = bonds
                        .get(&idv.to_string())
                        .and_then(|x| x.as_i64())
                        .unwrap_or(0);
                    game.add_person(BasePerson {
                        person_index: 0,
                        person_type: PersonType::Npc,
                        train_type: -1,
                        chara_id: idv as u32,
                        friendship: bond as i32,
                        is_hint: false,
                        card_id: None,
                    });
                    game.persons.len() - 1
                })
            } else {
                continue; // 剧本 NPC 人头枠：不进分布
            };
            if idx < game.persons.len() && !dist[t].contains(&(idx as i32)) {
                dist[t].push(idx as i32);
            }
        }
    }
    game.base.distribution = dist;

    // 羁绊快照 → 人头 → 卡组（支援卡固有「羁绊 ≥ 80 触发词条」吃卡面羁绊）
    for (k, val) in &bonds {
        let Some(idv) = k.parse::<i64>().ok() else {
            continue;
        };
        let Some(f) = val.as_i64() else { continue };
        if !(1..=6).contains(&idv) {
            continue; // 嘉宾羁绊在追加时写入；NPC 枠不建模
        }
        let idx = (idv - 1) as usize;
        if idx < game.persons.len() {
            game.persons[idx].friendship = f as i32;
        }
    }
    let n = game.base.deck.len().min(game.persons.len());
    for i in 0..n {
        game.base.deck[i].friendship = game.persons[i].friendship;
    }

    // 叹号（提示卡）：报文的 tips_event_partner_array 给的是卡位
    for c in &cmds {
        for id in c
            .get("tips_ids")
            .and_then(|x| x.as_array())
            .into_iter()
            .flatten()
        {
            let Some(idv) = id.as_i64() else { continue };
            if (1..=6).contains(&idv) {
                let i = (idv - 1) as usize;
                if i < game.persons.len() {
                    game.persons[i].is_hint = true;
                }
            }
        }
    }

    // ---- 用面板状态覆盖局面 ----
    let turn = v.get("turn").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
    // 面板 turn 是 1 基（chara_info.turn），引擎是 0 基
    let panel_turn = turn;
    game.base.turn = (turn - 1).clamp(0, 77);
    // 记录起点回合（0 基，面板 turn−1）
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
        u.vital = v
            .get("vital")
            .and_then(|x| x.as_i64())
            .unwrap_or(uma.vital as i64) as i32;
        u.max_vital = v
            .get("max_vital")
            .and_then(|x| x.as_i64())
            .unwrap_or(uma.max_vital as i64) as i32;
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
    if !want_turns.is_empty()
        && let Ok(mask) = RaceMask::from_turns(&want_turns)
    {
        let have: Vec<i32> = (RaceMask::MIN_TURN..=RaceMask::MAX_TURN)
            .filter(|t| uma.career_races.contains_turn(*t))
            .collect();
        if have != want_turns {
            game.uma_mut().career_races = mask;
            eprintln!(
                "赛程校准（报了 {} 场）：{have:?} → {want_turns:?}",
                want_turns.len()
            );
            schedule_note = json!({"before": have, "after": want_turns});
        }
    }
    // 演目 Lv 上限：面板 `max_act_level` 是权威值（R 吾妻 = 6 / SSR 吾妻 = 10），
    // 面板没给时保留按卡组推出来的值。
    if let Some(cap) = v.get("max_act_level").and_then(|x| x.as_i64()) {
        game.act_level_cap = cap.clamp(1, 10) as i32;
    }
    if let Some(acts) = v.get("acts").and_then(|x| x.as_array()) {
        for a in acts {
            let at = a.get("act_type").and_then(|x| x.as_i64()).unwrap_or(0);
            if (1..=5).contains(&at) {
                let i = (at - 1) as usize;
                game.act_level[i] = a.get("level").and_then(|x| x.as_i64()).unwrap_or(1) as i32;
                game.act_gauge[i] = a.get("gauge").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
                // 教え：报文里带 advice_id 就回填（基础教诲；Lv 由绑定演目的当前 Lv 决定）
                if let Some(aid) = a.get("advice_id").and_then(|x| x.as_i64()) {
                    game.advice_base[i] = aid as i32;
                }
            }
        }
    }

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

/// 面板报文里的马娘 id（`chara_id`）；查不到就退回配置里的示例马娘
fn panel_uma_id(v: &Value, cfg: &Config) -> u32 {
    let id = v.get("chara_id").and_then(|x| x.as_u64()).unwrap_or(0) as u32;
    if id > 0 && Data::get().is_ok_and(|d| d.get_uma(id).is_ok()) {
        id
    } else {
        eprintln!(
            "马娘 id {id} 不在 umaDB 里，回退配置示例马娘 {}",
            cfg.sim.uma
        );
        cfg.sim.uma
    }
}
