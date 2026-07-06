# -*- coding: utf-8 -*-
"""Fuzz: случайные программы RVM-1, lockstep-дифф против эмулятора.

Генерация только терминирующихся программ: прямолинейный код + переходы
строго вперёд + HLT в конце.
"""
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vm.isa import BY_NAME, Insn                  # noqa: E402
from tests.test_rvm import lockstep               # noqa: E402

SEEDS = range(50)


def gen_program(rng: random.Random, n: int = 30):
    prog = []
    interesting = [0, 1, 0xF, 0x10, 0xFF, 0x100, 0xFFFF, 0x10000,
                   0xFFFFFFFF, 0x7FFFFFFF, 0x80000000, 0xDEADBEEF]
    for i in range(n):
        kind = rng.random()
        r = lambda: rng.randrange(8)
        v = lambda: rng.choice(interesting) if rng.random() < 0.5 \
            else rng.randrange(1 << 32)
        if kind < 0.25:
            prog.append(Insn(BY_NAME["MOVI"], d=r(), imm=v()))
        elif kind < 0.40:
            prog.append(Insn(BY_NAME[rng.choice(["ADD", "SUB", "MOV"])],
                             d=r(), s=r()))
        elif kind < 0.55:
            prog.append(Insn(BY_NAME[rng.choice(["ADDI", "SUBI"])],
                             d=r(), imm=v()))
        elif kind < 0.70:
            # адреса из малого пула — чаще hit/overwrite
            addr = rng.choice([0x10, 0x20, 0x30, 0xFFFFFFFF, 0])
            prog.append(Insn(BY_NAME[rng.choice(["STOREI", "LOADI"])],
                             d=r(), imm=addr))
        elif kind < 0.80:
            prog.append(Insn(BY_NAME[rng.choice(["STORE", "LOAD"])],
                             d=r(), s=r()))
        elif kind < 0.90:
            target = rng.randrange(i + 1, n + 1)  # только вперёд
            op = rng.choice(["JEQ", "JNE", "JLT", "JGE"])
            prog.append(Insn(BY_NAME[op], d=r(), s=r(), imm=target))
        elif kind < 0.95:
            prog.append(Insn(BY_NAME["PUTC"], d=r()))
        else:
            prog.append(Insn(BY_NAME["GETC"], d=r()))
    prog.append(Insn(BY_NAME["HLT"]))
    return prog


@pytest.mark.parametrize("seed", SEEDS)
def test_fuzz_lockstep(seed):
    rng = random.Random(seed)
    prog = gen_program(rng)
    inp = bytes(rng.randrange(256) for _ in range(rng.randrange(4)))
    m, steps = lockstep(prog, inp=inp, max_steps=500)
    assert m.st in ("hlt", "err:NOSLOT")   # прямые переходы могут выйти за код
