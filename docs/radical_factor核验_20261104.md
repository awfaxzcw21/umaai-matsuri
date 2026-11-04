# #38 `radical_factor_max` 在本仓有没有配对增益 — 核验（2026-11-04）

> 一句话裁决：**有正增益**——在「官方 1-ply FlatSearch 当整局决策者」这条臂上，把
> `radical_factor_max` 从 50.0 压到 1.4，三分量（五维和 / 五维分 / PT）配对差 95% t 区间
> **全部同侧显著为正**（17/17 分片同号，K=128 复现 6/6 同号）。
> 但同一个旋钮在**本仓当前的强策档（mccli 2-ply）上逐字无效**（同种子输出 sha1 逐字节相同）。
> 所以：lab 那句「+826」数值不可迁移，lab 那句「真凶是 `radical_factor_max`」**不能**解释我们此前
> MC 无增益——我们的 2-ply 臂根本没调 `FlatSearch`。

口径声明：按 QOS-13 / AGENTS.md 测试准则 5，判据只看配对三分量；「离锚点更近」不是目标，
本文件的结论没有使用锚点距离。跨 0 只留档；全部分量同侧显著才谈转正——本次达成同侧显著，
但**是否改配置需要单独决策**（见 §7 局限：测的是自建臂，不是生产臂）。

---

## 1. 旋钮语义（代码级，非二手）

引擎是 path 依赖，源码在本仓之外（`Cargo.toml:25-28` 引 `../umaai_next/crates/*`）。

| 事实 | 位置 |
|---|---|
| `radical_factor = (remain / (max_turn+1))^0.5 × radical_factor_max` | `../umaai_next/crates/umasim/src/search/flat_search.rs:436-439` |
| 候选值按 `rank_ratio^radical_factor` 加权（越大越取高分尾部；=0 退化为普通均值） | `../umaai_next/crates/umasim/src/search/result.rs:95-135` |
| 同时驱动 UCB 分配/选择 `select_ucb_action` → `weighted_mean(radical_factor)` | `flat_search.rs:297-318`、`520`、`544-557` |
| 决定 `SearchOutput.best_action_idx`（即真正被执行的动作） | `result.rs:182`、`217-232` |
| 代码默认值 = 50.0 | `../umaai_next/crates/umasim/src/search/config.rs:66` |

一句话：它不是打分权重，是**「在 rollout 分布里看重多少高分尾部」的偏置**。50 意味着根动作
基本按尾样本挑（近似 max），1.4 意味着接近均值但有尾部倾斜。

## 2. 是不是运行时读的？——读，但本仓决策面没有消费者

**读**：`crates/umaai-matsuri` 侧 `crates/matsuri-cli/src/state.rs:19` 定 `DATA_ROOT = "data"`，
`:63` 调 `Config::load_with_overlay_file(DATA_ROOT, ".")` ⇒ 基线 `data/ai_config.toml`
（`[search]` 在 `:49`，`radical_factor_max = 50.0` 在 `:57`）+ **当前工作目录同名覆盖层**合并。
覆盖层结构体 `SearchOverlay` 字段全 `Option`，含 `radical_factor_max`
（`../umaai_next/crates/umaai/src/config/mod.rs:328-352`，`:266` 处 apply）。

控制实验（证覆盖层真被解析）：`run_ctl/ai_config.toml` 写 `[search] bogus_key_38 = 1.0`，
报错逐字列出可接受字段，含 `radical_factor_max` ⇒ 解析路径活着（`out/pilot_ctl.log`）。

**但没有消费者**（本仓 HEAD，实测于 `e2a7642`）：

| 决策路径 | 是否吃到 TOML 的 `[search]` | 证据 |
|---|---|---|
| 强策档 = mccli 2-ply（`MC_POLICY=search`，面板用的那条） | **否**：`two_ply_rows` 自己对每个根动作做 `MC_K` 次 rollout 取均值再取 max，**完全不调 `FlatSearch`** | `crates/matsuri-cli/src/mccli.rs:112-190` |
| mccli 1-ply（`one_ply_rows`，调官方 `FlatSearch`） | **否**：`SearchConfig { search_n, max_depth:0, expected_search_stdev, ..SearchConfig::default() }` ⇒ `radical_factor_max` 走**代码默认 50.0**，与 TOML 无关 | `mccli.rs:58-75` |
| `bin matsuri`（`Runner` / `MatsuriTrainer` 生产档） | **否**：`main.rs:72` 建 Runner，`umaai/src` 里除 `config/mod.rs` 外无人引用 `SearchConfig`/`FlatSearch` | `crates/matsuri-cli/src/main.rs:21,72`；全量 grep |
| 引擎侧唯一消费者 | `umaai-tools` 的 `probe` 子命令（观测用，把 `cfg.search` 传给 `probe::search_root`） | `../umaai_next/crates/umaai-tools/src/cli.rs:949-952`、`probe.rs:85-91` |

