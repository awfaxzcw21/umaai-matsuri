在跳出领域业务逻辑（如“根性锁”、“洗牌刷新”、“吾妻卡阶”等特定剧本策略）的框架后，从**底层计算、搜索算法、博弈论与机器学习**等纯算法与工程架构视角来看，`umaai-matsuri` 这类随机离散马尔可夫决策过程（Stochastic MDP）模拟器在算法层面依然存在巨大的提升空间。

以下是针对 `umaai-matsuri` 模拟器在**算法层（Algorithm Layer）**的 4 大核心优化维度与重构方案：

---

### 一、 搜索与决策算法优化（Search & Decision Algorithms）

目前模拟器主要依赖单步或浅层启发式打分（Heuristic Evaluator）。由于赛马娘育成存在大量的随机性（随机分布、事件触发、失败率判定），直接使用传统的 Minimax 或纯单步估值极易失效。

#### 1. 带概率节点的期望最大化蒙特卡洛树搜索（Expectimax MCTS / Chance-Node MCTS）
* **算法瓶颈**：标准 MCTS 适用于棋类等确定性博弈，而在赛马娘中，训练员决定动作（Action Node）后，环境会通过概率分布（Chance Node）决定人头分布与随机事件。
* **算法优化**：引入 **Expectimax MCTS** 或 **Sparse Sampling（稀疏采样树）**。在决策树中显式建模“决策节点”与“概率节点”：
  * **决策节点**：选择最佳动作（训练、比赛、外出等）。
  * **概率节点**：根据得意率、不在率公式，对下回合可能的 $N$ 种人头分布进行概率加权采样（Chance Sampling），计算动作的**期望 Q 值（Expected Q-Value）**而非单一结局，从而精准评估高风险/高收益动作的稳健性。

#### 2. 共享随机数流（Common Random Numbers, CRN）方差控制
* **算法瓶颈**：在评估动作 A（如踩速度稽古）与动作 B（如外出刷新）时，如果各自使用独立的随机种子进行蒙特卡洛 Rollout，随机事件的巨大方差会掩盖动作本身的优劣。
* **算法优化**：采用 Monte Carlo 统计学中的 **CRN（Common Random Numbers）对偶采样技术**。在从同一状态  $S_t$ 对不同候选动作 $A_1, A_2, \dots$  进行长程模拟时，**强制 downstream 的随机事件流（如第 $t+1 \sim t+n$ 回合的随机事件和人头分布）使用相同的随机数种子**。这能消除 90% 以上的环境随机噪声，用极少的采样次数（如 5~10 次 Rollout）即可精准比较出动作间的相对边际收益。

#### 3. 动作空间与状态置换表（Transposition Table & Zobrist Hashing）
* **算法瓶颈**：在长程前瞻搜索（如夏合宿 4 轮爆搜或末段 URA 规划）中，不同动作序列可能导向完全相同的局面（例如：“第 1 回合外出 + 第 2 回合休息”与“第 1 回合休息 + 第 2 回合外出”后的属性与体力状态基本等价）。
* **算法优化**：
  * 为 `MatsuriView` 设计基于 **Zobrist Hash** 的 64 位/128 位状态哈希签名。
  * 建立**置换表（Transposition Table）**缓存已搜索过的局面子树评估值，避免重合路径的重复模拟，将多轮前瞻爆搜的时空复杂度由指数级$O(k^d)$降低至多项式级。

---

### 二、 估值函数与模型拟合优化（Value Function & Model Approximations）

目前代码中的打分函数（如 `pt_favor` 和 `status_catchup`）多为线性加权，无法拟合非线性与强耦合的面板收益。

#### 1. 分段非线性边际价值函数（Piecewise Non-Linear Value Approximator）
* **算法瓶颈**：属性（1200 属性减半、上限 2200 封顶）与技能点（Skill PT）的真实价值是非线性的。线性打分器在属性接近上限时容易发生“估值崩塌”或“盲目溢出”。
* **算法优化**：将静态线性权重重构为**分段凸/凹函数（Piecewise Value Function）**或 **Sigmoid 衰减边际函数**：

$$V(\Delta S) = w_i \cdot \frac{1}{1 + e^{\alpha (S_{current} - S_{target})}} \cdot \Delta S$$

  使模型在属性低位时表现出高求胜欲，在接近 2200 上限时自动将评估权重平滑转移至副属性与 Skill PT，实现无缝的收益切换。

