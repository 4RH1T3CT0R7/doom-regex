# -*- coding: utf-8 -*-
"""Кодек строки-состояния RVM-1 (ТОЛЬКО для тестов/инструментов/эмулятора.

Драйверы состояние не разбирают — это привилегия тулинга, не host-цикла).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from vm.isa import ROM, WORD_MASK, NUM_REGS, Insn, hexw


@dataclass
class MachState:
    st: str = "run"          # run | hlt | err:CODE
    ph: int = 0
    pc: int = 0
    regs: list[int] = field(default_factory=lambda: [0] * NUM_REGS)
    clk: int = 0
    inp: bytes = b""
    out: bytes = b""
    prog: list[Insn] = field(default_factory=list)
    ram: dict[int, int] = field(default_factory=dict)   # v1.1
    fb: bytes = b""                                     # v1.2


def encode(m: MachState) -> str:
    regs = "".join(f"|R{i}:{hexw(m.regs[i])}" for i in range(NUM_REGS))
    prog = "".join(ins.encode(a) for a, ins in enumerate(m.prog))
    ram = "".join(f"[{hexw(a)}:{hexw(v)}]" for a, v in sorted(m.ram.items()))
    return (
        f"RVM1|ST:{m.st}|PH:{m.ph}|PC:{hexw(m.pc)}{regs}"
        f"|CLK:{hexw(m.clk)}|IN:{m.inp.hex()}|OUT:{m.out.hex()}|"
        f"{ROM}#P{prog}#M{ram}#F{m.fb.hex()}#E"
    )


_HEAD = re.compile(
    r"\ARVM1\|ST:(?P<st>run|hlt|err:[A-Z]+)\|PH:(?P<ph>\d)\|PC:(?P<pc>[0-9a-f]{8})"
    + "".join(rf"\|R{i}:(?P<r{i}>[0-9a-f]{{8}})" for i in range(NUM_REGS))
    + r"\|CLK:(?P<clk>[0-9a-f]{8})\|IN:(?P<in>[0-9a-f]*)\|OUT:(?P<out>[0-9a-f]*)\|"
)


def decode_head(state: str) -> MachState:
    """Разбирает заголовок (регистры/PC/IN/OUT); зоны не трогает."""
    mo = _HEAD.match(state)
    if not mo:
        raise ValueError(f"не RVM1-состояние: {state[:80]}…")
    m = MachState()
    m.st = mo["st"]
    m.ph = int(mo["ph"])
    m.pc = int(mo["pc"], 16) & WORD_MASK
    m.regs = [int(mo[f"r{i}"], 16) for i in range(NUM_REGS)]
    m.clk = int(mo["clk"], 16)
    m.inp = bytes.fromhex(mo["in"])
    m.out = bytes.fromhex(mo["out"])
    return m


def trace_tuple(state: str):
    """(pc, regs, out_hex) для потрейсового диффа с эмулятором."""
    m = decode_head(state)
    return m.pc, tuple(m.regs), m.out.hex()
