import io

ROOT = r"E:/HJM/umaai-matsuri"

# ---------------- game/mod.rs ----------------
p = ROOT + "/crates/matsuri-core/src/game/mod.rs"
s = io.open(p, encoding="utf-8").read()

# 1) 教え type3 按百分比；advice_mods 返回 (倍率, 平值, 百分比)
s = s.replace(
    '''    fn advice_mods(&self, act: usize) -> (f64, i32) {
        let aid = self.advice_id.get(act).copied().unwrap_or(0);
        if aid <= 0 {
            return (1.0, 0);
        }''',
    '''    fn advice_mods(&self, act: usize) -> (f64, i32, f64) {
        let aid = self.advice_id.get(act).copied().unwrap_or(0);
        if aid <= 0 {
            return (1.0, 0, 0.0);
        }''', 1)
s = s.replace(
    '''        let mut gain = 0.0;
        let mut vital = 0;''',
    '''        let mut gain = 0.0;
        let mut vital = 0;
        let mut vital_pct = 0.0;''', 1)
s = s.replace(
    '''                3 => vital += e.value,
                _ => {}
            }
        }
        (1.0 + gain + self.gain_pct, vital)''',
    '''                3 => vital_pct += e.value as f64,
                _ => {}
            }
        }
        (1.0 + gain + self.gain_pct, vital, vital_pct)''', 1)

# do_train 调用点
s = s.replace(
    '''        let (mult, vital_delta) = self.advice_mods(train);
        let vs = &self.data.value_scale;''',
    '''        let (mult, vital_delta, vital_pct) = self.advice_mods(train);
        let vital_delta = vital_delta + (value.vital as f64 * vital_pct / 100.0) as i32;
        let vs = &self.data.value_scale;''', 1)
# do_keiko 调用点
s = s.replace(
    '''        let (mult, vital_delta) = self.advice_mods(idx);
        let vs = &self.data.value_scale;''',
    '''        let (mult, vital_delta, vital_pct) = self.advice_mods(idx);
        let vital_delta = vital_delta + (value.vital as f64 * vital_pct / 100.0) as i32;
        let vs = &self.data.value_scale;''', 1)

# 2) 稽古失败不升 Lv / 不清 gauge
s = s.replace(
    '''        self.act_gauge[idx] = 0;
        self.act_level[idx] = (self.act_level[idx] + 1).min(10);
        Ok(())''',
    '''        if !failed {
            self.act_gauge[idx] = 0;
            self.act_level[idx] = (self.act_level[idx] + 1).min(10);
        }
        Ok(())''', 1)

# 3) 终局技能分：引擎已按 pt_score_rate=2.0 计一次，这里只补到 2.46 的差额
s = s.replace(
    '''                    let curve = 45000.0_f64.min(pt * 2.46);''',
    '''                    // 引擎 score_parts 已计 pt_score_rate(2.0)，补 0.46 到官方 2.46/点
                    let curve = 45000.0_f64.min(pt * 0.46);''', 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("mod.rs 已修：教え百分比 / 稽古失败 / PT 重复计分")

# ---------------- mccli.rs ----------------
p = ROOT + "/crates/matsuri-cli/src/mccli.rs"
s = io.open(p, encoding="utf-8").read()

# 4) 1 基回合 → 0 基
s = s.replace(
    '''    let turn = v.get("turn").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
    game.base.turn = turn.clamp(0, 77);''',
    '''    let turn = v.get("turn").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
    // 面板 turn 是 1 基（chara_info.turn），引擎是 0 基
    let panel_turn = turn;
    game.base.turn = (turn - 1).clamp(0, 77);''', 1)

# 5) 突破字段名
s = s.replace(
    '''                let mut rank = c
                    .get("limit")
                    .and_then(|x| x.as_i64())
                    .unwrap_or(4)''',
    '''                let mut rank = c
                    .get("limit_break")
                    .or_else(|| c.get("limit"))
                    .and_then(|x| x.as_i64())
                    .unwrap_or(4)''', 1)

# 6) card_type_count 先清零
s = s.replace(
    '''        if real.len() >= 5 {
            for c in &real {
                game.base.card_type_count[(c.card_type.clamp(0, 6)) as usize] += 1;
            }''',
    '''        if real.len() >= 5 {
            game.base.card_type_count = [0; 7];
            for c in &real {
                game.base.card_type_count[(c.card_type.clamp(0, 6)) as usize] += 1;
            }''', 1)

# 7) 教え id 回填 + 人头分布回填（放在 acts 覆盖之后）
s = s.replace(
    '''                game.act_level[i] = a.get("level").and_then(|x| x.as_i64()).unwrap_or(1) as i32;
                game.act_gauge[i] = a.get("gauge").and_then(|x| x.as_i64()).unwrap_or(0) as i32;''',
    '''                game.act_level[i] = a.get("level").and_then(|x| x.as_i64()).unwrap_or(1) as i32;
                game.act_gauge[i] = a.get("gauge").and_then(|x| x.as_i64()).unwrap_or(0) as i32;
                // 教え：报文里带 advice_id 就回填，否则保持 0
                if let Some(aid) = a.get("advice_id").and_then(|x| x.as_i64()) {
                    game.advice_id[i] = aid as i32;
                    game.advice_level[i] = game.act_level[i].clamp(1, 10);
                }''', 1)

# 人头分布：persons.train_type → distribution
s = s.replace(
    '''    let actions = game.list_actions()?;''',
    '''    // 人头分布（count_training_persons 依赖它，空 vec 会按 0 人结算）
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

    let actions = game.list_actions()?;''', 1)

# 8) 排序 unwrap → unwrap_or(Equal)
s = s.replace(
    '''    rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());''',
    '''    rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));''', 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("mccli 已修：回合基/突破字段/类型计数/教え/分布/排序")

# ---------------- cardDB: 30323 类型 ---------------- 
import json
p = ROOT + "/data/cardDB.json"
db = json.load(open(p, encoding="utf-8"))
if "30323" in db:
    db["30323"]["cardType"] = 5
    json.dump(db, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("cardDB 30323 cardType=5（友人）")

p = ROOT + "/scripts/add_card_from_wiki.py"
s = io.open(p, encoding="utf-8").read()
s = s.replace('"cardType": 2,', '"cardType": 5,', 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("脚本已同步 cardType=5")
