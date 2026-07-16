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
    # v1.3b: беззнаковые деление/остаток (микрофазы PH:4, restoring по
    # битам; /0 -> quot=0xFFFFFFFF, rem=делимое — детерминированно)
    Op(0x67, "DIV",    "ds"),   # R[d] /= R[s]
    Op(0x68, "MOD",    "ds"),   # R[d] %= R[s]
    # v1.4: fused-инструкции внутренних циклов рендера (план O4).
    # Само-повтор: пока счётчик C != 0 — эффект одного пикселя за такт
    # (PC не меняется); при C == 0 — обычный переход к следующей.
    # Привязка регистров (см. стабы rvm_span_loop/rvm_col_loop):
    #   DSPAN: A=position, B=step, C=пикселей, D=src, U=cmap, T=dest;
    #     spot = ((A>>4)&0xFC0)|(A>>26); M[T]=M[U+M[D+spot]]&0xFF;
    #     A+=B; T+=1; C-=1.
    #   DCOL:  A=frac, B=fracstep, C=пикселей, D=src, U=cmap, T=dest;
    #     spot = (A>>16)&127; M[T]=M[U+M[D+spot]]&0xFF;
    #     A+=B; T+=320; C-=1.
    Op(0x69, "DSPAN",  ""),
    Op(0x6A, "DCOL",   ""),
    # v1.4b: суперопкоды fixed-point горячих путей.
    # DIV48 (фиксрег): B = ((A<<32)|B... точнее (A:B 48-бит: A=верхние 16,
    #   B=нижние 32) / C; restoring 48 бит-фаз в PH:4 (MF как DIV).
    #   Семантика = rvm_div48(nhi, nlo, d) из m_fixed.c; /0 -> ffffffff.
    Op(0x6C, "DIV48",  ""),
    # FMUL (фиксрег): B = ((int64)(int32)A * (int32)B) >> 16 mod 2^32 —
    #   семантика FixedMul 16.16. Хорнер 8 фаз с 48-бит акк + знаковые
    #   коррекции (PH:3, MF:<i><acc12>).
    Op(0x6B, "FMUL",   ""),
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


def zone_g() -> str:
    """#G — spot-цифра n1 DSPAN: :uv=r, r = (u>>2)*4 + (v>>2)
    (верхние 2 бита цифр A[11:8]=a2 и A[31:28]=a7)."""
    dig = "0123456789abcdef"
    parts = ["#G"]
    for u in range(16):
        for v in range(16):
            parts.append(f":{dig[u]}{dig[v]}={dig[(u >> 2) * 4 + (v >> 2)]}")
    return "".join(parts)


def zone_j() -> str:
    """#J — spot-цифра n0 DSPAN: :uv=r, r = (u&3)*4 + (v>>2)
    (нижние 2 бита a7 и верхние 2 бита a6)."""
    dig = "0123456789abcdef"
    parts = ["#J"]
    for u in range(16):
        for v in range(16):
            parts.append(f":{dig[u]}{dig[v]}={dig[(u & 3) * 4 + (v >> 2)]}")
    return "".join(parts)


ROM = (zone_d() + zone_q() + zone_l() + zone_a() + zone_s()
       + zone_b() + zone_o() + zone_x() + zone_h() + zone_t()
       + zone_g() + zone_j())

# --- фреймбуфер -------------------------------------------------------------
# Окно адресов f0xxxx: STORE в него пишет младший байт в ячейку зоны #F.
# Зона пре-populated фикс. числом ячеек [oooo:vv] (offset 4 hex, байт 2 hex).
FB_PREFIX = "f0"          # старшие 2 hex-цифры адреса окна
FB_BASE = 0xF00000
FB_W, FB_H = 320, 200     # Doom-кадр (этап 1 был 64x32)
FB_CELLS = FB_W * FB_H

# --- WAD-зона #W (G2c) -------------------------------------------------------
# Read-mostly байты WAD в постраничной зоне: [ppppp:32hex] — страница 16 байт,
# page = addr>>4 (5 hex), off = addr & 0xF. Диапазон адресов 00a00010..00efffff
# (данные с выровненного WAD_DATA; слово размера лежит отдельной #M-ячейкой
# по адресу WAD_BASE). LOAD/STORE-правила ветвятся по off (16 веток,
# несматченные группы пусты в replacement — PCRE2_SUBSTITUTE_UNSET_EMPTY,
# у Python-regex так по умолчанию).
WAD_BASE = 0xA00000
WAD_DATA = 0xA00010
WAD_PAGE = 16

# --- v2.0: плоская зона #N (design-n.md) -------------------------------------
# Адреса [0, NRAM_TOP) живут в плотной позиционной зоне #N: слот 8 hex,
# адрес имплицитен позицией (данные программы + zone-память Doom).
# Highmem (стек ELVM 0xffffxx и всё вне #N/#F/#W-диапазонов) — в #M.
# Профиль зон: боевой по умолчанию; RVM_TEST_PROFILE=small — малые зоны
# для lockstep-сьюта (python-regex не тянет боевые фикс-прыжки).
import os as _os

TEST_PROFILE = _os.environ.get("RVM_TEST_PROFILE", "") == "small"
if TEST_PROFILE:
    NRAM_TOP = 0x1000         # 4096 слов, зона 32КБ
    PROG_SLOTS = 0x1000       # паддинг #P (v2.0 этап 3)
    WAD_PAGES = 16            # окно #W: 256 байт (WAD-правила на HOP)
else:
    NRAM_TOP = 0x700000       # 7.3М слов, зона 58.7МБ
    PROG_SLOTS = 0x100000     # 16^5 слотов x 23 симв = 24.1МБ
    # полное окно [WAD_DATA, FB_BASE): зона #W тотальна для диапазона
    # адресов 00a00010..00efffff — дерево страниц без за-зонных прыжков
    WAD_PAGES = (0xF00000 - 0xA00010) // 16

# Паддинг #P до PROG_SLOTS — НЕФЕТЧАБЕЛЬНЫЕ заглушки (23 симв): эхо
# fetch "I(?P=p7)..." не матчит '-' против hex-цифры PC -> trap_noslot,
# бит-в-бит с refemu (NOSLOT при pc >= len(prog)). Опкод ff был бы HLT
# и маскировал бы беглые переходы под чистый останов.
PROG_PAD_SLOT = "I--------:------------;"

# --- v2.0 Э3: фиксированные смещения зон (заголовок фикс-длинный на
# PH:0/1/2 — |MF: живёт только внутри PH:3/4, IN/OUT в хвосте) --------------
HDR_LEN = (len("RVM1|ST:run|PH:0|CI:") + 12 + len("|PC:") + 8
           + NUM_REGS * (len("|Rx:") + 8) + len("|CLK:") + 8 + 1)
# ROM определяется ниже (зависит от таблиц); OFF_* — см. offsets()


def zone_offsets(rom_len: int) -> dict[str, int]:
    """Абсолютные смещения НАЧАЛА СОДЕРЖИМОГО зон (после '#X')."""
    off_n = HDR_LEN + rom_len + 2
    off_p = off_n + 8 * NRAM_TOP + 2
    off_f = off_p + 23 * PROG_SLOTS + 2
    off_w = off_f + 9 * FB_CELLS + 2
    off_m = off_w + 40 * WAD_PAGES + 2
    return {"N": off_n, "P": off_p, "F": off_f, "W": off_w, "M": off_m}
