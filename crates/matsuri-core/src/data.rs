//! 剧本资产（`scenario_matsuri.json`）：schema、载入与校验
//!
//! 剧本数据**不进引擎仓**：根目录由宿主注入（终端 / Web / Android 各按自家布局），
//! 本模块只负责「从给定位置读 + 校验」，不碰任何写死路径。
//!
//! 当前只演示最小字段；S2 后在这里补演目 / gauge / 教え / 合宿等剧本资产。

use std::path::Path;

use anyhow::{Context, Result, ensure};
use serde::Deserialize;

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
    /// 演目稽古基础值（5 个演目）
    #[serde(default)]
    pub keiko_basic_value: Vec<[i32; 7]>,
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
