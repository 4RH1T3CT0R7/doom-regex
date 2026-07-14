# -*- coding: utf-8 -*-
"""Запекает реальную трассу regex-подстановок (для визуализации):
серия проходов Маркова с окнами диффов строки-состояния."""
import json
import re as stdre
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import regex                                    # noqa: E402

from vm.asm import assemble                     # noqa: E402
from vm.genpattern import build_rules           # noqa: E402
from vm.refemu import RefEmu                    # noqa: E402
from vm.statecodec import encode                # noqa: E402

COMPILED = [(n, regex.compile(p),
             stdre.sub(r"\$\{(\w+)\}", r"\\g<\1>", r))
            for n, p, r in build_rules()]

PROG = assemble("""
    MOVI R0, 0x0000fffe
    MOVI R1, 0x00000003
    ADD R0, R1
    MOVI R2, 0xd7
    STOREI R2, 0xf00655
    MUL R0, R1
    HLT
""")


def diff_span(a: str, b: str):
    if a == b:
        return None
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[-1 - j] == b[-1 - j]:
        j += 1
    return i, len(a) - j, len(b) - j


def main() -> None:
    ref = RefEmu(PROG)
    state = encode(ref.m)
    frames = []
    for _ in range(120):
        applied = None
        for name, pat, repl in COMPILED:
            new, n = pat.subn(repl, state)
            if n:
                applied = name
                break
        if applied is None:
            break
        d = diff_span(state, new)
        if d:
            i, ea, eb = d
            w0 = max(0, i - 48)
            frames.append({
                "rule": applied,
                "pos": i,
                "before": state[w0:min(len(state), ea + 48)],
                "after": new[w0:min(len(new), eb + 48)],
                "hl_b": [i - w0, ea - w0],
                "hl_a": [i - w0, eb - w0],
                "head": new[:96],
                "len": len(new),
            })
        state = new

    meta = {
        "state_len": len(state),
        "n_rules": len(COMPILED),
        "program": "MOVI/ADD/STOREI(FB)/MUL/HLT",
        "frames": frames,
    }
    out = Path(sys.argv[1])
    out.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"проходов: {len(frames)}, состояние: {len(state)} симв -> {out}")


if __name__ == "__main__":
    main()
