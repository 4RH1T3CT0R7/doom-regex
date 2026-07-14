# -*- coding: utf-8 -*-
"""Транслятор ELVM EIR (текстовый формат) -> RVM-ассемблер (.rvs).

Семантика воспроизводит ir/ir.c ELVM:
  * неявный `JMP main` получает pc=0; pc растёт по базовым блокам
    (границы — метки и инструкции переходов);
  * метки .text -> pc блока; метки .data -> адрес слова данных;
  * цель у переходов — ПЕРВЫЙ аргумент (`jeq target, dst, src`);
  * слова 32-битные (G2b: тулчейн ELVM переведён на 32-бит — Doom
    fixed_t 16.16 не влезал в 24 бита); wrap mod 2^32 у RVM нативный.

Register-indirect переходы: регистры хранят EIR-pc; в начале программы —
ТРАМПЛИН-ТАБЛИЦА (слот i = JMP на блок i), так что `JMPR r` прыгает в
слот=EIR-pc и трамплином уходит на блок. Прямые переходы резолвятся
в метки блоков без трамплина.

Маппинг регистров: A->R0 B->R1 C->R2 D->R3 SP->R4 BP->R5;
R7(U) — материализация констант, R6(T) свободен для будущих нужд.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

M24 = 0xFFFFFFFF   # слова 32-бит (были 24)
REGMAP = {"A": "R0", "B": "R1", "C": "R2", "D": "R3", "SP": "R4", "BP": "R5"}
SCRATCH = "R7"

JCC = {"jeq": "JEQ", "jne": "JNE", "jlt": "JLT", "jge": "JGE",
       "jgt": "JGT", "jle": "JLE"}
CMP = {"eq": "JEQ", "ne": "JNE", "lt": "JLT", "ge": "JGE",
       "gt": "JGT", "le": "JLE"}


class EirError(ValueError):
    pass


def _parse_operand(tok: str):
    tok = tok.strip()
    if tok in REGMAP:
        return ("reg", tok)
    try:
        return ("imm", int(tok, 0) & M24)
    except ValueError:
        return ("ref", tok)


def _tokenize_eir(text: str):
    """-> список (kind, payload): label/op/data-directive.

    Метки/слова данных несут номер ПОДСЕКЦИИ ('.data N'): 8cc эмитит
    строковые литералы вложенных инициализаторов в подсекцию N+1 прямо
    посреди внешнего инициализатора, а ir.c сериализует данные по
    возрастающим подсекциям — раскладка в порядке файла битая
    (указатели перемежались бы байтами строк)."""
    out = []
    section = "text"
    subsec = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(".text"):
            section = "text"
            continue
        if line.startswith(".data"):
            section = "data"
            parts = line.split()
            subsec = int(parts[1]) if len(parts) > 1 else 0
            continue
        if line.startswith((".file", ".loc")):
            continue
        # метки (возможно несколько + инструкция на той же строке)
        while ":" in line.split(None, 1)[0]:
            head, _, rest = line.partition(":")
            if " " in head or "\t" in head:
                break
            out.append(("label", section, (head, subsec), lineno))
            line = rest.strip()
            if not line:
                break
        if not line:
            continue
        parts = line.split(None, 1)
        if parts[0].startswith(".") and parts[0] not in (".long", ".string"):
            print(f"eir2rvs: warning: пропущена директива {parts[0]} "
                  f"(строка {lineno})", file=sys.stderr)
            continue
        op = parts[0].lower()
        args = [a.strip() for a in parts[1].split(",")] if len(parts) > 1 else []
        if section == "data":
            if op == ".long":
                out.append(("data", subsec, args[0], lineno))
            elif op == ".string":
                s = parts[1].strip()
                if not (s.startswith('"') and s.endswith('"')):
                    raise EirError(f"строка {lineno}: кривой .string")
                body = (s[1:-1].encode("latin-1")
                        .decode("unicode_escape").encode("latin-1"))
                for b in body:
                    out.append(("data", subsec, str(b), lineno))
                out.append(("data", subsec, "0", lineno))
            else:
                raise EirError(f"строка {lineno}: неизвестная data-директива {op}")
        else:
            out.append(("op", op, args, lineno))
    return out


def translate(eir_text: str) -> str:
    toks = _tokenize_eir(eir_text)

    # --- проход 1: pc блоков; данные копим по подсекциям (семантика ir.c:
    # serialize_data сериализует подсекции по возрастанию, метки данных
    # привязываются в момент сериализации своей подсекции) ------------------
    labels: dict[str, int] = {"main": 1}
    pc = 1                       # pc=0 занят неявным JMP main
    prev_boundary = True
    insts = []                   # (op, args, pc, lineno, fname)
    subsecs: dict[int, list] = {}   # n -> [("label", имя) | ("word", raw)]

    cur_fn = ""                  # текущая text-функция (метка не с точки)
    for kind, a, b, lineno in toks:
        if kind == "label":
            name, subsec = b
            if a == "text":
                if not prev_boundary:
                    pc += 1
                labels[name] = pc
                prev_boundary = True
                if not name.startswith("."):
                    cur_fn = name
            else:
                subsecs.setdefault(subsec, []).append(("label", name))
        elif kind == "data":
            subsecs.setdefault(a, []).append(("word", b))
        else:  # op
            insts.append((a, b, pc, lineno, cur_fn))
            if a in JCC or a == "jmp":
                pc += 1
                prev_boundary = True
            else:
                prev_boundary = False
    max_pc = pc

    # сериализация данных: подсекции по возрастанию
    data_addr = 0
    data_words = []              # (addr, raw-значение или ref)
    for n in sorted(subsecs):
        for what, payload in subsecs[n]:
            if what == "label":
                labels[payload] = data_addr
            else:
                data_words.append((data_addr, payload))
                data_addr += 1

    # семантика ir.c: _edata = адрес за последним словом данных; в память
    # дописывается слово со значением _edata+1 (heap-указатель malloc)
    labels.setdefault("_edata", data_addr)
    data_words.append((data_addr, str(data_addr + 1)))
    data_addr += 1

    def resolve(v):
        k, x = _parse_operand(v)
        if k == "ref":
            if x not in labels:
                # выражение символ+смещение / символ-смещение (эмитит 8cc
                # для адресной арифметики в .data, напр. 'mousearray+1')
                m = re.fullmatch(r"([A-Za-z_.$][\w.$]*)([+-]\d+)", x)
                if m and m.group(1) in labels:
                    return ("imm",
                            (labels[m.group(1)] + int(m.group(2)))
                            & 0xFFFFFFFF)
                raise EirError(f"неизвестная метка {x!r}")
            return ("imm", labels[x])
        return (k, x)

    # --- эмиссия ------------------------------------------------------------
    L: list[str] = []
    emit = L.append
    tmp_n = 0

    def newlab(base: str) -> str:
        nonlocal tmp_n
        tmp_n += 1
        return f"_{base}{tmp_n}"

    def materialize(val) -> str:
        """Значение (reg|imm) -> имя регистра (imm через SCRATCH)."""
        k, x = val
        if k == "reg":
            return REGMAP[x]
        emit(f"    MOVI {SCRATCH}, {x}")
        return SCRATCH

    # данные -> .mem
    for addr, raw in data_words:
        k, x = resolve(raw)
        assert k == "imm"
        if x:
            emit(f"    .mem {addr} {x}")

    # трамплин-таблица: слот i == EIR pc i (метки в комментарии ниже)
    emit("; трамплин: слот i = JMP блок_i (JMPR по регистровым pc)")
    for i in range(max_pc + 1):
        emit(f"    JMP Bpc_{i}")

    # блоки: Bpc_0 = неявный JMP main (семантика ir.c)
    emit("Bpc_0:")
    emit(f"    JMP Bpc_{labels['main']}")
    cur_pc = 0

    # --- v1.2: нативные стабы битовых builtin-ов ELVM-libc ------------------
    # Тела __builtin_and/or/xor/shl/shr/sar/not (циклы по 32 итерации на
    # операцию!) заменяются нативными опкодами RVM. Конвенция вызова 8cc:
    # на входе [SP]=retpc(EIR-pc), [SP+1]=a, [SP+2]=b; результат в B;
    # ret = pop retpc + JMPR (через трамплин). Семантика краёв (сдвиг>=32)
    # у опкодов совпадает с C-циклами — tier-1 остаётся оракулом.
    BUILTIN_STUBS = {
        "__builtin_and": "BAND", "__builtin_or": "BOR",
        "__builtin_xor": "BXOR", "__builtin_shl": "SHL",
        "__builtin_shr": "SHR",  "__builtin_sar": "SAR",
        "__builtin_not": None,   # BXOR с 0xffffffff
        "__builtin_mul": "MUL",  # v1.3: микрофазный нативный MUL
    }

    def emit_stub(mnem: str | None) -> None:
        emit("    MOV T, SP")
        emit("    ADDI T, 1")
        emit("    LOAD B, T")           # B = a
        if mnem is None:                # not: B ^= ~0
            emit("    MOVI U, 4294967295")
            emit("    BXOR B, U")
        else:
            emit("    ADDI T, 1")
            emit("    LOAD U, T")       # U = b
            emit(f"    {mnem} B, U")
        emit("    LOAD T, SP")          # retpc
        emit("    ADDI SP, 1")
        emit("    JMPR T")              # в трамплин по EIR-pc

    prev_fn = ""
    for op, args, ipc, lineno, fname in insts:
        if fname in BUILTIN_STUBS:
            if ipc != cur_pc:
                for missing in range(cur_pc + 1, ipc + 1):
                    emit(f"Bpc_{missing}:")
                cur_pc = ipc
            if fname != prev_fn:
                emit_stub(BUILTIN_STUBS[fname])
            prev_fn = fname
            continue                     # мёртвое тело не эмитим
        if fname != prev_fn:
            emit(f"; @fn {fname}")
        prev_fn = fname
        if ipc != cur_pc:
            for missing in range(cur_pc + 1, ipc + 1):
                emit(f"Bpc_{missing}:")
            cur_pc = ipc

        if op == "mov":
            d = REGMAP[args[0]]
            k, x = resolve(args[1])
            emit(f"    MOV {d}, {REGMAP[x]}" if k == "reg"
                 else f"    MOVI {d}, {x}")
        elif op in ("add", "sub"):
            d = REGMAP[args[0]]
            k, x = resolve(args[1])
            mnem = op.upper() + ("" if k == "reg" else "I")
            emit(f"    {mnem} {d}, {REGMAP[x] if k == 'reg' else x}")
            # 32-битные слова: wrap mod 2^32 у RVM нативный, WR24 не нужен
        elif op == "load":
            d = REGMAP[args[0]]
            k, x = resolve(args[1])
            emit(f"    LOAD {d}, {REGMAP[x]}" if k == "reg"
                 else f"    LOADI {d}, {x}")
        elif op == "store":
            v = REGMAP[args[0]]                    # значение (dst EIR)
            k, x = resolve(args[1])                # адрес (src EIR)
            if k == "reg":
                emit(f"    STORE {REGMAP[x]}, {v}")
            else:
                emit(f"    STOREI {v}, {x}")
        elif op == "putc":
            k, x = resolve(args[0])
            emit(f"    PUTC {materialize((k, x))}")
        elif op == "getc":
            emit(f"    GETC {REGMAP[args[0]]}")
        elif op == "exit":
            emit("    HLT")
        elif op == "dump":
            pass
        elif op == "jmp":
            k, x = resolve(args[0])
            emit(f"    JMPR {REGMAP[x]}" if k == "reg"
                 else f"    JMP Bpc_{x}")
        elif op in JCC:
            tgt = resolve(args[0])
            d = REGMAP[args[1]]
            s = materialize(resolve(args[2]))
            mnem = JCC[op]
            if tgt[0] == "imm":
                emit(f"    {mnem} {d}, {s}, Bpc_{tgt[1]}")
            else:                                  # регистровая цель
                taken, done = newlab("t"), newlab("d")
                emit(f"    {mnem} {d}, {s}, {taken}")
                emit(f"    JMP {done}")
                emit(f"{taken}:")
                emit(f"    JMPR {REGMAP[tgt[1]]}")
                emit(f"{done}:")
        elif op in CMP:
            d = REGMAP[args[0]]
            s = materialize(resolve(args[1]))
            true_l, end_l = newlab("T"), newlab("E")
            emit(f"    {CMP[op]} {d}, {s}, {true_l}")
            emit(f"    MOVI {d}, 0")
            emit(f"    JMP {end_l}")
            emit(f"{true_l}:")
            emit(f"    MOVI {d}, 1")
            emit(f"{end_l}:")
        else:
            raise EirError(f"строка {lineno}: неизвестный op {op!r}")

    # хвостовые пустые блоки + страховочный останов
    for missing in range(cur_pc + 1, max_pc + 1):
        emit(f"Bpc_{missing}:")
    emit("    HLT")
    return "\n".join(L) + "\n"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("eir", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()
    rvs = translate(args.eir.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rvs, encoding="utf-8")
    print(f"{args.output}: {rvs.count(chr(10))} строк")


if __name__ == "__main__":
    main()
