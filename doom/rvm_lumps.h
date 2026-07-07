/* RVM: побайтовые аксессоры дисковых структур WAD + конвертер patch_t.
 * На ELVM sizeof(char)==sizeof(short)==sizeof(int)==1 слову, поэтому
 * упакованные файловые структуры читаются ТОЛЬКО явной сборкой из байтов.
 * (У BFDoom это делал host через bfhost_load_* — мы делаем в машине.) */
#ifndef RVM_LUMPS_H
#define RVM_LUMPS_H

#define RVM_WAD_BASE 0xa00000   /* [.]=размер, [+1..]=байты WAD (см. runtime) */

int RU_u16(const void *p);
int RU_i16(const void *p);
int RU_i32(const void *p);   /* 24 бита достаточно: смещения в лампах < 16М */

/* Прямое чтение len байт WAD по абсолютному offset в dest. Надёжнее пути
 * fseek+fread (структура wad_file на ELVM хранит fstream по смещению,
 * ломающему позиционирование). */
void RU_WadRead(unsigned int offset, void *dest, int len);

/* Конвертирует patch-ламп из дисковой упаковки в словную раскладку ELVM
 * (заголовок + columnofs, указывающие в приложенную сырую копию).
 * Совместим по сигнатуре с W_CacheLumpNum/Name; результат кэшируется. */
void *RVM_CachePatchNum(int lump, int tag);
void *RVM_CachePatchName(const char *name, int tag);

#endif
