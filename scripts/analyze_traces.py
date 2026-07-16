# -*- coding: utf-8 -*-
"""Сопоставление трасс: refemu tail_trace.bin (pc:u32,op:u8 на шаг) против
rvm_trace1.log (--trace-every 1 с головой состояния). Берём из rvm-лога
только PH:0-головы (момент «инструкция готова к исполнению»): их
последовательность (pc, op из CI) обязана совпасть 1:1 с шагами refemu
(DSPAN/DCOL-пиксели повторяют pc в обеих трассах). Первый дифф — точка
расхождения."""
import re
import struct
import sys

TRACE_BIN = r"C:\dev\doom-regex-out\run\tail_trace.bin"
RVM_LOG = r"C:\dev\doom-regex-out\run\rvm_trace1.log"

raw = open(TRACE_BIN, "rb").read()
ref = [struct.unpack_from("<IB", raw, i) for i in range(0, len(raw), 5)]
print(f"refemu: {len(ref)} шагов хвоста")

pat = re.compile(
    r"\[pass (\d+) \|.*?\| RVM1\|ST:run\|PH:0\|CI:([0-9a-f]{2})"
    r"[0-9a-f-]{10}\|PC:([0-9a-f]{8})", re.S)
rvm = []
with open(RVM_LOG, encoding="utf-8", errors="replace") as f:
    for line in f:
        m = pat.search(line)
        if m:
            rvm.append((int(m.group(1)), int(m.group(3), 16),
                        int(m.group(2), 16)))
print(f"rvm: {len(rvm)} PH:0-голов")

# выравнивание: refemu-запись 0 == состояние снапшота; rvm-голова 0 может
# соответствовать записи 0 (если снапшот PH:0 и это ещё та же инструкция)
# или 1 (первая выборка) — пробуем оба смещения
best = None
for off in (0, 1, 2):
    if off < len(ref) and rvm and ref[off][0] == rvm[0][1]:
        best = off
        break
if best is None:
    print("не выровнялось: ref[0..2] =",
          [(hex(p), hex(o)) for p, o in ref[:3]],
          "rvm[0] =", (hex(rvm[0][1]), hex(rvm[0][2])))
    sys.exit(1)
print(f"смещение выравнивания: {best}")

n = min(len(rvm), len(ref) - best)
for i in range(n):
    rp, rpc, rop = rvm[i]
    epc, eop = ref[i + best]
    if rpc != epc or rop != eop:
        print(f"\nРАСХОЖДЕНИЕ на PH:0-голове #{i} (проход {rp}):")
        print(f"  rvm:    pc={rpc:08x} op={rop:02x}")
        print(f"  refemu: pc={epc:08x} op={eop:02x}")
        print("\nконтекст refemu:")
        for j in range(max(0, i + best - 5), min(len(ref), i + best + 6)):
            mark = " <--" if j == i + best else ""
            print(f"  [{j - best}] pc={ref[j][0]:08x} op={ref[j][1]:02x}{mark}")
        print("\nконтекст rvm:")
        for j in range(max(0, i - 5), min(len(rvm), i + 6)):
            mark = " <--" if j == i else ""
            print(f"  [{j}] pass={rvm[j][0]} pc={rvm[j][1]:08x} "
                  f"op={rvm[j][2]:02x}{mark}")
        sys.exit(0)
print(f"\nвсе {n} сопоставленных голов СОВПАДАЮТ"
      f" (rvm={len(rvm)}, refemu={len(ref)})")
