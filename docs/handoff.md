# 交接提示词

> 对话上下文很长时，经用户同意后用一段话交接；新上下文只读本文件即可续接。
> 写法：只写「下一个窗口无法从代码 / git 状态里恢复」的信息，不写过程叙述。
> 最近一次刷新：2026-11-03 深夜（**上游已前进**：`upstream/main` = `9203506`，见
> `docs/上游并档对账_20261103.md`；上一版还是 10-27 的 S0/S1 期，已过期）。

- **当前目标**：引擎**只补「量」、不补「价」**，把终局产出推到够凹 LF 的水平；决策层复用 port 线
  上游那份 S7 打分（`MatsuriTrainer` + `MatsuriValue`）。终点是「照着黑板面板实机玩骏大祭」，
  不是「引擎指标好看」。
- **本仓现状**：分支 `feat/panel-backend`，HEAD `2230dcb`；远端 `origin/feat/panel-backend` 仍在 `4d2de28`
  ⇒ **本地领先 4 个提交、未推送（推送要另行点头）**。
  11-03 这组：`ada633b`（演目平摊 10 挪出乘区）/ `f1db6e8`（稽古补上整条 home 腿）/
  `ba70c2f`（真机 PT 逐回合台账）/ `86d183f`（`status_weights` 判读）/ `4d2de28`（本文件刷新）/
  `70bb3b8`（剧本事实层 + `matsuri query`）/ `d9f99f9`（`tools/crn_gate.py` + 三臂配对定案）/
  `1c0416d`（上游并档对账 + 三个只读脚本）/ `2230dcb`（交接刷新）。
  门禁基线复跑全绿：fmt 干净、clippy 干净、`cargo test --workspace --release --offline` =
  **43 / 8 / 3 / 2 / 2 + 5**（多的 5 条是 `query` 口子的 schema/命中用例）。
  上一批未提交件（事实层两件套 / 环境页 / 后端发布配对报告 / `tools/crn_gate.py` /
  `crates/matsuri-cli/src/query.rs` / 本文件）**已全部落在 `70bb3b8`–`2230dcb` 里**。
  仍未提交且不该提交的残留：`data/cardDB.json.bak_20261031`、`sweep_friend.py`、`sweep_vs.py`（历史遗留，未动）。
  ~~⚠️ 黑板的后端落后两格~~ **已发布（11-03 深夜，经点头）**：面板 `rust/` 的 `backend_build.json`
  现在 `git_commit = 4d2de28`，2 exe + 10 个 `data/` 全刷新，exe 与 `target/release/` 逐字节同 sha。
  发布前后做了三臂 CRN 配对（`docs/后端发布前后配对_20261103.md`）：
  **旧后端是虚高的**——它把演目平摊 10 也算进了四个非主属性的乘区，比新口径高 ~1,560 五维和 /
  ~19,096 五维分；补上 home 腿只回来 +664 / +7,934 ⇒ **净效果离单局锚点更远**（五维分 −17%），
  且这与 `docs/festa平摊加项_20261103.md:64` 当时的预测逐字对上 ⇒ 是去虚高不是新 bug，**不回滚**。
  **缺口已经逐源定量（`docs/逐源台账_20261103.md`，11-03 深夜）**：同段 t30→终 PT 引擎 4,796 vs 真机 6,504
  = **−1,708**，拆开是 赛后奖励 **−735**（引擎 15 场恒 1,743、真机同 15 场 2,478，零方差 ⇒ 查表缺口）/
  稽古 **−600**（每手 73.3 vs 120.5，多打 4.7 手只补回一部分）/ 发表会 **−550 → ✅已修**（#16，11-03 深夜：
  补第 4 场 t78 + 档界改成 `min(全演目 Lv) ≥ need` + 到回合必结算；配对门禁 PT +583.7/局，**产出计**；
  定案见 `docs/发表会档位一手_20261103.md`）/ 训练 +43（率仍低一半，靠多打 8.9 手抹平）。
  两条判定：**#25 一分不补**（真机 t30 后没打过自选赛，`race_free = 0` 与真机一致，只保留「合并前必修」）；
  **#24 不是量的缺口**（它是估值层定价，跟产出量不同一条线）。另发现**回合预算口径差**：引擎的比赛在
  `Stage::Begin` 自动结算不占行动，每局比真机多做 ~13 个训练动作 ⇒ 修「比赛回合禁训练」必须与稽古每手加项同批。
