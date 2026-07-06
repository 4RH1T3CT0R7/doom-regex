# -*- coding: utf-8 -*-
"""Двухпроходный ассемблер RVM-1: .rvs -> list[Insn].

Синтаксис:
    ; комментарий
    label:
        MOVI R0, 0x14
        JEQ  R3, R2, end     ; метка -> индекс инструкции
        JGT  A, B, loop      ; псевдо: JLT с перестановкой
"""
from __future__ import annotations

from vm.isa import BY_NAME, PSEUDO, REG_NAMES, WORD_MASK, Insn


class AsmError(ValueError):
    pass


def _tokenize(line: str) -> list[str]:
    line = line.split(";", 1)[0].strip()
    if not line:
        return []
    return line.replace(",", " ").split()


def _num(tok: str, labels: dict[str, int], lineno: int) -> int:
    if tok in labels:
        return labels[tok]
    try:
        return int(tok, 0) & WORD_MASK
    except ValueError:
        raise AsmError(f"строка {lineno}: не число и не метка: {tok!r}")


def _reg(tok: str, lineno: int) -> int:
    r = REG_NAMES.get(tok.upper())
    if r is None:
        raise AsmError(f"строка {lineno}: не регистр: {tok!r}")
    return r


def assemble(src: str) -> list[Insn]:
    # проход 1: метки
    labels: dict[str, int] = {}
    parsed: list[tuple[int, list[str]]] = []
    addr = 0
    for lineno, raw in enumerate(src.splitlines(), 1):
        toks = _tokenize(raw)
        while toks and toks[0].endswith(":"):
            label = toks[0][:-1]
            if label in labels:
                raise AsmError(f"строка {lineno}: дубль метки {label!r}")
            labels[label] = addr
            toks = toks[1:]
        if toks:
            parsed.append((lineno, toks))
            addr += 1

    # проход 2: кодирование
    prog: list[Insn] = []
    for lineno, toks in parsed:
        name = toks[0].upper()
        swap = False
        if name in PSEUDO:
            name, swap = PSEUDO[name], True
        op = BY_NAME.get(name)
        if op is None:
            raise AsmError(f"строка {lineno}: неизвестная инструкция {toks[0]!r}")
        args = toks[1:]

        def need(n):
            if len(args) != n:
                raise AsmError(
                    f"строка {lineno}: {name} ждёт {n} операндов, дано {len(args)}")

        if op.fmt == "ds":
            need(2)
            d, s = _reg(args[0], lineno), _reg(args[1], lineno)
            if swap:
                d, s = s, d
            prog.append(Insn(op, d=d, s=s))
        elif op.fmt == "di":
            need(2)
            prog.append(Insn(op, d=_reg(args[0], lineno),
                             imm=_num(args[1], labels, lineno)))
        elif op.fmt == "dsi":
            need(3)
            d, s = _reg(args[0], lineno), _reg(args[1], lineno)
            if swap:
                d, s = s, d
            prog.append(Insn(op, d=d, s=s, imm=_num(args[2], labels, lineno)))
        elif op.fmt == "d":
            need(1)
            prog.append(Insn(op, d=_reg(args[0], lineno)))
        elif op.fmt == "i":
            need(1)
            prog.append(Insn(op, imm=_num(args[0], labels, lineno)))
        else:  # ""
            need(0)
            prog.append(Insn(op))
    return prog
