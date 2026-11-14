#!/usr/bin/env bash
# 把压缩的完整采集日志还原到 ./raw/ 下（不改动仓库内的 .gz 文件）
# 用法: ./unpack.sh [--verify]
#   --verify  只做 gzip 完整性校验，不解压
set -e
cd "$(dirname "$0")"

if [ "${1:-}" = "--verify" ]; then
    for gz in run*/*.gz; do
        gzip -t "$gz" && echo "OK  $gz"
    done
    exit 0
fi

mkdir -p raw
for gz in run*/*.gz; do
    run=$(dirname "$gz")
    name=$(basename "$gz" .gz)
    mkdir -p "raw/$run"
    gunzip -c "$gz" > "raw/$run/$name"
    printf '还原 %-32s %s 行\n' "raw/$run/$name" "$(wc -l < "raw/$run/$name")"
done

echo ""
echo "完成。还原后的文件在 raw/ 下（已由 .gitignore 排除，不会误提交）。"
