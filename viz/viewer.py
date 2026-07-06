# -*- coding: utf-8 -*-
"""Визуализатор фреймбуфера RegexVM (pygame-ce).

Строго read-only к состоянию машины: читает fb-файл (честная копия зоны
#F, которую пишет драйвер), парсит ячейки [oooo:vv], рендерит. Клавиатура
пишется в append-only файл ввода (байты), который драйвер сплайсит в |IN:.

Запуск:
  py -3.11 viz/viewer.py --fb-file C:/dev/doom-regex-out/run/fb.txt
      [--input-file C:/dev/doom-regex-out/run/input.bin] [--scale 12]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vm.isa import FB_W, FB_H  # noqa: E402

CELL = re.compile(r"\[([0-9a-f]{4}):([0-9a-f]{2})\]")

# 256-цветная палитра: индекс -> RGB (плавный градиент + первые 16 ярких)
def palette(i: int) -> tuple[int, int, int]:
    base = [(0, 0, 0), (255, 255, 255), (255, 64, 64), (64, 255, 64),
            (64, 64, 255), (255, 255, 64), (255, 64, 255), (64, 255, 255),
            (128, 128, 128), (192, 96, 0), (96, 192, 0), (0, 96, 192),
            (192, 0, 96), (96, 0, 192), (0, 192, 96), (224, 224, 224)]
    if i < 16:
        return base[i]
    v = (i - 16) * 255 // 239
    return (v, v, 255 - v)


def parse_fb(text: str) -> tuple[int, bytes]:
    nl = text.find("\n")
    passes = int(text[:nl] or 0)
    fb = bytearray(FB_W * FB_H)
    for mo in CELL.finditer(text, nl + 1):
        off = int(mo.group(1), 16)
        if off < len(fb):
            fb[off] = int(mo.group(2), 16)
    return passes, bytes(fb)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fb-file", type=Path, required=True)
    ap.add_argument("--input-file", type=Path, default=None)
    ap.add_argument("--scale", type=int, default=12)
    ap.add_argument("--record", type=Path, default=None,
                    help="папка для PNG каждого нового кадра")
    ap.add_argument("--once", action="store_true",
                    help="отрисовать один кадр в PNG и выйти (headless)")
    args = ap.parse_args()

    if args.once:
        # headless-режим для тестов: PNG без окна
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    size = (FB_W * args.scale, FB_H * args.scale)
    screen = pygame.display.set_mode(size)
    pygame.display.set_caption("RegexVM framebuffer")
    surf = pygame.Surface((FB_W, FB_H))

    last_passes = -1
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN and args.input_file:
                ch = ev.unicode.encode("latin-1", "ignore")
                if ch:
                    with args.input_file.open("ab") as f:
                        f.write(ch)

        if args.fb_file.exists():
            try:
                passes, fb = parse_fb(args.fb_file.read_text("ascii"))
            except (ValueError, OSError):
                passes, fb = last_passes, None
            if fb is not None and passes != last_passes:
                last_passes = passes
                px = pygame.PixelArray(surf)
                for y in range(FB_H):
                    row = y * FB_W
                    for x in range(FB_W):
                        px[x, y] = palette(fb[row + x])
                del px
                pygame.transform.scale(surf, size, screen)
                pygame.display.flip()
                pygame.display.set_caption(
                    f"RegexVM framebuffer — pass {passes}")
                if args.record:
                    args.record.mkdir(parents=True, exist_ok=True)
                    pygame.image.save(
                        surf, str(args.record / f"frame_{passes:010d}.png"))
                if args.once:
                    pygame.image.save(surf, str(args.fb_file.with_suffix(".png")))
                    return
        time.sleep(0.1)


if __name__ == "__main__":
    main()
