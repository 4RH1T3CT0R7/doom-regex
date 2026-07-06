/* Платформенный слой doomgeneric для RegexVM (замена doomgeneric_bf.c).
 *
 * Кадры: 8-битный палитровый I_VideoBuffer (320x200) копируется словами
 * в FB-окно RVM (0xf00000+i); на eli это обычная память. Палитру (PLAYPAL)
 * применяет визуализатор. Никаких RGB-пакетов host'у.
 *
 * Ввод: неблокирующий GETC-поллинг; событие = 2 байта (0x80|pressed, key),
 * 0 = нет ввода. Часы: детерминированные фейковые тики (+=28, ~35 fps).
 *
 * RVM_FRAME_CHECKSUM: Fletcher-свёртка кадра в OUT ('F', s1+1, s2+1) —
 * golden-чексуммы, сравнимые между eli / refemu / regex-машиной.
 */
#include <stdint.h>
#include <stdio.h>

#include "doomgeneric.h"

extern unsigned char *I_VideoBuffer;   /* 320x200, палитровые индексы */

#define RVM_FB_BASE 0xf00000
#define RVM_FB_PIXELS 64000            /* 320*200 */

void DG_Init() {
}

void DG_DrawFrame() {
  int i;
  unsigned char *src = I_VideoBuffer;
  int *dst = (int *) RVM_FB_BASE;
  for (i = 0; i < RVM_FB_PIXELS; i++) {
    dst[i] = src[i];
  }
#ifdef RVM_FRAME_CHECKSUM
  {
    int s1 = 0, s2 = 0;
    for (i = 0; i < RVM_FB_PIXELS; i++) {
      s1 += src[i];
      while (s1 >= 255) s1 -= 255;
      s2 += s1;
      while (s2 >= 255) s2 -= 255;
    }
    putchar('F');
    putchar(s1 + 1);
    putchar(s2 + 1);
  }
#endif
}

void DG_SleepMs(uint32_t ms) {
  (void) ms;
}

uint32_t DG_GetTicksMs() {
  static uint32_t ticks = 0;
  ticks += 28;
  return ticks;
}

int DG_GetKey(int *pressed, unsigned char *key) {
  int c = getchar();
  if (c != 0x80 && c != 0x81) {
    return 0;                          /* 0/мусор = нет события */
  }
  *pressed = (c == 0x81);
  *key = (unsigned char) getchar();
  return 1;
}

void DG_SetWindowTitle(const char *title) {
  (void) title;
}

int main() {
#ifdef RVM_TIMEDEMO
  char *argv[] = {"doom", "-iwad", "doom1.wad", "-timedemo", "demo1", 0};
  doomgeneric_Create(5, argv);
#else
  char *argv[] = {"doom", "-iwad", "doom1.wad", 0};
  doomgeneric_Create(3, argv);
#endif
  for (;;) {
    doomgeneric_Tick();
  }
  return 0;
}
