/* Реализация rvm_lumps.h — см. заголовок. */
#include "doomtype.h"
#include "w_wad.h"
#include "z_zone.h"

#include "rvm_lumps.h"

void RU_WadRead(unsigned int offset, void *dest, int len) {
    const char *src = (const char *) (RVM_WAD_BASE + 1) + offset;
    char *out = dest;
    int i;
    for (i = 0; i < len; i++) {
        out[i] = src[i];
    }
}

int RU_u16(const void *p) {
    const unsigned char *b = p;
    return b[0] + (b[1] << 8);
}

int RU_i16(const void *p) {
    int v = RU_u16(p);
    if (v >= 32768) {
        v -= 65536;
    }
    return v;
}

int RU_i32(const void *p) {
    const unsigned char *b = p;
    return b[0] + (b[1] << 8) + (b[2] << 16);
}

/* --- конвертер patch_t --------------------------------------------------
 * Дисковый формат: u16 width, height; i16 left, top; u32 columnofs[width];
 * далее колонки (байтовые посты). Словная раскладка ELVM patch_t совпадает
 * по ПОРЯДКУ полей, поэтому после конверсии обычные обращения
 * patch->width / patch->columnofs[i] работают без правки потребителей.
 * columnofs пересчитываются на приложенную сырую копию лампа. */

static void **rvm_patch_cache;

void *RVM_CachePatchNum(int lump, int tag) {
    unsigned char *raw;
    int *out;
    int w, h, i, rawlen, hdr;

    (void) tag;
    if (!rvm_patch_cache) {
        rvm_patch_cache = Z_Malloc(numlumps * sizeof(void *), PU_STATIC, 0);
        for (i = 0; i < (int) numlumps; i++) {
            rvm_patch_cache[i] = 0;
        }
    }
    if (rvm_patch_cache[lump]) {
        return rvm_patch_cache[lump];
    }

    raw = W_CacheLumpNum(lump, PU_STATIC);
    rawlen = W_LumpLength(lump);
    w = RU_u16(raw);
    h = RU_u16(raw + 2);
    hdr = 4 + w;                       /* словный заголовок + columnofs */

    out = Z_Malloc((hdr + rawlen) * sizeof(int), PU_STATIC, 0);
    out[0] = w;
    out[1] = h;
    out[2] = RU_i16(raw + 4);
    out[3] = RU_i16(raw + 6);
    for (i = 0; i < w; i++) {
        out[4 + i] = hdr + RU_i32(raw + 8 + i * 4);
    }
    for (i = 0; i < rawlen; i++) {
        out[hdr + i] = raw[i];
    }

    W_ReleaseLumpNum(lump);
    rvm_patch_cache[lump] = out;
    return out;
}

void *RVM_CachePatchName(const char *name, int tag) {
    return RVM_CachePatchNum(W_GetNumForName((char *) name), tag);
}
