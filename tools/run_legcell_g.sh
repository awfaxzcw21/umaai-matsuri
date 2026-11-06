#!/usr/bin/env bash
# #95 G 臂：失败**不回复体力**（拿掉 +10 这个对冲）。判「引擎敢在低体力稽古」是不是因为
# 模型里失败≈便宜的回复。A7 对照＝同 exe 不设 env，必须与 A2 逐字节相同。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_g
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for arm in G A7; do
  for s in 1 2 3 4 5; do
    ( if [ "$arm" = G ]; then export UMA_FAIL_NO_HEAL=1; else unset UMA_FAIL_NO_HEAL; fi
      MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
      MC_DUMP=$OUT/dump_${arm}_s$s.jsonl \
      ./target/release/matsuri-dist.exe _state_t30_cap10.json \
      $OUT/res_${arm}_s$s.json > $OUT/log_${arm}_s$s.txt 2>&1 ) &
  done
done
wait
echo "[done] G + A7 各 150 局"
