# -*- coding: utf-8 -*-
"""Референс-эмулятор RVM-1 — оракул для дифф-тестов.

Семантика обязана до бита совпадать со спекой vm/isa.md и правилами
genpattern.py. Один step() = один архитектурный шаг (одна инструкция).
"""
from __future__ import annotations

from vm.isa import WORD_MASK, Insn
from vm.statecodec import MachState


class RefEmu:
    def __init__(self, prog: list[Insn], inp: bytes = b""):
        self.m = MachState(prog=list(prog), inp=inp)

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
            R[d] = m.ram.get(R[s], 0)
        elif op == "LOADI":
            R[d] = m.ram.get(imm, 0)
        elif op == "STORE":
            m.ram[R[d]] = R[s]
        elif op == "STOREI":
            m.ram[imm] = R[d]
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
        elif op == "PUTC":
            m.out += bytes([R[d] & 0xFF])
        elif op == "GETC":
            if m.inp:
                R[d], m.inp = m.inp[0], m.inp[1:]
            else:
                R[d] = 0
        elif op == "HLT":
            m.st = "hlt"
            return False
        else:  # pragma: no cover
            m.st = "err:BADOP"
            return False

        m.pc = nxt & WORD_MASK
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
