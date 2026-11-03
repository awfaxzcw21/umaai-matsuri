# 交接提示词

> 对话上下文很长时，经用户同意后用一段话交接；新上下文只读本文件即可续接。
> 写法：只写「下一个窗口无法从代码 / git 状态里恢复」的信息，不写过程叙述。
> 最近一次刷新：2026-11-03 深夜（**上游已前进**：`upstream/main` = `9203506`，见
> `docs/上游并档对账_20261103.md`；上一版还是 10-27 的 S0/S1 期，已过期）。

- **当前目标**：引擎**只补「量」、不补「价」**，把终局产出推到够凹 LF 的水平；决策层复用 port 线
  上游那份 S7 打分（`MatsuriTrainer` + `MatsuriValue`）。终点是「照着黑板面板实机玩骏大祭」，
  不是「引擎指标好看」。
- **本仓现状**：分支 `feat/panel-backend`，HEAD `4d2de28`（= 远端 `origin/feat/panel-backend`，备份到位）。
  11-03 这组：`ada633b`（演目平摊 10 挪出乘区）/ `f1db6e8`（稽古补上整条 home 腿）/
  `ba70c2f`（真机 PT 逐回合台账）/ `86d183f`（`status_weights` 判读）/ `4d2de28`（本文件刷新）。
  门禁基线复跑全绿：fmt 干净、clippy 干净、`cargo test --workspace --release --offline` =
  **43 / 8 / 3 / 2 / 2 + 5**（多的 5 条是 `query` 口子的 schema/命中用例）。
  **工作区还有一批未提交**：剧本事实层两件套 + 环境页 + 后端发布配对报告 + `tools/crn_gate.py` +
  `crates/matsuri-cli/src/query.rs`（改动 `main.rs` 加 `query` 分支，不进跑局路径）+ 本文件。
  ~~⚠️ 黑板的后端落后两格~~ **已发布（11-03 深夜，经点头）**：面板 `rust/` 的 `backend_build.json`
  现在 `git_commit = 4d2de28`，2 exe + 10 个 `data/` 全刷新，exe 与 `target/release/` 逐字节同 sha。
  发布前后做了三臂 CRN 配对（`docs/后端发布前后配对_20261103.md`）：
  **旧后端是虚高的**——它把演目平摊 10 也算进了四个非主属性的乘区，比新口径高 ~1,560 五维和 /
  ~19,096 五维分；补上 home 腿只回来 +664 / +7,934 ⇒ **净效果离单局锚点更远**（五维分 −17%），
  且这与 `docs/festa平摊加项_20261103.md:64` 当时的预测逐字对上 ⇒ 是去虚高不是新 bug，**不回滚**。
  剩下的缺口在「量」这一侧，候选是 #24（`revise` 进估值层，须与 `soft_cap_gain` 同批）与 #25（freeRaces 义务候选）。
- **待办（按能开工的顺序）**：
  0. **新增（`docs/上游并档对账_20261103.md` §8）**：
     ① **freeRaces 义务候选**——`data/umaDB.json` 一手字段里玉藻 102102 有义务区间
     `t12–28 需 1 场 / t29–48 需 4 场(grade≤3)`，而我们 `game/traits.rs:106` 只在面板 45/69
     （引擎 44/68）给 `Action::Race` ⇒ 一合并上游 freeRaces 闸门就**必育成失败**；这条不依赖合并，自己就该补。
     ② 数据卫生（低优先，**不是缺口**）：`data/events.json` 的 `race_career`/`race_g1` 两行五维/PT
     在剧本路径被 `race_reward` 逐格覆盖（`game/mod.rs:1521-1532`），只缺来源注释；
     缺的 `race_ura_*` 三行（4105/4106/4107）只在"走引擎默认 `do_race`"时才会回退成 `race_career`。
     `extra_status{random 1×10}` 引擎只有校验没有消费点，别当 #22 的线索。
  1. **#24** 把压缩口径接进估值层——**判据变了**：上游 `ga-r2` 权重经核**不是在补 revise 洞**
     （倍数解释权重方差 ≈ 0%，Spearman 0.00），不可迁移的真实理由是 `2×2` 定案
     「revise 必须与 `soft_cap_gain` **同批**接，单接会更远」。仍卡在合线：合上游 = 合 **19 个提交 / 18 个冲突文件**，
     真难合的只有 `game/mod.rs`、`game/traits.rs`、`trainer.rs`、`config.rs`。
     两处必须按我们裁决的语义冲突：上游稽古 `keiko_flat` 给全五轴含主属性（`traits.rs:411-417`）vs
     我们 `ada633b` 不给；上游**全链无 `soft_cap`**（`git grep soft_cap upstream/main -- crates/` 零命中）。
  2. **#16** 大祭发表会的上限解放档位语义（与 #18 共用 `effect_type` 那批语义，顺手做最省）。
  3. **#21** 影子模式验收：**依赖库兰本人打一局真骏大祭**攒 `out/_shadow_log.jsonl`（11-03 夜该文件仍不存在），
     才能算顶栏 vs 引擎的分歧率。
  4. 未钉死且**不许写常数**的都登记在 `docs/festa平摊加项_20261103.md` §未钉死
     （festa 主属性/SP 高 Lv 的额外因子、`effect_type` 106/107/109/206、稽古失败分支）。
