#!/usr/bin/env bash
# #88 补两臂：C＝`UMA_KEIKO_FAIL_NOPENALTY=1`（照掷失败、失败时什么都不发生）
#        A2＝新 exe 但不设任何 env ⇒ 与旧 exe 的 A 臂逐字节对照，证明「加开关本身不改行为」。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_ab
cd E:/HJM/_wt_legcell
for arm in C A2; do
  for s in 1 2 3 4 5; do
    ( if [ "$arm" = C ]; then export UMA_KEIKO_FAIL_NOPENALTY=1; else unset UMA_KEIKO_FAIL_NOPENALTY UMA_KEIKO_NO_FAIL; fi
      MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
      MC_DUMP=$OUT/dump_${arm}_s$s.jsonl \
      ./target/release/matsuri-dist.exe _state_t30_cap10.json \
      $OUT/res_${arm}_s$s.json > $OUT/log_${arm}_s$s.txt 2>&1 ) &
  done
done
wait
echo "[done] C + A2 各 150 局"
