#!/usr/bin/env bash
# #88 稽古失败模型 A/B：同一个插桩 exe、同 5 组种子（MC_SEED=1..5 × MC_N=30 ＝ 150 局），
# 只差一个环境变量 UMA_KEIKO_NO_FAIL ⇒ B 臂稽古手不套训练失败模型。
# 必须在测量壳根目录跑（exe 读相对路径的 data/、_state_t30_cap10.json）。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_ab
cd E:/HJM/_wt_legcell
for arm in fail nofail; do
  for s in 1 2 3 4 5; do
    if [ "$arm" = nofail ]; then export UMA_KEIKO_NO_FAIL=1; else unset UMA_KEIKO_NO_FAIL; fi
    ( if [ "$arm" = nofail ]; then export UMA_KEIKO_NO_FAIL=1; else unset UMA_KEIKO_NO_FAIL; fi
      MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
      MC_DUMP=$OUT/dump_AB_${arm}_s$s.jsonl \
      ./target/release/matsuri-dist.exe _state_t30_cap10.json \
      $OUT/res_AB_${arm}_s$s.json > $OUT/log_AB_${arm}_s$s.txt 2>&1 ) &
  done
done
wait
echo "[done] both arms 150 局"
