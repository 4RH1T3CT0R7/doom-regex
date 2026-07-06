# -*- coding: utf-8 -*-
"""Дифф-тесты: regex-машина (правила rules_bf) против оракула refbf.

Запуск: py -3.11 -m pytest tests/ -v
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bf.refbf import run_bf, TapeError          # noqa: E402
from bf.rules_bf import RULES, make_state        # noqa: E402
from bf.gen_rules import render_body             # noqa: E402
from proto.driver import (                       # noqa: E402
    Rule, load_rgxset, read_out_hex, run,
)
import regex                                     # noqa: E402
import hashlib                                   # noqa: E402


def compile_rules():
    import re as stdre
    out = []
    for name, pat, repl in RULES:
        out.append(Rule(name, regex.compile(pat),
                        stdre.sub(r"\$\{(\d+)\}", r"\\g<\1>", repl)))
    return out


COMPILED = compile_rules()


def run_machine(code: str, inp: bytes = b"", max_passes: int = 3_000_000):
    state = make_state(code, inp)
    final, passes, reason = run(COMPILED, state,
                                max_passes=max_passes, echo_out=False)
    return final, passes, reason


def machine_output(final_state: str) -> bytes:
    return bytes.fromhex(read_out_hex(final_state))


# --- базовые инструкции -------------------------------------------------

@pytest.mark.parametrize("code,expected", [
    ("", b""),
    ("+.", b"\x01"),
    ("++.", b"\x02"),
    ("+-.", b"\x00"),
    ("-.", b"\xff"),                 # wrap вниз
    ("+" * 255 + ".", b"\xff"),
    ("+" * 256 + ".", b"\x00"),      # wrap вверх
    ("+" * 16 + ".", b"\x10"),       # перенос f->10
    (">+.", b"\x01"),                # рост ленты
    ("+>++><<.>.", b"\x01\x02"),     # движение в обе стороны
    ("+++[-].", b"\x00"),            # цикл до нуля
    ("[.]", b""),                    # пропуск цикла при нуле
    ("++[>+<-]>.", b"\x02"),         # перенос значения
    ("+[[-]].", b"\x00"),            # вложенные скобки: пропуск/вход
])
def test_basics_vs_oracle(code, expected):
    assert run_bf(code) == expected, "оракул не согласен с ожиданием теста"
    final, _, reason = run_machine(code)
    assert reason == "hlt", f"машина не остановилась чисто: {reason}\n{final[:200]}"
    assert machine_output(final) == expected


def test_input_cat():
    inp = b"Hi!"
    final, _, reason = run_machine(",[.,]", inp)
    assert reason == "hlt"
    assert machine_output(final) == run_bf(",[.,]", inp) == inp


def test_input_eof_reads_zero():
    final, _, reason = run_machine(",.", b"")
    assert reason == "hlt"
    assert machine_output(final) == b"\x00"


def test_digit0():
    code = (ROOT / "bf" / "programs" / "digit0.bf").read_text()
    final, _, reason = run_machine(code)
    assert reason == "hlt"
    assert machine_output(final) == run_bf(code) == b"0"


def test_hello_world():
    code = (ROOT / "bf" / "programs" / "hello.bf").read_text()
    expected = run_bf(code)
    assert expected == b"Hello World!\n"
    final, passes, reason = run_machine(code)
    assert reason == "hlt", f"{reason}: {final[:200]}"
    assert machine_output(final) == expected
    print(f"\nhello.bf: {passes} passes")


def test_left_edge_traps():
    with pytest.raises(TapeError):
        run_bf("<")
    final, _, reason = run_machine("<")
    assert reason == "err"
    assert "|ST:err:TAPE" in final


def test_rgxset_roundtrip(tmp_path):
    """gen_rules -> load_rgxset: хэш сходится, правила компилируются."""
    body = render_body(RULES)
    digest = hashlib.sha256(body.encode()).hexdigest()
    p = tmp_path / "r.rgxset"
    p.write_bytes((f"#rgxset/1\n#sha256:{digest}\n" + body).encode())
    rules = load_rgxset(p)
    assert [r.name for r in rules] == [name for name, _, _ in RULES]


def test_rgxset_tamper_detected(tmp_path):
    body = render_body(RULES).replace("plus_ff", "plus_FF")
    digest = hashlib.sha256(render_body(RULES).encode()).hexdigest()
    p = tmp_path / "bad.rgxset"
    p.write_bytes((f"#rgxset/1\n#sha256:{digest}\n" + body).encode())
    with pytest.raises(ValueError, match="hash mismatch"):
        load_rgxset(p)
