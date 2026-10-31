//! 剧本资产（`scenario_matsuri.json`）：schema、载入与校验
//!
//! 剧本数据**不进引擎仓**：根目录由宿主注入（终端 / Web / Android 各按自家布局），
//! 本模块只负责「从给定位置读 + 校验」，不碰任何写死路径。
//!
//! 当前只演示最小字段；S2 后在这里补演目 / gauge / 教え / 合宿等剧本资产。

use std::path::Path;

use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use umaai_core::gamedata::EventData;

/// 剧本数据文件名（放在宿主注入的剧本数据根下）
pub const MATSURI_DATA_FILE: &str = "scenario_matsuri.json";

/// 剧本全部静态数据
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MatsuriData {
    /// 剧本 ID（骏大祭为 15）
    pub scenario_id: i32,
    /// S1 占位：剧本自持资源；S2 后替换为演目账本
    pub sample_resource: i32,
    /// 训练基础值表：`[训练 0-4][等级] -> [速 耐 力 根 智 PT 体力消耗]`
    pub training_basic_value: Vec<Vec<[i32; 7]>>,
    /// 演目稽古基础值（5 个演目，Junior 档 = 静态表 5101-5105）
    #[serde(default)]
    pub keiko_basic_value: Vec<[i32; 7]>,
    /// 演目稽古基础值 · **Classic 以后档**（memo §3.2；静态表只记了 Junior 档）
    ///
    /// 切换时点 = `KEIKO_LATE_TURN`（turn 23，Junior 进度汇报会后）。为空则一直用 Junior 档。
    #[serde(default)]
    pub keiko_basic_value_late: Vec<[i32; 7]>,
    /// 演目等级效果（官方 intake 3.3）
    #[serde(default)]
    pub act_level_effects: Vec<ActLevelEffect>,
    /// 研钻之教え「追加の稽古」发生率（官方 intake 3.4）
    #[serde(default)]
    pub kensan_rate: Vec<i32>,
    /// 教え效果（官方 intake 3.4）
    #[serde(default)]
    pub advice_effects: Vec<AdviceEffect>,
    /// 伝受回合（0 基）
    #[serde(default)]
    pub advice_grant_turns: Vec<i32>,
    /// 每次伝受分配的教え id 列表
    #[serde(default)]
    pub advice_grant_order: Vec<Vec<i32>>,
    /// 発表会
    #[serde(default)]
    pub check_points: Vec<CheckPointDef>,
    /// 数值缩放（锚定 action_log 实测均值）
    #[serde(default)]
    pub value_scale: ValueScale,
    /// 报文五维快照的补偿系数（3b）
    ///
    /// `train_stat_anchor` 直接吃报文 `stat_gains`（游戏算好的全加成真值），
    /// 但引擎 rollout 的训练/稽古次数与真实仍有差（引擎约 21~30 次 vs 真实 34 次），
    /// 所以留一个总量补偿旋钮：1.0 = 不补偿。
    #[serde(default = "one")]
    pub stat_anchor_scale: f64,
    /// 比赛后隐藏事件（bwiki 238 条，剧本 15 专属）
    #[serde(default)]
    pub hidden_events: Vec<HiddenEventDef>,
    /// 剧本 15 专属剧情事件选项（报文 harvest 解码的 57 组真实事件，id 9001-9058）
    ///
    /// 与引擎核心 `events.json` 的 `uma_events`（5001-5007 通用占位）并存：
    /// 每回合 `event_distribution` 的 uma 分支从「通用 + 剧本专属」合并池抽，
    /// 剧本专属事件带真实选项（属性 / 体力 / 干劲 / PT 组合），`player_select=true`。
    #[serde(default)]
    pub scenario_events: Vec<EventData>,
    /// 赛后奖励基准表（未乘任何加成）
    ///
    /// 2026-11-01 一手报文复算：赛后五维/PT 由「马娘固有赛后事件」给出，其**通用基准值**
    /// 为目標賽 五维各 +5 / PT +72、URA 五维各 +10 / PT +40·60·80。取代原先的
    /// `race_pt_target` / `race_pt_target_late` / `race_stat_gain`（那三个是拿参考局
    /// 实测值反推的拟合常量，不是机制）。
    #[serde(default)]
    pub race_reward: RaceRewardTable,
    /// 属性软上限（报文 `chara_info.default_max_*`，各局恒 1200）
    ///
    /// 增量中**落在该值以上的部分减半**（向下取整），不是「超过就整条减半」：
    /// pre=1195 / 满增量 30 → 实得 5 + 12 = 17（cid168 t76 URA 準決勝 wiz 实测）。
    /// `0` = 关闭。
    #[serde(default = "default_soft_cap")]
    pub soft_cap: i32,
    /// 早局（起点回合 < 29）的训练/稽古 PT 补偿系数
    ///
    /// 引擎 `train_pt`/`keiko_pt` 是按 t38 前后标定的：从早局起步时整体 PT 偏低约 20%，
    /// 而晚局起步已偏高，故只在起点回合 < 29（面板 t30 前）时放大。
    /// `1.0`（默认）= 不补偿。
    #[serde(default = "one")]
    pub early_pt_scale: f64,
    /// 训练/稽古接近上限的收益衰减半衰距离（剧本 15 专属防偏科机制）
    ///
    /// 引擎 `calc_training_value` 是静态表、不看当前属性/五维上限，导致搜索一直堆
    /// 已高的属性（面板实测速度练到接近上限还继续推荐，耐力/力量起不来）。
    /// 真实训练收益会随属性逼近上限而递减；本字段是衰减公式 `remain/(remain+K)` 的 K：
    /// 某维剩余空间等于 K 时，该维收益打 5 折。`0`（默认）= 不衰减。
    #[serde(default)]
    pub cap_decay: f64,
    /// 衰减系数下限（防止贴上限时收益完全归零）
    ///
    /// `1.0`（默认）= 不设下限（此时衰减公式在贴上限时趋近 0）。
    #[serde(default = "one")]
    pub cap_decay_floor: f64,
    /// 每次训练给「该训练位上每个人头」的羁绊增量（补真实「一起训练涨羁绊」）
    ///
    /// 引擎 `MatsuriGame::do_train` 覆写后不再涨羁绊，导致羁绊开局多少整局多少、
    /// 永远到不了 80 闪彩圈。真实每次一起训练该位人头羁绊约 +3~7。
    /// `0`（默认）= 不涨（保持旧行为）。
    #[serde(default)]
    pub friend_gain_per_train: i32,
    /// 新增彩圈（羁绊 ≥80 且得意）对训练收益的倍率增量（每人头）
    ///
    /// 锚点 `stat_gains` 是「起步回合」的游戏预测值，已含起步时已有的彩圈；本字段只对
    /// 「起步后新触发」的彩圈生效（增量 = 当前彩圈数 − 起步彩圈数），避免双重计算。
    /// 真实彩圈约 +20~30%。`0.0`（默认）= 不生效（保持旧行为）。
    #[serde(default)]
    pub shining_boost: f64,
    /// 凹羁绊窗口：前 N 回合优先练「得意且羁绊未满」的训练（模拟真人前期凹 80）
    ///
    /// `0`（默认）= 不启用凹羁绊。
    #[serde(default)]
    pub friend_window: i32,
}

