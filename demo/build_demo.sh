#!/usr/bin/env bash
# Сборка скачиваемого демо-пакета (папка dist/doom-regex-demo):
#   doomregex_demo.exe (вьювер-лаунчер, Win32/GDI, без зависимостей)
#   rvm.exe            (честная машина: цикл pcre2_substitute)
#   rules_rvm.rgxset   (540 правил, sha256 проверяется при загрузке)
#   snapshot.rvstate   (строка-состояние у входа в рендер кадра E1M1)
#   README.txt
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${DOOMREGEX_OUT:-/c/dev/doom-regex-out}"
DIST="$OUT/dist/doom-regex-demo"
SNAP="${1:-$OUT/run/g2d_fb_snapshot.rvstate}"

export TMP="$OUT/tmp" TEMP="$OUT/tmp"
mkdir -p "$OUT/tmp" "$DIST"

# зеркалим исходник вьювера в ASCII-путь (кириллица в пути ломает gcc)
mkdir -p "$OUT/build/src/demo"
cp "$REPO/demo/viewer/doomregex_demo.c" "$REPO/demo/viewer/playpal.h" \
   "$OUT/build/src/demo/"

/c/msys64/mingw64/bin/gcc -O2 -Wall -mwindows \
    -o "$DIST/doomregex_demo.exe" \
    "$OUT/build/src/demo/doomregex_demo.c" -lgdi32

cp "$OUT/build/rvm.exe" "$DIST/"
cp "$REPO/vm/rules_rvm.rgxset" "$DIST/"
[ -f "$SNAP" ] && cp "$SNAP" "$DIST/snapshot.rvstate" \
    || echo "ВНИМАНИЕ: снапшот не найден ($SNAP) — положи snapshot.rvstate руками"

cat > "$DIST/README.txt" <<'EOF'
DOOM, посчитанный заменой текста — живое демо
=============================================

Запуск: двойной клик doomregex_demo.exe

УПРАВЛЕНИЕ (это интерактивный DOOM, машина считает ~3 минуты на кадр -
нажимайте и ждите, слайд-шоу честное): WASD/стрелки - движение,
Ctrl - огонь, Пробел - открыть дверь, Esc/Enter - меню.
Нажатия пишутся в input.bin, машина читает их своей инструкцией GETC
из зоны |IN: строки-состояния - драйвер лишь дописывает байты в хвост.

Что происходит: rvm.exe — машина, у которой есть ровно одна операция,
глобальная regex-подстановка над строкой-состоянием (алгоритм Маркова,
540 правил, PCRE2, ~800 замен/с на 90-МБ строке). В строке лежат регистры, память, программа (DOOM,
скомпилированный через 8cc/ELVM) и видеозона. Окно показывает кадр E1M1,
который машина рендерит прямо сейчас, и ленту подстановок: правило,
что оно съело и что подставило.

Файлы: doomregex_demo.exe (вьювер), rvm.exe (машина),
rules_rvm.rgxset (правила, sha256 проверяется), snapshot.rvstate
(строка-состояние у входа в рендер кадра).

Проект: https://github.com/<user>/doom-regex
EOF

echo "OK: $DIST"
ls -la "$DIST"
