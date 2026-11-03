# umaai-matsuri（骏大祭剧本仓）

`umaai-next` 引擎仓的「朋、史解き ―午式駿大祭―」（**駿大祭編** / scenarioId **15**）独立实现仓。

- **开发代号**：`matsuri`（别名：mee-umasim 参考库用 `shuntaisai`，本仓统一用 `matsuri`）
- **形态**：workspace 五 crate（与 `umaai-onsen` 同构）——`matsuri-core`（机制 + 训练员 + 装配，
  宿主稳定面）/ `matsuri-sim`（模拟器与基线裁判）/ `matsuri-ai`（在线决策核心，骨架）/
  `matsuri-cli`（终端入口与手动训练员）/ `matsuri-tools`（测卡 / 复盘，骨架）。
- **依赖**：开发期以 path 引用 `../umaai_next` 的 `umaai-core` / `umasim` / `umaai`；稳定期换
  git tag。不 fork 引擎、不在引擎仓加剧本 crate。
- **数据**：引擎静态数据已拷入 `data/`；剧本资产 `scenario_matsuri.json` 与配置默认层
  `matsuri_config.toml` 由本仓自持，根目录由宿主注入。

## 当前状态

- **2026-11-03（凹 LF 线）：发表会语义已用报文钉死（#16）**——4 场（面板 t24/48/68/**78**）、
  档 = `min(全演目 Lv) ≥ need`（28/28、0 例外）、到回合必结算、第 4 场 buff 无一手行留 `null`；
  配对门禁 PT **+583.7/局**，但两臂同在锚点一侧 ⇒ **产出计、不可结论**。
  定案与未解释项见 [`docs/发表会档位一手_20261103.md`](docs/发表会档位一手_20261103.md)。
- **S1 建仓完成**：五 crate 改名、依赖路径、引擎静态数据、CI 与文档骨架已落地。
- **S0 机制与用语文档基本收口（2026-10-31）**：`single_mode_15_*` 11 张表与 `single_mode_training_effect`
  已解码；彩圈 +2、gauge 归属、合宿延长 / Lv+1、U18 延长回合、U33 合宿外出、友人分身、稽古基础值、
  **训练值两层口径**、目标赛 +4、`check_point_effect 204`、10 月前半 turn 66、排练失败仍 Lv+1 均已定为口径。
  剩余项：U02 Senior 第三档暂定、U19 延长段支援卡事件、同名双人头、成功档数值、NPC 不在率、
  追加稽古发生率 / 上下档等，见 [`docs/plan/stubs.md`](docs/plan/stubs.md)。
- **S2 数据层待启动**：解码结果待落成 `scenario_matsuri.json` 与 `data.rs`；资产中的
  `sample_resource` / `sample_order` 仍为 `STUB`，见 [`docs/plan/stubs.md`](docs/plan/stubs.md)。

## 结构

| crate / 文件 | 职责 |
|---|---|
| `crates/matsuri-core/src/lib.rs` | 模块声明 + 对外重导出 + `MAX_TURN` |
| `crates/matsuri-core/src/game/mod.rs` | 局面 `MatsuriGame` + 资源快照 `MatsuriView` + `build_game` |
| `crates/matsuri-core/src/game/stage.rs` | 阶段机 `Stage`（含 `StageId` 投影） |
| `crates/matsuri-core/src/game/action.rs` | `Action` + `Display` + `ActionEnum` |
| `crates/matsuri-core/src/game/traits.rs` | 契约 impl（`Game` / `TrainingValueSource` / `FlatSearchGame`） |
| `crates/matsuri-core/src/{data,config}.rs` | 剧本资产 / 剧本配置段 |
| `crates/matsuri-core/src/trainer.rs` | 样例训练员 `MatsuriTrainer` |
| `crates/matsuri-cli/src/main.rs` | 终端前端：bin `matsuri`（跑局） |
| `crates/matsuri-cli/src/manual.rs` | 无颜色手动训练员（机制核对） |
| `crates/matsuri-cli/src/gate.rs` | 配对门禁判官：bin `matsuri-gate`（CRN 同种子逐局配对，只认五维和 / 五维分 / PT） |
| `crates/matsuri-cli/src/{lib,state}.rs` | 面板状态 → 引擎局面的共享装配（`matsuri-mccli` / `matsuri-dist` 共用） |
| `crates/matsuri-cli/tests/state_t30.rs` | t30 参考局回归：引擎训练值逐格命中面板真值（fixture 在 `tests/data/`） |
| `crates/matsuri-cli/tests/gate_cli.rs` | 配对门禁判官端到端：自比 ⇒ 无显著差异 / 样本号不一致 ⇒ 报错 |
| `crates/matsuri-sim/src/bench.rs` | 配对门禁统计内核（bootstrap 区间 + 符号翻转置换检验，无 IO） |
| `crates/matsuri-sim` | 模拟器与基线裁判（骨架） |
| `crates/matsuri-ai` | 在线决策核心（骨架） |
| `crates/matsuri-tools` | 外围工具（骨架） |
| `tools/publish_panel_backend.py` | 把引擎后端发布到黑板面板的 `rust/` 目录（exe + `data/`） |
| `data/` | 引擎静态表 + 剧本资产 + 双配置默认层 |
| `docs/` | 本仓文档骨架 |

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/README.md`](docs/README.md) | 本仓文档索引与引擎文档入口 |
| [`docs/plan/scenario_dev_plan.md`](docs/plan/scenario_dev_plan.md) | 开发计划：阶段状态 / 决策记录 / 待决事项 |
| [`docs/plan/intake.md`](docs/plan/intake.md) | 接入包：情报位置 / 机制 / 数值 / 数据表 / 待确认 |
| [`docs/plan/stubs.md`](docs/plan/stubs.md) | 数值占位登记（`STUB` / `TBD`） |
| [`docs/mechanism.md`](docs/mechanism.md) | 机制文档（公式 / 流程 / 实现落点） |
| [`docs/glossary.md`](docs/glossary.md) | 术语单一事实源 + 口径决定记录 |
| [`docs/tests_overview.md`](docs/tests_overview.md) | 测试一览 |
| [`docs/handoff.md`](docs/handoff.md) | 交接提示词 |

## 运行与验收

```powershell
# 数据根是代码级常量 data/（crates/matsuri-cli/src/main.rs 的 DATA_ROOT）
cargo run --release --bin matsuri --            # 样例训练员
cargo run --release --bin matsuri -- random     # 引擎随机基线（对照用）
cargo run --release --bin matsuri -- manual     # 无颜色人工选择（机制核对用）

