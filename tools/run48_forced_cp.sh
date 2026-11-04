#!/bin/bash
# #48 发表会兜底的代价：配对 CRN 双臂
#   A 臂 = 兜底开（面板默认行为）；B 臂 = MC_NO_FORCED=1 关兜底
#   同 exe（含 CP 打点，已验证 RNG 中性）、同 MC_SEED/MC_N ⇒ 同一批初始局
set -u
cd "$(dirname "$0")"
EXE=out/arm_cp48.exe
K=${K:-8}
N=${N:-24}
SHARDS=${SHARDS:-8}
P=${P:-5}
jobs=""
for s in $(seq 1 "$SHARDS"); do
  jobs="$jobs A:$s B:$s"
done
echo "$jobs" | tr ' ' '\n' | grep . | xargs -P "$P" -I{} bash -c '
  pair="{}"; arm="${pair%%:*}"; s="${pair##*:}"
  if [ "$arm" = "B" ]; then export MC_NO_FORCED=1; fi
  MC_POLICY=search MC_K='"$K"' MC_SEED="$s" MC_N='"$N"' MC_DUMP=out/s48_${arm}_s${s}.jsonl \
    '"$EXE"' _state_t30.json out/s48_${arm}_s${s}.json \
    > out/s48_${arm}_s${s}.log 2>&1 || echo "FAIL {}"
'
echo "WAVE_DONE exit=$?"