- **待办（按能开工的顺序）**：
  0. **新增（`docs/上游并档对账_20261103.md` §8）**：
     ① **freeRaces 义务候选**——`data/umaDB.json` 一手字段里玉藻 102102 有义务区间
     `t12–28 需 1 场 / t29–48 需 4 场(grade≤3)`，而我们 `game/traits.rs:106` 只在面板 45/69
     （引擎 44/68）给 `Action::Race` ⇒ 一合并上游 freeRaces 闸门就**必育成失败**；这条不依赖合并，自己就该补。
     ⚠ 但它**一分不补**：逐源台账证明 run179 在 t30 后没打过自选赛（账本里那 4 行是 R5 误归的
     `event_id 7000/7005/10/45`），所以这是「合并后不炸」的闸门修复，不是产出缺口。
     ② 数据卫生（低优先，**不是缺口**）：`data/events.json` 的 `race_career`/`race_g1` 两行五维/PT
     在剧本路径被 `race_reward` 逐格覆盖（`game/mod.rs:1521-1532`），只缺来源注释；
     缺的 `race_ura_*` 三行（4105/4106/4107）只在"走引擎默认 `do_race`"时才会回退成 `race_career`。
     `extra_status{random 1×10}` 引擎只有校验没有消费点，别当 #22 的线索。
  1. **量缺口三刀（`docs/逐源台账_20261103.md` 定案，按「确定性 × 分数」排）**：
     ① **赛后奖励分档 −735 PT**（≈ −2,062 分）：引擎 `race_reward.target` 对所有目标赛只有一个
     `{stat:5, pt:72}` 档，真机同 15 场是 128×9 / 256×3 / 124 / 186 / 248 ⇒ **回 `master.mdb` 拿表**，
     不拿 735/15 反推常数。这块零方差，最值得先动。
     ② **稽古每手 −47.2 PT/手（净 −600）**：先出**逐手曲线**（真机 27→219 随回合涨），
     再判是 `keiko_basic_value_late` 的 PT 列偏低还是教诲 `sp_practice_pct` 该再乘；
     等级封顶那条**已排除**（真机面板 `max_act_level = 6` = 引擎 cap）。
     ③ ~~**发表会缺第 4 场 −550**~~ ✅**已修（#16）**：第 4 场 t78 已进 `check_points`，档界与
        「到回合必结算」按报文 28/28 改正；**剩余 −600 全部押在 ②（稽古逐手曲线）**。
     ⚠ 同批约束：引擎比赛不占行动回合（每局多做 ~13 个训练动作），若单独修「比赛回合禁训练」
     会把三分量一起压低，必须与 ② 同时改。
  2. **#24** 把压缩口径接进估值层——**判据变了**：上游 `ga-r2` 权重经核**不是在补 revise 洞**
     （倍数解释权重方差 ≈ 0%，Spearman 0.00），不可迁移的真实理由是 `2×2` 定案
     「revise 必须与 `soft_cap_gain` **同批**接，单接会更远」。仍卡在合线：合上游 = 合 **19 个提交 / 18 个冲突文件**，
     真难合的只有 `game/mod.rs`、`game/traits.rs`、`trainer.rs`、`config.rs`。
     两处必须按我们裁决的语义冲突：上游稽古 `keiko_flat` 给全五轴含主属性（`traits.rs:411-417`）vs
     我们 `ada633b` 不给；上游**全链无 `soft_cap`**（`git grep soft_cap upstream/main -- crates/` 零命中）。
  3. **#16** 大祭发表会语义：✅**档界 + 第 4 场 + 一次性奖励腿已钉死**
     （`docs/发表会档位一手_20261103.md`）；⚠️**「上限解放」这一腿不在发表会表里** ——
     新增一手：`single_mode_15_check_point_effect` 全 20 行的 `effect_type` 只有
     `{2 灵感, 4 得意率, 202 友情, 204 比赛加成}`，**没有抬 `max_*` 的通道**；
     而 `LF靶子_20261103.md` §2b 实测的抬升回合是 **t31 / t55**，也不等于发表会回合 24/48/68/78。
     ⇒ §2b 里「这是大祭发表会的奖励」那句归因**没有表支持**，落点要另找
     （候选：演目 `act_effect` / 伝受 / 本番），钉死前不要把 +141/+55/+53/+25/+68 当常数写进引擎。
     ✅ **#37 已把落点找到并就地否决接线**（`docs/继承事件一手_20261103.md`）：是剧本级事件
     `story_id 400000040`「心意的继承」（`event_id 7040`@t31 / `7041`@t55，`effect_type` 1/2），
     覆盖到那两个回合的 10 局**全部触发、每局两次、无选项**，同一条报文**既抬 `max_*` 又送五维**
     （每局 `Σds` 167–490、`Σdm` 23–66；`default_max_*` 20/20 不动）。
     **驱动量没拆到公式**（变量是继承双亲，双亲属性不在这套客户端报文里；`event_effected_factor_array`
     只在这一事件出现、与收益 `r≈+0.67~+0.73`，但同局两次之间它自己会变 ⇒ 是**输出清单不是输入**）
     ⇒ 维持「不当常数写进引擎」，并且**这条否决现在有了机制依据**，不再是「先别写」的谨慎。
  4. **#21** 影子模式验收：**依赖库兰本人打一局真骏大祭**攒 `out/_shadow_log.jsonl`（11-03 夜该文件仍不存在），
     才能算顶栏 vs 引擎的分歧率。
  5. 未钉死且**不许写常数**的都登记在 `docs/festa平摊加项_20261103.md` §未钉死
     （festa 主属性/SP 高 Lv 的额外因子、`effect_type` 106/107/109/206、稽古失败分支）。
