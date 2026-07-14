# -*- coding: utf-8 -*-
"""Референс-эмулятор RVM-1 — оракул для дифф-тестов.

Семантика обязана до бита совпадать со спекой vm/isa.md и правилами
genpattern.py. Один step() = один архитектурный шаг (одна инструкция).
"""
from __future__ import annotations

from vm.isa import FB_BASE, WAD_DATA, WORD_MASK, Insn
from vm.statecodec import MachState


def _mem_read(m: MachState, addr: int) -> int:
    off = addr - FB_BASE
    if 0 <= off < len(m.fb):
        return m.fb[off]
    woff = addr - WAD_DATA
    if 0 <= woff < len(m.wad):
        return m.wad[woff]
    return m.ram.get(addr, 0)


def _mem_write(m: MachState, addr: int, val: int) -> None:
    off = addr - FB_BASE
    if 0 <= off < len(m.fb):
        if not isinstance(m.fb, bytearray):
            m.fb = bytearray(m.fb)
        m.fb[off] = val & 0xFF
        return
    woff = addr - WAD_DATA
    if 0 <= woff < len(m.wad):
        if not isinstance(m.wad, bytearray):
            m.wad = bytearray(m.wad)
        m.wad[woff] = val & 0xFF
        return
    m.ram[addr] = val


class RefEmu:
    def __init__(self, prog: list[Insn], inp: bytes = b"",
                 ram: dict[int, int] | None = None, wad: bytes = b""):
        self.m = MachState(prog=list(prog), inp=inp, ram=dict(ram or {}),
                           wad=wad)

    def step(self) -> bool:
        """Исполняет одну инструкцию. False, если машина не в run."""
        m = self.m
        if m.st != "run":
            return False
        if m.pc >= len(m.prog):
            m.st = "err:NOSLOT"
            return False
        ins = m.prog[m.pc]
        op, d, s, imm = ins.op.name, ins.d, ins.s, ins.imm
        if d >= len(m.regs) or s >= len(m.regs):
            # машина: операнды вне [0-7] не матчат ни один исполнитель
            m.st = "err:BADOP"
            return False
        R = m.regs
        nxt = m.pc + 1

        if op == "MOV":
            R[d] = R[s]
        elif op == "MOVI":
            R[d] = imm & WORD_MASK
        elif op == "ADD":
            R[d] = (R[d] + R[s]) & WORD_MASK
        elif op == "ADDI":
            R[d] = (R[d] + imm) & WORD_MASK
        elif op == "SUB":
            R[d] = (R[d] - R[s]) & WORD_MASK
        elif op == "SUBI":
            R[d] = (R[d] - imm) & WORD_MASK
        elif op == "LOAD":
            R[d] = _mem_read(m, R[s])
        elif op == "LOADI":
            R[d] = _mem_read(m, imm)
        elif op == "STORE":
            _mem_write(m, R[d], R[s])
        elif op == "STOREI":
            _mem_write(m, imm, R[d])
        elif op == "JMP":
            nxt = imm
        elif op == "JMPR":
            nxt = R[d]
        elif op == "JEQ":
            if R[d] == R[s]:
                nxt = imm
        elif op == "JNE":
            if R[d] != R[s]:
                nxt = imm
        elif op == "JLT":
            if R[d] < R[s]:
                nxt = imm
        elif op == "JGE":
            if R[d] >= R[s]:
                nxt = imm
        elif op == "WR24":
            R[d] &= 0x00FFFFFF
        elif op == "BAND":
            R[d] &= R[s]
        elif op == "BOR":
            R[d] |= R[s]
        elif op == "BXOR":
            R[d] ^= R[s]
        elif op == "SHL":
            # семантика __builtin_shl: s >= 32 -> 0 (не mod 32)
            R[d] = (R[d] << R[s]) & WORD_MASK if R[s] < 32 else 0
        elif op == "SHR":
            R[d] = (R[d] >> R[s]) if R[s] < 32 else 0
        elif op == "SAR":
            # арифметический: заполнение знаком; s >= 32 -> все биты знака
            sign = R[d] >> 31
            if R[s] >= 32:
                R[d] = WORD_MASK if sign else 0
            else:
                R[d] = ((R[d] >> R[s])
                        | ((WORD_MASK << (32 - R[s])) & WORD_MASK
                           if sign and R[s] else 0)) & WORD_MASK
        elif op == "MUL":
            R[d] = (R[d] * R[s]) & WORD_MASK
        elif op == "DIV":
            R[d] = (R[d] // R[s]) if R[s] else WORD_MASK
        elif op == "MOD":
            if R[s]:
                R[d] = R[d] % R[s]
        elif op in ("DSPAN", "DCOL"):
            # A=pos, B=step, C=n, D=src, U(7)=cmap, T(6)=dest.
            # Контракт fused-инструкций: texel/цвет обязаны быть
            # материализованными #M-ячейками, dest — FB-ячейкой; miss =
            # err:BADOP (правило не матчит -> catch-all, согласовано).
            if R[2] == 0:
                pass                        # конец цикла -> next
            else:
                if op == "DSPAN":
                    spot = ((R[0] >> 4) & 0xFC0) | (R[0] >> 26)
                    dstep = 1
                else:
                    spot = (R[0] >> 16) & 127
                    dstep = 320
                texel = m.ram.get((R[3] + spot) & WORD_MASK)
                col = (None if texel is None
                       else m.ram.get((R[7] + (texel & 0xFF)) & WORD_MASK))
                dest_off = R[6] - FB_BASE
                if col is None or not (0 <= dest_off < len(m.fb)):
                    m.st = "err:BADOP"
                    return False
                if not isinstance(m.fb, bytearray):
                    m.fb = bytearray(m.fb)
                m.fb[dest_off] = col & 0xFF
                R[0] = (R[0] + R[1]) & WORD_MASK
                R[6] = (R[6] + dstep) & WORD_MASK
                R[2] = (R[2] - 1) & WORD_MASK
                nxt = m.pc                  # само-повтор
        elif op == "PUTC":
            if not isinstance(m.out, bytearray):
                m.out = bytearray(m.out)
            m.out.append(R[d] & 0xFF)
        elif op == "GETC":
            if m.inp_pos < len(m.inp):
                R[d] = m.inp[m.inp_pos]
                m.inp_pos += 1
            else:
                R[d] = 0
        elif op == "HLT":
            m.st = "hlt"
            return False
        else:  # pragma: no cover
            m.st = "err:BADOP"
            return False

        m.pc = nxt & WORD_MASK
        if m.pc >= len(m.prog):
            # машина «сплавляет» неудачную выборку в трап в том же
            # архитектурном шаге (exec -> PH:2 -> fetch-fail -> NOSLOT);
            # эмулятор обязан не задерживаться в транзиентном ST:run
            m.st = "err:NOSLOT"
            return False
        return True

    def run(self, max_steps: int = 10_000_000) -> str:
        n = 0
        while self.step():
            n += 1
            if n > max_steps:
                raise RuntimeError("превышен лимит шагов")
        return self.m.st

    def trace_tuple(self):
        m = self.m
        return m.pc, tuple(m.regs), m.out.hex()
