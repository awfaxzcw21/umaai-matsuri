# 交接提示词

> 对话上下文很长时，经用户同意后用一段话交接；新上下文只读本文件即可续接。
> 写法：只写「下一个窗口无法从代码 / git 状态里恢复」的信息，不写过程叙述。
> 最近一次刷新：2026-11-03 深夜（上一版还是 10-27 的 S0/S1 期，已过期）。

- **当前目标**：引擎**只补「量」、不补「价」**，把终局产出推到够凹 LF 的水平；决策层复用 port 线
  上游那份 S7 打分（`MatsuriTrainer` + `MatsuriValue`）。终点是「照着黑板面板实机玩骏大祭」，
  不是「引擎指标好看」。
- **本仓现状**：分支 `feat/panel-backend`，HEAD `86d183f`。11-03 这一组四个提交：
  `ada633b`（演目平摊 10 挪出乘区）/ `f1db6e8`（稽古补上整条 home 腿）/
  `ba70c2f`（真机 PT 逐回合台账）/ `86d183f`（`status_weights` 判读）。
- **待办（按能开工的顺序）**：
  1. **#24** 把 `revise` 接进估值层 `value.rs::status_score`，然后复扫 `status_weights`，
     检验 `智x2` 的优势是否缩水。**卡在 #5/#6（两线合并）**——本仓 HEAD 没有估值层，
     而实验壳的稽古仍是缺 home 腿的单腿态。开工前先确认隔壁 port 会话合了没有。
  2. **#16** 大祭发表会的上限解放档位语义（与 #18 共用 `effect_type` 那批语义，顺手做最省）。
  3. **#21** 影子模式验收：**依赖库兰本人打一局真骏大祭**攒 `out/_shadow_log.jsonl`，
     才能算顶栏 vs 引擎的分歧率。
  4. 未钉死且**不许写常数**的都登记在 `docs/festa平摊加项_20261103.md` §未钉死
     （festa 主属性/SP 高 Lv 的额外因子、`effect_type` 106/107/109/206、稽古失败分支）。
- **明天开工先检查**（他要求的「先检查一下所有东西」）：
  1. `git log --oneline -6` + `git status --short`：HEAD 是否仍是 `86d183f`、未跟踪只剩
     仓根那三件旧散物（`data/cardDB.json.bak_20261031`、`sweep_friend.py`、`sweep_vs.py`）；
  2. `git ls-remote origin feat/panel-backend` 与本地 HEAD 对齐 ⇒ 备份到位；
  3. `cargo fmt --all --check`、`cargo test --workspace --release --offline`
     （基线 43 / 8 / 3 / 2 / 2 全绿；撞 LNK1104 是文件锁，**直接重跑**别改配置）；
  4. 隔壁有没有合线（#5/#6）⇒ 决定 #24 能否开工；`out/_shadow_log.jsonl` 有没有新行 ⇒ #21。
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
  `tools/sweep_status_weights.py`、`tools/keiko_two_leg_identity.py`、`data/scenario_matsuri.json`。
- **约束**：上游 `umaai-project/umaai-matsuri` 与祖仓 `xf8410/umaai-rs` **只读**；
  通用引擎 `E:/HJM/umaai_next` 保持 diff-empty；`E:/HJM/umaai-matsuri-port`（隔壁 WIP）**不可编辑**；
  实验壳 `E:/HJM/_s7x2` 是 port 线的 tar 副本、可丢弃，跑完脚本会把它的 `matsuri_config.toml` 恢复出厂；
  上游缺陷只留本地文档、不外报；**任何出网写（push / PR / issue / 删远程分支）一次一动作、各自点头**；
  游戏接管永久关闭。
