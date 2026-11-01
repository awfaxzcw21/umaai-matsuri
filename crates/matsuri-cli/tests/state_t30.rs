//! t30 参考局回归：装配后的引擎训练值必须逐格命中面板真值
//!
//! fixture = 骏大祭 t30 面板快照（绝好调、训练 Lv2、马娘 106301）。
//! 断言口径 = **16 个非零分量**（5 训练 × 面板 `stat_gains` / `skill_point`）：
//! 引擎 `calc_training_value` 的输出要逐格等于游戏自己的预测值。
//!
//! 这条测试同时钉住三件装配事实：
//! - 马娘是报文里的 106301（成长率 `[0,0,0,20,10]`，不是示例马娘的 `[0,20,0,0,10]`）；
//! - 训练设施等级 Lv2 → 基础值表第 2 行；
//! - 人数加成 = 支援卡 + 嘉宾（剧本 NPC 人头枠 101..115 不计）。

use matsuri_cli::state::assemble_game;
use umasim::game::Game;

const TRAINS: [&str; 5] = ["速度", "耐力", "力量", "根性", "智力"];

/// 每个训练位的人数加成（卡 + 嘉宾）
const EXPECT_PERSONS: [usize; 5] = [3, 1, 4, 2, 2];

#[test]
fn t30_engine_value_matches_panel_truth() {
    // 装配读仓根 `data/`（卡表 / umaDB / 剧本资产），测试 cwd 在 crate 目录 → 先切仓根
    let root = matsuri_core::testing::repo_root();
    std::env::set_current_dir(&root).expect("切到仓根");
    let fixture = root.join("crates/matsuri-cli/tests/data/state_t30.json");
    let raw = std::fs::read_to_string(&fixture).expect("读 fixture");
    let panel: serde_json::Value = serde_json::from_str(&raw).expect("解析 fixture");

    let mut st = assemble_game(&raw).expect("装配对局");
    let g = &mut st.game;

    // 马娘：报文里的 106301（生野狄杜斯），成长率 [0,0,0,20,10]
    assert_eq!(g.uma().uma_id, 106301, "马娘应是报文里的 chara_id");
    assert_eq!(
        g.uma().five_status_bonus,
        [0, 0, 0, 20, 10],
        "成长率应跟报文马娘走"
    );

    let cmds = panel["commands"].as_array().expect("fixture 有 5 条命令");
    assert_eq!(cmds.len(), 5);
    let mut checked = 0usize;
    for (t, c) in cmds.iter().enumerate() {
        let name = c["train"].as_str().expect("训练名");
        assert_eq!(name, TRAINS[t], "命令顺序应与训练下标一致");
        assert_eq!(g.train_level(t), 2, "{name} 设施等级 Lv2");

        let buffs = g.calc_training_buff(t).expect("训练 buff");
        let value = g.calc_training_value(&buffs, t).expect("训练值");

        // 人数加成：支援卡 + 嘉宾；剧本 NPC 人头枠（101..115）不计
        assert_eq!(
            g.count_training_persons(t),
            EXPECT_PERSONS[t],
            "{name} 人数加成"
        );
        // 面板的 `persons` 是报文字段（原始 id 数），含 NPC 枠 → 比引擎人数多
        let raw_persons = c["persons"].as_i64().unwrap_or(0);
        assert!(
            raw_persons >= EXPECT_PERSONS[t] as i64,
            "{name} 面板 persons 不该少于引擎人数"
        );

        let gains = c["stat_gains"].as_object().expect("stat_gains");
        for (i, n) in TRAINS.iter().enumerate() {
            let truth = gains.get(*n).and_then(|x| x.as_i64()).unwrap_or(0) as i32;
            assert_eq!(value.status_pt[i], truth, "{name} 的 {n} 收益");
            if truth > 0 {
                checked += 1;
            }
        }
        let pt = c["skill_point"].as_i64().expect("skill_point") as i32;
        assert_eq!(value.status_pt[5], pt, "{name} 的 PT 收益");
        if pt > 0 {
            checked += 1;
        }
    }
    assert_eq!(checked, 16, "16 个非零分量应逐格命中面板真值");
}
