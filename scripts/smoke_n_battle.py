# -*- coding: utf-8 -*-
"""Боевой смоук зоны #N: PCRE2-драйвер на состояниях боевого профиля
(58.7МБ #N — python-regex такие правила не компилирует, поэтому оракул
здесь refemu, а исполнитель rvm.exe). Гоняет LOADI/STOREI/LOAD/STORE по
случайным и краевым адресам, сверяет финальные регистры/слоты."""
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

assert os.environ.get("RVM_TEST_PROFILE", "") != "small", \
    "боевой смоук запускается без RVM_TEST_PROFILE"

from vm.asm import assemble          # noqa: E402
from vm.isa import NRAM_TOP          # noqa: E402
from vm.refemu import RefEmu         # noqa: E402
from vm.statecodec import encode, decode_head  # noqa: E402

RVM = Path(os.environ.get("DOOMREGEX_OUT", r"C:\dev\doom-regex-out")) \
    / "build" / "rvm.exe"
RULES = ROOT / "vm" / "rules_rvm.rgxset"


def run_case(asm: str, ram: dict[int, int], max_passes: int = 400) -> str:
    prog = assemble(asm)
    e = RefEmu(prog)
    e.m.ram.update(ram)
    state = encode(e.m)
    with tempfile.TemporaryDirectory() as td:
        st = Path(td) / "in.rvstate"
        fin = Path(td) / "fin.rvstate"
        hdr = json.dumps({"fmt": "rvstate/1", "pass": 0,
                          "len": len(state), "sha256": None})
        st.write_text(hdr + "\n" + state, encoding="ascii", newline="\n")
        proc = subprocess.run(
            [str(RVM), "--rules", str(RULES), "--state", str(st),
             "--max-passes", str(max_passes), "--save-final", str(fin),
             "--quiet"], capture_output=True)
        assert proc.returncode in (0, 3), proc.stderr.decode("utf8", "replace")
        raw = fin.read_text(encoding="ascii")
        return raw[raw.index("\n") + 1:]


def main() -> None:
    random.seed(7)
    edge = [0, 1, NRAM_TOP - 1, NRAM_TOP, NRAM_TOP + 5]
    addrs = edge + [random.randrange(NRAM_TOP) for _ in range(8)]
    bad = 0
    for a in addrs:
        v = random.getrandbits(32)
        # LOADI из предзаписанной ячейки
        fin = run_case(f"LOADI R1, {hex(a)}\nHLT\n", {a: v})
        m = decode_head(fin)
        ok = m.st == "hlt" and m.regs[1] == v
        # STOREI + LOADI туда же (через регистры для LOAD/STORE-путей)
        fin2 = run_case(
            f"MOVI R2, {hex(a)}\nMOVI R3, 0x{v ^ 0xffffffff:08x}\n"
            f"STORE R2, R3\nLOAD R4, R2\nHLT\n", {})
        m2 = decode_head(fin2)
        ok2 = m2.st == "hlt" and m2.regs[4] == (v ^ 0xffffffff)
        zone = "#N" if a < NRAM_TOP else "#M"
        print(f"addr={a:#010x} [{zone}] loadi={'OK' if ok else 'FAIL'} "
              f"store/load={'OK' if ok2 else 'FAIL'}", flush=True)
        bad += (not ok) + (not ok2)
    print("ИТОГ:", "OK" if bad == 0 else f"{bad} FAIL")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
