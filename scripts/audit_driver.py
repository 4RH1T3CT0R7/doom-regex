# -*- coding: utf-8 -*-
"""Honesty-аудит драйверов (HONESTY.md п.4).

Драйверу разрешено: цикл подстановок, копирование IO-байтов (IN-сплайс,
OUT-эхо, CLK), экспорт FB/состояния. Запрещено: арифметика над разобранным
состоянием, ветвление по игровым данным, интерпретация смысла зон.

Скрипт — консервативный детектор: ищет в driver/rvm_driver.c и
proto/driver.py запрещённые механизмы (callouts, embedded-code, парсинг
регистров/памяти) и «смысловые» литералы зон, которых драйвер знать
не должен. Белый список — IO-контракт.

Запуск: py -3.11 scripts/audit_driver.py  (exit 0 = чисто)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Запрещённые механизмы исполнения кода/семантики в PCRE2/regex
FORBIDDEN = [
    (r"\(\?\{", "embedded code (?{...} в паттерне"),
    (r"pcre2_callout", "PCRE2 callouts"),
    (r"PCRE2_SUBSTITUTE_EXTENDED", "extended-substitute (условная логика "
                                   "в replacement)"),
]

# Смысловые литералы состояния, которых драйвер знать не должен.
# Белый список IO-контракта: |IN: (сплайс ввода), |OUT: (эхо вывода),
# |CLK: (часы), #F (экспорт кадра), RVM1 (магия формата), ST:hlt/err
# (детект останова для выхода из цикла).
FORBIDDEN_LITERALS = [
    (r'"\|R[0-9]', "чтение регистров"),
    (r'"#M', "лазанье в память #M"),
    (r'"#P', "лазанье в программу #P"),
    (r'"\|PC:', "чтение PC"),
    (r'"\|CI:', "чтение CI"),
    (r'"#[DQLASBOXHT]"', "ROM-зоны"),
]


def audit(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    # комментарии не считаем (доки внутри драйвера легальны)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"(?m)^\s*#.*$", "", text) if path.suffix == ".py" else text
    hits = []
    for pat, why in FORBIDDEN + FORBIDDEN_LITERALS:
        for mo in re.finditer(pat, text):
            line = text[:mo.start()].count("\n") + 1
            hits.append(f"{path.name}:{line}: {why} [{mo.group(0)!r}]")
    return hits


def main() -> None:
    targets = [ROOT / "driver" / "rvm_driver.c", ROOT / "proto" / "driver.py"]
    all_hits: list[str] = []
    for t in targets:
        if not t.exists():
            print(f"нет файла: {t}", file=sys.stderr)
            sys.exit(2)
        all_hits += audit(t)
    if all_hits:
        print("HONESTY-АУДИТ ПРОВАЛЕН:")
        for h in all_hits:
            print(" ", h)
        sys.exit(1)
    print("honesty-аудит драйверов: чисто "
          f"({', '.join(t.name for t in targets)})")


if __name__ == "__main__":
    main()
