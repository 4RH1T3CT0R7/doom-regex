# Doom → RegexVM: заметки порта (Этап 2)

## Статус G2a (2026-07-07)

✅ **Весь doomgeneric (81 файл) компилируется 8cc в EIR** и линкуется в
`bfdoom-linked.eir` (~1.44М строк, ~919k IR-инструкций) тулчейном BFDoom.

Понадобилось всего 3 патча upstream ozkl/doomgeneric (`doom/patches/`):
- `i_video.h` — битовые поля `struct color` → обычные `uint8_t` (8cc не умеет
  битовые поля);
- `p_switch.c` — `buttonlist->soundorg` → `buttonlist[0].soundorg` (стрелка
  на массиве);
- `m_menu.c` — `--skullAnimCounter <= 0` → раздельный декремент (lvalue).

Сборка (out-дерево, ASCII-путь):
```
C:\dev\doom-regex-out\build\src\bfdoom\           # клон jasperdevs/BFDoom
  vendor/elvm/       # их vendored ELVM (собран msys-gcc; sed -i 's/ -Werror//' Makefile)
  vendor/doomgeneric/doomgeneric/   # upstream ozkl + наши 3 патча
  build/probe/*.eir  # 81 пофайловый EIR (tools/probe-doomgeneric.sh)
  build/bfdoom-linked.eir           # слинкованный (node tools/link-bfdoom-eir.mjs)
```

## Ключевое отличие нашей архитектуры от BFDoom

BFDoom обслуживает WAD **патченным host'ом** (bfio-протокол: BF просит байты,
JS-host отвечает) и рендерит кадры RGB-пакетами через putchar. Их host также
взял на себя «fast-path» части рантайма — граница честности слабее нашей.

Наш план (честнее):
1. **WAD-в-RAM**: doom1.wad упаковывается по 4 байта в 32-битные слова ячеек
   #M начальным состоянием (данные — часть захешированного стартового
   состояния, как ROM-картридж). `ports/elvm-libc/runtime.c` форкается:
   fopen/fread/fseek читают из памяти (чистый C, без host-протокола).
2. **Кадры**: свой `doomgeneric_rvm.c` — копирует 8-битный палитровый
   I_VideoBuffer (320x200) в FB-окно (нужно расширить FB_CELLS до 64000);
   палитра PLAYPAL — в визуализаторе.
3. **Ввод**: DG_GetKey через GETC (коды doomgeneric); часы — фейковые
   детерминированные тики (+=28), как у BFDoom.

## Известные препятствия до G2d (первый кадр)

- **Байтовая распаковка WAD**: нужен нативный опкод BEXT (извлечение байта
  из слова — 1 проход, 4 варианта правила) — иначе shifts-builtins бит-сериальны.
  Заодно v1.2: AND/OR/XOR (ниббл-таблицы), SHL/SHR — для FixedMul/рендера.
- **Размер состояния**: #P для 919k инструкций ≈ 21МБ (23 байта/слот) +
  трамплин ~300k слотов + WAD ~20МБ ячейками @4байта/слово ≈ 45-50МБ строка.
  Нужна компактизация: короче формат слота (адрес из позиции?), WAD-зона
  с плотной упаковкой (свой формат #W без [addr:]-обвязки, индексация как FB).
- **Cold-start**: миллионы инструкций инициализации → часы; решение —
  прогнать один раз, снапшот .rvstate, resume (--save-final уже есть).
- **eli-смоук**: их runtime.c ждёт bfio-host; наш форк с WAD-в-памяти
  сможет бежать на stock eli (WAD зальётся стартовым GETC-лоадером или
  предзаполнением .data при линковке).

## Следующие шаги

1. Форк runtime.c: memory-WAD (без bfio) + doomgeneric_rvm.c (FB/GETC).
2. eli: `-timedemo demo1` до конца, golden-чексуммы кадров (гейт G2a).
3. RVM v1.2: BEXT/AND/OR/XOR/SHL/SHR (+ тесты lockstep).
4. Компактный формат #P/#W → бюджет состояния ≤ 25МБ (гейт G2c).
5. eir2rvs на 919k инструкций (перф самого транслятора + ассемблера).
