//! 面板后端：读 `_mc_state.json` → 官方 MatsuriGame + FlatSearch → 写 `_mc_result.json`
//!
//! 用法：matsuri-mccli <state.json> <result.json> [search_n]
//!
//! 状态字段与黑板现有的 `_mc_state.json` 对齐（turn/vital/stats/stat_caps/skill_point/acts）。

use anyhow::{Context, Result};
use rand::SeedableRng;
use rand::rngs::StdRng;
use serde_json::{Value, json};

use matsuri_core::game::{Action, MatsuriGame};
use matsuri_core::trainer::MatsuriRolloutTrainer;
use umasim::game::Game;
use umasim::search::{FlatSearch, FlatSearchGame, SearchConfig};

use matsuri_cli::state;

/// 动作 → 面板用的中文标签（与黑板规则引擎同口径）
fn label_of(a: &Action) -> String {
    const TRAIN: [&str; 5] = ["速度", "耐力", "力量", "根性", "智力"];
    const ACT: [&str; 5] = ["流鏑馬", "神旗争奪戦", "納舞", "大曳神輿", "飛駆"];
    match a {
        Action::Train(i) => format!("训练 {}", TRAIN[(*i as usize).min(4)]),
        Action::Keiko(i) => format!("稽古 {}", ACT[(*i as usize).min(4)]),
        Action::Outing => "出行".to_string(),
        Action::Rest => "休息".to_string(),
        Action::Clinic => "保健室".to_string(),
        Action::Race => "自选比赛".to_string(),
    }
}

/// 剧本阶段机里「训练 / 比赛决策点」的编号（见 `matsuri-core/src/game/stage.rs`：Begin=0/Train=1/End=2）。
const STAGE_TRAIN: u64 = 1;

/// 从当前 Train 阶段推进到**下一个** Train 阶段（中间的 End / 下回合 Begin 一并跑完）。
///
/// 返回 `false` = 已经到终局，后面没有决策点了。
fn to_next_train(g: &mut MatsuriGame, seed: u64) -> Result<bool> {
    while g.next() {
        if g.stage().get() == STAGE_TRAIN {
            return Ok(true);
        }
        let mut rr = StdRng::seed_from_u64(seed ^ 0x51ED_2701_9E37_79B9);
        g.run_stage(&MatsuriRolloutTrainer, &mut rr)?;
    }
    Ok(false)
}

/// 2-ply：根动作的价值 = 「下一步最优子动作」的期望分，而不是根动作自己的单步均值。
///
/// CRN 做法：同一次 `fork_for_rollout(seed)` + 根动作 + 推进到下一个决策点，得到 `base`；
/// 各子动作都从 `base.clone()` 分叉，因此子动作之间的差异不含「未来事件抽签」的噪声。
/// 单步（1-ply）：官方 FlatSearch 只在根节点评估每个动作（UCB + rollout）。
/// 一行搜索结果：动作标签 / 均值 / 标准误 / 采样数
type Row = (String, f64, f64, u32);

fn one_ply_rows(
    game: &MatsuriGame,
    actions: &[Action],
    search_n: usize,
) -> Result<(Vec<Row>, u32)> {
    // 上限从 800 抬到 5000：官方核心约 12,000 rollout/秒，800 远低于可承受预算
    // 探索项标准差（cpuct × 本值 × √total / n）。实测本剧本单次 rollout σ≈4,800，
    // 内核默认 2200 只有一半 → UCB 过早收敛（n=5000 时仍有动作只分到 256 次）。env 可覆盖以便扫描。
    let cfg_search = SearchConfig {
        search_n: search_n.clamp(50, 5000),
        max_depth: 0,
        expected_search_stdev: std::env::var("MC_EXPECTED_STDEV")
            .ok()
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(4800.0),
        ..SearchConfig::default()
    };
    let search = FlatSearch::<MatsuriGame>::new(cfg_search);
    let mut rng = StdRng::seed_from_u64(20261028);

    let out = search.search_with(game, actions, &mut rng, |g, a, seed| {
        let mut g = g.fork_for_rollout(seed);
        let mut r = StdRng::seed_from_u64(seed ^ 0x9E37_79B9_7F4A_7C15);
        g.apply_root_action(a, &mut r)?;
        while g.next() {
            let mut rr = StdRng::seed_from_u64(seed ^ 0x51ED_2701);
            g.run_stage(&MatsuriRolloutTrainer, &mut rr)?;
        }
        g.search_score(1.0)
    })?;

    let mut rows: Vec<Row> = out
        .actions
        .iter()
        .zip(out.action_results.iter())
        .map(|(a, ar)| {
            let n = ar.count();
            let mean = ar.mean();
            let se = if n > 1 {
                ar.stdev() / (n as f64).sqrt()
            } else {
                0.0
            };
            (label_of(a), mean, se, n)
        })
        .collect();
    rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let total = out.action_results.iter().map(|a| a.count()).sum::<u32>();
    Ok((rows, total))
}

