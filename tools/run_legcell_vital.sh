#!/usr/bin/env bash
# #93 第2步：量引擎稽古落子时的体力分布（A5 行为＝现行 A，只是多了只读 histogram）。
# 自检：A5 与 A2（上一版 exe）同种子必须逐字节相同，否则 histogram 污染了行为、下面读数作废。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_vital
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for s in 1 2 3 4 5; do
  ( unset UMA_KEIKO_NO_FAIL UMA_KEIKO_FAIL_NOPENALTY UMA_KEIKO_SP_LIMIT_ADD UMA_ADVICE_18_NOFAIL
    MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
    MC_DUMP=$OUT/dump_A5_s$s.jsonl \
    ./target/release/matsuri-dist.exe _state_t30_cap10.json \
    $OUT/res_A5_s$s.json > $OUT/log_A5_s$s.txt 2>&1 ) &
done
wait
echo "[done] A5 150 局（体力直方图）"
