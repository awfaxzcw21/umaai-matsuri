//! `matsuri query`：剧本事实层的只读问答口子
//!
//! 给「只读代码、不吃报文」的使用方（含另一条线的 AI）一条不用翻 `docs/` 的入口。
//! **运行时读盘、不 embed**：embed 会让 exe 落后于文档，正是面板那条线踩过的坑。
//!
//! 用法（从仓根跑）：
//!   matsuri query                # 目录：编号 + 条目 + 来源等级
//!   matsuri query 1200           # 关键词命中哪几条
//!   matsuri query --misread      # 三条必读误读纠正
//!   matsuri query --all          # 全部条目 + 缺口
//!   matsuri query --json 赛后    # 机读输出
//!
//! 铁律：**来源等级永远跟着值一起打印**；条目没标就打成「未标注（不许当一手）」。

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, anyhow, bail};
use serde_json::Value;

/// 事实层文件（相对仓根）
pub const FACT_FILE: &str = "docs/剧本事实表_20261103.json";

/// 来源等级缺失时的固定话术——不是兜底文案，是防「把文档层当一手」的闸门
const UNSOURCED: &str = "未标注（不许当一手）";

/// 从当前目录往上找事实层（最多四跳，覆盖 `cargo run` 落在 crate 目录的情形）
pub fn locate() -> Result<PathBuf> {
    let rel = Path::new(FACT_FILE);
    let mut dir = std::env::current_dir().context("取当前目录失败")?;
    for _ in 0..5 {
        let cand = dir.join(rel);
        if cand.is_file() {
            return Ok(cand);
        }
        if !dir.pop() {
            break;
        }
    }
    Err(anyhow!(
        "找不到事实层 {FACT_FILE}：本命令必须在仓根跑（cargo run -p matsuri-cli --bin matsuri -- query …）"
    ))
}

fn load() -> Result<Value> {
    let path = locate()?;
    let text =
        std::fs::read_to_string(&path).with_context(|| format!("读 {} 失败", path.display()))?;
    serde_json::from_str(&text).with_context(|| format!("{} 不是合法 JSON", path.display()))
}

fn arr<'a>(root: &'a Value, key: &str) -> &'a [Value] {
    root.get(key)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}

/// 数组 / 对象按缩进 JSON 呈现，字符串原样
fn as_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => serde_json::to_string_pretty(other).unwrap_or_else(|_| other.to_string()),
    }
}

fn field<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    v.get(key).filter(|x| !x.is_null())
}

fn text_of(v: &Value, key: &str) -> String {
    field(v, key).map(as_text).unwrap_or_default()
}

/// 一条记录里所有可搜文本（含键名与证据指针），供关键词命中
fn hit(v: &Value, kw: &str) -> bool {
    v.to_string().to_lowercase().contains(&kw.to_lowercase())
}

fn print_entry(e: &Value) {
    let src = field(e, "来源等级")
        .map(as_text)
        .unwrap_or_else(|| UNSOURCED.to_string());
    println!(
        "[{}] {}    来源等级：{src}",
        text_of(e, "编号"),
        text_of(e, "条目")
    );
    println!("  值：{}", as_text(field(e, "值").unwrap_or(&Value::Null)));
    for key in ["口径", "未接进盘", "常见误读"] {
        if let Some(x) = field(e, key) {
            println!("  {key}：{}", as_text(x));
        }
    }
    if let Some(x) = field(e, "证据") {
        println!("  证据：");
        for p in x.as_array().into_iter().flatten() {
            println!("    · {}", as_text(p));
        }
    }
    println!();
}

fn print_misread(m: &Value) {
    println!("[{}] {}", text_of(m, "编号"), text_of(m, "错法"));
    println!("  正确：{}", text_of(m, "正确"));
    println!("  危害：{}\n", text_of(m, "危害"));
}

fn print_gap(g: &Value) {
    println!(
        "缺口：{} ｜ 关联 {} ｜ {}",
        text_of(g, "项"),
        text_of(g, "任务"),
        text_of(g, "态度")
    );
}

