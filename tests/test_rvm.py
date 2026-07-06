# -*- coding: utf-8 -*-
"""RVM-1: потрейсовые дифф-тесты regex-машины против референс-эмулятора.

Строжайшая проверка: на каждом архитектурном шаге (момент PH:0) строка
regex-машины обязана БАЙТ-В-БАЙТ совпадать с encode() состояния эмулятора.
"""
import re as stdre
import sys
from pathlib import Path

import pytest
import regex

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vm.asm import assemble                          # noqa: E402
from vm.genpattern import build_rules                # noqa: E402
from vm.isa import BY_NAME, Insn                     # noqa: E402
from vm.refemu import RefEmu                         # noqa: E402
from vm.statecodec import encode, decode_head        # noqa: E402


def compile_rules():
    out = []
    for name, pat, repl in build_rules():
        out.append((name, regex.compile(pat),
                    stdre.sub(r"\$\{(\w+)\}", r"\\g<\1>", repl)))
    return out


COMPILED = compile_rules()


def one_pass(state: str):
    for name, pat, repl in COMPILED:
        new, n = pat.subn(repl, state)
        if n:
            return new, name
    return state, None


def advance_to_ph0(state: str, limit: int = 20):
    """Прогоняет проходы до следующего момента PH:0 (или останова)."""
    for _ in range(limit):
        state, applied = one_pass(state)
        if applied is None:
            return state, False
        m = decode_head(state)
        if m.st != "run":
            return state, False
        if m.ph == 0:
            return state, True
    raise AssertionError("PH:0 не достигнут за лимит проходов")


def lockstep(prog, inp=b"", max_steps=100_000):
    """Пошаговый дифф: encode(эмулятор) == строка regex-машины на каждом шаге."""
    ref = RefEmu(prog, inp)
    state = encode(ref.m)
    step = 0
    while True:
        assert state == encode(ref.m), (
            f"расхождение на шаге {step}:\n regex: {state[:200]}\n"
            f" refemu:{encode(ref.m)[:200]}")
        ref_alive = ref.step()
        state, rx_alive = advance_to_ph0(state)
        step += 1
        if not ref_alive or not rx_alive:
            assert state == encode(ref.m), (
                f"расхождение в финале:\n regex: {state[:200]}\n"
                f" refemu:{encode(ref.m)[:200]}")
            assert ref_alive == rx_alive
            return decode_head(state), step
        assert step <= max_steps


def P(*lines):
    return assemble("\n".join(lines))


# --- покомандные вектора -------------------------------------------------

