#!/usr/bin/env bash
# #95 H 臂：关掉发表会兜底（forced_target 让搜索分数让位那条硬规则）。
# A8 对照＝同 exe 不设 env，必须与 A2/A7 逐字节相同。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_h
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for arm in H A8; do
  for s in 1 2 3 4 5; do
    ( if [ "$arm" = H ]; then export UMA_NO_FORCED_TARGET=1; else unset UMA_NO_FORCED_TARGET; fi
      MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
      MC_DUMP=$OUT/dump_${arm}_s$s.jsonl \
      ./target/release/matsuri-dist.exe _state_t30_cap10.json \
      $OUT/res_${arm}_s$s.json > $OUT/log_${arm}_s$s.txt 2>&1 ) &
  done
done
wait
echo "[done] H + A8 各 150 局"