- **明天开工先检查**（他要求的「先检查一下所有东西」）：
  1. `git log --oneline -8` + `git status --short`：HEAD 是否仍是 `2230dcb`（或其后的本地新提交）、未跟踪只剩
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
    ⚠ 它被 `.gitignore` 第 9 行忽略 ⇒ **新 worktree / 新 clone 拿不到**，要从主仓 `cp` 过去，
    否则 dist 直接报「读状态失败」（这是已登记的复现性缺口，谁要闭合就把导出命令写进 `tools/`）。
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
  **`docs/后端发布前后配对_20261103.md`（11-03 深夜三臂 CRN：旧后端虚高、净效果离锚点更远、不回滚）**、
  **逐源台账两件（11-03 深夜新增）**：`docs/逐源台账_20261103.md`（引擎每笔增益按来源记账 vs 真机 run179 同段，
  −1,708 PT 的三源分解 + 桶落点表 + 复现两条坑）、`tools/pt_ledger_segment.py`（真机账本分段版，
  `--run 179 --from 30`；**先打印整局行做交叉校验**、只统计 `same_run=1` 且正差分，
  它的 `classify()` 与 `pt_ledger.main()` 内层同规则，**改一处必须改两处**）、
  `tools/patch_src_pt_ledger_for_measure.patch`（壳内打点的可 apply 存档，壳拆了也能重建）。
- **约束**：上游 `umaai-project/umaai-matsuri` 与祖仓 `xf8410/umaai-rs` **只读**；
  通用引擎 `E:/HJM/umaai_next` 保持 diff-empty；`E:/HJM/umaai-matsuri-port`（隔壁 WIP）**不可编辑**；
  实验壳 `E:/HJM/_s7x2` 是 port 线的 tar 副本、可丢弃，跑完脚本会把它的 `matsuri_config.toml` 恢复出厂；
  测量壳 `E:/HJM/_old5750`（`5750354`）与 `E:/HJM/_mid_ada`（`ada633b`）为三臂配对临时开的 worktree，
  **定案后已拆除**；三臂二进制都在 gitignored 的 `out/arm_*.exe`（复跑靠它们，不依赖 worktree 还在）。
  逐源台账壳 **`E:/HJM/_srcledger`（detached @`2230dcb`，仍在）**：只加 `game/ledger.rs` + 6 处 snapshot/accrue
  + `dist.rs` 的 `gain_ledger` 输出，**不并回主仓**；它与主仓**共用 `CARGO_TARGET_DIR`**，
  跑完必须从主仓 `cargo build --release -p matsuri-cli` 还原 `target/release/matsuri-dist.exe`（本次已还原，
  三分量复验 7483.14075 / 17053.48175 / 6533.90075，与发布臂行为一致、sha 不同属正常）。
  分腿打点壳 **`E:/HJM/_keileg`（detached @`282df51`，仍在，#39 + #40）**：#39 加 `game/keiko_legs.rs` +
  `do_keiko_impl` 一处调用 + `dist.rs` 的 `keiko_legs` 输出；#40 再加 `game/train_legs.rs`
  （`[腿][训练Lv][人头]` 分解表）与同文件的**盘面人头表** `BOARD`（`[非合宿/合宿][20 列]`，
  在 `MatsuriRolloutTrainer::select_action` 里记 offer/take），`dist.rs` 输出 `train_legs` / `train_board`。
  **插桩永不并回主仓**；存档 `tools/patch_train_legs_for_measure.patch`（592 行，对当前壳树
  `git apply --check --reverse` 通过 ⇒ 就是壳相对 `282df51` 的全量差）。
  它用**私有** `CARGO_TARGET_DIR=_keileg/target`，不碰主仓产物。
  三壳/三臂的冻结二进制：`_keileg/out/arm_head_keileg.exe`（`46674ce3…`，#39）、
  `_keileg/out/arm_board.exe`（`4917d52522c41fa2b584562e7e51a3e88ef2c8c4`，#40 盘面臂，
  产出 `_keileg/out_head4000/trainboard_result.json`）、`_srcledger/out/arm_keileg.exe`（`81b172cb…`）。
  #40 的腿分解臂 = 同一壳早先编的 `_keileg/out_head4000/trainleg2_result.json`；
  两臂门禁三分量逐位相同（7960.43 / 41067.04 / 7863.12）⇒ 插桩非侵入。
  主仓 `target/release/matsuri-dist.exe`
  已在 #39 跑完后从 HEAD 重编还原（`099e108b…`，#40 结束后复验仍是这个 sha）。
