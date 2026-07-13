# -*- coding: utf-8 -*-
"""RVM-1: единая таблица опкодов и константы (источник правды для
ассемблера, референс-эмулятора, генератора правил и тестов).

Спека: vm/isa.md.
"""
from __future__ import annotations

from dataclasses import dataclass

WORD_BITS = 32
WORD_MASK = (1 << WORD_BITS) - 1
WORD_HEX = 8            # 8 hex-цифр на слово
NUM_REGS = 8            # R0..R7

# имена регистров для ассемблера (алиасы ELVM + скретч)
REG_NAMES = {
    "R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5, "R6": 6, "R7": 7,
    "A": 0, "B": 1, "C": 2, "D": 3, "SP": 4, "BP": 5, "T": 6, "U": 7,
}


@dataclass(frozen=True)
class Op:
    code: int          # 2 hex-цифры
    name: str
    fmt: str           # "ds" | "di" | "dsi" | "d" | "i" | ""
    # ds: dst,src(reg); di: dst,imm; dsi: dst,src,imm(target); d: dst; i: imm


OPS = [
    Op(0x01, "MOV",    "ds"),
    Op(0x02, "MOVI",   "di"),
    Op(0x10, "ADD",    "ds"),
    Op(0x11, "ADDI",   "di"),
    Op(0x12, "SUB",    "ds"),
    Op(0x13, "SUBI",   "di"),
    Op(0x20, "LOAD",   "ds"),    # R[d] = M[R[s]]      (v1.1)
    Op(0x21, "LOADI",  "di"),    # R[d] = M[imm]       (v1.1)
    Op(0x22, "STORE",  "ds"),    # M[R[d]] = R[s]      (v1.1)
    Op(0x23, "STOREI", "di"),    # M[imm] = R[d]       (v1.1)
    Op(0x30, "JMP",    "i"),
    Op(0x38, "JMPR",   "d"),    # PC = R[d] (indirect jump)
    Op(0x31, "JEQ",    "dsi"),
    Op(0x32, "JNE",    "dsi"),
    Op(0x33, "JLT",    "dsi"),   # unsigned <
    Op(0x34, "JGE",    "dsi"),   # unsigned >=
    Op(0x50, "WR24",   "d"),    # R[d] &= 0x00ffffff (24-бит wrap EIR)
    Op(0x40, "PUTC",   "d"),
    Op(0x41, "GETC",   "d"),
    # --- v1.2: нативные битовые (семантика = __builtin_* ELVM-libc:
    #     сдвиг на s >= 32 даёт 0 (SHL/SHR) либо все-биты-знака (SAR)) ---
    Op(0x60, "BAND",   "ds"),   # R[d] &= R[s]
    Op(0x61, "BOR",    "ds"),   # R[d] |= R[s]
    Op(0x62, "BXOR",   "ds"),   # R[d] ^= R[s]
    Op(0x63, "SHL",    "ds"),   # R[d] <<= R[s]   (логический, s>=32 -> 0)
    Op(0x64, "SHR",    "ds"),   # R[d] >>= R[s]   (логический, s>=32 -> 0)
    Op(0x65, "SAR",    "ds"),   # R[d] >>= R[s]   (арифметический)
    # v1.3: умножение mod 2^32 (микрофазы PH:3 на regex-уровне,
    # атомарно в refemu; семантика = __builtin_mul)
    Op(0x66, "MUL",    "ds"),   # R[d] *= R[s]
    Op(0xFF, "HLT",    ""),
]

BY_NAME = {op.name: op for op in OPS}
BY_CODE = {op.code: op for op in OPS}

# псевдоинструкции: перестановка операндов d<->s
PSEUDO = {"JGT": "JLT", "JLE": "JGE"}


@dataclass(frozen=True)
class Insn:
    op: Op
    d: int = 0
    s: int = 0
    imm: int = 0

    def encode(self, addr: int) -> str:
        return (f"I{addr:08x}:{self.op.code:02x}{self.d:01x}{self.s:01x}"
                f"{self.imm:08x};")


