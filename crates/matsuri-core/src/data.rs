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
    /// 転成（融合）效果（master.mdb `single_mode_15_fusion_effect`，9 行）
    #[serde(default)]
    pub fusion_effects: Vec<FusionEffect>,
    /// 融合授予（默认预设：玩家可选，配置给一组）
    #[serde(default)]
    pub fusion_grants: Vec<FusionGrant>,
    /// 伝受回合（0 基）
    #[serde(default)]
    pub advice_grant_turns: Vec<i32>,
    /// 每次伝受分配的教え id 列表
    #[serde(default)]
    pub advice_grant_order: Vec<Vec<i32>>,
    /// 発表会
    #[serde(default)]
    pub check_points: Vec<CheckPointDef>,
    /// 数值缩放（**已停用**，2026-11-01 步骤 2）
    ///
    /// 训练 / 稽古结算不再读它：下段改用引擎 `default_calc_training_value` 原样
    /// （§3.3 残差分析逐格命中面板真值，见 `docs/TODO_20261101_pm.md` 步骤 2）。
    /// 字段保留只为旧诊断脚本仍能读写同一份 JSON；**不要再往里填系数**。
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
    /// 自选比赛（目標外比赛）窗口：哪个回合能报名、几场、什么等级
    ///
    /// 2026-11-01 报文实证：面板回合 45 / 69 各 3 场、全部 G1、需 12,000 粉。
    /// 空表 = 不建模自选比赛（引擎不会给出「报名自选比赛」这个动作）。
    #[serde(default)]
    pub free_races: Vec<FreeRaceDef>,
    /// 属性软上限（报文 `chara_info.default_max_*`，各局恒 1200）
    ///
    /// 增量中**落在该值以上的部分减半**（向下取整），不是「超过就整条减半」：
    /// pre=1195 / 满增量 30 → 实得 5 + 12 = 17（cid168 t76 URA 準決勝 wiz 实测）。
    /// `0` = 关闭。
    #[serde(default = "default_soft_cap")]
    pub soft_cap: i32,
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
    /// 目標外 GI / GII / GIII（`null` = 无干净样本，未定）
    ///
    /// GII 留 `null`：memo 只列了 GI（1 ステ +10 / PT +45）与 GIII（1 ステ +8 / PT +35），
    /// GII 没有样本 —— 「未知」不是「与目標賽同」。
    #[serde(default)]
    pub non_target: Vec<Option<RaceRewardDef>>,
}

/// 自选比赛（目標外比赛）窗口
///
/// 2026-11-01 报文实证（34 局 `race_random_program_array` 全一致）：可报名窗口在
/// **面板回合 45 / 69**（引擎 0 基回合 = 面板 − 1 ⇒ 44 / 68），每窗口 3 场，
/// 全部 G1（master.mdb `race.grade = 100`），报名需 12,000 粉。
/// 剧本 15 的窗口只落在 11 月前半 ⇒ 池子里没有 GIII ⇒ GIII 档在本剧本**不可达**。
#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FreeRaceDef {
    /// 引擎回合（0 基；= 面板回合 − 1）
    pub turn: i32,
    /// 本窗口可报名的场数（真机恒 3）
    pub count: i32,
    /// 比赛等级（1 = G1 … 4 = OP，与引擎 `race_grades` 同口径）
    pub grade: i32,
}

/// 教え效果
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct AdviceEffect {
    pub advice_id: i32,
    pub level: i32,
    /// 条件类型（master.mdb `condition_type`）：0 = 无条件，2 = 体力阈值，3 = 友情非发生
    #[serde(default)]
    pub condition_type: i32,
    /// 条件值（`condition_value`）：体力阈值等
    #[serde(default)]
    pub condition_value: i32,
    pub effect_type: i32,
    /// 效果值（master.mdb `effect_value_4`；其余三列在全部 153 行里都与它相同或为 0）
    pub value: i32,
}