⇒ **只改 `data/ai_config.toml:57` 在本仓不改变任何决策行为**。恒等证明见 §3。

## 3. 恒等证明：生产强策档对这个旋钮逐字不敏感

冻结 exe `arm_strong41.exe`（sha1 `c93c5e84f97fca80dc6192b9b3237271d58033bf`，与
`E:/HJM/_keileg/out/arm_strong41.exe` 逐字节相同；只读副本，未向 `_keileg` 写任何东西），
`MC_POLICY=search MC_K=8 MC_SEED=1 MC_N=3`，工作目录覆盖层分别 50.0 / 1.4：

```
out/pilot_r50.json  sha1 2004e804eb94a42706a9da834c2ff28d31807174
out/pilot_r14.json  sha1 2004e804eb94a42706a9da834c2ff28d31807174   ← 完全相同
```

批量层面的同一信号：`paired_gate_ci38.py` 逐片比对双臂整份 JSON，flat1 臂 17 片**无一相同**
（旋钮活着）；而上面这对 2-ply 对照片逐字节相同（旋钮无效）。

## 4. 为了能测这个旋钮，另搭了一条臂（`MC_POLICY=flat1`）

结构零不等于「旋钮无增益」。于是在一次性测量壳里加了一条臂，把**官方 1-ply FlatSearch 当整局
决策者**跑，`radical_factor_max` 只从合并后的配置读（`loaded.search.radical_factor_max`），
所以 A/B 动的正是 `data/ai_config.toml:57` 这一个字段，别的一个都没动。

- 壳：`E:/HJM/_wt_r50`（一次性，未并回主仓）。`shell/` 是 `git worktree add --detach` 在
  `282df51`，`CARGO_TARGET_DIR=E:/HJM/_wt_r50/target`；新增 `flatarm.rs`（`FlatTrainer`）+
  改 `state.rs`（把 `SearchConfig` 透出来）+ `dist.rs`（`MC_POLICY=flat1` 分派、记 `radical_max_used`）。
- 搜索**串行**（`with_parallel(false)`），保证浮点求和顺序在双臂间一致。
- 事件选择委托 `MatsuriRolloutTrainer`，双臂共用同一事件流 ⇒ 差异只落在训练决策上。
- exe：`E:/HJM/_wt_r50/arm_r50.exe` sha1 **`6b2bffe02d177707f8352487e1ccc006762c7562`**。
- 旋钮确实活着（同 seed 同 N，只有 TOML 不同）：

```
out/sens_f50.json  flat1(K=50,radical_max=50)  five_sum=7937.0 pt=7621.0
out/sens_f14.json  flat1(K=50,radical_max=1.4) five_sum=8189.5 pt=8090.5
out/pilot_f50k50.json 与 sens_f50.json 同 sha1（230faf9d…）⇒ 同参复跑逐字可复现
```

## 5. 配对 CRN 设计

| 项 | 值 |
|---|---|
| 起点局面 | `_state_t30.json`（t30 锚点报文，sha1 `948084ee45ce0d1457232981ecf95cd5ffccad88`，与 `_keileg` 副本同字） |
| seed 分片 | `MC_SEED` = 38001…38017（17 片；局内种子 `seed_base*0x5DEECE66D1CE4A1F + i*0xD1B54A32D192ED03`） |
| 每片局数 | `MC_N = 25` ⇒ 每臂 425 局 |
| 预算 | `MC_K = 64`（= `search_n`；面板生产值是 1024，这里受机时约束取 64，另跑 128 复现） |
| 双臂 | 只有工作目录覆盖层 `ai_config.toml` 的 `[search] radical_factor_max` = 50.0 vs 1.4 |
| 配对单位 | seed 分片；df = 16 |
| 并发 | `xargs -P 6`（≤6 引擎进程），单片约 330 s，34 片约 32 min |
| 脚本 | `E:/HJM/_wt_r50/run_batch.sh`（断点续跑：已产出且 `n` 对得上的分片跳过）、`paired_gate_ci38.py` |

## 6. 结果

**主批 K=64（17 片 × 25 局 × 2 臂；逐片 0/17 相同）** — 差 = r50 − r14，正数即 1.4 更优：

