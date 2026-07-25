# -*- coding: utf-8 -*-
"""Ассемблирует .rvs и собирает начальное .rvstate для драйверов.

Запуск:
  py -3.11 vm/rvs2state.py vm/programs/fib.rvs -o gen/fib.rvstate [--input "..."]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vm.asm import assemble_full          # noqa: E402
from vm.refemu import RefEmu              # noqa: E402
from vm.statecodec import encode          # noqa: E402
from proto.driver import save_state       # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--input", default="")
    ap.add_argument("--wad-mem", type=Path,
                    help="сырой WAD в зону #W начального состояния "
                         "(+ ячейка размера по WAD_BASE); WAD лежит в "
                         "начальном состоянии как данные")
    args = ap.parse_args()

    prog, ram = assemble_full(args.source.read_text(encoding="utf-8"))
    wad = b""
    if args.wad_mem:
        from vm.isa import WAD_BASE
        wad = args.wad_mem.read_bytes()
        ram = dict(ram)
        ram[WAD_BASE] = len(wad)
    state = encode(RefEmu(prog, args.input.encode("latin-1"), ram,
                          wad=wad).m)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_state(args.output, state, passes=0)
    print(f"{args.output}: {len(prog)} insns, state len {len(state)}")


if __name__ == "__main__":
    main()
