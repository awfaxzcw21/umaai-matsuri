#!/usr/bin/env bash
# #90 A″ 臂：行为与 A 完全相同（不设任何 env），只是多打「SP 那一列的链」逐层读数。
# 自检：A″ 与 A2（同种子、上一版 exe）必须逐字节相同 ⇒ 证明新打点只读不写。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_sp
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for s in 1 2 3 4 5; do
  ( unset UMA_KEIKO_NO_FAIL UMA_KEIKO_FAIL_NOPENALTY UMA_KEIKO_SP_LIMIT_ADD
    MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
    MC_DUMP=$OUT/dump_SPA_s$s.jsonl \
    ./target/release/matsuri-dist.exe _state_t30_cap10.json \
    $OUT/res_SPA_s$s.json > $OUT/log_SPA_s$s.txt 2>&1 ) &
done
wait
echo "[done] A″ 150 局"