| 分量 | 臂 50.0 均 | 臂 1.4 均 | 配对差(1.4−50.0) | 95% t 区间 (df=16) | 判读 |
|---|---|---|---|---|---|
| 五维和 | 8163.2 | 8346.1 | **+182.9** | [+149.7, +216.2] | 显著为正 |
| 五维分 | 45108.4 | 47666.4 | **+2558.0** | [+2113.9, +3002.1] | 显著为正 |
| PT | 8005.0 | 8244.1 | **+239.1** | [+203.7, +274.6] | 显著为正 |

逐片符号：**17/17 三个分量全同侧**（五维和 −47.3…−334.9；五维分 −660.8…−4446.9；PT −108.4…−338.6）。
诊断：训练决策数两臂相同 20825；被発表会兜底改掉的动作数 50.0 臂 2748 vs 1.4 臂 2195
（⇒ 50 臂更多根选择落在非训练上，才会被兜底改写；这是旋钮生效的旁证）。

**复现 K=128（6 片 × 10 局，为排除「预算太薄 ⇒ 尾部挑选噪声」这一解释）**：
+119.6 [+59.2, +180.0]、+1680.0 [+941.0, +2419.0]、+192.0 [+132.6, +251.4]（均为 1.4−50.0，df=5），
**6/6 同号、0/6 片相同**。方向与幅度排序都不变。

## 7. 局限（别把这条读成「去改配置」）

1. **测的是自建臂，不是生产臂。** `flat1` 把官方 1-ply FlatSearch 当整局决策者；本仓面板走的
   2-ply 臂对这个旋钮**恒等不敏感**（§3）。改 `data/ai_config.toml:57` 在当前代码里是**空操作**。
   要吃到这个增益，得先把决策面接上 `FlatSearch` 并让 `search_n`/`radical` 从配置读——
   那是另一件事（接线 + 门禁），不是本文件的结论范围。
2. `search_n` 只测了 64/128，生产配置写 1024；旋钮与预算有交互（`radical_factor` 越大越靠尾样本，
   小预算下噪声更大），1024 下的增益幅度**未测**。
3. 只有一个起点局面（t30 锚点）、一个剧本（15）、一套卡组（报文卡组 6 张）。
4. 只测了 50.0 与 1.4 两点，没扫中间值；**1.4 这个数字的来源是 lab 的「旧仓生产档」二手说法**，
   不是本仓标定出来的最优值。本文件只支持「1.4 ≫ 50.0」，不支持「1.4 最优」。
5. 没有机制级解释为什么 50 更差（只有「根选择被尾样本牵走 → 更多落到非训练 → 被兜底改写」这条
   旁证）。按 AGENTS.md「机制没拆到公式只留档」，不写进 data/ 或配置。
6. lab 的 `+826` 与本仓 `+2558`（五维分）不同数量级，**不迁移**；lab 那句「此前 MC 无增益是因为
   跑在默认 50」在本仓**不成立**，因为本仓 2-ply 臂压根没读这个旋钮（也无 `FlatSearch`）。

## 8. 复现

```bash
# 0) 一次性壳里的测量臂（只读主仓，产物全在 _wt_r50）
cd /e/HJM/_wt_r50/shell && CARGO_TARGET_DIR=/e/HJM/_wt_r50/target cargo build --release --bin matsuri-dist
cp /e/HJM/_wt_r50/target/release/matsuri-dist.exe /e/HJM/_wt_r50/arm_r50.exe   # sha1 6b2bffe0…

# 1) 恒等证明（生产强策档，冻结 exe）
cd /e/HJM/_wt_r50/run_r50 && MC_POLICY=search MC_K=8 MC_SEED=1 MC_N=3 ../arm_strong41.exe _state_t30.json ../out/pilot_r50.json
cd /e/HJM/_wt_r50/run_r14 && MC_POLICY=search MC_K=8 MC_SEED=1 MC_N=3 ../arm_strong41.exe _state_t30.json ../out/pilot_r14.json
sha1sum /e/HJM/_wt_r50/out/pilot_r{50,14}.json     # 应逐字相同

# 2) 主批：17 片 × 25 局 × 2 臂（LIMIT 分波跑，避开 harness 对后台进程树的限速）
cd /e/HJM/_wt_r50 && bash run_batch.sh 64 6 6      # 重复调用直到 34 片齐

# 3) 判据：配对三分量 + 95% t 区间（df=分片数-1）
cd /e/HJM/_wt_r50 && python paired_gate_ci38.py --k 64 --a r50 --b r14

# 4) 复现臂（更大预算）
cd /e/HJM/_wt_r50 && bash run_rep38.sh 6 6 && python paired_gate_ci38.py --k 128 --a r50 --b r14
```

