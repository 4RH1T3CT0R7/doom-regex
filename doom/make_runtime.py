# -*- coding: utf-8 -*-
"""Генерирует runtime_rvm.c из BFDoom ports/elvm-libc/runtime.c.

Замена host-протокола bfio на WAD-в-памяти (честная граница: WAD — часть
начального состояния или грузится стартовым GETC-лоадером; никакой
логики на host-стороне).

Запуск: py -3.11 doom/make_runtime.py <bfdoom_root> -o <runtime_rvm.c>
"""
import argparse
import re
import sys
from pathlib import Path

WAD_LAYER = '''\
/* --- WAD в памяти (замена bfio-host-протокола BFDoom; см. HONESTY.md) ---
 * Раскладка: [RVM_WAD_BASE] = размер (0 => не загружен),
 *            [RVM_WAD_BASE+1 ..] = байты WAD по слову на байт.
 * На RVM зона предзаполняется начальным состоянием; на eli грузится
 * один раз стартовым GETC-лоадером. Кодировка потока: нибл+1 (два
 * значения 1..16 на байт) — builtin getchar 8cc мапит 0 в EOF(-1),
 * сырые нулевые байты через него не проходят. */
#define RVM_WAD_BASE 0xa00000

static int wad_in_byte(void) {
  int h = getchar() - 1;
  int l = getchar() - 1;
  return ((h & 15) << 4) + (l & 15);
}

static void wad_ensure_loaded(void) {
  int *szp = (int *) RVM_WAD_BASE;
  char *p;
  int a, b, c, size, i;
  if (*szp) {
    return;
  }
  a = wad_in_byte();
  b = wad_in_byte();
  c = wad_in_byte();
  size = a + (b << 8) + (c << 16);
  p = (char *) (RVM_WAD_BASE + 1);
  for (i = 0; i < size; i++) {
    p[i] = wad_in_byte();
  }
  *szp = size;
}

static int wad_path_size(const char *path) {
  int n = 0;
  if (!path) {
    return 0;
  }
  while (path[n]) {
    n++;
  }
  if (n < 4 || path[n - 4] != '.'
      || (path[n - 3] != 'w' && path[n - 3] != 'W')) {
    return 0;
  }
  wad_ensure_loaded();
  return *(int *) RVM_WAD_BASE;
}
'''

FREAD_COPY = '''\
  {
    char *src = (char *) (RVM_WAD_BASE + 1) + wad_pos;
    int i;
    for (i = 0; i < total; i++) {
      out[i] = src[i];
    }
  }
'''


def cut_functions(text: str, start_marker: str, end_marker: str) -> str:
    """Вырезает блок от начала строки start_marker до строки end_marker
    (не включая её)."""
    i = text.index(start_marker)
    j = text.index(end_marker)
    assert i < j, (start_marker, end_marker)
    return text[:i] + text[j:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bfdoom_root", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    src = (args.bfdoom_root / "ports" / "elvm-libc" / "runtime.c")
    text = src.read_text(encoding="utf-8")

    # 1) bfio-протокол -> слой WAD-в-памяти
    text = cut_functions(text, "static void bfio_prefix",
                         "static int is_read_mode")
    text = text.replace("static int is_read_mode",
                        WAD_LAYER + "\nstatic int is_read_mode", 1)

    # 2) все bfhost_* (host-side fast-path BFDoom) — удаляем целиком
    text = cut_functions(text, "int bfhost_poll_key", "char *strrchr")

    # 3) точки вызова протокола -> память
    n_path = text.count("bfio_path_size(")
    assert n_path == 3, n_path          # fopen, stat, open
    text = text.replace("bfio_path_size(", "wad_path_size(")

    fread_proto = ("  bfio_prefix('D');\n"
                   "  bfio_send_u24(wad_pos);\n"
                   "  bfio_send_u24(total);\n"
                   "  bfio_send_u24((int) out);\n")
    assert fread_proto in text
    text = text.replace(fread_proto, FREAD_COPY)

    assert "bfio_" not in text and "bfhost_" not in text, "остатки протокола"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "/* СГЕНЕРИРОВАНО doom/make_runtime.py — не редактировать руками.\n"
        " * Источник: BFDoom ports/elvm-libc/runtime.c (GPL-2.0), форк:\n"
        " * bfio-host-протокол заменён на WAD-в-памяти. */\n" + text,
        encoding="utf-8")
    print(f"{args.output}: {len(text.splitlines())} строк")


if __name__ == "__main__":
    main()