/// 転成（融合）效果：master.mdb `single_mode_15_fusion_effect`（9 行）
///
/// 融合后**基础教诲的等级效果整条被这张表的固定档替掉**（与演目 Lv 无关，约等于 Lv10），
/// 所绑定的新教诲效果再叠加其上。
#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
pub struct FusionEffect {
    /// 被融合的**基础教诲** id（101 / 201 / 301 / 401 / 501）
    pub advice_id: i32,
    /// 条件类型（同 `AdviceEffect::condition_type`）
    #[serde(default)]
    pub condition_type: i32,
    /// 条件值
    #[serde(default)]
    pub condition_value: i32,
    pub effect_type: i32,
    /// 效果值（master.mdb `effect_value_4`）
    pub value: i32,
}

/// 融合授予：某个回合把哪个新教诲配出去（玩家选择，配置给一组默认预设）
///
/// 引擎按**顺序**把新教诲发给「还没被融合的基础教诲」所在的槽位（memo §4.1：
/// turn 23 融合 2 个、turn 47 融合剩余 3 个），所以预设里只写新教诲与回合。
#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
pub struct FusionGrant {
    /// 融合发生的回合（0 基：23 = 第二年、47 = 第三年）
    pub turn: i32,
    /// 新教诲 id（601 / 701 / 801 / 901 / 1001）
    pub fusion_id: i32,
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
    /// 得意率累计总量（type 4，%）。
    ///
    /// **不是增量**：master.mdb `single_mode_15_check_point_effect` 同一 schedule 的
    /// 「成功 / 大成功」两档给的是**该场发表会之后**的累计值（成功档 = 上一场大成功的值）。
    /// 引擎目前没有落位概率模型（`deyilv` 仍是占位），暂按「训练效果%」近似处理。
    #[serde(default)]
    pub gain_pct: i32,
    /// 友情ボーナス累计总量（type 202，%）：作用于**全部友情训练**（不只演目排练）。
    #[serde(default)]
    pub friend_pct: i32,
    /// ヒント発生率累计总量（type 2，%）：引擎暂无提示/技能模型 ⇒ **只记录不生效**。
    #[serde(default)]
    pub hint_pct: i32,
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
        for fr in &self.free_races {
            ensure!(fr.turn >= 0, "free_races 的回合不能为负: {}", fr.turn);
            ensure!(fr.count > 0, "free_races[turn={}] 的场数必须 > 0", fr.turn);
            ensure!(
                (1..=4).contains(&fr.grade),
                "free_races[turn={}] 的等级必须在 1..=4（1=G1），实际 {}",
                fr.turn,
                fr.grade
            );
            // 等级 → `non_target` 表位（1=G1 ⇒ 下标 0）：档位是 `null` = 未知，
            // 未知不许猜成 0 奖励 —— 直接在载入期报错。
            ensure!(
                self.race_reward
                    .non_target
                    .get((fr.grade - 1) as usize)
                    .is_some_and(|row| row.is_some()),
                "free_races[turn={}] 用到等级 {}，但 race_reward.non_target 里没有该档的奖励",
                fr.turn,
                fr.grade
            );
        }
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

    #[test]
    fn real_asset_training_table_matches_memo_lv1_5() {
        // memo 2026-11-01 版 L38-64（普通训练基础值 Lv1-5），Lv1 行与 master.mdb
        // single_mode_training_effect 逐格互证通过；列序：速/耐/力/根/智/技能Pt/体力
        let expect: Vec<Vec<[i32; 7]>> = vec![
            // 速度
            vec![
                [12, 0, 2, 0, 0, 7, -20],
                [13, 0, 2, 0, 0, 7, -21],
                [14, 0, 2, 0, 0, 7, -22],
                [15, 0, 3, 0, 0, 7, -24],
                [16, 0, 3, 0, 0, 7, -25],
            ],
            // 耐力
            vec![
                [0, 10, 0, 3, 0, 7, -20],
                [0, 11, 0, 4, 0, 7, -21],
                [0, 12, 0, 4, 0, 7, -22],
                [0, 13, 0, 5, 0, 7, -24],
                [0, 14, 0, 6, 0, 7, -25],
            ],
            // 力量
            vec![
                [0, 6, 9, 0, 0, 7, -21],
                [0, 6, 10, 0, 0, 7, -22],
                [0, 6, 11, 0, 0, 7, -23],
                [0, 7, 12, 0, 0, 7, -25],
                [0, 8, 13, 0, 0, 7, -25],
            ],
            // 毅力
            vec![
                [2, 0, 2, 12, 0, 7, -21],
                [2, 0, 2, 13, 0, 7, -22],
                [2, 0, 2, 14, 0, 7, -23],
                [3, 0, 2, 15, 0, 7, -25],
                [3, 0, 2, 16, 0, 7, -25],
            ],
            // 智力
            vec![
                [2, 0, 0, 0, 8, 6, 5],
                [2, 0, 0, 0, 9, 6, 5],
                [2, 0, 0, 0, 10, 6, 5],
                [3, 0, 0, 0, 11, 6, 5],
                [3, 0, 0, 0, 12, 6, 5],
            ],
        ];
        let data = MatsuriData::load(crate::testing::data_dir()).expect("真实剧本资产应可载入");
        assert_eq!(data.training_basic_value, expect);
    }

