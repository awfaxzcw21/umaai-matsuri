#!/usr/bin/env bash
# #97：发表会档位（大成功率）到底被兜底买了多少 —— A10 现行 vs H10 关兜底，同 exe 同种子。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_cp
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for arm in A10 H10; do
  for s in 1 2 3 4 5; do
    ( if [ "$arm" = H10 ]; then export UMA_NO_FORCED_TARGET=1; else unset UMA_NO_FORCED_TARGET; fi
      MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
      MC_DUMP=$OUT/dump_${arm}_s$s.jsonl \
      ./target/release/matsuri-dist.exe _state_t30_cap10.json \
      $OUT/res_${arm}_s$s.json > $OUT/log_${arm}_s$s.txt 2>&1 ) &
  done
done
wait
echo "[done] A10 + H10 各 150 局"
