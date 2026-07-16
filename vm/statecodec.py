# -*- coding: utf-8 -*-
"""Кодек строки-состояния RVM-1 (ТОЛЬКО для тестов/инструментов/эмулятора.

Драйверы состояние не разбирают — это привилегия тулинга, не host-цикла).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from vm.isa import (ROM, WORD_MASK, NUM_REGS, FB_CELLS, NRAM_TOP,
                    WAD_DATA, WAD_PAGE, Insn, hexw)


@dataclass
class MachState:
    st: str = "run"          # run | hlt | err:CODE
    ph: int = 0
    pc: int = 0
    regs: list[int] = field(default_factory=lambda: [0] * NUM_REGS)
    clk: int = 0
    inp: bytes = b""
    inp_pos: int = 0        # потреблённый префикс IN (O(1)-GETC без срезов)
    out: bytes = b""
    prog: list[Insn] = field(default_factory=list)
    ram: dict[int, int] = field(default_factory=dict)   # v1.1
    fb: bytes = bytes(FB_CELLS)                         # v1.2: пиксель=байт
    wad: bytes = b""        # G2c: байты WAD с адреса WAD_DATA (зона #W)


def _ci(m: MachState) -> str:
    """CI-кэш: текущая инструкция (op+d+s+imm, 12 hex) или прочерки."""
    if m.pc < len(m.prog):
        i = m.prog[m.pc]
        return f"{i.op.code:02x}{i.d:01x}{i.s:01x}{i.imm:08x}"
    return "-" * 12


def encode(m: MachState) -> str:
    regs = "".join(f"|R{i}:{hexw(m.regs[i])}" for i in range(NUM_REGS))
    # #P — плотный массив слотов ФИКСИРОВАННОЙ ширины (23 симв):
    # fetch прыгает к слоту суммой фикс-прыжков по цифрам PC (O(1))
    prog = "".join(ins.encode(a) for a, ins in enumerate(m.prog))
    # v2.0: адреса < NRAM_TOP живут в плотной позиционной зоне #N
    # (слот 8 hex, адрес имплицитен позицией); highmem — в #M.
    nbuf = bytearray(b"0" * (8 * NRAM_TOP))
    for a, v in m.ram.items():
        if 0 <= a < NRAM_TOP:
            nbuf[8 * a:8 * a + 8] = f"{v & WORD_MASK:08x}".encode("ascii")
    nz = nbuf.decode("ascii")
    # #M НЕ сортирован: правило вставки prepend'ит новую ячейку сразу после
    # #M за O(1); dict Python хранит порядок вставки => reversed воспроизводит
    # порядок машины байт-в-байт (обновления существующих ячеек порядок не
    # меняют ни там, ни там). Фильтр highmem порядок вставки сохраняет.
    ram = "".join(f"[{hexw(a)}:{hexw(v)}]"
                  for a, v in reversed(list(m.ram.items()))
                  if not 0 <= a < NRAM_TOP)
    # FB: пре-populated ячейки [offset4:byte2] — store_fb всегда hit;
    # зона ДО #M (фиксированный размер => короткие сканы, #M растёт позади)
    fb = "".join(f"[{i:04x}:{m.fb[i]:02x}]" for i in range(len(m.fb)))
    # #W: WAD постранично [ppppp:32hex] (страница 16 байт, page=addr>>4);
    # хвост последней страницы дополняется нулями
    wparts = []
    wad = bytes(m.wad)
    for i in range(0, len(wad), WAD_PAGE):
        pg = (WAD_DATA + i) >> 4
        chunk = wad[i:i + WAD_PAGE].ljust(WAD_PAGE, bytes([0]))
        wparts.append(f"[{pg:05x}:{chunk.hex()}]")
    wz = "".join(wparts)
    # v2.0 Э2: IN/OUT в ХВОСТЕ строки — их переменная длина больше не
    # сдвигает зоны (предусловие фикс-смещений); сентинел |Z терминирует
    # OUT для putc-якоря
    return (
        f"RVM1|ST:{m.st}|PH:{m.ph}|CI:{_ci(m)}|PC:{hexw(m.pc)}{regs}"
        f"|CLK:{hexw(m.clk)}|"
        f"{ROM}#N{nz}#P{prog}#F{fb}#W{wz}#M{ram}#E"
        f"|IN:{m.inp[m.inp_pos:].hex()}|OUT:{bytes(m.out).hex()}|Z"
    )


_HEAD = re.compile(
    r"\ARVM1\|ST:(?P<st>run|hlt|err:[A-Z]+)\|PH:(?P<ph>\d)"
    r"\|CI:(?P<ci>[0-9a-f-]{12})\|PC:(?P<pc>[0-9a-f]{8})"
    r"(?:\|MF:(?P<mf>[0-9a-f]{18}|[0-9a-f]{13}|[0-9a-f]{9}))?"   # v1.3: транзиентная микрофаза MUL
    + "".join(rf"\|R{i}:(?P<r{i}>[0-9a-f]{{8}})" for i in range(NUM_REGS))
    + r"\|CLK:(?P<clk>[0-9a-f]{8})\|"
)
# v2.0 Э2: IN/OUT в хвосте строки
_TAIL = re.compile(r"\|IN:(?P<in>[0-9a-f]*)\|OUT:(?P<out>[0-9a-f]*)\|Z\Z")


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
    mt = _TAIL.search(state)
    if not mt:
        raise ValueError("нет хвостового IN/OUT (|IN:..|OUT:..|Z)")
    m.inp = bytes.fromhex(mt["in"])
    m.out = bytes.fromhex(mt["out"])
    return m


def trace_tuple(state: str):
    """(pc, regs, out_hex) для потрейсового диффа с эмулятором."""
    m = decode_head(state)
    return m.pc, tuple(m.regs), m.out.hex()
