# 面板后端接缝 · 2026-11-02

> 状态：**接缝已通，实测通过**。本文记录「黑板面板怎么接本仓引擎」「接在哪」「验过什么」「还欠什么」。

## 1. 接缝在哪

面板（`E:/HJM/jidianbeiAI0.22`，**不是** `祭典杯黑板_v1.0` —— 后者是另一份纯 Python 独立发行版）
与本仓引擎之间是**进程 + JSON 文件**，不是 HTTP：

```
面板 → out/_mc_state.json → matsuri-mccli.exe <state> <result> <n> → out/_mc_result_<turn>.json
面板 → out/_dist_state.json → matsuri-dist.exe  <state> <result>    → out/_dist_result_<turn>.json
```

- 两个 bin 共用 `crates/matsuri-cli/src/state.rs` 的装配，保证对「当前局面」的理解逐字一致。
- 面板 env：`MC_PLY=2` / `MC_K=1024` / `MC_SEED=0`；`argv[3]` 是推演次数
  （面板按剩余回合缩放：turn≥45 用 200，否则 `max(50, 200*turn/45)`）。
- 面板以 `cwd=<面板>/rust/` 启动后端，exe 按**相对路径** `data/` 读剧本资产
  ⇒ 发布目录必须同时含 exe 与 `data/`（与 `_ref01/matsuri-ref-01/rust/` 同布局）。

## 2. 发布

```powershell
cargo build --release -p matsuri-cli
python tools/publish_panel_backend.py            # → <面板>/rust/
```

发两个 exe（`matsuri-mccli.exe` / `matsuri-dist.exe`）+ 九个数据文件
（`ai_config.toml` / `cardDB.json` / `constants.json` / `events.json` / `manifest.json` /
`matsuri_config.toml` / `scenario_matsuri.json` / `text_data_dict.json` / `umaDB.json`）。
发布记录（commit + exe 校验和）写在 `<面板>/rust/backend_build.json`。

**发布前面板是坏的**：`paths.rust_exe()` 只找 `MATSURI_EXE_DIR` 与面板 `rust/` 目录，
两处都没有 ⇒ 官方后端定位失败 ⇒ 面板**悄悄退化成纯 Python worker**（`tools/mc_worker.py`），
面板上显示的还是它自己的规则引擎结果。

## 3. 两条线（重要）

| 线 | 面板后端 | 说明 |
|---|---|---|
| **我们自己的线** `backup/paired-gate-20261102`（HEAD `85539fd` = 步骤 1~10 完成） | ✅ 齐全 | `state.rs` / `mccli.rs` 都在，`Cargo.toml` 已声明 `matsuri-mccli` / `matsuri-dist`，对当前引擎仓 `a8859e0` 编译通过 ⇒ **接缝 = build + publish，不用移植** |
| 上游那条线 `feat/paired-gate-and-mechanism-fixes`（PR #2） | ❌ 空 | 没有 `mccli` / `state.rs`；`dist` 被改成单参、不读 state ⇒ 面板按老协议调会把结果写进 state 文件 |

**分支 `feat/panel-backend` 建的在我们自己的线上**（`git checkout -B feat/panel-backend backup/paired-gate-20261102`）。
以后要把面板切到上游模型版本，要重做一次装配（新模型的人头 / 演目 / 教诲账本全换了结构）；
**JSON 协议不变，所以届时只是换 exe**。

## 4. 验过什么

- `matsuri-mccli` 用面板真实状态文件 `out/_state_t15.json` 跑通：出 25600 次推演、5 个训练动作排名。
- `matsuri-dist` 同一文件跑通：产出三分量（`five_sum` mean 8342.7 / `pt` mean 5135.1）。
- `paths.rust_exe("matsuri-mccli")` → `(<面板>/rust/matsuri-mccli.exe, <面板>/rust/)`。
- **输入侧闭环**：面板 `_mc_state.json`（turn 78）与报文 `210203_0522_notify_response.bin` 逐项相等
  （五维 1957/1438/1624/1303/1532、PT 7654、体力 108、上限 2233/1852/1867/1700/1823、回合 78、马娘 106301）。