/// 两步（2-ply）：每个根动作先下，再推到下一个训练回合，然后对子动作逐个 rollout 到终局。
/// 根动作价值 = 子动作均值里最好的那个（等价「下一步也选对」的可达值）。
/// 子动作共享同一个 fork 点（CRN），所以子动作之间的差是配对差，噪声被抵消掉。
fn two_ply_rows(
    game: &MatsuriGame,
    actions: &[Action],
    k: usize,
) -> Result<(Vec<Row>, Value, u32)> {
    let mut rows: Vec<Row> = Vec::new();
    let mut plan: Vec<Value> = Vec::new();
    let mut total: u32 = 0;
    // 种子基（MC_SEED）：换一次就能检验「结论是不是这套抽样序列碰巧跑出来的」
    let seed_base: u64 = std::env::var("MC_SEED")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(0);
    for (ai, a) in actions.iter().enumerate() {
        // 子局面候选动作：推进的回合数只由根动作决定（与种子无关）→ 探一次即可
        let mut probe = game.fork_for_rollout(0x5555_0001_0000 ^ ai as u64);
        let mut pr = StdRng::seed_from_u64(0x1234_5678_9ABC_DEF0 ^ ai as u64);
        probe.apply_root_action(a, &mut pr)?;
        let child_actions = if to_next_train(&mut probe, 0x5555_0001_0000 ^ ai as u64)? {
            probe.list_actions()?
        } else {
            Vec::new()
        };

        let mut sums = vec![0.0f64; child_actions.len()];
        let mut sqs = vec![0.0f64; child_actions.len()];
        let mut cnt = 0usize;
        for ki in 0..k {
            let seed = seed_base
                .wrapping_mul(0x5DEE_CE66_D1CE_4A1F)
                .wrapping_add((ai as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15))
                .wrapping_add((ki as u64).wrapping_mul(0xD1B5_4A32_D192_ED03));
            let mut base = game.fork_for_rollout(seed);
            let mut r = StdRng::seed_from_u64(seed ^ 0xA1B2_C3D4_5E6F_7081);
            base.apply_root_action(a, &mut r)?;
            if !to_next_train(&mut base, seed)? {
                break;
            }
            for (j, b) in child_actions.iter().enumerate() {
                let mut g = base.clone();
                let mut r2 = StdRng::seed_from_u64(seed ^ 0xB00B_5EED_0000_0001 ^ j as u64);
                g.apply_root_action(b, &mut r2)?;
                while g.next() {
                    let mut rr = StdRng::seed_from_u64(seed ^ 0x51ED_2701_9E37_79B9);
                    g.run_stage(&MatsuriRolloutTrainer, &mut rr)?;
                }
                let sc = g.search_score(1.0)?;
                sums[j] += sc;
                sqs[j] += sc * sc;
            }
            cnt += 1;
        }

        let mut best: (f64, usize, f64) = (f64::NEG_INFINITY, usize::MAX, 0.0);
        if cnt > 0 {
            for j in 0..child_actions.len() {
                let m = sums[j] / cnt as f64;
                if m > best.0 {
                    let var = (sqs[j] / cnt as f64 - m * m).max(0.0);
                    best = (m, j, (var / cnt as f64).sqrt());
                }
            }
        }
        // cnt == 0（没有下一训练 / 推进失败）：给 0 分占位，别把 ±inf 写进 JSON
        let score = if best.0.is_finite() { best.0 } else { 0.0 };
        rows.push((label_of(a), score, best.2, cnt as u32));
        total += (cnt * child_actions.len()) as u32;
        plan.push(json!({
            "action": label_of(a),
            "next": child_actions.get(best.1).map(label_of),
            "score": score,
            "se": best.2,
            "n": cnt,
            "no_next_train": child_actions.is_empty(),
        }));
    }
    rows.sort_by(|x, y| y.1.partial_cmp(&x.1).unwrap_or(std::cmp::Ordering::Equal));
    Ok((rows, json!(plan), total))
}