/// 比赛后隐藏事件（bwiki「隐藏事件一览」，剧本 15 专属）
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
pub struct HiddenEventDef {
    /// 五维属性收益（速 / 耐 / 力 / 根 / 智）
    #[serde(default)]
    pub stat: [i32; 5],
    /// 技能点
    #[serde(default)]
    pub pt: i32,
    /// 体力（可为负）
    #[serde(default)]
    pub vital: i32,
}

#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
pub struct ValueScale {
    #[serde(default = "one")]
    pub train_stat: f64,
    #[serde(default = "one")]
    pub train_pt: f64,
    #[serde(default = "one")]
    pub keiko_stat: f64,
    #[serde(default = "one")]
    pub keiko_pt: f64,
}

fn one() -> f64 {
    1.0
}

fn default_soft_cap() -> i32 {
    1200
}

/// 单场赛后奖励基准：`stat` = 五维每维，`pt` = 技能点（都是**未乘加成**的原始值）
#[derive(Debug, Clone, Copy, Default, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RaceRewardDef {
    pub stat: i32,
    pub pt: i32,
}

/// 赛后奖励基准表
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RaceRewardTable {
    /// 目標賽（生涯赛）：五维各 +5 / PT +72
    #[serde(default)]
    pub target: RaceRewardDef,
    /// URA 予選 / 準決勝 / 決勝：五维各 +10 / PT +40 / +60 / +80
    #[serde(default)]
    pub ura: Vec<RaceRewardDef>,
    /// 目標外 GI / GII / GIII（**本轮不建模**，只留表位；`null` = 无干净样本，未定）
    #[serde(default)]
    pub non_target: Vec<Option<RaceRewardDef>>,
}

/// 教え效果
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct AdviceEffect {
    pub advice_id: i32,
    pub level: i32,
    pub effect_type: i32,
    pub value: i32,
}

