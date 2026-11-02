#!/usr/bin/env bash
# 同一棵树、同一个 exe 的压缩开关 A/B：只翻剧本资产里的阈值，跑 CRN 同种子配对。
#
# 为什么不用改代码：剧本资产是**运行时**从 `data/` 读的（`data_dir()` = crate 清单目录上溯两级，
# 编译期固化），所以「改 JSON 里的阈值 + 重跑」就是纯配置 A/B，二进制一个字节没变。
# 判据照旧：只看终局三分量（五维和 / 五维分 / PT），配对差均值 + 95% 区间（总体 std × 1.96/√n），
# 区间跨 0 只留档不定论，禁止用系数凑。
#
# 用法：ab_softcap_oneside.sh <worktree> <N> <OUT.tsv> <资产字段名> <ON 值> <OFF 值> [训练员]
#   例：ab_softcap_oneside.sh /e/HJM/umaai-matsuri-softcap 10 docs/x.tsv status_soft_cap 1200 0 random
set -u
DIR="${1:?worktree 路径}"
N="${2:-10}"
OUT="${3:?输出 tsv}"
KEY="${4:?资产字段名}"
ON="${5:-1200}"
OFF="${6:-0}"
TR="${7:-random}"
BIN="$DIR/target/release/matsuri.exe"
[ -x "$BIN" ] || BIN="$DIR/target/release/matsuri"
[ -x "$BIN" ] || { echo "找不到 release 二进制：$BIN（先 cargo build --release -p matsuri-cli）" >&2; exit 1; }
JSON="$DIR/data/scenario_matsuri.json"
[ -f "$JSON" ] || { echo "找不到剧本资产：$JSON" >&2; exit 1; }
CARDS="303174, 303114, 303004, 303224, 303204, 303234"

overlay() { printf '[sim]\nseed = %s\ncards = [%s]\n' "$1" "$CARDS"; }

# 终局三分量：五维和（盒子里 5 个数相加）· 五维分（评分行）· PT
parse() {
    local five score pt a b c d e
    five=$(grep -E '^│ *[0-9]+' "$1" | tail -1 | grep -oE '[0-9]+' | head -5 | tr '\n' ' ')
    score=$(grep -E '^评分 ' "$1" | head -1 | grep -oE '[0-9]+' | head -1)
    pt=$(grep -E '^PT ' "$1" | head -1 | grep -oE '[0-9]+' | head -1)
    read -r a b c d e <<< "$five"
    echo "$((a+b+c+d+e)) ${score:-0} ${pt:-0}"
}

sweep() { # $1 = 阈值  $2 = 落盘文件
    : > "$2"
    for ((s = 0; s < N; s++)); do
        ( cd "$DIR" && overlay "$s" > ai_config.toml && "$BIN" "$TR" </dev/null > /tmp/ab_one.txt 2>/dev/null )
        read -r sum score pt <<< "$(parse /tmp/ab_one.txt)"
        for v in "$sum" "$score" "$pt"; do
            [ "$v" -gt 0 ] 2>/dev/null || { echo "seed $s PARSE_FAIL（$v）" >&2; exit 1; }
        done
        printf '%s\t%s\t%s\t%s\n' "$s" "$sum" "$score" "$pt" >> "$2"
    done
}

BK=$(mktemp); cp "$JSON" "$BK"
ORIG_ON=$(grep -oE "\"$KEY\": *[0-9-]+" "$JSON" | grep -oE '[0-9-]+$' | head -1)
[ "$ORIG_ON" = "$ON" ] || echo "⚠️ 资产里 $KEY 当前是 $ORIG_ON，不是 $ON（照旧跑，跑完恢复原值）" >&2

sweep "$ON" /tmp/ab_on.tsv
sed -i -E "s/(\"$KEY\": *)-?[0-9]+/\1$OFF/" "$JSON"
grep -qE "\"$KEY\": *$OFF" "$JSON" || { echo "❌ 阈值没翻成 $OFF，恢复后退出" >&2; cp "$BK" "$JSON"; exit 1; }
sweep "$OFF" /tmp/ab_off.tsv
cp "$BK" "$JSON"; rm -f "$BK" "$DIR/ai_config.toml" /tmp/ab_one.txt

# 配对差 = ON − OFF（ON 是带压缩的那一臂）
{
    printf 'seed\ton_sum\ton_score\ton_pt\toff_sum\toff_score\toff_pt\td_sum\td_score\td_pt\n'
    paste /tmp/ab_on.tsv /tmp/ab_off.tsv | awk -F'\t' '{
        printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n", $1,$2,$3,$4,$6,$7,$8,$2-$6,$3-$7,$4-$8
    }'
} > "$OUT"
rm -f /tmp/ab_on.tsv /tmp/ab_off.tsv

awk -F'\t' 'NR>1 {
    n++;
    for (c = 8; c <= 10; c++) { s[c] += $c; q[c] += $c * $c; if ($c > 0) p[c]++; else if ($c < 0) m[c]++; else z[c]++ }
} END {
    if (!n) exit
    print "n=" n "（配对差 = 压缩开 − 压缩关）"
    split("五维和 五维分 PT", name, " ")
    for (c = 8; c <= 10; c++) {
        mean = s[c] / n
        var = q[c] / n - mean * mean
        ci = 1.96 * sqrt(var / n)
        printf "%-6s 均值 %+9.1f  95%%CI [%+.1f, %+.1f]  同号 正%d/负%d/零%d\n",
            name[c - 7], mean, mean - ci, mean + ci, p[c], m[c], z[c]
    }
}' "$OUT"
echo "=== done: $OUT ==="
