# -*- coding: utf-8 -*-
"""Эталонная трасса хвоста: refemu от rvs, тихий пробег до --from-step,
затем лог (pc:u32, op:u8) каждого шага до HLT в бинарный файл.
Для бисекции расхождений rvm.exe (первый неверный исполненный опкод)."""
import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vm.asm import assemble_full          # noqa: E402
from vm.refemu import RefEmu              # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rvs", type=Path, required=True)
    ap.add_argument("--wad-mem", type=Path, required=True)
    ap.add_argument("--from-step", type=int, required=True)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--progress-every", type=int, default=20_000_000)
    args = ap.parse_args()

    prog, ram = assemble_full(args.rvs.read_text(encoding="utf-8"))
    from vm.isa import WAD_BASE
    wad = args.wad_mem.read_bytes()
    ram = dict(ram)
    ram[WAD_BASE] = len(wad)
    e = RefEmu(prog, b"", ram, wad=wad)
    print(f"{len(prog)} insns; тихий пробег до {args.from_step}", flush=True)

    t0 = time.time()
    steps = 0
    while steps < args.from_step:
        if not e.step():
            print(f"HLT раньше from-step на шаге {steps}!", flush=True)
            return
        steps += 1
        if steps % args.progress_every == 0:
            print(f"  {steps} ({steps/(time.time()-t0)/1e6:.2f}М/с)",
                  flush=True)

    print(f"хвост с шага {steps}: пишу трассу в {args.output}", flush=True)
    buf = bytearray()
    pack = struct.Struct("<IB").pack
    while True:
        pc = e.m.pc
        op = prog[pc].op.code if pc < len(prog) else 0xFF
        buf += pack(pc, op)
        if not e.step():
            break
        steps += 1
    args.output.write_bytes(bytes(buf))
    print(f"HLT на шаге {steps}; хвост {len(buf)//5} записей "
          f"({time.time()-t0:.0f}s всего)", flush=True)


if __name__ == "__main__":
    main()
