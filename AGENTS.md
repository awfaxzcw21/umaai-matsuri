# umaai-matsuri 项目规则

## 对话规则与工作规范

1. **语言**：思考和回答使用中文，语气简洁、专业。
2. **避免信息过载**：需要分析大量代码 / 数据并提取结论时，使用子代理。
3. **方案选择**：涉及不在上下文中的领域知识或需要选型时，停下来给出建议交用户选择，不直接生成代码。
4. **提案前审视**：提案前从「做减法」的角度重新考虑一次。

## 项目上下文

- 本仓是 **骏大祭剧本仓**：官方名「朋、史解き ―午式駿大祭―」（駿大祭編），
  `scenarioId = 15`；开发代号 **`matsuri`**，参考库别名 `shuntaisai`（mee-umasim）。
- 引擎仓为并列目录 `../umaai_next`，本仓以 path / git 依赖引用它的 `umaai-core` / `umasim` / `umaai`；
  **不 fork 引擎、不在引擎仓加剧本 crate**。
- 引擎契约：`../umaai_next/docs/scenario/contracts.md`；接入纪律：
  `../umaai_next/docs/scenario/README.md`；仓库形态 / 开发流程 / 性能优化：
  `../umaai_next/docs/scenario/{repo_form,dev_process,perf_plan}.md`。
- 成熟参考实现：`../umaai-onsen`（温泉剧本仓，workspace 五 crate 形态来源）。**本机当前没有这个目录**，
  需要时另行拉取；不要因为它不在就重排 workspace。
- 本仓文档索引见 `docs/README.md`，含开发计划、接入包、stub 登记、机制、术语、测试与交接。

## 证据与机制真实性（最高优先，压过一切便利性）

- 数据来源优先级：**`master.mdb` 表侧 > so 反汇编 > 真机报文 > 游戏截图 > 视频观察**；这条只管**基准值取哪边**，
  不管**机制存不存在**——机制是否存在一律以真机实测反证（`cap_decay` 那条衰减就是被报文否掉的）。
  低优先级的读数不得覆盖高优先级结论。机制口径落 `docs/mechanism.md`、术语落 `docs/glossary.md`，本文件只留指针。
- **一手报文不在本仓**：`E:/HJM/jidianbeiAI0.22/out/hachimi_raw`（msgpack `.bin`，是**活口径本、还在长**，
  说「全量 n=」前先重数）。解码器 `E:/HJM/jidianbeiAI0.22/msgpack_lite.py` 的 `decode_file(path)`（**没有 `unpackb`**）；
  完整 `master.mdb`（其实是 SQLite）在 `E:/HJM/_ref01/matsuri-ref-01/data/`；已解码的派生索引在 `E:/HJM/_verify_p0/`，
  **别重跑最重的那步解码**。同目录还有 `race_entry.py` / `race_schedule.py` 两个现成赛程解码器，不要自己重写链路。
- **禁止用拟合系数凑分**：机制没拆到公式就只留档，不许写进 `data/` 或配置。`cap_decay` 这种**为凑分引入的旋钮**
  一律不许加回来（现置 0；**报文实测训练增益不随距上限的剩余空间衰减**）。带出处的折算常数
  （带出处、非调参的折算常数不算拟合，但**不许改成环境变量旋钮**；技能分折算见 `crates/matsuri-cli/src/dist.rs`，
  它的**当前值以工作树为准**，别照文档写死）。
- 训练增量用**单手配对**量：同回合内带 `params_inc_dec_info_array` 的 board 报文 P，配其后第一条带
  `command_result` 的结算报文 R，`delta = R.five − P.five`。**跨回合差分会把事件增益混进训练增量，
  由此得到的「衰减显著为负」已作废**；现成实现见 `tools/hand_gain.py`，定案见 `docs/每回合收益表_20261103.md`。

## 文档规则

以下文档随代码同步维护，在提交准备阶段统一更新：

1. **README 状态**：结构 / 验收命令 / 数据说明的现状变化，不写修改过程。
2. **接入包**（`docs/plan/intake.md`）：情报位置、机制 / 数值 / 数据表清单、待确认问题；
   S0 核对期间持续更新，结论分流到机制、术语与 stub。
3. **开发计划**（`docs/plan/scenario_dev_plan.md`）：阶段状态、决策记录、待决事项。
4. **数值占位**（`docs/plan/stubs.md`）：`STUB` / `TBD` 的位置、来源、替换条件与基线影响。
5. **机制 / 术语 / 测试**：`docs/mechanism.md`、`docs/glossary.md`、`docs/tests_overview.md`
   分别随机制改动、口径决定、测试增删更新。
6. **handoff**（`docs/handoff.md`）：上下文很长时，经用户同意后写交接提示词。
7. **测量定案**：报文 / 截图量出来的机制结论写 `docs/<主题>_YYYYMMDD.md`，脚本进 `tools/`、派生数据进
   `out/_*.{json,csv,txt}`，**必须可重跑**；文里要区分「量到的形状」和「可以写进配置的常数」。

