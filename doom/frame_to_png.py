# -*- coding: utf-8 -*-
"""Дамп палитрового кадра (DUMP:<64000 байт>) + PLAYPAL из WAD -> PNG.

Запуск: py -3.11 doom/frame_to_png.py <dump.bin> <doom1.wad> <out.png>
"""
import struct
import sys
import zlib
from pathlib import Path

W, H = 320, 200


def playpal(wad: bytes) -> list:
    # найти лампу PLAYPAL через каталог
    numlumps = int.from_bytes(wad[4:8], "little")
    ofs = int.from_bytes(wad[8:12], "little")
    for i in range(numlumps):
        e = ofs + i * 16
        name = wad[e + 8:e + 16].split(b"\x00")[0]
        if name == b"PLAYPAL":
            pos = int.from_bytes(wad[e:e + 4], "little")
            pal = wad[pos:pos + 768]
            return [(pal[j * 3], pal[j * 3 + 1], pal[j * 3 + 2])
                    for j in range(256)]
    raise SystemExit("PLAYPAL не найдена")


def write_png(path: Path, rgb: bytes):
    def chunk(typ, data):
        c = typ + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        raw += rgb[y * W * 3:(y + 1) * W * 3]
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main():
    dump = Path(sys.argv[1]).read_bytes()
    wad = Path(sys.argv[2]).read_bytes()
    out = Path(sys.argv[3])
    i = dump.rfind(b"DUMP:")
    if i < 0:
        raise SystemExit("маркер DUMP: не найден")
    px = dump[i + 5:i + 5 + W * H]
    if len(px) < W * H:
        raise SystemExit(f"кадр неполный: {len(px)}/{W*H}")
    pal = playpal(wad)
    rgb = bytearray()
    for p in px:
        r, g, b = pal[p]
        rgb += bytes((r, g, b))
    write_png(out, bytes(rgb))
    # статистика: сколько уникальных цветов (не чёрный экран?)
    uniq = len(set(px))
    print(f"{out}: {W}x{H}, уникальных индексов {uniq}")


if __name__ == "__main__":
    main()