fn main() {
    if let Err(e) = run() {
        eprintln!("错误: {e:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let state_path = args
        .get(1)
        .cloned()
        .unwrap_or_else(|| "out/_mc_state.json".into());
    let result_path = args
        .get(2)
        .cloned()
        .unwrap_or_else(|| "out/_mc_result.json".into());
    let search_n: usize = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(200);

    let raw = std::fs::read_to_string(&state_path).context("读状态失败")?;
    // v 仍要保留：`live` 即时收益（commands）直接从原始报文读，不经引擎装配
    let v: Value = serde_json::from_str(&raw).context("解析状态失败")?;
    let loaded = state::assemble_game(&raw)?;
    let game = loaded.game;
    let turn = loaded.panel_turn;
    let deck_fallback = loaded.deck_fallback;
    let schedule_note = loaded.schedule_note;

    let actions = game.list_actions()?;
    // 搜索形态：MC_PLY=2（默认）两步搜索；MC_PLY=1 退回官方 FlatSearch 根节点评估。
    let want_ply: usize = std::env::var("MC_PLY")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(2);
    // 先探一下「有没有下一个训练」：终局回合两步没有意义，直接单步（省掉 2.5 万次 rollout）
    let want_ply = if want_ply >= 2 {
        let probe_seed = 0x2B1E_0000_0000_0001u64;
        let mut probe = game.fork_for_rollout(probe_seed);
        if to_next_train(&mut probe, probe_seed)? {
            want_ply
        } else {
            1
        }
    } else {
        want_ply
    };
    let (rows, plan, rollouts, ply) = if want_ply >= 2 {
        // 2-ply：根动作价值 = 「下一步最优子动作」的期望分，子动作之间由 CRN 配对（见 two_ply_rows）
        let k: usize = std::env::var("MC_K")
            .ok()
            .and_then(|s| s.parse::<usize>().ok())
            .unwrap_or(512);
        let (rows, plan, total) = two_ply_rows(&game, &actions, k)?;
        if rows.iter().all(|r| r.3 == 0) {
            // 子局面全都推进不动（极端终局）：退回单步，别让面板拿到一排 0 分
            let (rows, total) = one_ply_rows(&game, &actions, search_n)?;
            (rows, Value::Null, total, 1usize)
        } else {
            (rows, plan, total, 2usize)
        }
    } else {
        let (rows, total) = one_ply_rows(&game, &actions, search_n)?;
        (rows, Value::Null, total, 1usize)
    };
    // 発表会兜底（旧「S2 排程规则」）已删除 —— 见 docs/PT缺口逐源_20261106.md §22 与 #97。
    // 三条实测理由：① t30 锚点上开着它和关掉它，三场发表会大成功率都是 150/150＝100%（没买到档）；
    // ② from-t1 锚点（#49）开着反而把 CP2 大成功率从 73.6% 压到 29.9%（McNemar p=0.0000），
    //    且每局改写 35 次落子（45% 的手），三分量显著更差；
    // ③ 真机侧对应物是 `auto_setting_info`（玩家**声明**的演目配置，#50），没有「逐回合改落位」的一手出处；
    //    而本剧本发表会**到回合必结算、未达标也发成功档**，
    //    ⇒ 不存在「不兜底就育成失败」这个前提。
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
            let Some(name) = c.get("train").and_then(|x| x.as_str()) else {
                continue;
            };
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
            let fail = c
                .get("failure_rate")
                .and_then(|x| x.as_f64())
                .unwrap_or(0.0);
            val += pt * 2.46 + vital * 0.35 - fail * 0.8;
            // #76：稽古候选过去也被写成「训练 X」，顶栏把稽古当训练报且与同轴训练重名；只改标签不动数值。
            let keiko = c
                .get("is_keiko")
                .and_then(|x| x.as_bool())
                .unwrap_or(false)
                || c
                    .get("command_id")
                    .and_then(|x| x.as_i64())
                    .is_some_and(|id| (5101..=5105).contains(&id));
            let idx = NAMES5.iter().position(|n| *n == name).unwrap_or(0) as u8;
            live.push((
                label_of(&if keiko { Action::Keiko(idx) } else { Action::Train(idx) }),
                val,
            ));
        }
        live.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    }

    let result = json!({
        "turn": turn,
        "ok": true,
        "engine": "umaai-matsuri-official",
        "ply": ply,
        "plan": plan,
        "deck_fallback": deck_fallback,
        "schedule": schedule_note,
        "ranked": ranked,
        "live": live.iter().map(|(l, sc)| json!({"label": l, "score": sc})).collect::<Vec<_>>(),
        "rollouts": rollouts
    });
    std::fs::write(&result_path, serde_json::to_string(&result)?)?;
    Ok(())
}
