# -*- coding: utf-8 -*-
"""G2e: сборка клипа из кадров frame_NNNNN.rvfb (экспорт #F драйвером)
в PNG-секвенцию и MP4/GIF. Кадры — ячейки [oooo:vv], палитра PLAYPAL."""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "doom"))
from frame_to_png import playpal, write_png  # noqa: E402

CELL = re.compile(rb"\[([0-9a-f]{4}):([0-9a-f]{2})\]")


def rvfb_to_fb(path: Path) -> bytes:
    d = path.read_bytes()
    body = d[d.index(b"\n") + 1:]
    fb = bytearray(64000)
    for off, val in CELL.findall(body):
        fb[int(off, 16)] = int(val, 16)
    return bytes(fb)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--wad", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--mp4", type=Path)
    ap.add_argument("--gif", type=Path)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args()

    pal = playpal(args.wad.read_bytes())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    frames = sorted(args.src.glob("frame_*.rvfb"))
    print(f"кадров: {len(frames)}")
    for i, f in enumerate(frames):
        fb = rvfb_to_fb(f)
        rgb = bytearray()
        for b in fb:
            rgb += bytes(pal[b])
        write_png(args.out_dir / f"{i:04d}.png", bytes(rgb))
    if args.mp4 and frames:
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(args.fps),
             "-i", str(args.out_dir / "%04d.png"),
             "-vf", f"scale=iw*{args.scale}:ih*{args.scale}:flags=neighbor",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
             str(args.mp4)], check=True, capture_output=True)
        print(f"MP4: {args.mp4} ({args.mp4.stat().st_size/1e6:.1f}МБ)")
    if args.gif and frames:
        from PIL import Image
        imgs = [Image.open(args.out_dir / f"{i:04d}.png").resize(
                    (320 * args.scale, 200 * args.scale), Image.NEAREST)
                for i in range(len(frames))]
        imgs[0].save(args.gif, save_all=True, append_images=imgs[1:],
                     duration=1000 // args.fps, loop=0, optimize=True)
        print(f"GIF: {args.gif} ({args.gif.stat().st_size/1e6:.1f}МБ)")


if __name__ == "__main__":
    main()
