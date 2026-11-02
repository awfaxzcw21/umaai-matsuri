#!/usr/bin/env bash
set -u
OURS=/e/HJM/umaai-matsuri
UP=/e/HJM/umaai-matsuri-upstream
CARDS="303174, 303114, 303004, 303224, 303204, 303234"
N="${1:-5}"
# 提取每回合 PT：行形如 "T64  体 .. PT 2393" -> "64 2393"
trace() { grep -E '^T[0-9]+ ' "$1" | sed -E 's/^T([0-9]+).*PT ([0-9]+).*/\1 \2/'; }
declare -A do_pt dup_pt dturn
for ((s=0; s<N; s++)); do
  ( cd "$OURS" && printf '[sim]\nseed = %s\ncards = [%s]\n' "$s" "$CARDS" > ai_config.toml && ./target/release/matsuri.exe random 2>/dev/null > /tmp/t_o.txt )
  ( cd "$UP" && printf '[sim]\nseed = %s\ncards = [%s]\n' "$s" "$CARDS" > ai_config.toml && ./target/release/matsuri random 2>/dev/null > /tmp/t_u.txt )
  while read -r t v; do do_pt[$t]=$(( ${do_pt[$t]:-0} + v )); dturn[$t]=1; done < <(trace /tmp/t_o.txt)
  while read -r t v; do dup_pt[$t]=$(( ${dup_pt[$t]:-0} + v )); dturn[$t]=1; done < <(trace /tmp/t_u.txt)
done
rm -f "$OURS/ai_config.toml" "$UP/ai_config.toml" /tmp/t_o.txt /tmp/t_u.txt
printf 'turn\tours_pt\tup_pt\tdelta(up-ours)\n'
for t in $(seq 1 78); do
  [ "${dturn[$t]:-0}" = 1 ] || continue
  o=${do_pt[$t]:-0}; u=${dup_pt[$t]:-0}
  printf '%s\t%s\t%s\t%s\n' "$t" "$((o/N))" "$((u/N))" "$(((u-o)/N))"
done