    #[test]
    fn real_asset_check_point_buffs_match_master_mdb() {
        // master.mdb `single_mode_15_check_point_effect`（20 行）：3 场发表会 × {成功, 大成功}，
        // 每一档给的是**该场之后的累计总量**（「成功」档 = 上一场「大成功」档的值）。
        // effect_type：4 得意率 / 202 友情ボーナス / 2 ヒント発生率（204 比赛加成在引擎里硬编码）。
        // (schedule, 成功(得意,友情,ヒント), 大成功(得意,友情,ヒント))
        type Buff3 = (i32, i32, i32);
        let expect: &[(i32, Buff3, Buff3)] = &[
            // (schedule, 成功(得意,友情,ヒント), 大成功(得意,友情,ヒント))
            (1, (20, 0, 20), (40, 5, 50)),
            (2, (40, 5, 50), (60, 10, 100)),
            (3, (60, 10, 100), (80, 20, 150)),
        ];
        let data = MatsuriData::load(crate::testing::data_dir()).expect("真实剧本资产应可载入");
        assert_eq!(data.check_points.len(), expect.len(), "发表会场次数");
        for cp in &data.check_points {
            let e = expect
                .iter()
                .find(|(s, _, _)| *s == cp.schedule)
                .unwrap_or_else(|| panic!("schedule {} 不在 master 表里", cp.schedule));
            assert_eq!(
                (
                    cp.success.gain_pct,
                    cp.success.friend_pct,
                    cp.success.hint_pct
                ),
                e.1,
                "schedule {} 成功档",
                cp.schedule
            );
            assert_eq!(
                (cp.great.gain_pct, cp.great.friend_pct, cp.great.hint_pct),
                e.2,
                "schedule {} 大成功档",
                cp.schedule
            );
        }
    }

