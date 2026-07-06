# -*- coding: utf-8 -*-
"""Линкер EIR: конкатенация с манглингом локальных меток (.L*/.S*)
по образцу BFDoom tools/link-bfdoom-eir.mjs (без зависимости от node).

Запуск: py -3.11 doom/link_eir.py -o out.eir file1.eir file2.eir ...
"""
import argparse
import re
from pathlib import Path

LOCAL = re.compile(r"\.(L|S)([A-Za-z0-9_.]*)")


def mangle(text: str, name: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return LOCAL.sub(lambda m: f".{prefix}_{m.group(1)}{m.group(2)}", text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    parts = []
    for f in args.inputs:
        parts.append(f"\n# linked {f.stem}\n")
        parts.append(mangle(f.read_text(encoding="utf-8"), f.stem))
        parts.append("\n")
    args.output.write_text("".join(parts), encoding="utf-8")
    print(f"{args.output}: {len(args.inputs)} файлов, "
          f"{sum(len(p.splitlines()) for p in parts)} строк")


if __name__ == "__main__":
    main()
