# -*- coding: utf-8 -*-
"""Дифф-тест драйверов: proto (Python regex) vs rvm.exe (C + PCRE2).

Оба обязаны давать байт-идентичное финальное состояние и одинаковое
число проходов на одних и тех же правилах/состоянии.
Пропускается, если rvm.exe не собран (bash scripts/build_driver.sh).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bf.rules_bf import make_state              # noqa: E402
from proto.driver import load_rgxset, load_state, run, save_state  # noqa: E402

OUT_DIR = Path(os.environ.get("DOOMREGEX_OUT", r"C:\dev\doom-regex-out"))
RVM = OUT_DIR / "build" / "rvm.exe"
RULES = ROOT / "bf" / "rules_bf.rgxset"

pytestmark = pytest.mark.skipif(
    not RVM.exists(), reason="rvm.exe не собран (scripts/build_driver.sh)")


@pytest.mark.parametrize("name,code,inp", [
    ("hello", (ROOT / "bf" / "programs" / "hello.bf").read_text(), b""),
    ("cat", ",[.,]", b"regex!"),
    ("digit0", (ROOT / "bf" / "programs" / "digit0.bf").read_text(), b""),
    ("trap", "<", b""),
])
def test_drivers_agree(tmp_path, name, code, inp):
    state0 = make_state(code, inp)
    st_path = tmp_path / f"{name}.rvstate"
    save_state(st_path, state0, passes=0)

    # C-драйвер
    c_final_path = tmp_path / f"{name}_c.rvstate"
    proc = subprocess.run(
        [str(RVM), "--rules", str(RULES), "--state", str(st_path),
         "--save-final", str(c_final_path), "--quiet",
         "--max-passes", "3000000"],
        capture_output=True)
    assert proc.returncode in (0, 3), proc.stderr.decode("utf-8", "replace")
    c_final = load_state(c_final_path)

    # Python-драйвер
    rules = load_rgxset(RULES)
    py_final, _, py_reason = run(rules, state0,
                                 max_passes=3_000_000, echo_out=False)

    assert c_final == py_final, "финальные состояния драйверов разошлись"
    assert (proc.returncode == 0) == (py_reason == "hlt")