    #[test]
    fn real_asset_advice_effects_match_master_mdb() {
        // master.mdb `single_mode_15_advice_effect`（153 行）：十种教诲逐级效果。
        // 列：(教诲 id, Lv, 条件类型, 条件值, 效果类型, 效果值=effect_value_4)
        // 条件类型：0 无条件 / 2 体力阈值（不屈）/ 3 友情非发生（助力）。
        type Row = (i32, i32, i32, i32, i32, i32);
        let expect: &[Row] = &[
            (101, 1, 0, 0, 13, 3),
            (101, 1, 0, 0, 16, 3),
            (101, 2, 0, 0, 13, 5),
            (101, 2, 0, 0, 16, 3),
            (101, 3, 0, 0, 13, 7),
            (101, 3, 0, 0, 16, 3),
            (101, 4, 0, 0, 13, 9),
            (101, 4, 0, 0, 16, 3),
            (101, 5, 0, 0, 13, 10),
            (101, 5, 0, 0, 16, 3),
            (101, 6, 0, 0, 13, 11),
            (101, 6, 0, 0, 16, 3),
            (101, 7, 0, 0, 13, 12),
            (101, 7, 0, 0, 16, 3),
            (101, 8, 0, 0, 13, 13),
            (101, 8, 0, 0, 16, 3),
            (101, 9, 0, 0, 13, 14),
            (101, 9, 0, 0, 16, 3),
            (101, 10, 0, 0, 13, 15),
            (101, 10, 0, 0, 16, 3),
            (201, 1, 0, 0, 4, 10),
            (201, 2, 0, 0, 4, 13),
            (201, 3, 0, 0, 4, 16),
            (201, 4, 0, 0, 4, 20),
            (201, 5, 0, 0, 4, 25),
            (201, 6, 0, 0, 4, 30),
            (201, 7, 0, 0, 4, 35),
            (201, 8, 0, 0, 4, 40),
            (201, 9, 0, 0, 4, 45),
            (201, 10, 0, 0, 4, 50),
            (301, 1, 0, 0, 2, 30),
            (301, 2, 0, 0, 2, 33),
            (301, 3, 0, 0, 2, 36),
            (301, 4, 0, 0, 2, 40),
            (301, 5, 0, 0, 2, 45),
            (301, 6, 0, 0, 2, 50),
            (301, 7, 0, 0, 2, 55),
            (301, 8, 0, 0, 2, 60),
            (301, 9, 0, 0, 2, 70),
            (301, 10, 0, 0, 2, 80),
            (401, 1, 0, 0, 20, 47),
            (401, 1, 3, 0, 203, 20),
            (401, 2, 0, 0, 20, 47),
            (401, 2, 3, 0, 203, 22),
            (401, 3, 0, 0, 20, 47),
            (401, 3, 3, 0, 203, 24),
            (401, 4, 0, 0, 20, 47),
            (401, 4, 3, 0, 203, 26),
            (401, 5, 0, 0, 20, 47),
            (401, 5, 3, 0, 203, 28),
            (401, 6, 0, 0, 20, 47),
            (401, 6, 3, 0, 203, 30),
            (401, 7, 0, 0, 20, 47),
            (401, 7, 3, 0, 203, 32),
            (401, 8, 0, 0, 20, 47),
            (401, 8, 3, 0, 203, 34),
            (401, 9, 0, 0, 20, 47),
            (401, 9, 3, 0, 203, 37),
            (401, 10, 0, 0, 20, 47),
            (401, 10, 3, 0, 203, 40),
            (501, 1, 0, 0, 3, -1),
            (501, 2, 0, 0, 3, -2),
            (501, 3, 0, 0, 3, -3),
            (501, 4, 0, 0, 3, -4),
            (501, 5, 0, 0, 3, -5),
            (501, 6, 0, 0, 3, -6),
            (501, 7, 0, 0, 3, -8),
            (501, 8, 0, 0, 3, -10),
            (501, 9, 0, 0, 3, -12),
            (501, 10, 0, 0, 3, -15),
            (601, 1, 0, 0, 13, 5),
            (601, 1, 0, 0, 206, 5),
            (601, 2, 0, 0, 13, 6),
            (601, 2, 0, 0, 206, 6),
            (601, 3, 0, 0, 13, 7),
            (601, 3, 0, 0, 206, 7),
            (601, 4, 0, 0, 13, 8),
            (601, 4, 0, 0, 206, 8),
            (601, 5, 0, 0, 13, 9),
            (601, 5, 0, 0, 206, 9),
            (601, 6, 0, 0, 13, 10),
            (601, 6, 0, 0, 206, 10),
            (601, 7, 0, 0, 13, 13),
            (601, 7, 0, 0, 206, 13),
            (601, 8, 0, 0, 13, 16),
            (601, 8, 0, 0, 206, 16),
            (601, 9, 0, 0, 13, 20),
            (601, 9, 0, 0, 206, 20),
            (601, 10, 0, 0, 13, 25),
            (601, 10, 0, 0, 206, 25),
            (701, 1, 0, 0, 8, 20),
            (701, 2, 0, 0, 8, 22),
            (701, 3, 0, 0, 8, 24),
            (701, 4, 0, 0, 8, 27),
            (701, 5, 0, 0, 8, 30),
            (701, 6, 0, 0, 8, 35),
            (701, 7, 0, 0, 8, 40),
            (701, 8, 0, 0, 8, 50),
            (701, 9, 0, 0, 8, 65),
            (701, 10, 0, 0, 8, 80),
            (801, 1, 0, 0, 18, 1),
            (801, 1, 0, 0, 202, 1),
            (801, 2, 0, 0, 18, 1),
            (801, 2, 0, 0, 202, 2),
            (801, 3, 0, 0, 18, 1),
            (801, 3, 0, 0, 202, 3),
            (801, 4, 0, 0, 18, 1),
            (801, 4, 0, 0, 202, 4),
            (801, 5, 0, 0, 18, 1),
            (801, 5, 0, 0, 202, 5),
            (801, 6, 0, 0, 18, 1),
            (801, 6, 0, 0, 202, 6),
            (801, 7, 0, 0, 18, 1),
            (801, 7, 0, 0, 202, 7),
            (801, 8, 0, 0, 18, 1),
            (801, 8, 0, 0, 202, 8),
            (801, 9, 0, 0, 18, 1),
            (801, 9, 0, 0, 202, 9),
            (801, 10, 0, 0, 18, 1),
            (801, 10, 0, 0, 202, 10),
            (901, 1, 2, 45, 205, 100),
            (901, 2, 2, 40, 205, 100),
            (901, 3, 2, 35, 205, 100),
            (901, 4, 2, 30, 205, 100),
            (901, 5, 2, 25, 205, 100),
            (901, 6, 2, 20, 205, 100),
            (901, 7, 2, 15, 205, 100),
            (901, 8, 0, 0, 203, 1),
            (901, 8, 2, 10, 205, 100),
            (901, 9, 0, 0, 203, 3),
            (901, 9, 2, 5, 205, 100),
            (901, 10, 0, 0, 203, 5),
            (901, 10, 2, 0, 205, 100),
            (1001, 1, 0, 0, 21, 1),
            (1001, 1, 0, 0, 203, 1),
            (1001, 2, 0, 0, 21, 2),
            (1001, 2, 0, 0, 203, 2),
            (1001, 3, 0, 0, 21, 3),
            (1001, 3, 0, 0, 203, 3),
            (1001, 4, 0, 0, 21, 4),
            (1001, 4, 0, 0, 203, 4),
            (1001, 5, 0, 0, 21, 5),
            (1001, 5, 0, 0, 203, 5),
            (1001, 6, 0, 0, 21, 8),
            (1001, 6, 0, 0, 203, 7),
            (1001, 7, 0, 0, 21, 11),
            (1001, 7, 0, 0, 203, 9),
            (1001, 8, 0, 0, 21, 15),
            (1001, 8, 0, 0, 203, 11),
            (1001, 9, 0, 0, 21, 20),
            (1001, 9, 0, 0, 203, 13),
            (1001, 10, 0, 0, 21, 25),
            (1001, 10, 0, 0, 203, 15),
        ];
        let data = MatsuriData::load(crate::testing::data_dir()).expect("真实剧本资产应可载入");
        assert_eq!(data.advice_effects.len(), expect.len(), "教诲效果行数");
        for (row, e) in data.advice_effects.iter().zip(expect) {
            assert_eq!(
                (
                    row.advice_id,
                    row.level,
                    row.condition_type,
                    row.condition_value,
                    row.effect_type,
                    row.value
                ),
                *e,
                "教诲 {} Lv{} 效果 {}",
                row.advice_id,
                row.level,
                row.effect_type
            );
        }
    }

