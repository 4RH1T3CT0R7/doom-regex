#!/usr/bin/env bash
# Сборка Doom -> EIR нашим конвейером (Git Bash / MSYS2).
# Отличия от BFDoom probe: наш runtime (WAD-в-памяти), наш платформенный
# слой doomgeneric_rvm.c, разрешение 320x200 (без масштабирования 2x),
# опции RVM_TIMEDEMO / RVM_FRAME_CHECKSUM.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${DOOMREGEX_OUT:-/c/dev/doom-regex-out}"
BFD="$OUT/build/src/bfdoom"
ELVM="$BFD/vendor/elvm"
DOOM="$BFD/vendor/doomgeneric/doomgeneric"
BUILD="$OUT/build/doom"
MODE="${1:-timedemo}"    # timedemo | interactive

CFLAGS=(-S -D__eir__ '-DINT_MIN=(-2147483647-1)' -DSHRT_MAX=32767 -DEISDIR=21
        -DSEEK_SET=0 -DSEEK_END=2
        -DDOOMGENERIC_RESX=320 -DDOOMGENERIC_RESY=200
        -I"$REPO/doom"
        -I"$BFD/ports/elvm-libc" -I"$ELVM" -I"$ELVM/libc" -I"$ELVM/out"
        -I"$DOOM")
if [ "$MODE" = "timedemo" ]; then
  CFLAGS+=(-DRVM_WARP -DRVM_DUMP_FRAME=60)
fi

SOURCES=(dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop
  d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus
  i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox
  m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors
  p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr
  p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data
  r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib
  st_stuff s_sound tables v_video wi_stuff w_checksum w_file w_main w_wad
  z_zone w_file_stdc i_input i_video doomgeneric)

mkdir -p "$BUILD"

# честный memset: BFDoom инжектит bfhost_fill_words (host-side fill) в
# vendored libc/string.h — заменяем на чистый C-цикл (идемпотентно).
py -3.11 - "$ELVM/libc/string.h" <<'PYEOF2'
import sys
from pathlib import Path
p = Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
bad = """#ifdef __eir__
  bfhost_fill_words((int*)d, n, c & 255);
  return d;
#else
"""
if bad in t:
    t = t.replace(bad, """#ifdef __eir__
  { size_t _i; for (_i = 0; _i < n; _i++) ((char*)d)[_i] = c; }
  return d;
#else
""")
    p.write_text(t, encoding="utf-8")
    print("string.h: memset -> честный __eir__ цикл")
PYEOF2

py -3.11 "$REPO/doom/make_runtime.py" "$BFD" -o "$BUILD/runtime_rvm.c"
py -3.11 "$REPO/doom/apply_port_patches.py" "$DOOM"

cd "$ELVM"
fail=0
for base in "${SOURCES[@]}"; do
  ./out/8cc "${CFLAGS[@]}" -o "$BUILD/$base.eir" "$DOOM/$base.c" \
    > "$BUILD/$base.log" 2>&1 || { echo "FAIL $base"; fail=1; }
done
./out/8cc "${CFLAGS[@]}" -o "$BUILD/runtime_rvm.eir" "$BUILD/runtime_rvm.c" \
  > "$BUILD/runtime_rvm.log" 2>&1 || { echo "FAIL runtime_rvm"; fail=1; }
./out/8cc "${CFLAGS[@]}" -o "$BUILD/doomgeneric_rvm.eir" \
  "$REPO/doom/doomgeneric_rvm.c" > "$BUILD/doomgeneric_rvm.log" 2>&1 \
  || { echo "FAIL doomgeneric_rvm"; fail=1; }
./out/8cc "${CFLAGS[@]}" -o "$BUILD/rvm_lumps.eir" \
  "$REPO/doom/rvm_lumps.c" > "$BUILD/rvm_lumps.log" 2>&1 \
  || { echo "FAIL rvm_lumps"; fail=1; }
[ "$fail" = 0 ] || exit 1

# runtime первым (как у BFDoom), платформенный слой последним
py -3.11 "$REPO/doom/link_eir.py" -o "$OUT/build/doom-rvm.eir" \
  "$BUILD/runtime_rvm.eir" "$BUILD/rvm_lumps.eir" \
  $(for b in "${SOURCES[@]}"; do echo "$BUILD/$b.eir"; done) \
  "$BUILD/doomgeneric_rvm.eir"
echo "OK: $OUT/build/doom-rvm.eir ($MODE)"
