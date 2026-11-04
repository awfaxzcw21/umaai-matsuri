#!/usr/bin/env bash
# #38 双臂配对 CRN 批次：radical_factor_max 50.0 vs 1.4（只改工作目录 ai_config.toml 覆盖层）
# 用法：bash run_batch.sh <K> [并发数] [本次最多派发几个分片]
set -u
K=${1:-128}
PAR=${2:-6}
LIMIT=${3:-0}
ROOT=/e/HJM/_wt_r50
N=25
SEEDS="38001 38002 38003 38004 38005 38006 38007 38008 38009 38010 38011 38012 38013 38014 38015 38016 38017"

: > "$ROOT/out/jobs_k$K.txt"
for s in $SEEDS; do
  for arm in r50 r14; do
    o="$ROOT/out/flat_k${K}_${arm}_s${s}.json"
    # 断点续跑：已产出且能解析出 n/games 的分片跳过（同 seed 同 exe 结果逐字可复现）
    if [ -s "$o" ] && grep -qE "\"n\": ?$N\b" "$o"; then
      echo "skip $arm s$s" >> "$ROOT/out/skip_k$K.txt"
      continue
    fi
    echo "cd $ROOT/run_$arm && MC_POLICY=flat1 MC_K=$K MC_SEED=$s MC_N=$N ../arm_r50.exe _state_t30.json ../out/flat_k${K}_${arm}_s${s}.json > ../out/flat_k${K}_${arm}_s${s}.log 2>&1" >> "$ROOT/out/jobs_k$K.txt"
  done
done
echo "jobs=$(wc -l < "$ROOT/out/jobs_k$K.txt") parallel=$PAR limit=$LIMIT K=$K N=$N"
if [ "$LIMIT" -gt 0 ]; then
  head -n "$LIMIT" "$ROOT/out/jobs_k$K.txt" | xargs -P "$PAR" -d '\n' -I CMD bash -c CMD
else
  xargs -P "$PAR" -d '\n' -I CMD bash -c CMD < "$ROOT/out/jobs_k$K.txt"
fi
echo "done jobs_k$K"