## 代码结构

**workspace 五 crate，按终端业务形态划分。** `matsuri-core` 是宿主稳定面（机制 + 训练员 +
装配）；`matsuri-sim`（离线模拟）与 `matsuri-ai`（在线决策）是两条业务线 lib；终端入口由
`matsuri-cli` 承载；`matsuri-tools` 是外围工具面。业务 crate 之间不允许反向依赖
（sim / ai 不得依赖 cli）。重依赖各归其位，**在对应业务真正用上前不要往 `Cargo.toml` 加依赖或 feature**。

| crate / 文件 | 职责 |
|---|---|
| `crates/matsuri-core/src/lib.rs` | 模块声明 + 对外重导出 + 常量（**不含实现**） |
| `crates/matsuri-core/src/game/mod.rs` | 局面 `MatsuriGame` + `MatsuriView` + `build_game` |
| `crates/matsuri-core/src/game/stage.rs` | 阶段机 `Stage`（含 `StageId` 投影） |
| `crates/matsuri-core/src/game/action.rs` | `Action` + `Display` + `ActionEnum` + 动作结算入口 |
| `crates/matsuri-core/src/game/traits.rs` | 契约 impl（`Game` / `TrainingValueSource` / `FlatSearchGame`） |
| `crates/matsuri-core/src/{data,config}.rs` | 剧本资产 schema / 剧本配置段 |
| `crates/matsuri-core/src/trainer.rs` | 样例训练员（真实剧本换成自己的手写 / 搜索训练员） |
| `crates/matsuri-core/src/testing.rs` | 测试定位件（仓根 / 数据根） |
| `crates/matsuri-sim` | 离线模拟：跑一局 / 批量跑局 / 基线裁判 / 测卡；只出 lib |
| `crates/matsuri-ai` | 在线决策：帧段解析 / Session 接线 / 决策循环；只出 lib |
| `crates/matsuri-cli` | 终端入口：**4 个 bin**——`matsuri`（切 `matsuri` / `random` / `manual`）、`matsuri-mccli`、`matsuri-dist`（门禁采样）、`matsuri-gate`（配对判官） |
| `crates/matsuri-cli/src/manual.rs` | 无颜色手动训练员（人工核对机制） |
| `crates/matsuri-cli/src/{lib,state}.rs` | 面板状态 → 引擎局面的共享装配（两个 bin 共用；lib target 供回归测试进） |
| `crates/matsuri-tools` | 外围工具：测卡 / 复盘；**当前只立骨架、无 bin**（其 `Cargo.toml` 自述依赖与 bin 待实现） |

- **契约 trait impl 不可拆文件**：一个 trait 的 `impl` 必须整体落在同一文件。
- **可见性**：crate 内跨模块调用标 `pub(crate)`；跨 crate 消费面标 `pub`。
- **阶段自持**：内部强类型 `Stage`，对外只投影 `StageId`（显式赋号，改号 = 旧种子失效）。
- **不碰引擎**：接入新剧本时引擎仓 `git diff` 为空；确需新能力时回引擎仓按「带默认实现、非破坏」加钩子。
- **不预置剧本形态**：阶段 / 事件 / 训练 / 比赛 / 资源等强依赖剧本的实现，先在
  `docs/plan/scenario_dev_plan.md` 列为「待确认项」，S0 机制核对后再自建。

## 数据与配置

- **统一数据目录 `data/`（剧本仓唯一发布库，拷仓即跑）**：剧本资产 `scenario_matsuri.json` +
  剧本配置默认层 `matsuri_config.toml`；引擎仓 `gamedata/` 的六张静态表与 `ai_config.toml` 已拷入。
- bin 的数据根是**代码级常量**（`crates/matsuri-cli/src/main.rs` 的 `DATA_ROOT`，跑局从仓根发起）。
  引擎覆盖层约定保留：工作目录放同名 `ai_config.toml` / `matsuri_config.toml` 存在才合并。
- lib 侧不写死路径：`MatsuriData::load` / `MatsuriConfig::load*` / `Data::init` 的根由调用方注入。
- 配置层未知键**严格报错**；剧本专属配置段归本仓，不进引擎配置。

## Rust 编码规范

### 错误处理
1. `Result` 优先；可用 `Result` 的场合不用 `unwrap`。
2. 取 `Option` 内部值时优先 `ok_or_else` 转 `Result`；越界报错，不静默取末档。
3. 仅测试 / 临时代码允许 `unwrap` / `panic`。

### 代码结构
1. **新类型原则**：固定结构的数据建新类型，避免裸 Tuple。
2. **文档化注释**：新增函数 / 结构体须有 rustdoc 注释，函数注释 ≤10 行、文件头 ≤15 行；
   注释必须对应代码现状，历史修改写 `README.md` 状态行或剧本仓文档。
3. 变量命名简短；长词可用缩写。
4. **`Deref` 底座不得与契约同名**（`BaseGame` 的公开方法不得与 `Game` 契约方法同名）。