@pytest.mark.parametrize("prog,checks", [
    (P("MOVI R0, 0xdeadbeef", "HLT"), {"r0": 0xDEADBEEF}),
    (P("MOVI R5, 7", "MOV R2, R5", "HLT"), {"r2": 7}),
    # ADD: переносы через все ниббл-границы
    (P("MOVI R0, 0x0000ffff", "MOVI R1, 1", "ADD R0, R1", "HLT"),
     {"r0": 0x00010000}),
    (P("MOVI R0, 0xffffffff", "ADDI R0, 1", "HLT"), {"r0": 0}),
    (P("MOVI R0, 0x0fffffff", "ADDI R0, 0xf0000001", "HLT"), {"r0": 0}),
    (P("MOVI R0, 0x1a2b3c4d", "MOVI R1, 0x9876fedc", "ADD R0, R1", "HLT"),
     {"r0": (0x1A2B3C4D + 0x9876FEDC) & 0xFFFFFFFF}),
    # SUB: займы
    (P("MOVI R0, 0", "SUBI R0, 1", "HLT"), {"r0": 0xFFFFFFFF}),
    (P("MOVI R0, 0x10000000", "MOVI R1, 1", "SUB R0, R1", "HLT"),
     {"r0": 0x0FFFFFFF}),
    (P("MOVI R0, 100", "SUBI R0, 42", "HLT"), {"r0": 58}),
    # переходы
    (P("JMP 2", "MOVI R0, 1", "HLT"), {"r0": 0}),
    (P("MOVI R6, 3", "JMPR R6", "MOVI R0, 1", "HLT"), {"r0": 0}),
    (P("MOVI R0, 5", "MOVI R1, 5", "JEQ R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 0}),
    (P("MOVI R0, 5", "MOVI R1, 6", "JEQ R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 1}),
    (P("MOVI R0, 5", "MOVI R1, 6", "JNE R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 0}),
    (P("MOVI R0, 5", "MOVI R1, 5", "JNE R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 1}),
    # JLT/JGE: беззнаковые границы
    (P("MOVI R0, 1", "MOVI R1, 2", "JLT R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 0}),
    (P("MOVI R0, 2", "MOVI R1, 2", "JLT R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 1}),
    (P("MOVI R0, 0xffffffff", "MOVI R1, 1", "JLT R0, R1, 4",
       "MOVI R2, 1", "HLT"), {"r2": 1}),
    (P("MOVI R0, 0x7fffffff", "MOVI R1, 0x80000000", "JLT R0, R1, 4",
       "MOVI R2, 1", "HLT"), {"r2": 0}),
    (P("MOVI R0, 3", "MOVI R1, 3", "JGE R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 0}),
    (P("MOVI R0, 2", "MOVI R1, 3", "JGE R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 1}),
    # псевдо
    (P("MOVI R0, 9", "MOVI R1, 3", "JGT R0, R1, 4", "MOVI R2, 1", "HLT"),
     {"r2": 0}),
    # --- память: hit/miss/insert (first/middle/last), перезапись -----------
    (P("MOVI R0, 0xabcd", "STOREI R0, 0x100", "LOADI R1, 0x100", "HLT"),
     {"r1": 0xABCD}),
    (P("LOADI R1, 0x77", "HLT"), {"r1": 0}),                    # miss -> 0
    (P("MOVI R0, 1", "STOREI R0, 0x50",                          # вставки:
       "MOVI R0, 2", "STOREI R0, 0x10",                          # в начало
       "MOVI R0, 3", "STOREI R0, 0x90",                          # в конец
       "MOVI R0, 4", "STOREI R0, 0x50",                          # перезапись
       "LOADI R1, 0x10", "LOADI R2, 0x50", "LOADI R3, 0x90", "HLT"),
     {"r1": 2, "r2": 4, "r3": 3}),
    (P("MOVI R0, 0x200", "MOVI R1, 0xbeef", "STORE R0, R1",      # рег-адрес
       "MOVI R2, 0x200", "LOAD R3, R2", "HLT"),
     {"r3": 0xBEEF}),
    (P("MOVI R0, 0xffffffff", "MOVI R1, 7", "STORE R0, R1",      # крайний адрес
       "LOADI R2, 0xffffffff", "HLT"), {"r2": 7}),
    # --- фреймбуфер: окно 00f0oooo -----------------------------------------
    (P("MOVI R0, 0xab", "STOREI R0, 0xf00000",                    # первый px
       "LOADI R1, 0xf00000", "HLT"), {"r1": 0xAB}),
    (P("MOVI R0, 0xf007ff", "MOVI R1, 0x11223344", "STORE R0, R1",  # последний
       "LOAD R2, R0", "HLT"), {"r2": 0x44}),                     # байт обрезан
    (P("MOVI R0, 0xf01000", "MOVI R1, 5", "STORE R0, R1",         # вне зоны ->
       "LOAD R2, R0", "HLT"), {"r2": 5}),                        # обычный #M
    (P("LOADI R1, 0xf00033", "HLT"), {"r1": 0}),                 # чтение нуля
])
def test_units_lockstep(prog, checks):
    m, _ = lockstep(prog)
    assert m.st == "hlt"
    for k, want in checks.items():
        assert m.regs[int(k[1:])] == want, k


def test_putc_getc():
    prog = P("GETC R0", "PUTC R0", "GETC R1", "PUTC R1",
             "GETC R2", "PUTC R2", "HLT")          # echo 2 байта + EOF->0
    m, _ = lockstep(prog, inp=b"Hi")
    assert m.st == "hlt"
    assert m.out == b"Hi\x00"
    assert m.regs[2] == 0


def test_putc_low_byte():
    m, _ = lockstep(P("MOVI R0, 0x11223344", "PUTC R0", "HLT"))
    assert m.out == b"\x44"


def test_trap_badop():
    prog = [Insn(BY_NAME["MOVI"], d=0, imm=1)]
    # руками портим: опкод 0x99 не существует
    bad = Insn.__new__(Insn)
    object.__setattr__(bad, "op", type(prog[0].op)(0x99, "XXX", ""))
    object.__setattr__(bad, "d", 0)
    object.__setattr__(bad, "s", 0)
    object.__setattr__(bad, "imm", 0)
    state = encode(RefEmu([bad]).m)
    state, alive = advance_to_ph0(state)
    assert not alive and "|ST:err:BADOP" in state


def test_trap_noslot():
    prog = P("JMP 5", "HLT")
    m, _ = lockstep(prog)
    assert m.st == "err:NOSLOT"


def test_fib20_g1a():
    src = (ROOT / "vm" / "programs" / "fib.rvs").read_text(encoding="utf-8")
    prog = assemble(src)
    m, steps = lockstep(prog)
    assert m.st == "hlt"
    assert m.regs[0] == 6765, hex(m.regs[0])
    print(f"\nfib(20): {steps} архитектурных шагов, R0=0x{m.regs[0]:08x}")
