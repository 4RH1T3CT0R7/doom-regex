# -*- coding: utf-8 -*-
"""Сборка GIF/MP4 из кадров+масок timelapse_writes.py: оригинал кадра +
подсветка пикселей, записанных машиной за интервал (свежие — ярко-зелёные,
предыдущий интервал — гаснущие). Виден фронт рендера: колонны, спаны."""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 320, 200
FRESH = np.array([80, 255, 120], dtype=np.float32)   # свежие записи
FADE = np.array([40, 160, 90], dtype=np.float32)     # прошлый интервал


def load_mask(p: Path) -> np.ndarray:
    bits = np.frombuffer(p.read_bytes(), dtype=np.uint8)
    return np.unpackbits(bits, bitorder="little")[:W * H].reshape(H, W)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--gif", type=Path)
    ap.add_argument("--mp4", type=Path)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--ms", type=int, default=70)
    args = ap.parse_args()

    pngs = sorted(args.src.glob("*.png"))
    out_frames = []
    # накопительный след: интенсивность подсветки пикселя затухает
    # экспоненциально с "возрастом" его последней записи
    heat = np.zeros((H, W), dtype=np.float32)
    for p in pngs:
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.float32)
        mp = p.with_suffix(".msk")
        mask = load_mask(mp) if mp.exists() else np.zeros((H, W), np.uint8)
        heat *= 0.82
        heat[mask.astype(bool)] = 1.0
        if heat.max() < 0.05:
            continue                     # тишина — кадр пропускаем
        a = (heat * 0.85)[..., None]
        comp = img * (1 - a) + FRESH * a
        fr = Image.fromarray(comp.clip(0, 255).astype(np.uint8))
        if args.scale != 1:
            fr = fr.resize((W * args.scale, H * args.scale), Image.NEAREST)
        out_frames.append(fr)

    # финал — чистый кадр с длинной паузой
    clean = Image.open(pngs[-1]).convert("RGB")
    if args.scale != 1:
        clean = clean.resize((W * args.scale, H * args.scale), Image.NEAREST)
    out_frames.append(clean)

    if args.gif:
        durations = [args.ms] * (len(out_frames) - 1) + [3000]
        out_frames[0].save(
            args.gif, save_all=True, append_images=out_frames[1:],
            duration=durations, loop=0, optimize=True)
        print(f"GIF: {args.gif} ({args.gif.stat().st_size/1e6:.1f}МБ, "
              f"{len(out_frames)} кадров)")
    if args.mp4:
        tmp = args.src / "_comp"
        tmp.mkdir(exist_ok=True)
        for i, fr in enumerate(out_frames):
            fr.save(tmp / f"{i:04d}.png")
        import subprocess
        fps = max(1, round(1000 / args.ms))
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(fps),
             "-i", str(tmp / "%04d.png"), "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-crf", "18", str(args.mp4)],
            check=True, capture_output=True)
        print(f"MP4: {args.mp4} ({args.mp4.stat().st_size/1e6:.1f}МБ)")


if __name__ == "__main__":
    main()