def hexw(v: int) -> str:
    return f"{v & WORD_MASK:08x}"


# --- константные зоны (ROM) -------------------------------------------------

def zone_d() -> str:
    return "#D0123456789abcdef"


def zone_q() -> str:
    return "#Q" + "".join(f":{d}{d}" for d in "0123456789abcdef")


def zone_l() -> str:
    dig = "0123456789abcdef"
    return "#L" + "".join(f":{dig[i]}{dig[j]}"
                          for i in range(16) for j in range(i + 1, 16))


def _fulladder_zone(tag: str, sub: bool) -> str:
    dig = "0123456789abcdef"
    parts = [tag]
    for a in range(16):
        for b in range(16):
            for c in range(2):
                if not sub:
                    r = a + b + c
                else:
                    r = a - b - c
                out = r & 0xF
                cy = 1 if (r > 15 or r < 0) else 0
                parts.append(f":{dig[a]}{dig[b]}{c}={dig[out]}{cy}")
    return "".join(parts)


def zone_a() -> str:
    """#A — полный сумматор ниббла: :abc=oc'"""
    return _fulladder_zone("#A", sub=False)


def zone_s() -> str:
    """#S — полный вычитатель ниббла (c = borrow-in, c' = borrow-out)."""
    return _fulladder_zone("#S", sub=True)


def _bitop_zone(tag: str, fn) -> str:
    """Таблица битовой операции по парам ниблов: :ab=r"""
    dig = "0123456789abcdef"
    parts = [tag]
    for a in range(16):
        for b in range(16):
            parts.append(f":{dig[a]}{dig[b]}={dig[fn(a, b)]}")
    return "".join(parts)


def zone_b() -> str:
    """#B — AND ниблов."""
    return _bitop_zone("#B", lambda a, b: a & b)


def zone_o() -> str:
    """#O — OR ниблов."""
    return _bitop_zone("#O", lambda a, b: a | b)


def zone_x() -> str:
    """#X — XOR ниблов."""
    return _bitop_zone("#X", lambda a, b: a ^ b)


def zone_h() -> str:
    """#H — сдвиг пары ниблов: :abk=r, r = ((a<<4|b) << k >> 4) & 0xF
    (k=1..3; левый сдвиг пары hi=a,lo=b даёт новую цифру на месте a).
    Для SHR/SAR та же таблица читается парой (prev,cur) со сдвигом 4-k."""
    dig = "0123456789abcdef"
    parts = ["#H"]
    for a in range(16):
        for b in range(16):
            for k in range(1, 4):
                r = (((a << 4) | b) << k >> 4) & 0xF
                parts.append(f":{dig[a]}{dig[b]}{k}={dig[r]}")
    return "".join(parts)


def zone_t() -> str:
    """#T — умножение нибла на нибл с переносом: :dkc=oc'
    (o = (d*k+c) & 0xF, c' = (d*k+c) >> 4; c,c' в 0..15 hex)."""
    dig = "0123456789abcdef"
    parts = ["#T"]
    for d in range(16):
        for k in range(16):
            for c in range(16):
                r = d * k + c
                parts.append(f":{dig[d]}{dig[k]}{dig[c]}="
                             f"{dig[r & 0xF]}{dig[r >> 4]}")
    return "".join(parts)


ROM = (zone_d() + zone_q() + zone_l() + zone_a() + zone_s()
       + zone_b() + zone_o() + zone_x() + zone_h() + zone_t())

# --- фреймбуфер -------------------------------------------------------------
# Окно адресов f0xxxx: STORE в него пишет младший байт в ячейку зоны #F.
# Зона пре-populated фикс. числом ячеек [oooo:vv] (offset 4 hex, байт 2 hex).
FB_PREFIX = "f0"          # старшие 2 hex-цифры адреса окна
FB_BASE = 0xF00000
FB_W, FB_H = 64, 32       # демо-размер этапа 1 (Doom: 320x200 на этапе 2)
FB_CELLS = FB_W * FB_H
