# -*- coding: utf-8 -*-
"""Разовая правка genpattern: атомарное позиционирование зон в
параметрических хелперах + скан #M целыми ячейками."""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "vm" / "genpattern.py"
t = p.read_text(encoding="utf-8")

# 1) параметрические таблицы: {HOP}{tq}/{tl}/{table} -> (?>{HOP}{...})
n1 = 0
for var in ("tq", "tl", "table"):
    old = "{HOP}{" + var + "}"
    new = "(?>{HOP}{" + var + "})"
    cnt = t.count(old) - t.count(new)   # уже обёрнутые не трогаем
    # заменяем только необёрнутые вхождения
    out = []
    i = 0
    while True:
        j = t.find(old, i)
        if j < 0:
            out.append(t[i:])
            break
        if t[max(0, j - 3):j] == "(?>":
            out.append(t[i:j + len(old)])
        else:
            out.append(t[i:j])
            out.append(new)
            n1 += 1
        i = j + len(old)
    t = "".join(out)

# 2) скан #M по целым ячейкам: M[^#]*?\[  ->  M(?:\[[^\]]*+\])*?\[
# (в rf-строках genpattern это байты 'M[^#]*?\[')
needle = r"M[^#]*?\["
n2 = t.count(needle)
t = t.replace(needle, r"M(?:\[[^\]]*+\])*?\[")

p.write_text(t, encoding="utf-8")
print("таблиц обёрнуто:", n1, "| #M-сканов:", n2)