## 测试规范

1. **前期验证**：机制是否成立以**对话核对为主**（`docs/plan/intake.md` 的确认清单），
   自动化测试随业务落地逐步补齐，不用测试代替机制确认。
2. **分层**：单元测试（内联，私有细节 / 纯函数）用 `assert`；机制集成测试、基线测试与冒烟测试
   在业务落地后补到对应 crate 的 `tests/`。
3. 测试进程的 cwd 在各 crate 目录：数据根 / 基线路径统一经 `matsuri_core::testing` 取仓根。
   引擎 `umaai-core::testing` 的 `env!` 固化的是引擎仓路径，**跨仓不可用**。
4. **Release 档**跑 binary；`cargo test` 统一加 `--offline`。
5. **改动是否生效只认配对门禁**：同种子（CRN）逐局配对的**三分量**——五维和 / 五维分 / PT——看**贴近幅度
   95% 区间**；区间跨 0 只留档不转正。全臂同侧时「更近」≈「产出更多」，**必须换种子复跑**再下结论。
6. **门禁有锚点旁路**：t30 注入报文锚点后，五维和 / 五维分对「表侧」和「乘区」改动**不敏感**；
   判这类改动要么去锚点跑，要么只看 PT 分量。
7. **门禁的现成入口（别自己造）**：采样 `bin matsuri-dist`（`MC_DUMP` / `MC_N` / `MC_SEED` 环境变量）出两份
   同种子 dump，判官 `bin matsuri-gate`（Python 侧 `tools/gate.py`），口径与数字见
   `docs/TODO_20261101_pm.md` §步骤 10；盘上现成的成对样本是 `out/_dump_pre.jsonl` 与 `out/_dump_a.jsonl`。
   **注意重放旧 dump 只证明判官可复现，不等于对当前工作树做了新门禁**——新 A/B 要重新出 dump（落盘）＋基线 worktree。

## 测量壳与并行会话

- **要改代码才能量的东西只在一次性壳里做**：`git worktree add --detach` 到基线提交 ＋ 私有 `CARGO_TARGET_DIR`
  （现役壳 `E:/HJM/_keileg`，一次性壳如 `_wt_r50` / `_wt_hg`）。**插桩永不并回主仓**；每个壳归档
  `tools/patch_*_for_measure.patch`（对壳树跑 `git apply --check --reverse` 必须通过 ⇒ 它就是壳相对基线的
  全量差），冻结臂 exe 的 sha1 写进对应文档；**清理壳之前先把分片证据迁出来**。
- **并行会话只写文件，不碰 git 索引、不 commit**；由主线核对口径与区间算法后成组提交。
  16 核机器上引擎臂**并发上限 6**；查占用用 `tasklist //NH | grep -iE "arm_|matsuri"`——
  `tasklist /FI "IMAGENAME eq arm_*.exe"` 的**通配筛选不报错但恒空**（会把在跑的臂判成「空闲」），
  只按 `matsuri-dist.exe` 过滤也会漏判（臂 exe 发布时是改名拷贝的）。
- **后台进程树会被 harness 限速**（实测每进程只有 9%–25% CPU，前台满速）⇒ 长批次拆成前台分波跑
  （带 `LIMIT` 的续跑形式），不要一把丢后台。
- **判据只有一个事实源**：配对三分量 ＋ `T975` t 表（`tools/forced_cp_ci.py`、`tools/crn_gate.py`）。
  隔壁会话自带同算法脚本时**不并回主仓**（并回来只造第二个事实源）；主线核对＝用本仓判据对它的**原始分片**独立重聚。

## 工具使用

- 日常 `cargo fmt --all --check` / `cargo clippy --workspace --all-targets -- -D warnings` /
  `cargo test --workspace --release` 加 `--offline`。
- 需要改 registry（`cargo add` / `fetch`）时**先请求提权**。
- **同一 target 目录一次只跑一个 cargo**（并发会互等锁，表现为卡死）。
- 从剧本仓根执行 cargo 命令；不要再套 `--manifest-path`。

## Git 操作

1. **不自动 `commit` / `push`**：先确认用户已完成全部调整，再进入提交准备。
2. **提交准备**：同步更新 `README.md` 状态与剧本仓文档，由用户确认后再提交。
3. **提交范围**：涵盖工作树下由 Agent 与用户做的全部修改。
4. 提交说明需点明**默认行为是否等价**（引擎侧）；剧本侧数值位移须在剧本仓文档记口径分歧。
5. 遵循 Git 安全协议，避免破坏性操作。
6. **`upstream`（`umaai-project/umaai-matsuri`）与祖仓只读**：`clone` / `fetch` / `ls-remote` / 拉档可以做；
   任何**出网写**——push、开 / 关 PR、发 issue、删远端分支——**每次单独征得用户同意**，授权不延续到下一次。
   上游缺陷只记本地文档，不外报（作者自己在改繁中剧本）。
