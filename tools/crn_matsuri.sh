#!/usr/bin/env bash
# CRN 同种子配对对比（matsuri 真实档）：两仓同卡组同种子各跑 `matsuri matsuri`，比终局三分量。
# 与 tools/crn_compare.sh 的区别 = 训练员换成**各仓自己那份**（我们 `MatsuriTrainer` 样例 / 上游 S7 打分函数），
# 所以这里的差 = 剧本机制差 **+ 决策层差** 的混合，读的时候必须带这条口径。
# 判据不变：只看三分量（五维和 / 五维分 / PT）配对差的贴近幅度 95% 区间；跨 0 留档不定论；禁系数凑。
set -u
OURS="${OURS:-/e/HJM/umaai-matsuri}"   # 可指到别的 worktree（如一次性 rollout worktree）
UP="${UP:-/e/HJM/umaai-matsuri-upstream}"   # 也可指到别的 worktree（同仓 A/B：改过 vs 未改）
# 对侧二进制名：上游 worktree 是 `matsuri`（无扩展名），Windows 构建的是 `matsuri.exe`
UPBIN="${UPBIN:-./target/release/matsuri}"
CARDS="303174, 303114, 303004, 303224, 303204, 303234"
N="${1:-30}"
OUT="${2:-/tmp/crn_mz.tsv}"
# 训练员：默认 matsuri（各仓自己那份，差 = 机制差 + 决策层差）；传 random = 共享引擎那份（差 = 纯机制差）
TR="${3:-matsuri}"
# 上游训练员名（默认同 $3）：「各仓最强策略」对拼时我们叫 rollout、上游叫 matsuri
UPTR="${4:-$TR}"

overlay() { printf '[sim]\nseed = %s\ncards = [%s]\n' "$1" "$CARDS"; }

# 终局三分量：五维和（盒子里 5 个数相加）· 五维分（评分行）· PT
parse() {
    local five score pt
    five=$(grep -E '^│ *[0-9]+' "$1" | tail -1 | grep -oE '[0-9]+' | head -5 | tr '\n' ' ')
    score=$(grep -E '^评分 ' "$1" | head -1 | grep -oE '[0-9]+' | head -1)
    pt=$(grep -E '^PT ' "$1" | head -1 | grep -oE '[0-9]+' | head -1)
    read -r a b c d e <<< "$five"
    echo "$((a+b+c+d+e)) ${score:-0} ${pt:-0}"
}

: > "$OUT"
printf 'seed\tours_sum\tours_score\tours_pt\tup_sum\tup_score\tup_pt\td_sum\td_score\td_pt\n' > "$OUT"

for ((s=0; s<N; s++)); do
    ( cd "$OURS" && overlay "$s" > ai_config.toml && ./target/release/matsuri.exe "$TR" </dev/null > /tmp/mz_o.txt 2>/dev/null )
    ( cd "$UP"   && overlay "$s" > ai_config.toml && ./target/release/matsuri  "$UPTR" </dev/null > /tmp/mz_u.txt 2>/dev/null )
    read -r os osc opt <<< "$(parse /tmp/mz_o.txt)"
    read -r us usc upt <<< "$(parse /tmp/mz_u.txt)"
    # 自检：任何一格没解析出正数就是 parse 失效（别把 0 当数据写进表）
    for v in "$os" "$osc" "$opt" "$us" "$usc" "$upt"; do
        [ "$v" -gt 0 ] 2>/dev/null || { echo "seed $s PARSE_FAIL: ours=[$os/$osc/$opt] up=[$us/$usc/$upt]" >&2; continue 2; }
    done
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$s" "$os" "$osc" "$opt" "$us" "$usc" "$upt" \
        "$((os-us))" "$((osc-usc))" "$((opt-upt))" >> "$OUT"
done

rm -f "$OURS/ai_config.toml" "$UP/ai_config.toml" /tmp/mz_o.txt /tmp/mz_u.txt
echo "=== done: $OUT ==="
tail -n +1 "$OUT"