- 参考局终局报文 = `out/hachimi_raw/210234_0542_notify_response.bin`：五维 1998/1470/1656/1344/1573
  （和 8041）、PT 8339、上限 2233/1852/1867/1700/1823 —— 即门禁用的真值 run 179。

## 5. 报文链路（面板不动 ≠ 引擎问题）

hachimi（`E:/steam/.../UmamusumePrettyDerby_Jpn/hachimi/config.json`）两个键都会让面板收不到包：

| 键 | 症状 | 修法 |
|---|---|---|
| `load_libraries` | 游戏启动时会被写回 `[]` ⇒ 插件没加载 | 写回 `["hachimi\\hachimi_httpforward_plugin.dll"]` |
| `notifier_host` | 键被删 ⇒ 报文 POST 到默认端口 4693（URA），黑板在 4695 收不到 | 写 `http://127.0.0.1:4695` |

**2026-11-02 已写回 `notifier_host`（只插一个键、其余字节原样、备份 `config.json.bak_20261102_notifier`），
但必须重启游戏才生效。** 黑板接收器默认把报文转发到 4693，所以 URA 那边不受影响。

`tools/matsuri.py doctor` 第④项只查插件文件在不在，**查不出有没有被加载** ⇒ 那行绿灯不能当链路正常的证据。

## 6. 还欠什么（按优先级）

| # | 事项 | 说明 |
|---|---|---|
| 1 | **引擎机制残差** | 门禁基线：五维和 8379（+4.2%）/ 五维分 46297（+9.7%）/ **PT 5909（−29.1%）**。PT 缺口不在训练 PT，在剧本事件 PT（`single_mode_chara_effect` 只给 effect_group_id，收益表不在 master.mdb）。另缺：発表会三样（ファン+1000 / 理事長絆+15 / 稽古强化）、URA 收尾期 t68-77 的「演目+1 / 体力+30 / 干劲+1 / 失败率0」 |
| 2 | **288 张真机截图对账到回合级** | **2026-11-02 已做**：`tools/reconcile_ocr.py`（三路 OCR：整条×亮度掩膜、整条×颜色掩膜、逐格紧裁切 → 五维+PT 与 `_cand_cache.json` 的 355 份报文逐格比对）。结果 **exact 147 / noread 135 / nomatch 6**，其中 128 张定到唯一回合、与上传时间推定 100% 一致。产物：同目录 `对账结果_回合级.md` / `.json` / `_done.json`（可续跑）。**范围只到 t28**，t29–78 无截图。两个新发现：① 282–287（19:15:43）是 t28 打完「速度Lv2」后的状态（速 +62 力 +18），报文表里查无 ⇒ 就是缺的 **t29**，且 PT 实到 +62 vs 预览 +52，差 10 **未解释**；② 这 6 张的上传时间推定给 t28、实际在 t28 之后 ⇒ **补发场景下「取之前最近报文」会错一个回合**，与 `索引.md` 的警告一致 |
| 3 | **面板 `master_db` 路径** | 面板 `config.json` 指 `D:\Downloads\master.mdb`（Downloads 会被清）；稳定副本 `E:/HJM/_ref01/matsuri-ref-01/data/master.mdb`（同尺寸）。面板无 git，改前先备份 |
| 4 | **决策质量度量** | 面板顶栏「建议」是混合逻辑：规则引擎（`matsuri_ai`）与 MCTS 不一致时以 MCTS 为准。我们还没做「决策好不好」的度量 |
| 5 | 分支提交 | `feat/panel-backend` 上的 `tools/publish_panel_backend.py` + README 段**未提交**（按仓里 Git 规则等用户确认） |

## 7. 报文字段名（`chara_info`，踩过）

五维 = `speed` / `stamina` / `power` / **`guts`（根性）** / **`wiz`（智力）**；
体力 = **`vital`**（`stamina` 是耐力）；PT = `skill_point`；
上限 = `max_speed` / `max_stamina` / `max_power` / `max_guts` / `max_wiz`
（`default_max_*` 是基础上限 1200，**不是**本局上限）；
`card_id` = 马娘，`single_mode_chara_id` = 角色（参考局 = 179）。