- **当前测量壳（#41）**：同一壳 `E:/HJM/_keileg` 再加 `matsuri-cli/src/searcharm.rs`（把面板强策档
  = mccli 2-ply + 终局 1-ply + 发表会兜底 复现成整局策略）与 `train_legs.rs` 的 30 列 `VALUE` 表
  （逐根记「盘面最满人头 / 估值最优格人头 / 实际落子人头 / 人头-估值一致性」）。
  插桩重存为 `tools/patch_searcharm_value_for_measure.patch`（**1019 行 / 7 文件，含未跟踪的 `searcharm.rs`**；
  对当前壳树 `git apply --check --reverse` 通过 ⇒ 它才是壳相对 `282df51` 的全量差，旧的
  `patch_train_legs_for_measure.patch`（592 行）只到 #40 为止）。
  冻结臂：`_keileg/out/arm_strong41.exe`（`c93c5e84f97fca80dc6192b9b3237271d58033bf`，K=8/16/32 headline；
  同 exe 不设 `MC_POLICY` 即基策臂，CRN 保证双臂 `offer` 逐位相同）与
  `_keileg/out/arm_val41b.exe`（`7bf9342a25d92a22dab0e1af6aa969e3e9c47845`，多 `VALUE` 表；
  基策路径与前者逐字节同、搜索 smoke 门禁同值 ⇒ 打点非侵入）。读数与裁决在 `docs/强策档盘面读数_20261104.md`。
- **并行会话拓扑（2026-11-04 夜，同一 workspace）**：主线（#41，占 `_keileg`）之外另有四条独立线，
  各自专属目录、互不碰 git 索引、互不碰 `_keileg`：共因检验（`tools/train_slot_confound.py`）、
  #38 配置核验（`_wt_r50`）、高分局报文普查（`tools/lf_packet_scan.py`）、
  #42 人头加强三臂（`_wt_hg`，私有 `CARGO_TARGET_DIR`）。它们**只写文件不提交**，
  由主线核对口径与区间算法后统一成组提交。机器 16 核：引擎臂并发上限 6，
  查占用要按 `arm_*.exe` 过滤（只按 `matsuri-dist.exe` 过滤会漏判成「空闲」）。
  - **集成核对结果（主线，同日）**：会话① 重跑逐字节复现（`out/_slot_confound*.txt` 三份），
    但它的置换检验原先只印**上侧** p，而判据方向是「观测低于基线」⇒ 已改成同印下侧/上侧，
    下侧 p = 0.0005（稽古、全体）/ 0.0010（deck）/ 0.0050（need-key），普通层不显著；结论方向不变。
    会话④ 的四臂增益与三分量**点估计逐位独立复现**（68 份分片重聚原始计数），
    唯区间用 `T975=1.960`（正态）而非本仓惯用的 `t_{16}=2.120`，窄 8.2% ⇒ 保守区间已写进该文档 §5。
    `_wt_hg` 是一次性壳，故 `tools/s42_head_diag.py` 与插桩
    `tools/patch_head_boost_for_measure.patch`（基线 `282df51`，`--check --reverse` 通过）
    已拷进主仓；四份臂 exe + `out/s42/exe_sha1.txt` 仍在 `_wt_hg/out/`，**清理该壳前先迁证据**。
  - 仓根两个旧的旋钮扫描脚本 `sweep_friend.py` / `sweep_vs.py`（10-29/10-31，未跟踪）**会正则原地改
    `data/scenario_matsuri.json`** ⇒ 属拟合旋钮工具，别在门禁窗口跑，也不要提交。
  上游缺陷只留本地文档、不外报；**任何出网写（push / PR / issue / 删远程分支）一次一动作、各自点头**；
  游戏接管永久关闭。
