# -*- coding: utf-8 -*-
"""Референс-интерпретатор Brainfuck — оракул для дифф-тестов.

Семантика (обязана совпадать с правилами rules_bf.py):
  * ячейки 8 бит с wrap-around;
  * лента неограничена вправо, старт в ячейке 0;
  * '<' за левым краем — ошибка (TapeError);
  * ',' при пустом вводе пишет 0 (EOF=0);
  * ']' — безусловный прыжок к парной '[', которая перетестирует ячейку
    (наблюдаемая семантика стандартная).
"""
from bf.rules_bf import BF_CHARS


class TapeError(Exception):
    pass


def run_bf(code: str, input_bytes: bytes = b"", max_steps: int = 10_000_000):
    prog = "".join(ch for ch in code if ch in BF_CHARS)
    # прекомпилируем таблицу парных скобок
    stack, match = [], {}
    for i, ch in enumerate(prog):
        if ch == "[":
            stack.append(i)
        elif ch == "]":
            j = stack.pop()
            match[i], match[j] = j, i
    if stack:
        raise ValueError("несбалансированные скобки")

    tape = [0]
    head = 0
    ip = 0
    inp = list(input_bytes)
    out = bytearray()
    steps = 0
    while ip < len(prog):
        steps += 1
        if steps > max_steps:
            raise RuntimeError("превышен лимит шагов")
        op = prog[ip]
        if op == "+":
            tape[head] = (tape[head] + 1) & 0xFF
        elif op == "-":
            tape[head] = (tape[head] - 1) & 0xFF
        elif op == ">":
            head += 1
            if head == len(tape):
                tape.append(0)
        elif op == "<":
            if head == 0:
                raise TapeError("выход за левый край ленты")
            head -= 1
        elif op == ".":
            out.append(tape[head])
        elif op == ",":
            tape[head] = inp.pop(0) if inp else 0
        elif op == "[":
            if tape[head] == 0:
                ip = match[ip]  # на ']', ip+1 ниже перешагнёт
        elif op == "]":
            ip = match[ip] - 1  # на позицию перед '[': перетест
        ip += 1
    return bytes(out)
