#!/usr/bin/env bash
# Сборка rvm.exe (Git Bash / MSYS2). Кириллический OneDrive-путь не должен
# попадать в gcc: исходник зеркалится в ASCII-путь out-дерева.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${DOOMREGEX_OUT:-/c/dev/doom-regex-out}"
PCRE2_BUILD="$OUT/build/pcre2"
PCRE2_SRC="$OUT/build/src/pcre2"
MIRROR="$OUT/build/src/driver"

command -v gcc >/dev/null || export PATH="/c/msys64/mingw64/bin:$PATH"

mkdir -p "$MIRROR"
cp "$REPO/driver/rvm_driver.c" "$MIRROR/"

gcc -O2 -std=c11 -Wall -Wextra -static \
    -DPCRE2_CODE_UNIT_WIDTH=8 -DPCRE2_STATIC \
    -I "$PCRE2_BUILD" -I "$PCRE2_SRC/src" \
    "$MIRROR/rvm_driver.c" \
    "$PCRE2_BUILD/libpcre2-8.a" \
    -o "$OUT/build/rvm.exe"

"$OUT/build/rvm.exe" --selftest
echo "OK: $OUT/build/rvm.exe"
