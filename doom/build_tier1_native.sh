#!/usr/bin/env bash
# Tier-1 оракул: EIR -> C (ELVM C-бэкенд) -> нативный бинарь.
# Та же семантика EIR, что у eli, но в сотни раз быстрее — пригоден для
# полной инициализации Doom и снятия golden-чексумм кадров.
# (eli слишком медленный для cold-start Doom.)
set -euo pipefail

OUT="${DOOMREGEX_OUT:-/c/dev/doom-regex-out}"
ELVM="$OUT/build/src/bfdoom/vendor/elvm"
EIR="$OUT/build/doom-rvm.eir"
CFILE="$OUT/build/doom-rvm.c"
BIN="$OUT/build/doom-rvm-native.exe"

export PATH="/usr/bin:$PATH"
"$ELVM/out/elc" -c "$EIR" > "$CFILE"

# tier-1 native: (1) небуферизованный stdout (вывод в файл иначе буферизуется);
# (2) stdin в BINARY mode — Windows text mode схлопывает \r\n и теряет байты
# нибл-потока WAD (значения 10=\n, 13=\r встречаются в кодировке).
py -3.11 - "$CFILE" <<'PYEOF'
import sys
from pathlib import Path
p = Path(sys.argv[1])
t = p.read_text()
if "setvbuf" not in t:
    t = "#include <fcntl.h>\n#include <io.h>\n" + t
    needle = "int main() {\n"      # устойчивый якорь (mem[0] значение меняется)
    assert needle in t, "int main() не найден в generated C"
    t = t.replace(needle,
                  "int main() {\n"
                  " _setmode(_fileno(stdin), _O_BINARY);\n"
                  " _setmode(_fileno(stdout), _O_BINARY);\n"
                  " setvbuf(stdout, 0, _IONBF, 0);\n", 1)
    p.write_text(t)
    print("setvbuf + stdin binary mode вставлены")
PYEOF

gcc -O1 -o "$BIN" "$CFILE"
echo "OK: $BIN"
