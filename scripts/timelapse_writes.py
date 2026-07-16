# -*- coding: utf-8 -*-
"""Таймлапс ФРОНТА РЕНДЕРА: refemu доезжает до --from-step, затем m.fb
подменяется трекающим подклассом bytearray — каждые --every шагов
дампится кадр (PNG) и маска offset'ов, ЗАПИСАННЫХ за интервал (.msk,
битовая карта 64000/8 байт). Сцена статична и дифф по значениям слеп —
зато маска записей показывает, как движок кладёт колонны/спаны."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "doom"))

from vm.asm import assemble_full          # noqa: E402
from vm.refemu import RefEmu              # noqa: E402
from frame_to_png import playpal, write_png  # noqa: E402


class TrackedFB(bytearray):
    def __init__(self, src):
        super().__init__(src)
        self.touched = set()

    def __setitem__(self, key, val):
        super().__setitem__(key, val)
        if isinstance(key, int):
            self.touched.add(key)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rvs", type=Path, required=True)
    ap.add_argument("--wad-mem", type=Path, required=True)
    ap.add_argument("--from-step", type=int, required=True)
    ap.add_argument("--every", type=int, default=25_000)
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    args = ap.parse_args()

    prog, ram = assemble_full(args.rvs.read_text(encoding="utf-8"))
    from vm.isa import WAD_BASE
    wad = args.wad_mem.read_bytes()
    ram = dict(ram)
    ram[WAD_BASE] = len(wad)
    e = RefEmu(prog, b"", ram, wad=wad)
    pal = playpal(wad)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    steps = 0
    while steps < args.from_step:
        if not e.step():
            print(f"HLT раньше from-step на {steps}!", flush=True)
            return
        steps += 1
        if steps % 20_000_000 == 0:
            print(f"  {steps} ({steps/(time.time()-t0)/1e6:.2f}М/с)",
                  flush=True)

    e.m.fb = TrackedFB(e.m.fb)

    def dump(idx: int) -> None:
        rgb = bytearray()
        for b in e.m.fb:
            rgb += bytes(pal[b])
        write_png(args.out_dir / f"{idx:04d}.png", bytes(rgb))
        bits = bytearray(64000 // 8)
        for off in e.m.fb.touched:
            if off < 64000:
                bits[off >> 3] |= 1 << (off & 7)
        (args.out_dir / f"{idx:04d}.msk").write_bytes(bytes(bits))
        e.m.fb.touched.clear()

    print(f"хвост с {steps}: маски записей каждые {args.every}", flush=True)
    frame = 0
    dump(frame)
    while True:
        if not e.step():
            break
        steps += 1
        if steps % args.every == 0:
            frame += 1
            dump(frame)
    frame += 1
    dump(frame)
    print(f"HLT на {steps}; кадров {frame + 1} в {args.out_dir} "
          f"({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
