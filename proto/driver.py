# -*- coding: utf-8 -*-
"""Proto-драйвер regex-машины (Python, модуль `regex`).

ЧЕСТНОСТЬ (см. HONESTY.md): драйвер — «материнская плата», не CPU.
Ему разрешено ТОЛЬКО:
  * применять правила подстановки по алгоритму Маркова
    (первое совпавшее правило из фиксированного упорядоченного набора);
  * литеральный поиск маркеров |ST:hlt / |ST:err для останова;
  * копировать наружу hex-содержимое зоны |OUT: (механическое
    транскодирование hex->байты, аналог UART);
  * (позже) инжектить байты ввода литеральным splice после |IN:.
Никакого разбора состояния, арифметики над ним или знаний о программе.

Запуск:
  py -3.11 proto/driver.py --rules bf/rules_bf.rgxset --state gen/hello.rvstate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re as _stdlib_re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import regex

_GROUP_REF = _stdlib_re.compile(r"\$\{(\d+)\}")


@dataclass
class Rule:
    name: str
    pattern: "regex.Pattern"
    repl: str          # replacement в синтаксисе Python regex (\g<n>)


def load_rgxset(path: Path) -> list[Rule]:
    """Читает .rgxset, проверяет hash, компилирует правила."""
    text = path.read_bytes().decode("utf-8")
    lines = text.split("\n")
    if not lines or lines[0] != "#rgxset/1":
        raise ValueError(f"{path}: не .rgxset v1")
    if not lines[1].startswith("#sha256:"):
        raise ValueError(f"{path}: нет sha256 в заголовке")
    declared = lines[1][len("#sha256:"):]
    body = "\n".join(lines[2:])
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if actual != declared:
        raise ValueError(f"{path}: hash mismatch (правила подменены?)")

    rules: list[Rule] = []
    i = 2
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        if not line.startswith("#rule "):
            raise ValueError(f"{path}:{i + 1}: ожидалась '#rule'")
        name = line[len("#rule "):]
        pat_line, repl_line = lines[i + 1], lines[i + 2]
        assert pat_line.startswith("P:") and repl_line.startswith("R:")
        pattern = regex.compile(pat_line[2:])
        repl = _GROUP_REF.sub(r"\\g<\1>", repl_line[2:])
        rules.append(Rule(name, pattern, repl))
        i += 3
    return rules


def load_state(path: Path) -> str:
    """Читает .rvstate: строка 1 — JSON-заголовок, дальше байт-точное состояние."""
    raw = path.read_bytes()
    nl = raw.index(b"\n")
    header = json.loads(raw[:nl])
    state = raw[nl + 1:].decode("ascii")
    if header.get("fmt") != "rvstate/1":
        raise ValueError(f"{path}: не rvstate/1")
    digest = hashlib.sha256(state.encode("ascii")).hexdigest()
    if header.get("sha256") not in (None, digest):
        raise ValueError(f"{path}: state hash mismatch")
    return state


def save_state(path: Path, state: str, passes: int) -> None:
    header = {
        "fmt": "rvstate/1",
        "pass": passes,
        "len": len(state),
        "sha256": hashlib.sha256(state.encode("ascii")).hexdigest(),
    }
    path.write_bytes(json.dumps(header).encode("ascii") + b"\n"
                     + state.encode("ascii"))


OUT_TAG = "|OUT:"


def read_out_hex(state: str) -> str:
    """Литеральное чтение зоны OUT (без разбора смысла состояния)."""
    i = state.find(OUT_TAG)
    if i < 0:
        return ""
    j = state.find("|", i + len(OUT_TAG))
    return state[i + len(OUT_TAG):j]


def run(rules: list[Rule], state: str, *, max_passes: int | None = None,
        echo_out: bool = True, trace_every: int = 0,
        stats_file: Path | None = None):
    """Цикл Маркова. Возвращает (final_state, passes, exit_reason)."""
    passes = 0
    out_seen = 0
    t0 = time.perf_counter()
    stats_rows: list[str] = []

    while True:
        if max_passes is not None and passes >= max_passes:
            return state, passes, "max-passes"

        t_pass = time.perf_counter()
        for rule in rules:
            new_state, n = rule.pattern.subn(rule.repl, state)
            if n:
                state = new_state
                break
        else:
            # ни одно правило не совпало: фикс-точка
            reason = "hlt" if "|ST:hlt" in state else (
                "err" if "|ST:err" in state else "fixpoint-run")
            return state, passes, reason
        passes += 1

        if stats_file is not None:
            dt = (time.perf_counter() - t_pass) * 1000
            stats_rows.append(f"{passes},{dt:.3f},{len(state)},{rule.name}")
            if len(stats_rows) >= 1000:
                with stats_file.open("a", encoding="ascii") as f:
                    f.write("\n".join(stats_rows) + "\n")
                stats_rows.clear()

        if echo_out:
            out_hex = read_out_hex(state)
            if len(out_hex) > out_seen:
                delta = out_hex[out_seen:]
                sys.stdout.write(bytes.fromhex(delta).decode("latin-1"))
                sys.stdout.flush()
                out_seen = len(out_hex)

        if trace_every and passes % trace_every == 0:
            rate = passes / (time.perf_counter() - t0)
            print(f"\n[pass {passes} | {rate:.0f} pass/s | "
                  f"len {len(state)} | {rule.name}]", file=sys.stderr)

        if "|ST:hlt" in state:
            return state, passes, "hlt"
        if "|ST:err" in state:
            return state, passes, "err"


def main() -> int:
    ap = argparse.ArgumentParser(description="Regex-машина: цикл Маркова")
    ap.add_argument("--rules", required=True, type=Path)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--max-passes", type=int, default=None)
    ap.add_argument("--trace-every", type=int, default=0)
    ap.add_argument("--stats", type=Path, default=None)
    ap.add_argument("--save-final", type=Path, default=None)
    args = ap.parse_args()

    rules = load_rgxset(args.rules)
    state = load_state(args.state)
    t0 = time.perf_counter()
    state, passes, reason = run(
        rules, state, max_passes=args.max_passes,
        trace_every=args.trace_every, stats_file=args.stats)
    dt = time.perf_counter() - t0

    if args.save_final:
        save_state(args.save_final, state, passes)

    rate = passes / dt if dt > 0 else 0.0
    print(f"\n-- {reason} | {passes} passes | {dt:.2f}s | {rate:.0f} pass/s "
          f"| final len {len(state)}", file=sys.stderr)
    if reason == "hlt":
        return 0
    if reason == "err":
        print(f"state head: {state[:120]}", file=sys.stderr)
        return 3
    if reason == "max-passes":
        return 4
    return 5  # fixpoint-run: нарушение тотальности = баг генератора правил


if __name__ == "__main__":
    sys.exit(main())