## 8. 群里的 `Matsuri_Sim_Test.rar` = 上游构建，不是第三方（2026-11-02 查明）

- 8 个数据文件（`scenario_matsuri.json` / `ai_config.toml` / `matsuri_config.toml` / `events.json` / `constants.json` / `umaDB.json` / `cardDB.json` / `text_data_dict.json`）与 `upstream/main @ 80b72f4` **git hash 逐字节相同** ⇒ 就是作者（xulai1001）今天 18:08 打的那版 exe，**没有仓外的新东西**。exe 未运行（只读字节）；要看内容一律走 `git fetch upstream`，别靠二进制。
- `git fetch upstream main` 已做，`ea5f9ad..80b72f4` 共 **7 个新提交**，正压在我们的缺口上：
  - `9cee735` **S7 手写训练员**（教诲分配走 Trainer + `pt_favor` 目标口径 + 配置旋钮）⇒ 就是我们的「决策层无打分函数」
  - `5fea7d7` URA 口径（训练限定效果 72–77 / 68–71 空窗期 / hint 入账）、`4ec3428` URA 期全变稽古
  - `fc70b9f` 追加稽古 + 友人·理事长·记者落位修正（基准种子 61444）、`80b72f4` 训练位社交人头唯一性
  - `9327da3` **S8 View = 回合信息完整快照 + diag 埋点**（面板接缝直接要这个）
  - `8aa80ac` 守门测试（普通训练 Lv 计数、剧本结算加成叠加）
  - 模块比本地多：上游 `game/` = **14 个文件**（多出 `advice / deck / diag / distribute / event / person / roster / settle / train / view.rs`），本地只剩 4 个（`action / mod / stage / traits`）——`b65e6f4` 那次合并只保留了本仓独有修正，没跟它的拆分。
  - **分叉程度**：`HEAD..FETCH_HEAD` = 14 个提交不在我们线上，`FETCH_HEAD..HEAD` = 56 个提交是我们独有的（`ea5f9ad..80b72f4` 只是上游内部看到的 7 个）。合并是取交集的手工活，不是 fast-forward。
- 上游配置里可当**线索**（≠结论，仍需 master.mdb / 报文背书）：
  - 与我们**已真机实证**重合的：発表会 #1 大成功 = 五维 +5 / PT +200 / 理事長絆 +15 ✓；演目上限 6 / 带友人 10 ✓；目标赛演目槽 +4 ✓；転成 year2 101←601、201←701 ✓。**但 `training_level_up: 1` 和我们真机的「5 个训练 Lv 各 +1」不是一个口径**（1 = 触发一次？还是只 +1 级？），读源码前当未知。
  - 他们**补上我们缺的**：`check_point_rewards` 6 行（ファン数没有，理事長絆 / 演目负责人 / ヒント技名+Lv 有）、`career_end_rewards` 2 行、`finals_act = {vital 30, motivation 1, fail_rate_drop 100, extra_member 1}`、`check_points` 累计 buff（deyilv 20/40/60/80 + hint_rate 0→150）、`advice_windows [2,11,23,35,47,59]`、`acts` 的 `npc_chara_id`、`rules.friendship_gain{friend 4, card 7, lesson 3, charm 2}`、`[trainer]` 权重表（vital 1.2 / motivation 30 / friendship 0.6 / hint 1 / gauge 25 / act_level 120 / checkpoint 800 / fat 120）
  - **别直接搬的两处**：① `training.keiko` Junior 主属性 16/14/14/15/12 = 就是 `日文调查备忘 20261101` 那份 memo 的数，我们真机读数 11~17 对不上 ⇒ 上游也引的二手，不是独立证据；② `limits.five_status_limit_base [2200,1850,1800,1700,1800]` 与我们报文口径的 `default_max_* = 1200` 不在同一层，语义没钉死前不接。
- 解压副本在 `D:/Downloads/_simtest_ref/Matsuri_Sim_Test/`（临时，随时可删）。Windows 自带 `C:/Windows/System32/tar.exe`（bsdtar 3.8.4 / libarchive）能直接读 RAR5，**不用装 7-Zip**。
