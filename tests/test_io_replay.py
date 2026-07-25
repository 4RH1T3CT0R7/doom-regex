# -*- coding: utf-8 -*-
"""FIFO-порядок ввода и воспроизводимость replay."""
import re as stdre
import sys
from pathlib import Path

import regex

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vm.asm import assemble                              # noqa: E402
from vm.genpattern import build_rules                    # noqa: E402
from vm.refemu import RefEmu                             # noqa: E402
from vm.statecodec import encode                         # noqa: E402
from proto.driver import (                               # noqa: E402
    Rule, inject_input, load_journal, read_out_hex, run,
)


def compiled():
    return [Rule(n, regex.compile(p),
                 stdre.sub(r"\$\{(\w+)\}", r"\\g<\1>", r))
            for n, p, r in build_rules()]


RULES = compiled()
# echo 4 байта: getc/putc x4
ECHO = assemble("\n".join(["GETC R0", "PUTC R0"] * 4 + ["HLT"]))


def test_fifo_order_across_polls():
    """Две последовательные инжекции: порядок байтов обязан сохраниться."""
    state = encode(RefEmu(ECHO).m)
    state = inject_input(state, b"AB")
    state = inject_input(state, b"CD")   # хвост, не перед AB
    final, _, reason = run(RULES, state, max_passes=200_000, echo_out=False)
    assert reason == "hlt"
    assert bytes.fromhex(read_out_hex(final)) == b"ABCD"


def test_replay_reproduces_run(tmp_path):
    """(правила, состояние, журнал) => байт-идентичный повтор прогона."""
    journal = tmp_path / "run.journal"
    journal.write_text("0 4142\n9 4344\n", encoding="ascii")
    recs = load_journal(journal)
    state0 = encode(RefEmu(ECHO).m)

    final1, p1, r1 = run(RULES, state0, max_passes=200_000,
                         echo_out=False, replay=recs)
    final2, p2, r2 = run(RULES, state0, max_passes=200_000,
                         echo_out=False, replay=recs)
    assert (final1, p1, r1) == (final2, p2, r2)
    assert r1 == "hlt"
    assert bytes.fromhex(read_out_hex(final1)) == b"ABCD"