产物清单：`E:/HJM/_wt_r50/out/flat_k64_{r50,r14}_s380*.json`（34 片）、
`flat_k128_*`（12 片）、`pilot_*` / `sens_*` / `prov*_2ply`、各 `.log`、
`jobs_k64.txt` / `jobs_rep128.txt` / `skip_*.txt`。

## 9. 本次过程中的偏差记账

- 曾在 `E:/HJM/_keileg` 目录内跑过 `git status` / `git diff --stat`（意图只读，但 `git status`
  会刷新该工作树的索引缓存）。没有写任何跟踪文件，也没有向 `_keileg` 落任何产物。
- 两次误 `rm -f out/flat_k64_*` 删掉了约 18 个已完成分片，本文件所有数字来自**删后重跑的完整 34 片**。
- harness 对 `nohup`/后台进程树限速（每进程实测 ~9%–25% CPU，前台满速），故批次改成前台分波执行；
  `run_batch.sh` 因此加了断点续跑。
- 主仓工作区只读、无 git 写操作、未 push、未开新会话；本文件是新增未跟踪文件，**未 `git add`**。

## 10. 主线核对（集成会话②，同日）

用本仓自己的判据脚本对这条线的**原始分片**独立重聚，不复用它的 `paired_gate_ci38.py`：

```bash
cd /e/HJM/umaai-matsuri
python tools/forced_cp_ci.py --a "E:/HJM/_wt_r50/out/flat_k64_r50_s*.json" \
                             --b "E:/HJM/_wt_r50/out/flat_k64_r14_s*.json"
python tools/forced_cp_ci.py --a "E:/HJM/_wt_r50/out/flat_k128_r50_s*.json" \
                             --b "E:/HJM/_wt_r50/out/flat_k128_r14_s*.json"
```

| 核对项 | 本文（会话②自算） | 主线重聚（`tools/forced_cp_ci.py`，`t_16=2.120` / `t_5=2.571`） | 判定 |
|---|---|---|---|
| 五维和 K=64 | +182.9 [+149.7, +216.2] | +182.932 [+149.661, +216.203] | 逐位符 |
| 五维分 K=64 | +2558.0 [+2113.9, +3002.1] | +2558.0 [+2113.9, +3002.1] | 逐位符 |
| PT K=64 | +239.1 [+203.7, +274.6] | +239.127 [+203.652, +274.602] | 逐位符 |
| K=128 三分量 | +119.6 / +1680.0 / +192.0 | +119.600 / +1680.0 / +191.983（区间同） | 逐位符 |
| 逐片符号 | 17/17 三分量同侧 | 17/17，幅度 `+47.3…+334.9 / +660.8…+4446.9 / +108.4…+338.6` | 符 |
| 同种子双臂文件是否相同 | 0/17 | 0/17 | 符 |
| 生产强策档恒等（§3） | `pilot_r50` = `pilot_r14` | sha1 均 `2004e804eb…` | 符 |
| 冻结件出处 | `arm_r50.exe` / `arm_strong41.exe` / `_state_t30.json` | `6b2bffe0…` / `c93c5e84…`（与 `_keileg` 那份同字）/ `948084ee…` | 符 |
| 旋钮真被读到 | — | 分片里自带 `radical_max_used`＝`50.0` / `1.4`；`n=25` 双臂对齐 | 符 |

**口径提醒（不影响结论）**：`forced_cp_ci.py` 的 §④ 是 #48 专用的检查点段，这批 flat1 分片没有
`check_point_raw`，它会打印「这批是旧 exe，作废」——那是**脚本标签的错，不是这批数据作废**；
本条线只取它的 §①②③。会话②自己的 `paired_gate_ci38.py` 因此**不并回主仓**（与本仓判据同算法，
并回来只造第二个事实源），复现配方改用上表两条命令。

**裁决**：#38 关闭，结论按 §7-1 停在「**留档、不改配置**」——生产决策面不读这个旋钮（§2/§3 是恒等证明，
不是「效应小」），所以 §6 的显著增益**当前无处可用**；真要吃到它，得先把决策面接上 `FlatSearch`
并让 `search_n` / `radical` 从配置读，那是另一件事，且必须重开配对门禁（起点 t30、单剧本、单卡组）。
插桩与批次脚本已归档：`tools/patch_flat1_arm_for_measure.patch`（对 `_wt_r50/shell` 做
`git apply --check --reverse` 通过 ⇒ 它就是壳相对 `282df51` 的全量差，含 4 个未跟踪新文件），
`tools/run38_flat1_batch.sh`、`tools/run38_rep38.sh`。清理 `_wt_r50` 前先按 §8 迁走
`out/flat_k64_*` 与 `out/flat_k128_*` 共 46 份分片。