    #[test]
    fn real_asset_fusion_effects_match_master_mdb() {
        // master.mdb `single_mode_15_fusion_effect`（9 行）：转成后基础教诲的固定档效果。
        // 列：(被融合的基础教诲 id, 条件类型, 条件值, 效果类型, 效果值)
        type Row = (i32, i32, i32, i32, i32);
        let expect: &[Row] = &[
            (101, 0, 0, 13, 15),
            (101, 0, 0, 17, 1),
            (201, 0, 0, 4, 50),
            (201, 0, 0, 202, 10),
            (301, 0, 0, 2, 80),
            (301, 0, 0, 19, 1),
            (401, 3, 0, 203, 20),
            (401, 0, 0, 20, 47),
            (501, 0, 0, 3, -15),
        ];
        let data = MatsuriData::load(crate::testing::data_dir()).expect("真实剧本资产应可载入");
        assert_eq!(data.fusion_effects.len(), expect.len(), "转成效果行数");
        for (row, e) in data.fusion_effects.iter().zip(expect) {
            assert_eq!(
                (
                    row.advice_id,
                    row.condition_type,
                    row.condition_value,
                    row.effect_type,
                    row.value
                ),
                *e,
                "转成效果 教诲 {} 效果 {}",
                row.advice_id,
                row.effect_type
            );
        }
    }
}
