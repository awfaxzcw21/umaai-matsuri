#!/usr/bin/env bash
# #96 I 臂：兜底选格加「失败已被豁免优先」这一层（UMA_FORCED_IMMUNE_PREF=1）。
# A9 对照＝同 exe 不设 env ⇒ 必须与 A2/A7/A8 逐字节相同。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_i
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for arm in I A9; do
  for s in 1 2 3 4 5; do
    ( if [ "$arm" = I ]; then export UMA_FORCED_IMMUNE_PREF=1; else unset UMA_FORCED_IMMUNE_PREF; fi
      MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
      MC_DUMP=$OUT/dump_${arm}_s$s.jsonl \
      ./target/release/matsuri-dist.exe _state_t30_cap10.json \
      $OUT/res_${arm}_s$s.json > $OUT/log_${arm}_s$s.txt 2>&1 ) &
  done
done
wait
echo "[done] I + A9 各 150 局"
