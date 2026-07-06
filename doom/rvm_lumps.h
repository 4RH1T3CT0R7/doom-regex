/* RVM: побайтовые аксессоры дисковых структур WAD + конвертер patch_t.
 * На ELVM sizeof(char)==sizeof(short)==sizeof(int)==1 слову, поэтому
 * упакованные файловые структуры читаются ТОЛЬКО явной сборкой из байтов.
 * (У BFDoom это делал host через bfhost_load_* — мы делаем в машине.) */
#ifndef RVM_LUMPS_H
#define RVM_LUMPS_H

int RU_u16(const void *p);
int RU_i16(const void *p);
int RU_i32(const void *p);   /* 24 бита достаточно: смещения в лампах < 16М */

/* Конвертирует patch-ламп из дисковой упаковки в словную раскладку ELVM
 * (заголовок + columnofs, указывающие в приложенную сырую копию).
 * Совместим по сигнатуре с W_CacheLumpNum/Name; результат кэшируется. */
void *RVM_CachePatchNum(int lump, int tag);
void *RVM_CachePatchName(const char *name, int tag);

#endif
