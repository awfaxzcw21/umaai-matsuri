#!/usr/bin/env bash
# #94 A6 臂：行为＝现行 A，只多打「稽古落子时该演目挂的教诲 id」分布。
# 自检：A6 与 A2（上一版 exe）同种子逐字节相同 ⇒ 打点只读不写。
set -euo pipefail
OUT=E:/HJM/umaai-matsuri/out/legcell_advice2
cd E:/HJM/_wt_legcell
mkdir -p "$OUT"
for s in 1 2 3 4 5; do
  ( unset UMA_KEIKO_NO_FAIL UMA_KEIKO_FAIL_NOPENALTY UMA_KEIKO_SP_LIMIT_ADD UMA_ADVICE_18_NOFAIL
    MC_POLICY=search MC_K=64 MC_N=30 MC_SEED=$s \
    MC_DUMP=$OUT/dump_A6_s$s.jsonl \
    ./target/release/matsuri-dist.exe _state_t30_cap10.json \
    $OUT/res_A6_s$s.json > $OUT/log_A6_s$s.txt 2>&1 ) &
done
wait
echo "[done] A6 150 局（教诲分布）"
