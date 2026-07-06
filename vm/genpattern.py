# -*- coding: utf-8 -*-
r"""Генератор правил RVM-1 -> vm/rules_rvm.rgxset.

Правила программонезависимы: программа живёт в зоне #P строки-состояния.
Схема исполнения — vm/isa.md (v2: CI-кэш).

Конвейер фаз (v2, после перф-редизайна):
  PH:0 exec   — исполнители матчат опкод в CI: по ФИКСИРОВАННОМУ смещению
                заголовка (O(1)-отказ несовпавших правил, скана #P нет);
  PH:1 pcinc  — каскад инкремента PC;
  PH:2 fetch  — ЕДИНСТВЕННЫЙ скан #P: слот I<pc>: -> CI (шаг по слотам
                possessive-юнитами, откат только на границах слотов).
CPI: 3 прохода на линейную инструкцию, 2 на взятый переход (exec->fetch).

Идиомы (этап 0 + ревью спеки):
  * \A-якорь + литеральные гейты ST/PH — fail-fast;
  * динамический выбор регистра: backreference в имени тега R(?P=d):;
  * цепочка сумматора: 8 lookahead-lookup'ов в #A/#S;
  * упорядоченные дополнения вместо отрицаний; catch-all-трапы (тотальность);
  * (?P=name) — синтаксис backreference, совместимый с PCRE2 и Python regex.

Запуск: py -3.11 vm/genpattern.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- заголовки фаз ------------------------------------------------------
H0 = r"\ARVM1\|ST:run\|PH:0\|CI:"          # исполнители: далее (?<ci>...)
H1 = r"\ARVM1\|ST:run\|PH:1\|CI:(?<ci>.{12})\|PC:"
H2 = r"\ARVM1\|ST:run\|PH:2\|CI:.{12}\|PC:(?<pc>.{8})"

R0 = "RVM1|ST:run|PH:0|CI:"
R1 = "RVM1|ST:run|PH:1|CI:${ci}|PC:${pc}"   # exec -> pcinc
R2J = "RVM1|ST:run|PH:2|CI:${ci}|PC:"        # взятый переход -> fetch

# Идиомы сканов: откаты только на границах полей/зон/слотов.
HOP = r"(?:[^#]*+#)+?"
FIELD = r"(?:[^|#]*+\|)*?"
SLOT = r"(?:I[^;]*+;)*?"


def ci(op: int, operands: str) -> str:
    """Голова исполнителя: опкод-литерал в CI-кэше + захват операндов."""
    return H0 + rf"(?<ci>{op:02x}{operands})\|PC:(?<pc>.{{8}})"


def read_reg(reg_ref: str, capture: str) -> str:
    return rf"(?={FIELD}R{reg_ref}:{capture})"


def digits(prefix: str) -> str:
    return "".join(rf"(?<{prefix}{i}>.)" for i in range(7, -1, -1))


def adder_chain(table: str) -> str:
    parts = [rf"(?={HOP}{table}[^#]*?:(?P=d0)(?P=s0)0=(?<o0>.)(?<c1>.))"]
    for i in range(1, 7):
        parts.append(
            rf"(?={HOP}{table}[^#]*?:(?P=d{i})(?P=s{i})(?P=c{i})=(?<o{i}>.)(?<c{i+1}>.))")
    parts.append(rf"(?={HOP}{table}[^#]*?:(?P=d7)(?P=s7)(?P=c7)=(?<o7>.).)")
    return "".join(parts)


def out_digits() -> str:
    return "${o7}${o6}${o5}${o4}${o3}${o2}${o1}${o0}"


def lt_condition(tq: str = "Q", tl: str = "L") -> str:
    branches = []
    for k in range(7, -1, -1):
        eqs = "".join(
            rf"(?={HOP}{tq}[^#]*?:(?P=d{j})(?P=s{j}))" for j in range(7, k, -1))
        branches.append(eqs + rf"(?={HOP}{tl}[^#]*?:(?P=d{k})(?P=s{k}))")
    return "(?:" + "|".join(branches) + ")"


def consume_dst() -> str:
    return rf"(?<pre>{FIELD}R(?P=d):).{{8}}"


def build_rules() -> list[tuple[str, str, str]]:
    R: list[tuple[str, str, str]] = []

    # --- PH:2 fetch: единственный скан #P (шаг по слотам) ------------------
    R.append(("fetch",
              H2 + rf"(?={HOP}P{SLOT}I(?P=pc):(?<nci>.{{12}});)",
              "RVM1|ST:run|PH:0|CI:${nci}|PC:${pc}"))
    R.append(("trap_noslot", H2,
              "RVM1|ST:err:NOSLOT|PH:0|CI:------------|PC:${pc}"))

    # --- PH:1 pcinc: каскад (частый путь сразу после fetch) ----------------
    R.append(("pcinc_d0",
              H1 + r"(?<p>.{7})(?<x>[0-9a-e])"
              + rf"(?={HOP}D[0-9a-f]*?(?P=x)(?<n>.))",
              "RVM1|ST:run|PH:2|CI:${ci}|PC:${p}${n}"))
    for i in range(1, 8):
        R.append((f"pcinc_d{i}",
                  H1 + rf"(?<p>.{{{7 - i}}})(?<x>[0-9a-e])f{{{i}}}"
                  + rf"(?={HOP}D[0-9a-f]*?(?P=x)(?<n>.))",
                  "RVM1|ST:run|PH:2|CI:${ci}|PC:${p}${n}" + "0" * i))
    R.append(("pcinc_wrap", H1 + r"f{8}",
              "RVM1|ST:run|PH:2|CI:${ci}|PC:" + "0" * 8))

    # --- PH:0 исполнители: O(1)-отказ по CI-литералу -----------------------
    R.append(("movi",
              ci(0x02, r"(?<d>[0-7]).(?<imm>.{8})") + consume_dst(),
              R1 + "${pre}${imm}"))
    R.append(("mov",
              ci(0x01, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=s)", r"(?<v>.{8})") + consume_dst(),
              R1 + "${pre}${v}"))

    for name, op, table, imm_src in [
        ("add", 0x10, "A", False), ("addi", 0x11, "A", True),
        ("sub", 0x12, "S", False), ("subi", 0x13, "S", True),
    ]:
        if imm_src:
            head = ci(op, r"(?<d>[0-7])." + digits("s"))
            src_read = ""
        else:
            head = ci(op, r"(?<d>[0-7])(?<s>[0-7]).{8}")
            src_read = read_reg(r"(?P=s)", digits("s"))
        R.append((name,
                  head + src_read + read_reg(r"(?P=d)", digits("d"))
                  + adder_chain(table) + consume_dst(),
                  R1 + "${pre}" + out_digits()))

    # WR24: 24-битный wrap за один проход
    R.append(("wr24",
              ci(0x50, r"(?<d>[0-7]).{9}")
              + rf"(?<pre>{FIELD}R(?P=d):).{{2}}",
              R1 + "${pre}00"))

    # --- фреймбуфер (раньше #M-правил; окно 00f0oooo) ----------------------
    R.append(("storei_fb",
              ci(0x23, r"(?<d>[0-7]).00f0(?<o>.{4})")
              + read_reg(r"(?P=d)", r".{6}(?<b1>.)(?<b0>.)")
              + rf"(?<pre>{HOP}F[^#]*?\[(?P=o):).{{2}}",
              R1 + "${pre}${b1}${b0}"))
    R.append(("store_fb",
              ci(0x22, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=d)", r"00f0(?<o>.{4})")
              + read_reg(r"(?P=s)", r".{6}(?<b1>.)(?<b0>.)")
              + rf"(?<pre>{HOP}F[^#]*?\[(?P=o):).{{2}}",
              R1 + "${pre}${b1}${b0}"))
    R.append(("loadi_fb",
              ci(0x21, r"(?<d>[0-7]).00f0(?<o>.{4})")
              + rf"(?={HOP}F[^#]*?\[(?P=o):(?<fv>.{{2}})\])"
              + consume_dst(),
              R1 + "${pre}000000${fv}"))
    R.append(("load_fb",
              ci(0x20, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=s)", r"00f0(?<o>.{4})")
              + rf"(?={HOP}F[^#]*?\[(?P=o):(?<fv>.{{2}})\])"
              + consume_dst(),
              R1 + "${pre}000000${fv}"))

    # --- память #M ----------------------------------------------------------
    R.append(("loadi_hit",
              ci(0x21, r"(?<d>[0-7]).(?<imm>.{8})")
              + rf"(?={HOP}M[^#]*?\[(?P=imm):(?<mv>.{{8}})\])"
              + consume_dst(),
              R1 + "${pre}${mv}"))
    R.append(("loadi_miss",
              ci(0x21, r"(?<d>[0-7]).(?<imm>.{8})") + consume_dst(),
              R1 + "${pre}00000000"))
    R.append(("load_hit",
              ci(0x20, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=s)", r"(?<addr>.{8})")
              + rf"(?={HOP}M[^#]*?\[(?P=addr):(?<mv>.{{8}})\])"
              + consume_dst(),
              R1 + "${pre}${mv}"))
    R.append(("load_miss",
              ci(0x20, r"(?<d>[0-7])(?<s>[0-7]).{8}") + consume_dst(),
              R1 + "${pre}00000000"))
    R.append(("storei_hit",
              ci(0x23, r"(?<d>[0-7]).(?<imm>.{8})")
              + read_reg(r"(?P=d)", r"(?<v>.{8})")
              + rf"(?<pre>{HOP}M[^#]*?\[(?P=imm):).{{8}}",
              R1 + "${pre}${v}"))
    R.append(("storei_ins",       # O(1) prepend сразу после #M (без сортировки)
              ci(0x23, r"(?<d>[0-7]).(?<imm>.{8})")
              + read_reg(r"(?P=d)", r"(?<v>.{8})")
              + rf"(?<pre>{HOP}M)",
              R1 + "${pre}[${imm}:${v}]"))
    R.append(("store_hit",
              ci(0x22, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=d)", r"(?<addr>.{8})")
              + read_reg(r"(?P=s)", r"(?<v>.{8})")
              + rf"(?<pre>{HOP}M[^#]*?\[(?P=addr):).{{8}}",
              R1 + "${pre}${v}"))
    R.append(("store_ins",        # O(1) prepend сразу после #M
              ci(0x22, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=d)", r"(?<addr>.{8})")
              + read_reg(r"(?P=s)", r"(?<v>.{8})")
              + rf"(?<pre>{HOP}M)",
              R1 + "${pre}[${addr}:${v}]"))

    # --- переходы: взятые -> PH:2 (refetch), невзятые -> PH:1 ---------------
    R.append(("jmp", ci(0x30, r"..(?<imm>.{8})"), R2J + "${imm}"))
    R.append(("jmpr",
              ci(0x38, r"(?<d>[0-7]).{9}")
              + read_reg(r"(?P=d)", r"(?<v>.{8})"),
              R2J + "${v}"))
    R.append(("jeq_taken",
              ci(0x31, r"(?<d>[0-7])(?<s>[0-7])(?<imm>.{8})")
              + read_reg(r"(?P=d)", r"(?<v>.{8})")
              + read_reg(r"(?P=s)", r"(?P=v)"),
              R2J + "${imm}"))
    R.append(("jeq_skip", ci(0x31, r"[0-7][0-7].{8}"), R1))
    R.append(("jne_skip_eq",
              ci(0x32, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=d)", r"(?<v>.{8})")
              + read_reg(r"(?P=s)", r"(?P=v)"),
              R1))
    R.append(("jne_taken", ci(0x32, r"[0-7][0-7](?<imm>.{8})"),
              R2J + "${imm}"))
    R.append(("jlt_taken",
              ci(0x33, r"(?<d>[0-7])(?<s>[0-7])(?<imm>.{8})")
              + read_reg(r"(?P=d)", digits("d"))
              + read_reg(r"(?P=s)", digits("s"))
              + lt_condition(),
              R2J + "${imm}"))
    R.append(("jlt_skip", ci(0x33, r"[0-7][0-7].{8}"), R1))
    R.append(("jge_skip_lt",
              ci(0x34, r"(?<d>[0-7])(?<s>[0-7]).{8}")
              + read_reg(r"(?P=d)", digits("d"))
              + read_reg(r"(?P=s)", digits("s"))
              + lt_condition(),
              R1))
    R.append(("jge_taken", ci(0x34, r"[0-7][0-7](?<imm>.{8})"),
              R2J + "${imm}"))

    # --- I/O ----------------------------------------------------------------
    R.append(("putc",
              ci(0x40, r"(?<d>[0-7]).{9}")
              + read_reg(r"(?P=d)", r".{6}(?<b1>.)(?<b0>.)")
              + rf"(?<pre>{FIELD}OUT:[0-9a-f]*+)\|",
              R1 + "${pre}${b1}${b0}|"))
    R.append(("getc_in",
              ci(0x41, r"(?<d>[0-7]).{9}")
              + rf"(?<pre>{FIELD}R(?P=d):).{{8}}"
              + rf"(?<mid>{FIELD}IN:)(?<i1>[0-9a-f])(?<i0>[0-9a-f])",
              R1 + "${pre}000000${i1}${i0}${mid}"))
    R.append(("getc_eof",
              ci(0x41, r"(?<d>[0-7]).{9}") + consume_dst(),
              R1 + "${pre}00000000"))

    # --- останов и трапы (тотальность) --------------------------------------
    R.append(("hlt",
              ci(0xFF, r".{10}"),
              "RVM1|ST:hlt|PH:0|CI:${ci}|PC:${pc}"))
    R.append(("trap_noslot0",       # PH:0 с CI-прочерками (пустая программа)
              H0 + r"-{12}\|PC:(?<pc>.{8})",
              "RVM1|ST:err:NOSLOT|PH:0|CI:------------|PC:${pc}"))
    R.append(("trap_badop",
              H0 + r"(?<ci>.{12})\|PC:(?<pc>.{8})",
              "RVM1|ST:err:BADOP|PH:0|CI:${ci}|PC:${pc}"))
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
