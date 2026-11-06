#!/usr/bin/env bash
# #93 E 臂：只接「教诲 effect_type=18 ⇒ 失败豁免」这一条（不动公式、不动罚形、不动别的腿）。
# A4 对照：同 exe 不设 env ⇒ 与 A2/A 逐字节比，证明加开关本身不改行为。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_e
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for arm in E A4; do
  for s in 1 2 3 4 5; do
    ( if [ "$arm" = E ]; then export UMA_ADVICE_18_NOFAIL=1; else unset UMA_ADVICE_18_NOFAIL; fi
      MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
      MC_DUMP=$OUT/dump_${arm}_s$s.jsonl \
      ./target/release/matsuri-dist.exe _state_t30_cap10.json \
      $OUT/res_${arm}_s$s.json > $OUT/log_${arm}_s$s.txt 2>&1 ) &
  done
done
wait
echo "[done] E + A4 各 150 局"
