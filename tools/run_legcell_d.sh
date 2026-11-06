#!/usr/bin/env bash
# #89 D 臂：把演目 Lv 的 `sp_limit`（表侧 single_mode_15_act_effect 的 109 那列，
# 引擎 `data.rs` 解析出来却全仓没人用）当**加项**进稽古 SP 腿。
# 判据不是分数，是 **格子表本身**：`legcell_table.py` 的 festa 列 引擎/真机 比值
# 从 0.42~0.52×（高 Lv）挪不挪到 1.0× 附近。同种子同 exe，只差这一个 env。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_d
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for s in 1 2 3 4 5; do
  ( export UMA_KEIKO_SP_LIMIT_ADD=1
    MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
    MC_DUMP=$OUT/dump_D_s$s.jsonl \
    ./target/release/matsuri-dist.exe _state_t30_cap10.json \
    $OUT/res_D_s$s.json > $OUT/log_D_s$s.txt 2>&1 ) &
done
# A3 对照：新 exe 但不设任何 env ⇒ 与 A2（上一版 exe）逐局逐字节比，证明「加 D 开关本身不改行为」
( MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=1     MC_DUMP=$OUT/dump_A3_s1.jsonl     ./target/release/matsuri-dist.exe _state_t30_cap10.json     $OUT/res_A3_s1.json > $OUT/log_A3_s1.txt 2>&1 ) &
wait
echo "[done] D 臂 150 局 ＋ A3 对照 30 局"
