# -*- coding: utf-8 -*-
"""G2d-раннер: прогон Doom-RVS на refemu (байт-эквивалент regex-машины,
доказано lockstep-тестами) с чекпоинтами и снапшотом за N шагов до конца.

Схема money shot:
  1) прогон до HLT -> total шагов (кадр уходит в OUT DUMP-протоколом);
  2) прогон до (total - tail) -> снапшот encode() = строка regex-машины;
  3) rvm.exe --state снапшот доигрывает хвост ЧЕСТНО на PCRE2 до
     фикс-точки: DUMP-кадр материализуется в OUT-зоне regex-состояния.

Запуск:
  py -3.11 scripts/g2d_run.py --rvs X.rvs --wad wadfeed.bin \
      [--max-steps N] [--snapshot-at N -o snap.rvstate] [--dump-out out.bin]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vm.asm import assemble_full           # noqa: E402
from vm.refemu import RefEmu               # noqa: E402
from vm.statecodec import encode           # noqa: E402
from proto.driver import save_state        # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rvs", type=Path, required=True)
    ap.add_argument("--wad", type=Path, help="нибл+1-поток в IN (wadfeed)")
    ap.add_argument("--wad-mem", type=Path,
                    help="сырой WAD в зону #W (вместо IN-потока)")
    ap.add_argument("--snapshot-at-marker", default="",
                    help="снапшот, когда OUT заканчивается этим маркером")
    ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--snapshot-at", type=int, default=0)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--dump-out", type=Path,
                    help="писать OUT-байты эмулятора в файл в конце")
    ap.add_argument("--dump-fb", type=Path,
                    help="писать FB-зону (кадр) в файл в конце")
    ap.add_argument("--progress-every", type=int, default=2_000_000)
    ap.add_argument("--profile-from", type=int, default=0,
                    help="с этого шага собирать гистограмму pc->функция")
    args = ap.parse_args()

    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] ассемблирование {args.rvs.name}…",
          flush=True)
    prog, ram = assemble_full(args.rvs.read_text(encoding="utf-8"))
    inp = args.wad.read_bytes() if args.wad else b""
    wad = b""
    if args.wad_mem:
        from vm.isa import WAD_BASE
        wad = args.wad_mem.read_bytes()
        ram = dict(ram)
        ram[WAD_BASE] = len(wad)
    e = RefEmu(prog, inp, ram, wad=wad)
    print(f"[{time.strftime('%H:%M:%S')}] {len(prog)} insns, "
          f"in={len(inp)} байт; старт", flush=True)

    # карта адрес->функция из '; @fn' маркеров .rvs
    fn_starts = []
    if args.profile_from:
        idx = 0
        cur = "?"
        for line in args.rvs.read_text(encoding="utf-8").splitlines():
            ls = line.strip()
            if ls.startswith("; @fn "):
                cur = ls[6:]
                fn_starts.append((idx, cur))
            elif ls and not ls.startswith(";") and not ls.endswith(":")                     and not ls.startswith(".mem"):
                idx += 1
        import bisect
        fn_addrs = [a for a, _ in fn_starts]
        fn_names = [n for _, n in fn_starts]
        from collections import Counter
        prof = Counter()

    steps = 0
    t1 = time.time()
    tick = args.progress_every
    marker = args.snapshot_at_marker.encode("latin-1")
    out_len_seen = -1
    while True:
        if marker:
            cur = len(e.m.out)
            if cur != out_len_seen:               # только после PUTC
                out_len_seen = cur
                if e.m.out.endswith(marker):
                    assert args.output, "--snapshot-at-marker требует -o"
                    state = encode(e.m)
                    save_state(args.output, state, passes=0)
                    print(f"[snapshot@marker] шаг {steps}: {args.output} "
                          f"(len {len(state)})", flush=True)
                    marker = b""                  # один раз
        if args.snapshot_at and steps == args.snapshot_at:
            assert args.output, "--snapshot-at требует -o"
            state = encode(e.m)
            save_state(args.output, state, passes=0)
            print(f"[snapshot] шаг {steps}: {args.output} "
                  f"(len {len(state)})", flush=True)
        if args.profile_from and steps >= args.profile_from:
            k = bisect.bisect_right(fn_addrs, e.m.pc) - 1
            prof[fn_names[k] if k >= 0 else "?"] += 1
        if not e.step():
            print(f"[stop] st={e.m.st} на шаге {steps}", flush=True)
            break
        steps += 1
        if steps == tick:
            dt = time.time() - t1
            print(f"[{time.strftime('%H:%M:%S')}] {steps:,} шагов, "
                  f"{steps/dt:,.0f}/s, pc={e.m.pc}, out={len(e.m.out)}",
                  flush=True)
            tick += args.progress_every
        if args.max_steps and steps >= args.max_steps:
            print(f"[limit] {steps} шагов", flush=True)
            break

    print(f"итого: {steps:,} шагов за {time.time()-t0:,.0f}s; "
          f"OUT {len(e.m.out)} байт; st={e.m.st}", flush=True)
    if args.dump_out:
        args.dump_out.write_bytes(e.m.out)
        print(f"OUT -> {args.dump_out}", flush=True)
    if args.profile_from and prof:
        total = sum(prof.values())
        print(f"[profile] {total:,} шагов с {args.profile_from:,}:")
        for name, cnt in prof.most_common(25):
            print(f"  {cnt:>12,}  {100*cnt/total:5.1f}%  {name}", flush=True)
    if args.dump_fb:
        args.dump_fb.write_bytes(bytes(e.m.fb))
        print(f"FB -> {args.dump_fb}", flush=True)


if __name__ == "__main__":
    main()