fn run_parsed(args: &[String]) -> Result<()> {
    let mut json = false;
    let mut all = false;
    let mut misread = false;
    let mut kws: Vec<String> = Vec::new();
    for a in args {
        match a.as_str() {
            "--json" => json = true,
            "--all" => all = true,
            "--misread" => misread = true,
            "--help" | "-h" | "help" => {
                println!("用法: matsuri query [--all] [--misread] [--json] [关键词]");
                println!("事实层: {FACT_FILE}（须在仓根跑；来源等级始终随值一起打印）");
                return Ok(());
            }
            other if other.starts_with('-') => {
                bail!("未知选项 {other}（可选 --all / --misread / --json）");
            }
            other => kws.push(other.to_string()),
        }
    }
    // 只认一个关键词：多个会被后者静默覆盖，那会给出误导性的「零命中」
    let keyword: Option<String> = match kws.as_slice() {
        [] => None,
        [k] => Some(k.clone()),
        many => bail!(
            "只接受一个关键词，给了 {} 个：{}",
            many.len(),
            many.join(" / ")
        ),
    };

    let root = load()?;
    let entries = arr(&root, "条目");
    let misreads = arr(&root, "误读纠正");
    let gaps = arr(&root, "缺口登记");

    if json {
        let out: Value = match &keyword {
            Some(k) => Value::Array(
                entries
                    .iter()
                    .chain(misreads.iter())
                    .chain(gaps.iter())
                    .filter(|v| hit(v, k))
                    .cloned()
                    .collect(),
            ),
            None if misread => Value::Array(misreads.to_vec()),
            None => root.clone(),
        };
        println!("{}", serde_json::to_string_pretty(&out)?);
        return Ok(());
    }

    println!(
        "剧本事实层：{} 条 ｜ 误读纠正 {} 条 ｜ 缺口 {} 项（{}）",
        entries.len(),
        misreads.len(),
        gaps.len(),
        locate()?.display()
    );

    if misread {
        for m in misreads {
            print_misread(m);
        }
        return Ok(());
    }

    if let Some(k) = &keyword {
        let found_entries: Vec<&Value> = entries.iter().filter(|v| hit(v, k)).collect();
        let found_mis: Vec<&Value> = misreads.iter().filter(|v| hit(v, k)).collect();
        let found_gap: Vec<&Value> = gaps.iter().filter(|v| hit(v, k)).collect();
        if found_entries.is_empty() && found_mis.is_empty() && found_gap.is_empty() {
            println!("关键词「{k}」在事实层里**零命中**。");
            println!(
                "零命中不等于「没有这个机制」：事实层没写的东西一律属未定，不许猜、不许用拟合系数补。"
            );
            return Ok(());
        }
        println!(
            "命中「{k}」：条目 {} / 误读 {} / 缺口 {}\n",
            found_entries.len(),
            found_mis.len(),
            found_gap.len()
        );
        for m in found_mis {
            print_misread(m);
        }
        for e in found_entries {
            print_entry(e);
        }
        for g in found_gap {
            print_gap(g);
        }
        return Ok(());
    }

    if all {
        for m in misreads {
            print_misread(m);
        }
        for e in entries {
            print_entry(e);
        }
        for g in gaps {
            print_gap(g);
        }
        return Ok(());
    }

    println!("目录（关键词查询会连同来源等级一起打印）：");
    for e in entries {
        let src = field(e, "来源等级")
            .map(as_text)
            .unwrap_or_else(|| UNSOURCED.to_string());
        println!(
            "  [{}] {:<16} 来源等级：{src}",
            text_of(e, "编号"),
            text_of(e, "条目")
        );
    }
    let ids: Vec<String> = misreads.iter().map(|m| text_of(m, "编号")).collect();
    println!("\n先读这三条误读纠正：{}", ids.join(" / "));
    println!("（`matsuri query --misread` 看全文）");
    Ok(())
}

pub fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(2).collect();
    run_parsed(&args)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 测试用路径：不依赖当前工作目录（CI 从 crate 目录跑）
    fn fact_path() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../")
            .join(FACT_FILE)
    }

    fn fact() -> Value {
        let text = std::fs::read_to_string(fact_path()).expect("事实层文件在盘上");
        serde_json::from_str(&text).expect("事实层是合法 JSON")
    }

    #[test]
    fn fact_entries_carry_source_level_and_evidence() {
        let root = fact();
        let entries = arr(&root, "条目");
        assert!(entries.len() >= 8, "条目数异常：{}", entries.len());
        for e in entries {
            let id = text_of(e, "编号");
            assert!(
                field(e, "来源等级").is_some(),
                "{id} 缺来源等级 ⇒ 会被当一手用"
            );
            assert!(!arr(e, "证据").is_empty(), "{id} 缺证据指针");
            assert!(!text_of(e, "常见误读").is_empty(), "{id} 缺常见误读");
            assert!(!text_of(e, "口径").is_empty(), "{id} 缺口径");
            assert!(!text_of(e, "值").is_empty(), "{id} 缺值");
        }
    }

    #[test]
    fn misread_corrections_are_three_and_complete() {
        let root = fact();
        let ms = arr(&root, "误读纠正");
        assert_eq!(ms.len(), 3);
        for m in ms {
            assert!(!text_of(m, "错法").is_empty());
            assert!(!text_of(m, "正确").is_empty());
            assert!(!text_of(m, "危害").is_empty());
        }
    }

    #[test]
    fn keyword_hits_the_expected_entries() {
        let root = fact();
        let entries = arr(&root, "条目");
        let by_kw = |kw: &str| -> Vec<String> {
            entries
                .iter()
                .filter(|v| hit(v, kw))
                .map(|v| text_of(v, "编号"))
                .collect()
        };
        assert!(by_kw("软上限").contains(&"F2".to_string()));
        assert!(by_kw("得意率").contains(&"F7".to_string()));
        assert!(by_kw("revise").contains(&"F3".to_string()));
        assert!(by_kw("race_ura").contains(&"F5".to_string()));
        assert!(by_kw("义务").contains(&"F10".to_string()));
        assert!(by_kw("这个机制不存在").is_empty(), "零命中必须真的零命中");
    }

    #[test]
    fn multiple_keywords_and_unknown_flags_are_rejected() {
        let err = run_parsed(&["速".into(), "耐".into()])
            .unwrap_err()
            .to_string();
        assert!(err.contains("只接受一个关键词"), "实际: {err}");
        let err = run_parsed(&["--bogus".into()]).unwrap_err().to_string();
        assert!(err.contains("未知选项"), "实际: {err}");
    }

    #[test]
    fn unsourced_entry_prints_the_gate_text() {
        let bare = serde_json::json!({"编号": "F99", "条目": "裸条目", "值": {}});
        assert!(field(&bare, "来源等级").is_none());
        let shown = field(&bare, "来源等级")
            .map(as_text)
            .unwrap_or_else(|| UNSOURCED.to_string());
        assert_eq!(shown, "未标注（不许当一手）");
    }
}
