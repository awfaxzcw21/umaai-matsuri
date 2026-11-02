#!/usr/bin/env bash
# CRN 同种子配对对比：两仓同卡组同种子跑 `matsuri random`，比终局三分量（五维和/PT）。
# 判据（交接包 §5 铁律）：只看五维和/五维分/PT 在同种子逐局配对下的贴近幅度；跨 0 留档不定论。
set -u
OURS=/e/HJM/umaai-matsuri
UP=/e/HJM/umaai-matsuri-upstream
CARDS="303174, 303114, 303004, 303224, 303204, 303234"
N="${1:-30}"          # 种子数（0..N-1）
OUT="${2:-/tmp/crn_compare.tsv}"

overlay() { printf '[sim]\nseed = %s\ncards = [%s]\n' "$1" "$CARDS"; }

# 提取终局五维行（盒子的数字行）与 PT 行，输出 "five1 five2 five3 five4 five5 pt"
parse() {  # $1 = 输出文件
    local five pt
    five=$(grep -E '^│ *[0-9]+' "$1" | tail -1 | grep -oE '[0-9]+' | head -5 | tr '\n' ' ')
    pt=$(grep -E '^PT ' "$1" | head -1 | grep -oE '[0-9]+')
    echo "${five}${pt}"
}

: > "$OUT"
printf 'seed\tours_five_sum\tours_pt\tup_five_sum\tup_pt\tdelta_sum\tdelta_pt\n' > "$OUT"

for ((s=0; s<N; s++)); do
    ( cd "$OURS" && overlay "$s" > ai_config.toml && ./target/release/matsuri.exe random 2>/dev/null > /tmp/crn_ours.txt )
    ( cd "$UP"   && overlay "$s" > ai_config.toml && ./target/release/matsuri random 2>/dev/null > /tmp/crn_up.txt )
    read -r o1 o2 o3 o4 o5 opt <<< "$(parse /tmp/crn_ours.txt)"
    read -r u1 u2 u3 u4 u5 upt <<< "$(parse /tmp/crn_up.txt)"
    osum=$((o1+o2+o3+o4+o5)); usum=$((u1+u2+u3+u4+u5))
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$s" "$osum" "$opt" "$usum" "$upt" "$((osum-usum))" "$((opt-upt))" >> "$OUT"
done

rm -f "$OURS/ai_config.toml" "$UP/ai_config.toml" /tmp/crn_ours.txt /tmp/crn_up.txt
echo "=== done, results in $OUT ==="
cat "$OUT"
