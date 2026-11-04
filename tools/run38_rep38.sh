#!/usr/bin/env bash
# #38 复现臂：更大 rollout 预算（K=128）下 radical_factor_max 50.0 vs 1.4 的配对 CRN
# 用法：bash run_rep38.sh [并发数] [本次最多派发几个分片]
set -u
PAR=${1:-6}
LIMIT=${2:-0}
ROOT=/e/HJM/_wt_r50
K=128
N=10
SEEDS="38001 38002 38003 38004 38005 38006"
J="$ROOT/out/jobs_rep128.txt"

: > "$J"
: > "$ROOT/out/skip_rep128.txt"
for s in $SEEDS; do
  for arm in r50 r14; do
    o="$ROOT/out/flat_k${K}_${arm}_s${s}.json"
    if [ -s "$o" ] && grep -qE "\"n\": ?$N\b" "$o"; then
      echo "$arm s$s" >> "$ROOT/out/skip_rep128.txt"
      continue
    fi
    echo "cd $ROOT/run_$arm && MC_POLICY=flat1 MC_K=$K MC_SEED=$s MC_N=$N ../arm_r50.exe _state_t30.json ../out/flat_k${K}_${arm}_s${s}.json > ../out/flat_k${K}_${arm}_s${s}.log 2>&1" >> "$J"
  done
done
echo "jobs=$(wc -l < "$J") skipped=$(wc -l < "$ROOT/out/skip_rep128.txt") parallel=$PAR limit=$LIMIT K=$K N=$N"
if [ "$LIMIT" -gt 0 ]; then
  head -n "$LIMIT" "$J" | xargs -P "$PAR" -d '\n' -I CMD bash -c CMD
else
  xargs -P "$PAR" -d '\n' -I CMD bash -c CMD < "$J"
fi
echo "done rep128"