- **明天开工先检查**（他要求的「先检查一下所有东西」）：
  1. `git log --oneline -6` + `git status --short`：HEAD 是否仍是 `4d2de28`、未跟踪只剩
     仓根那三件旧散物（`data/cardDB.json.bak_20261031`、`sweep_friend.py`、`sweep_vs.py`）；
  2. `git ls-remote origin feat/panel-backend` 与本地 HEAD 对齐 ⇒ 备份到位；
  3. `cargo fmt --all --check`、`cargo test --workspace --release --offline`
     （基线 43 / 8 / 3 / 2 / 2 全绿；撞 LNK1104 是文件锁，**直接重跑**别改配置。
     ⚠️ 别把测试接在 `| tail` 后面——那样退出码是 `tail` 的，证明不了 cargo 过了）；
  4. 隔壁有没有合线（#5/#6）⇒ 决定 #24 能否开工；`out/_shadow_log.jsonl` 有没有新行 ⇒ #21；
  5. **`git fetch upstream && git log --oneline upstream/main -1`**：11-03 夜是 `9203506`，
     再往前就是又并了新东西，按 `docs/上游并档对账_20261103.md` 的三堆分类重判。
- **三条只有读了才知道的坑**：
  - 门禁的「贴近幅度」在双臂同侧于锚点时**恒等于该分量的原始差值**（实测斜率 0.997、残差 ≤0.5）。
    ⇒ 扫定价类旋钮**只能看五维分**那一格；五维和 / PT 两格是产出计。判读全文
    `docs/旋钮定价判读_20261103.md`。
  - 五维分口径 = `Σ five_status_final_score[revise(五维)]`，`revise(v)=2v−1200`，
    132,000 局零失败复现；核对脚本 `tools/revise_pricing_gap.py`（**必须从仓根跑**，表在 `data/`）。
  - CRN 跑法：状态文件是**仓根 `_state_t30.json`**（不是 `out/_mc_state.json`）；
    锚点 run179/cid179 = 五维 `[1998,1470,1656,1344,1573]`（和 8,041）/ 五维分 42,201 / PT 8,339；
    `matsuri-dist <state> <out>` + env `MC_DUMP/MC_N/MC_SEED`，判官 `matsuri-gate --a --b --five --pt --stat-score`。
- **关键文件**：`crates/matsuri-core/src/game/mod.rs`（`training_leg` / `do_keiko_impl`）、
  `docs/festa平摊加项_20261103.md`、`docs/PT台账_20261103.md`、`docs/旋钮定价判读_20261103.md`、
  **`docs/上游并档对账_20261103.md`（11-03 夜新增：合并面 19 提交 / 18 冲突 / 三堆分类 / 定价审计）**、
  `tools/sweep_status_weights.py`、`tools/keiko_two_leg_identity.py`、
  `tools/ga_r2_pricing_gap_20261103.py`、`tools/weights_vs_multiplier_20261103.py`、
  `tools/pt_favor_double_count_20261103.py`（三个都是纯表算只读脚本，从仓根跑）、`data/scenario_matsuri.json`、
  **剧本环境四件（11-03 深夜新增）**：`docs/剧本事实表_20261103.json`（机读十条 + 三条误读纠正 + 缺口登记）、
  `docs/剧本事实表_20261103.md`（人读版）、`docs/剧本环境_给第三条线_20261103.md`（一页上手：三条硬约束 /
  判据口径 / 快照给法）、`crates/matsuri-cli/src/query.rs`（`matsuri query` 问答口子，**运行时读盘不 embed**）、
  `tools/crn_gate.py`（配对门禁封装，多做的一件事 = 双臂同侧 ⇒ 降级成「产出计·不可结论」）、
  **`docs/后端发布前后配对_20261103.md`（11-03 深夜三臂 CRN：旧后端虚高、净效果离锚点更远、不回滚）**。
- **约束**：上游 `umaai-project/umaai-matsuri` 与祖仓 `xf8410/umaai-rs` **只读**；
  通用引擎 `E:/HJM/umaai_next` 保持 diff-empty；`E:/HJM/umaai-matsuri-port`（隔壁 WIP）**不可编辑**；
  实验壳 `E:/HJM/_s7x2` 是 port 线的 tar 副本、可丢弃，跑完脚本会把它的 `matsuri_config.toml` 恢复出厂；
  测量壳 `E:/HJM/_old5750`（`5750354`）与 `E:/HJM/_mid_ada`（`ada633b`）是为三臂配对临时开的 worktree，
  对应二进制已拷进 gitignored 的 `out/arm_*.exe`（复跑要靠它，删 worktree 前先确认 exe 在）；
  上游缺陷只留本地文档、不外报；**任何出网写（push / PR / issue / 删远程分支）一次一动作、各自点头**；
  游戏接管永久关闭。
