# -*- coding: utf-8 -*-
r"""Генератор правил RVM-1 -> vm/rules_rvm.rgxset.

Правила программонезависимы: программа живёт в зоне #P строки-состояния,
как BF-код жил в C:. Схема исполнения — vm/isa.md.

Идиомы (проверены этапом 0):
  * \A-якорь + литеральный гейт ST:run|PH:x — fail-fast;
  * lookahead-выборка I(?P=pc):<op> в #P (лениво);
  * динамический выбор регистра: backreference в имени тега R(?P=d):;
  * цепочка сумматора: 8 lookahead-lookup'ов в #A/#S, перенос через
    захваченные группы;
  * упорядоченные дополнения вместо отрицаний (JNE = «равно→skip» раньше
    «взят»); catch-all-трапы в конце (тотальность).

Запуск: py -3.11 vm/genpattern.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HEAD_RUN0 = r"\ARVM1\|ST:run\|PH:0\|PC:(?<pc>.{8})"
HEAD_RUN1 = r"\ARVM1\|ST:run\|PH:1\|PC:"
REP0 = "RVM1|ST:run|PH:0|PC:"
REP1 = "RVM1|ST:run|PH:1|PC:"

# Идиомы сканов (ревью спеки): откаты только на границах полей/зон.
# HOP: скачок к зоне #X — ленивое повторение possessive-юнитов «не-# + #».
# FIELD: шаг по полям заголовка «не-|# + |» — не пересекает #-зоны.
HOP = r"(?:[^#]*+#)+?"
FIELD = r"(?:[^|#]*+\|)*?"


def fetch(op: int, operands: str) -> str:
    """Lookahead-выборка инструкции по PC: literal-опкод + захват операндов."""
    return rf"(?={HOP}P[^#]*?I(?P=pc):{op:02x}{operands};)"


def read_reg(reg_ref: str, capture: str) -> str:
    return rf"(?={FIELD}R{reg_ref}:{capture})"


def digits(prefix: str) -> str:
    """8 именованных однобуквенных захватов: p7..p0 (MSB..LSB)."""
    return "".join(rf"(?<{prefix}{i}>.)" for i in range(7, -1, -1))


def adder_chain(table: str) -> str:
    """Цепочка полного сумматора/вычитателя: (d,s,c)->(o,c')."""
    parts = [rf"(?={HOP}{table}[^#]*?:(?P=d0)(?P=s0)0=(?<o0>.)(?<c1>.))"]
    for i in range(1, 7):
        parts.append(
            rf"(?={HOP}{table}[^#]*?:(?P=d{i})(?P=s{i})(?P=c{i})=(?<o{i}>.)(?<c{i+1}>.))")
    parts.append(rf"(?={HOP}{table}[^#]*?:(?P=d7)(?P=s7)(?P=c7)=(?<o7>.).)")
    return "".join(parts)


def out_digits() -> str:
    return "${o7}${o6}${o5}${o4}${o3}${o2}${o1}${o0}"


def lt_condition() -> str:
    """R[d] < R[s] (unsigned): альтернация «префикс равен, первая меньше»."""
    branches = []
    for k in range(7, -1, -1):
        eqs = "".join(
            rf"(?={HOP}Q[^#]*?:(?P=d{j})(?P=s{j}))" for j in range(7, k, -1))
        branches.append(eqs + rf"(?={HOP}L[^#]*?:(?P=d{k})(?P=s{k}))")
    return "(?:" + "|".join(branches) + ")"


def consume_dst() -> str:
    return rf"(?<pre>{FIELD}R(?P=d):).{{8}}"


def build_rules() -> list[tuple[str, str, str]]:
    R: list[tuple[str, str, str]] = []

    # --- PH:1 — каскад инкремента PC (первым: 50% всех проходов) ---------
    R.append(("pcinc_d0",
              HEAD_RUN1 + r"(?<p>.{7})(?<x>[0-9a-e])"
              + rf"(?={HOP}D[0-9a-f]*?(?P=x)(?<n>.))",
              REP0 + "${p}${n}"))
    for i in range(1, 8):
        R.append((f"pcinc_d{i}",
                  HEAD_RUN1 + rf"(?<p>.{{{7 - i}}})(?<x>[0-9a-e])f{{{i}}}"
                  + rf"(?={HOP}D[0-9a-f]*?(?P=x)(?<n>.))",
                  REP0 + "${p}${n}" + "0" * i))
    R.append(("pcinc_wrap", HEAD_RUN1 + r"f{8}", REP0 + "0" * 8))

    # --- пересылки ---------------------------------------------------------
    R.append(("movi",
              HEAD_RUN0 + fetch(0x02, r"(?<d>[0-7]).(?<imm>.{8})")
              + consume_dst(),
              REP1 + "${pc}${pre}${imm}"))
    R.append(("mov",
              HEAD_RUN0 + fetch(0x01, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=s)", r"(?<v>.{8})") + consume_dst(),
              REP1 + "${pc}${pre}${v}"))

    # --- АЛУ: ADD/ADDI/SUB/SUBI (один проход, цепочка #A/#S) ---------------
    for name, op, table, imm_src in [
        ("add", 0x10, "A", False), ("addi", 0x11, "A", True),
        ("sub", 0x12, "S", False), ("subi", 0x13, "S", True),
    ]:
        if imm_src:
            ftch = fetch(op, r"(?<d>[0-7])." + digits("s"))
            src_read = ""
        else:
            ftch = fetch(op, r"(?<d>[0-7])(?<s>[0-7]).{8}")
            src_read = read_reg(r"(?P=s)", digits("s"))
        R.append((name,
                  HEAD_RUN0 + ftch + src_read
                  + read_reg(r"(?P=d)", digits("d"))
                  + adder_chain(table) + consume_dst(),
                  REP1 + "${pc}${pre}" + out_digits()))

    # --- переходы -----------------------------------------------------------
    R.append(("jmp", HEAD_RUN0 + fetch(0x30, r"..(?<imm>.{8})"),
              REP0 + "${imm}"))
    R.append(("jmpr",
              HEAD_RUN0 + fetch(0x38, r"(?<d>[0-7]).{9}")
              + read_reg(r"(?P=d)", r"(?<v>.{8})"),
              REP0 + "${v}"))
    # JEQ: взят (равенство через backreference), иначе skip
    R.append(("jeq_taken",
              HEAD_RUN0 + fetch(0x31, r"(?<d>[0-7])(?<s>[0-7])(?<imm>.{8})")
              + read_reg(r"(?P=d)", r"(?<v>.{8})")
              + read_reg(r"(?P=s)", r"(?P=v)"),
              REP0 + "${imm}"))
    R.append(("jeq_skip", HEAD_RUN0 + fetch(0x31, r"[0-7][0-7].{8}"),
              REP1 + "${pc}"))
    # JNE: сначала «равно -> skip», затем безусловный «взят»
    R.append(("jne_skip_eq",
              HEAD_RUN0 + fetch(0x32, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=d)", r"(?<v>.{8})")
              + read_reg(r"(?P=s)", r"(?P=v)"),
              REP1 + "${pc}"))
    R.append(("jne_taken", HEAD_RUN0 + fetch(0x32, r"[0-7][0-7](?<imm>.{8})"),
              REP0 + "${imm}"))
    # JLT: взят при <, иначе skip; JGE — упорядоченное дополнение
    R.append(("jlt_taken",
              HEAD_RUN0 + fetch(0x33, r"(?<d>[0-7])(?<s>[0-7])(?<imm>.{8})")
              + read_reg(r"(?P=d)", digits("d"))
              + read_reg(r"(?P=s)", digits("s"))
              + lt_condition(),
              REP0 + "${imm}"))
    R.append(("jlt_skip", HEAD_RUN0 + fetch(0x33, r"[0-7][0-7].{8}"),
              REP1 + "${pc}"))
    R.append(("jge_skip_lt",
              HEAD_RUN0 + fetch(0x34, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=d)", digits("d"))
              + read_reg(r"(?P=s)", digits("s"))
              + lt_condition(),
              REP1 + "${pc}"))
    R.append(("jge_taken", HEAD_RUN0 + fetch(0x34, r"[0-7][0-7](?<imm>.{8})"),
              REP0 + "${imm}"))

    # --- I/O ----------------------------------------------------------------
    R.append(("putc",
              HEAD_RUN0 + fetch(0x40, r"(?<d>[0-7]).{9}")
              + read_reg(r"(?P=d)", r".{6}(?<b1>.)(?<b0>.)")
              + rf"(?<pre>{FIELD}OUT:[0-9a-f]*+)\|",
              REP1 + "${pc}${pre}${b1}${b0}|"))
    R.append(("getc_in",
              HEAD_RUN0 + fetch(0x41, r"(?<d>[0-7]).{9}")
              + rf"(?<pre>{FIELD}R(?P=d):).{{8}}"
              + rf"(?<mid>{FIELD}IN:)(?<i1>[0-9a-f])(?<i0>[0-9a-f])",
              REP1 + "${pc}${pre}000000${i1}${i0}${mid}"))
    R.append(("getc_eof",
              HEAD_RUN0 + fetch(0x41, r"(?<d>[0-7]).{9}") + consume_dst(),
              REP1 + "${pc}${pre}00000000"))

    # --- останов ------------------------------------------------------------
    R.append(("hlt",
              HEAD_RUN0 + fetch(0xFF, r".{10}"),
              "RVM1|ST:hlt|PH:0|PC:${pc}"))

    # --- трапы (тотальность; порядок: badop до noslot, wedge последним) ----
    R.append(("trap_badop",
              HEAD_RUN0 + r"(?=.*?#P[^#]*?I(?P=pc):)",
              "RVM1|ST:err:BADOP|PH:0|PC:${pc}"))
    R.append(("trap_noslot",
              HEAD_RUN0,
              "RVM1|ST:err:NOSLOT|PH:0|PC:${pc}"))
    R.append(("wedge", r"\ARVM1\|ST:run\|", "RVM1|ST:err:WEDGE|"))
    return R


def render_body(rules) -> str:
    lines = []
    for name, pat, repl in rules:
        assert "\n" not in pat and "\n" not in repl, name
        lines.append(f"#rule {name}")
        lines.append(f"P:{pat}")
        lines.append(f"R:{repl}")
    return "\n".join(lines) + "\n"


def main() -> None:
    rules = build_rules()
    body = render_body(rules)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    out = Path(__file__).with_name("rules_rvm.rgxset")
    out.write_bytes((f"#rgxset/1\n#sha256:{digest}\n" + body).encode("utf-8"))
    print(f"{out.name}: {len(rules)} rules, sha256={digest[:16]}…")


if __name__ == "__main__":
    main()