cargo fmt --all --check
cargo clippy --workspace --all-targets --offline -- -D warnings
cargo test --workspace --release --offline
```

### 面板后端（黑板面板的实时建议）

面板（`E:/HJM/jidianbeiAI0.22`）以**进程 + JSON 文件**接本仓引擎：

```
面板 → out/_mc_state.json → matsuri-mccli.exe <state> <result> <n> → out/_mc_result_<turn>.json
面板 → out/_dist_state.json → matsuri-dist.exe  <state> <result>    → out/_dist_result_<turn>.json
```

两个 bin 共用 `crates/matsuri-cli/src/state.rs` 的装配，保证对「当前局面」的理解逐字一致。
面板以 `cwd=<面板>/rust/` 启动后端，exe 按**相对路径** `data/` 读剧本资产 ⇒ 发布目录必须同时含 exe 与 `data/`。

```powershell
cargo build --release -p matsuri-cli
python tools/publish_panel_backend.py            # 发布到 <面板>/rust/
```

发布后 `paths.rust_exe()` 就能定位到后端（面板 `MATSURI_BACKEND=official` 走这条）；
发布记录（含 commit 与 exe 校验和）写在 `<面板>/rust/backend_build.json`。

### 配对门禁（机制改动的可判定性）

```powershell
# 改动前 / 改动后各出一份样本明细（同一 MC_SEED ⇒ 随机流对齐 ⇒ 逐局配对）
$env:MC_N=4000; $env:MC_SEED=0; $env:MC_DUMP="out/_dump_a.jsonl"
cargo run --release --bin matsuri-dist -- _state_t30.json out/_gate_a.json
$env:MC_DUMP="out/_dump_b.jsonl"
cargo run --release --bin matsuri-dist -- _state_t30.json out/_gate_b.json

# 判官：三个分量（真值 = 真实局 run 179）各自判「改动后是否显著更接近真值」
cargo run --release --bin matsuri-gate -- --a out/_dump_a.jsonl --b out/_dump_b.jsonl `
  --five 1998,1470,1656,1344,1573 --pt 8339 --stat-score 42201
```

- `cargo check` / `clippy` / `test` 通过（**不改引擎任何一行**）。
- 接入新剧本时引擎仓 `git diff` 为空——确需新能力时回引擎仓按「带默认实现、非破坏」加钩子。
- 三条随机轴（局面 / 事件 / 策略）+ 决策流只喂 `Trainer`；阶段自持、对外只投影 `StageId`；
  同一 `master` 下两次运行 / 两支分支逐位一致。
- 剧本资源给前端走 `MatsuriView`（形态 A 五条纪律），不进契约 trait。

## 依赖三档

```toml
# 1. 开发期：并列目录（当前写法）
umaai-core = { path = "../umaai_next/crates/umaai-core" }
umasim     = { path = "../umaai_next/crates/umasim" }
umaai      = { path = "../umaai_next/crates/umaai" }
umaai-cli  = { path = "../umaai_next/crates/umaai-cli" }   # 仅终端入口需要

# 2. 稳定期：同一 repo、同一 tag
# umaai = { git = "<engine repo>", tag = "engine-v0.1.0" }

# 3. 下游（Web / Android）：自喂帧、无文件监听，并剪掉并行后端
# umaai = { git = "<engine repo>", tag = "engine-v0.1.0", default-features = false }
```