/// 進捗発表会定义
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct CheckPointDef {
    pub schedule: i32,
    pub turn: i32,
    pub need: i32,
    pub success: BuffDef,
    pub great: BuffDef,
    /// 达标奖励：全能力 +N（官方発表会 t24/48/68 = 5/10/15）
    #[serde(default)]
    pub all_stat: i32,
    /// 达标奖励：技能点 +N（t24/48/68 = 200/300/400）
    #[serde(default)]
    pub pt: i32,
    /// 达标奖励：五维训练等级 +N（前两回各 +1）
    #[serde(default)]
    pub train_lv: i32,
    /// 达标奖励：体力 +N（本祭 t68 = 30）
    #[serde(default)]
    pub vital: i32,
    /// 达标奖励：干劲 +N（本祭 t68 = 1）
    #[serde(default)]
    pub motivation: i32,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct BuffDef {
    #[serde(default)]
    pub gain_pct: i32,
}

/// 演目等级效果（官方 intake 3.3）
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct ActLevelEffect {
    pub level: i32,
    /// 全基础 +N（105）
    pub all_base: i32,
    /// 友情加成 %（202）
    pub friend_pct: i32,
    /// SP 练习效果 %（201，编成卡类型 ≥5）
    pub sp_practice_pct: i32,
    /// SP 上限（109，编成卡类型 ≥5）
    pub sp_limit: i32,
    /// 基础上限（206）
    pub base_limit: i32,
}

impl MatsuriData {
    /// 从宿主注入的数据根载入 `scenario_matsuri.json`
    pub fn load(root: impl AsRef<Path>) -> Result<Self> {
        let path = root.as_ref().join(MATSURI_DATA_FILE);
        let text = std::fs::read_to_string(&path)
            .with_context(|| format!("读取剧本数据失败: {}", path.display()))?;
        Self::parse(&text).with_context(|| format!("剧本数据来源: {}", path.display()))
    }

    /// 解析并校验剧本数据（测试 / 内嵌场景复用）
    pub fn parse(text: &str) -> Result<Self> {
        let data: Self = serde_json::from_str(text).context("解析剧本数据失败")?;
        data.validate()?;
        Ok(data)
    }

    /// 载入期校验：剧本 ID 非负、五个训练、每训练至少一档、每档七值
    fn validate(&self) -> Result<()> {
        ensure!(self.scenario_id >= 0, "scenario_id 不能为负");
        ensure!(
            self.training_basic_value.len() == 5,
            "training_basic_value 应有 5 个训练，实际 {}",
            self.training_basic_value.len()
        );
        for (i, levels) in self.training_basic_value.iter().enumerate() {
            ensure!(!levels.is_empty(), "training_basic_value[{i}] 至少应有一档");
            for (level, row) in levels.iter().enumerate() {
                ensure!(
                    row.len() == 7,
                    "training_basic_value[{i}][{level}] 应有 7 个值，实际 {}",
                    row.len()
                );
            }
        }
        ensure!(
            self.race_reward.ura.is_empty() || self.race_reward.ura.len() == 3,
            "race_reward.ura 应为 3 档（予選/準決勝/決勝），实际 {}",
            self.race_reward.ura.len()
        );
        ensure!(
            self.race_reward.non_target.len() <= 3,
            "race_reward.non_target 最多 3 档（GI/GII/GIII），实际 {}",
            self.race_reward.non_target.len()
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"{
        "scenario_id": 15,
        "sample_resource": 3,
        "training_basic_value": [
            [[1, 0, 0, 0, 0, 0, -20]],
            [[0, 1, 0, 0, 0, 0, -20]],
            [[0, 0, 1, 0, 0, 0, -20]],
            [[0, 0, 0, 1, 0, 0, -20]],
            [[0, 0, 0, 0, 1, 0, 5]]
        ]
    }"#;

    #[test]
    fn parse_sample_ok() {
        let data = MatsuriData::parse(SAMPLE).expect("样例数据应可解析");
        assert_eq!(data.scenario_id, 15);
        assert_eq!(data.sample_resource, 3);
        assert_eq!(data.training_basic_value.len(), 5);
    }

    #[test]
    fn reject_wrong_training_count() {
        let text = SAMPLE.replace(
            r#"[[0, 0, 0, 0, 1, 0, 5]]
        ]"#,
            r#"[[0, 0, 0, 0, 1, 0, 5]]
        ,[[1, 0, 0, 0, 0, 0, -20]]]"#,
        );
        assert!(MatsuriData::parse(&text).is_err(), "六个训练应报错");
    }

    #[test]
    fn reject_bad_row_width() {
        let text = SAMPLE.replace("[1, 0, 0, 0, 0, 0, -20]", "[1, 0, 0, 0, 0, 0]");
        assert!(MatsuriData::parse(&text).is_err(), "六值行应报错");
    }
}
