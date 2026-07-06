# -*- coding: utf-8 -*-
"""G1b: конвейер C -> 8cc -> EIR -> eir2rvs -> RVM.

Проверки: вывод RVM (lockstep regex-машина vs refemu) == вывод eli
(референс-интерпретатор EIR). Пропускается, если ELVM-тулчейн не собран.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vm.asm import assemble_full           # noqa: E402
from vm.eir2rvs import translate           # noqa: E402
from vm.refemu import RefEmu               # noqa: E402
from vm.statecodec import encode           # noqa: E402
from tests.test_rvm import advance_to_ph0  # noqa: E402

ELVM = Path(os.environ.get("DOOMREGEX_OUT", r"C:\dev\doom-regex-out")) \
    / "build" / "src" / "elvm"
CC8 = ELVM / "out" / "8cc.exe"
ELI = ELVM / "out" / "eli.exe"

pytestmark = pytest.mark.skipif(
    not (CC8.exists() and ELI.exists()),
    reason="ELVM-тулчейн не собран (см. задачу G1b)")


def c_to_eir(tmp_path: Path, c_src: str) -> str:
    c_file = tmp_path / "prog.c"
    eir_file = tmp_path / "prog.eir"
    c_file.write_text(c_src, encoding="utf-8")
    proc = subprocess.run(
        [str(CC8), "-S", "-I" + str(ELVM), "-I" + str(ELVM / "libc"),
         "-o", str(eir_file), str(c_file)],
        capture_output=True, cwd=str(ELVM))
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return eir_file.read_text(encoding="utf-8")


def run_eli(tmp_path: Path, eir_text: str, inp: bytes = b"") -> bytes:
    f = tmp_path / "ref.eir"
    f.write_text(eir_text, encoding="utf-8")
    proc = subprocess.run([str(ELI), str(f)], input=inp, capture_output=True)
    return proc.stdout


def run_rvm_lockstep(eir_text: str, inp: bytes = b"", max_steps=200_000):
    """refemu и regex-машина в lockstep; возвращает (вывод, шаги)."""
    prog, ram = assemble_full(translate(eir_text))
    ref = RefEmu(prog, inp, ram)
    state = encode(ref.m)
    steps = 0
    while True:
        assert state == encode(ref.m), f"расхождение на шаге {steps}"
        ref_alive = ref.step()
        state, rx_alive = advance_to_ph0(state)
        steps += 1
        assert steps <= max_steps, "слишком долго — вероятно, зацикливание"
        if not ref_alive or not rx_alive:
            assert state == encode(ref.m)
            assert ref_alive == rx_alive
            return ref.m.out, steps


C_CASES = [
    ("putchar",
     'int main(){ putchar(72); putchar(105); putchar(10); return 0; }', b""),
    ("loop_sum",
     '''int main(){
          int s = 0;
          for (int i = 1; i <= 10; i++) s += i;   /* 55 = '7' */
          putchar(s);  putchar(10);  return 0; }''', b""),
    ("echo_getchar",
     '''int main(){
          int c;
          while ((c = getchar()) != 0 && c != -1) putchar(c);
          return 0; }''', b"ok!"),
    ("func_calls",
     '''int add3(int a, int b, int c) { return a + b + c; }
        int main(){ putchar(add3(30, 30, 12)); putchar(10); return 0; }''',
     b""),
    ("recursion_fib",
     '''int fib(int n){ return n < 2 ? n : fib(n-1) + fib(n-2); }
        int main(){ putchar(fib(10)); putchar(10); return 0; }''', b""),
]


@pytest.mark.parametrize("name,src,inp", C_CASES, ids=[c[0] for c in C_CASES])
def test_c_pipeline(tmp_path, name, src, inp):
    eir = c_to_eir(tmp_path, src)
    expected = run_eli(tmp_path, eir, inp)
    out, steps = run_rvm_lockstep(eir, inp)
    assert out == expected, (out, expected)
    print(f"\n{name}: {steps} шагов, вывод {out!r}")
