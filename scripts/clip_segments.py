# -*- coding: utf-8 -*-
"""G2e-параллелизм: refemu-прогон clip-программы со снапшотами на
границах сегментов (после каждого K-го '\f'-маркера кадра в OUT).
Сегментные снапшоты скармливаются независимым rvm.exe-воркерам
(--fb-seq-dir + --fb-seq-stop) — кадры детерминированы, склейка
тривиальна."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vm.asm import assemble_full          # noqa: E402
from vm.refemu import RefEmu              # noqa: E402
from vm.statecodec import encode          # noqa: E402


def save(path: Path, state: str) -> None:
    hdr = json.dumps({"fmt": "rvstate/1", "pass": 0, "len": len(state),
                      "sha256": None})
    path.write_text(hdr + "\n" + state, encoding="ascii", newline="\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rvs", type=Path, required=True)
    ap.add_argument("--wad-mem", type=Path, required=True)
    ap.add_argument("--at-frames", required=True,
                    help="счётчики '\\f', после которых снимать (напр. 20,40,60,80)")
    ap.add_argument("-o", "--out-prefix", required=True)
    args = ap.parse_args()

    marks = sorted(int(x) for x in args.at_frames.split(","))
    prog, ram = assemble_full(args.rvs.read_text(encoding="utf-8"))
    from vm.isa import WAD_BASE
    wad = args.wad_mem.read_bytes()
    ram = dict(ram)
    ram[WAD_BASE] = len(wad)
    e = RefEmu(prog, b"", ram, wad=wad)

    t0 = time.time()
    steps = ffs = 0
    out_seen = 0
    while marks:
        if not e.step():
            print(f"останов машины на шаге {steps} (ff={ffs})", flush=True)
            return
        steps += 1
        if len(e.m.out) != out_seen:
            for b in e.m.out[out_seen:]:
                if b == 12:
                    ffs += 1
                    if marks and ffs == marks[0]:
                        marks.pop(0)
                        p = Path(f"{args.out_prefix}{ffs}.rvstate")
                        save(p, encode(e.m))
                        print(f"[seg] ff#{ffs} шаг {steps}: {p} "
                              f"({time.time()-t0:.0f}s)", flush=True)
            out_seen = len(e.m.out)
        if steps % 100_000_000 == 0:
            print(f"  {steps} ({steps/(time.time()-t0)/1e6:.2f}М/с, "
                  f"ff={ffs})", flush=True)
    print(f"готово за {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