#### 2. 从遗传算法（GA）转向离线强化学习（TD(\\(\lambda\\)) / Actor-Critic / PPO）
* **算法瓶颈**：目前仓内使用的遗传算法（GA，如 `lab ga-r2`）本质上是在全局标量空间内寻找一组固定超参数，容易对特定种子（Seed）或特定卡组发生**过拟合（Overfitting）**。
* **算法优化**：将训练器升级为**基于离线 Reinforcement Learning（RL）的神经网络价值函数 $V_\theta(S)$**：
  * 使用 **TD($\lambda$)（时序差分学习）** 或 **PPO（近端策略优化）**，以海量自我博弈（Self-Play）产生的局面前后转换作为样本。
  * 让神经网络拟合从当前状态 $S_t$ 直至 77 回合结束时的**最终查定评分期望（Expected Endgame Score）**，替代人工手写规则。

#### 3. 教诲分配的 Counterfactual Regret Minimization（CFR）剪枝
* **算法瓶颈**：在第 2/11/23/35/47/59 回合的教诲分配窗口中，空间存在 $5! = 120$ 种排列。如果全局暴力评估 120 种方案，计算开销巨大。
* **算法优化**：引入 **CFR（反悔值最小化）** 或 **Top-K 支配性剪枝（Dominance Pruning）**。通过分析当前卡组构筑与卡片所在位置，提前剔除明显被支配的方案（例如非速卡挂速度特化），将 120 种组合瞬间剪枝至 Top-3~Top-5 候选集后再进行深层评估。

---

### 三、 底层计算性能与工程架构优化（Engine & Memory Architecture）

算法效率上限取决于单位时间内能够跑多少局模拟。

#### 1. 零内存分配的栈化局面表示（Stack-Allocated Bitboard State Representation）
* **算法瓶颈**：在 MCTS 或 Monte Carlo Rollout 中，每次克隆 `MatsuriGame` 局面都会发生堆内存分配（Heap Allocation），导致 CPU Cache Miss 和 GC/内存回收开销。
* **算法优化**：将 `MatsuriGame` 与 `MatsuriView` 中的核心数据结构（人头落位、演目槽 0-5、教诲挂载位等）压缩重构成 **Bitboard（位图掩码，如 `u128` / `u64`）**：
  * 使得整局游戏状态尺寸控制在几十个字节以内，完全满足 Rust 的 `Copy` trait 约束。
  * 局面克隆与状态迁移完全在 CPU 寄存器或 L1 Cache 中完成，实现**零堆分配（Zero-Allocation Simulation）**，可将单核模拟吞吐量提升 10~50 倍。

#### 2. SIMD 向量化与多线程批量并行采样（SIMD Batched Simulation）
* **算法瓶颈**：逐局串行跑模拟无法充分利用现代化 CPU 的 AVX-512 / NEON 向量指令集与 GPU 算力。
* **算法优化**：利用 Rust 的 `std::simd` 或 OpenCL/CUDA，将 32/64 局模拟的训练公式计算、失败率判定与属性累加转化为**向量化并行计算（SIMD Batch Execution）**，在大幅缩短 GA 调参或 RL 训练周期的同时，支持在线实时毫秒级高并发 MCTS 决策。

---

### 四、 总结：算法层演进路线图

| 优化维度 | 现状（当前 `umaai-matsuri` 实现） | 算法层重构目标 | 预期算法收益 |
| :--- | :--- | :--- | :--- |
| **搜索机制** | 单回合 Greedy 启发式打分 | **Expectimax MCTS + CRN 方差控制** | 消除长程随机性盲区，准确评估“洗牌刷新”与“合宿控体” |
| **估值模型** | 手工线性权重 + GA 遗传算法调参 | **分身/质变点非线性函数 + TD(\\(\lambda\\)) RL 神经网络** | 解决 GA 调参过拟合问题，拟合复杂的非线性面板与 PT 乘区 |
| **决策剪枝** | 120 种教诲排列全量评估 | **CFR 反悔值最小化 + 动作支配性剪枝** | 搜索剪枝率达 90%+，显著降低计算复杂度 |
| **底层性能** | 堆分配局面克隆 | **Bitboard 栈化内存 + SIMD 向量化采样** | 吞吐量提升 10–50 倍，支持每秒数十万次在线 Rollout |

通过在** Expectimax 搜索、CRN 方差控制、非线性价值拟合与 Bitboard 底层性能**这 4 个纯算法维度进行重构，模拟器不仅能摆脱对手写规则的依赖，还能在理论上自我博弈出人类未曾发现的“极值走法”，真正实现从 LG0 到 LF4 以上上限的跨越。