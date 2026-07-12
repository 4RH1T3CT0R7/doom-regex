# -*- coding: utf-8 -*-
"""Применяет ELVM-патчи к vendored doomgeneric (идемпотентно).

На ELVM sizeof(char)==sizeof(short)==sizeof(int)==1 слову: дисковые
структуры WAD не читаются через приведение указателей — каждый лоадер
переписывается на явную побайтовую сборку (RU_i16/RU_i32 из rvm_lumps.h),
а patch_t-лампы конвертируются в словную раскладку (RVM_CachePatch*).

Запуск: py -3.11 doom/apply_port_patches.py <путь к vendored doomgeneric/doomgeneric>
"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
MARK = "rvm_lumps.h"


def sub_n(text: str, pattern: str, repl: str, n: int = 1, flags=0) -> str:
    new, cnt = re.subn(pattern, repl, text, flags=flags)
    assert cnt == n, f"ожидалось {n} замен, вышло {cnt}: {pattern[:80]}"
    return new


def patch_file(name: str, fn) -> None:
    p = ROOT / name
    t = p.read_text(encoding="utf-8", errors="replace")
    if MARK in t:
        print(f"  {name}: уже пропатчен")
        return
    t = f'#include "{MARK}"\n' + t
    t = fn(t)
    p.write_text(t, encoding="utf-8")
    print(f"  {name}: ok")


# --- p_setup.c: 9 лоадеров ------------------------------------------------

def fix_p_setup(t: str) -> str:
    # размеры дисковых записей вместо sizeof ELVM-структур
    for st, size in [("mapvertex_t", 4), ("mapseg_t", 12),
                     ("mapsubsector_t", 4), ("mapsector_t", 26),
                     ("mapnode_t", 28), ("mapthing_t", 10),
                     ("maplinedef_t", 14), ("mapsidedef_t", 30)]:
        t = sub_n(t, rf"W_LumpLength\s*\(lump\)\s*/\s*sizeof\({st}\)",
                  f"W_LumpLength (lump) / {size}", 1)

    # Vertexes
    t = sub_n(t, r"for \(i=0 ; i<numvertexes ; i\+\+, li\+\+, ml\+\+\)",
              "for (i=0 ; i<numvertexes ; i++, li++, data += 4)")
    t = sub_n(t, r"li->x = SHORT\(ml->x\)<<FRACBITS;",
              "li->x = RU_i16(data)<<FRACBITS;")
    t = sub_n(t, r"li->y = SHORT\(ml->y\)<<FRACBITS;",
              "li->y = RU_i16(data+2)<<FRACBITS;")

    # Segs (диск: v1,v2,angle,linedef,side,offset — по 2 байта)
    t = sub_n(t, r"for \(i=0 ; i<numsegs ; i\+\+, li\+\+, ml\+\+\)",
              "for (i=0 ; i<numsegs ; i++, li++, data += 12)")
    t = sub_n(t, r"li->v1 = &vertexes\[SHORT\(ml->v1\)\];",
              "li->v1 = &vertexes[RU_u16(data)];")
    t = sub_n(t, r"li->v2 = &vertexes\[SHORT\(ml->v2\)\];",
              "li->v2 = &vertexes[RU_u16(data+2)];")
    t = sub_n(t, r"li->angle = \(SHORT\(ml->angle\)\)<<16;",
              "li->angle = (RU_i16(data+4))<<16;")
    t = sub_n(t, r"li->offset = \(SHORT\(ml->offset\)\)<<16;",
              "li->offset = (RU_i16(data+10))<<16;")
    t = sub_n(t, r"linedef = SHORT\(ml->linedef\);",
              "linedef = RU_u16(data+6);")
    t = sub_n(t, r"side = SHORT\(ml->side\);",
              "side = RU_u16(data+8);")

    # Subsectors
    t = sub_n(t, r"for \(i=0 ; i<numsubsectors ; i\+\+, ss\+\+, ms\+\+\)",
              "for (i=0 ; i<numsubsectors ; i++, ss++, data += 4)")
    t = sub_n(t, r"ss->numlines = SHORT\(ms->numsegs\);",
              "ss->numlines = RU_u16(data);")
    t = sub_n(t, r"ss->firstline = SHORT\(ms->firstseg\);",
              "ss->firstline = RU_u16(data+2);")

    # Sectors (floor,ceil,floorpic[8],ceilpic[8],light,special,tag)
    t = sub_n(t, r"for \(i=0 ; i<numsectors ; i\+\+, ss\+\+, ms\+\+\)",
              "for (i=0 ; i<numsectors ; i++, ss++, data += 26)")
    t = sub_n(t, r"ss->floorheight = SHORT\(ms->floorheight\)<<FRACBITS;",
              "ss->floorheight = RU_i16(data)<<FRACBITS;")
    t = sub_n(t, r"ss->ceilingheight = SHORT\(ms->ceilingheight\)<<FRACBITS;",
              "ss->ceilingheight = RU_i16(data+2)<<FRACBITS;")
    t = sub_n(t, r"ss->floorpic = R_FlatNumForName\(ms->floorpic\);",
              "ss->floorpic = R_FlatNumForName((char *)(data+4));")
    t = sub_n(t, r"ss->ceilingpic = R_FlatNumForName\(ms->ceilingpic\);",
              "ss->ceilingpic = R_FlatNumForName((char *)(data+12));")
    t = sub_n(t, r"ss->lightlevel = SHORT\(ms->lightlevel\);",
              "ss->lightlevel = RU_i16(data+20);")
    t = sub_n(t, r"ss->special = SHORT\(ms->special\);",
              "ss->special = RU_i16(data+22);")
    t = sub_n(t, r"ss->tag = SHORT\(ms->tag\);",
              "ss->tag = RU_i16(data+24);")

    # Nodes (x,y,dx,dy, bbox[2][4], children[2])
    t = sub_n(t, r"for \(i=0 ; i<numnodes ; i\+\+, no\+\+, mn\+\+\)",
              "for (i=0 ; i<numnodes ; i++, no++, data += 28)")
    t = sub_n(t, r"no->x = SHORT\(mn->x\)<<FRACBITS;",
              "no->x = RU_i16(data)<<FRACBITS;")
    t = sub_n(t, r"no->y = SHORT\(mn->y\)<<FRACBITS;",
              "no->y = RU_i16(data+2)<<FRACBITS;")
    t = sub_n(t, r"no->dx = SHORT\(mn->dx\)<<FRACBITS;",
              "no->dx = RU_i16(data+4)<<FRACBITS;")
    t = sub_n(t, r"no->dy = SHORT\(mn->dy\)<<FRACBITS;",
              "no->dy = RU_i16(data+6)<<FRACBITS;")
    t = sub_n(t, r"no->children\[j\] = SHORT\(mn->children\[j\]\);",
              "no->children[j] = RU_u16(data + 24 + j*2);")
    t = sub_n(t, r"no->bbox\[j\]\[k\] = SHORT\(mn->bbox\[j\]\[k\]\)<<FRACBITS;",
              "no->bbox[j][k] = RU_i16(data + 8 + j*8 + k*2)<<FRACBITS;")

    # Things (x,y,angle,type,options)
    t = sub_n(t, r"for \(i=0 ; i<numthings ; i\+\+, mt\+\+\)",
              "for (i=0 ; i<numthings ; i++, data += 10)")
    t = sub_n(t, r"switch \(SHORT\(mt->type\)\)",
              "switch (RU_i16(data+6))")
    t = sub_n(t, r"spawnthing\.x = SHORT\(mt->x\);",
              "spawnthing.x = RU_i16(data);")
    t = sub_n(t, r"spawnthing\.y = SHORT\(mt->y\);",
              "spawnthing.y = RU_i16(data+2);")
    t = sub_n(t, r"spawnthing\.angle = SHORT\(mt->angle\);",
              "spawnthing.angle = RU_i16(data+4);")
    t = sub_n(t, r"spawnthing\.type = SHORT\(mt->type\);",
              "spawnthing.type = RU_i16(data+6);")
    t = sub_n(t, r"spawnthing\.options = SHORT\(mt->options\);",
              "spawnthing.options = RU_i16(data+8);")

    # LineDefs (v1,v2,flags,special,tag,sidenum[2])
    t = sub_n(t, r"for \(i=0 ; i<numlines ; i\+\+, mld\+\+, ld\+\+\)",
              "for (i=0 ; i<numlines ; i++, ld++, data += 14)")
    t = sub_n(t, r"ld->flags = SHORT\(mld->flags\);",
              "ld->flags = RU_u16(data+4);")
    t = sub_n(t, r"ld->special = SHORT\(mld->special\);",
              "ld->special = RU_i16(data+6);")
    t = sub_n(t, r"ld->tag = SHORT\(mld->tag\);",
              "ld->tag = RU_i16(data+8);")
    t = sub_n(t, r"v1 = ld->v1 = &vertexes\[SHORT\(mld->v1\)\];",
              "v1 = ld->v1 = &vertexes[RU_u16(data)];")
    t = sub_n(t, r"v2 = ld->v2 = &vertexes\[SHORT\(mld->v2\)\];",
              "v2 = ld->v2 = &vertexes[RU_u16(data+2)];")
    t = sub_n(t, r"ld->sidenum\[0\] = SHORT\(mld->sidenum\[0\]\);",
              "ld->sidenum[0] = RU_i16(data+10);")
    t = sub_n(t, r"ld->sidenum\[1\] = SHORT\(mld->sidenum\[1\]\);",
              "ld->sidenum[1] = RU_i16(data+12);")

    # SideDefs (xoff,yoff,top[8],bottom[8],mid[8],sector)
    t = sub_n(t, r"for \(i=0 ; i<numsides ; i\+\+, msd\+\+, sd\+\+\)",
              "for (i=0 ; i<numsides ; i++, sd++, data += 30)")
    t = sub_n(t, r"sd->textureoffset = SHORT\(msd->textureoffset\)<<FRACBITS;",
              "sd->textureoffset = RU_i16(data)<<FRACBITS;")
    t = sub_n(t, r"sd->rowoffset = SHORT\(msd->rowoffset\)<<FRACBITS;",
              "sd->rowoffset = RU_i16(data+2)<<FRACBITS;")
    t = sub_n(t, r"sd->toptexture = R_TextureNumForName\(msd->toptexture\);",
              "sd->toptexture = R_TextureNumForName((char *)(data+4));")
    t = sub_n(t, r"sd->bottomtexture = R_TextureNumForName\(msd->bottomtexture\);",
              "sd->bottomtexture = R_TextureNumForName((char *)(data+12));")
    t = sub_n(t, r"sd->midtexture = R_TextureNumForName\(msd->midtexture\);",
              "sd->midtexture = R_TextureNumForName((char *)(data+20));")
    t = sub_n(t, r"sd->sector = &sectors\[SHORT\(msd->sector\)\];",
              "sd->sector = &sectors[RU_u16(data+28)];")

    # BlockMap: побайтовая сборка вперёд (запись i <= чтение 2i — безопасно)
    t = sub_n(t,
              r"blockmaplump\[i\] = SHORT\(blockmaplump\[i\]\);",
              "blockmaplump[i] = RU_i16((byte *)blockmaplump + i * 2);")
    return t


# --- r_data.c: текстуры, спрайт-заголовки, композиты ----------------------

def fix_r_data(t: str) -> str:
    t = sub_n(t, r"nummappatches = LONG \( \*\(\(int \*\)names\) \);",
              "nummappatches = RU_i32(names);")
    t = sub_n(t, r"numtextures1 = LONG\(\*maptex\);",
              "numtextures1 = RU_i32((byte *)maptex);")
    t = sub_n(t, r"numtextures2 = LONG\(\*maptex2\);",
              "numtextures2 = RU_i32((byte *)maptex2);")
    t = sub_n(t, r"directory = maptex\+1;",
              "directory = (int *)((byte *)maptex + 4);", 2)
    t = sub_n(t, r"offset = LONG\(\*directory\);",
              "offset = RU_i32((byte *)directory);")
    t = sub_n(t, r"for \(i=0 ; i<numtextures ; i\+\+, directory\+\+\)",
              "for (i=0 ; i<numtextures ; i++, "
              "directory = (int *)((byte *)directory + 4))")
    # maptexture_t: name@0, masked@8, width@12, height@14, patchcount@20,
    # patches@22 (mappatch_t по 10 байт: originx,originy,patch,stepdir,cmap)
    t = sub_n(t, r"SHORT\(mtexture->patchcount\)-1",
              "RU_i16((byte *)mtexture + 20)-1")
    t = sub_n(t, r"texture->width = SHORT\(mtexture->width\);",
              "texture->width = RU_i16((byte *)mtexture + 12);")
    t = sub_n(t, r"texture->height = SHORT\(mtexture->height\);",
              "texture->height = RU_i16((byte *)mtexture + 14);")
    t = sub_n(t, r"texture->patchcount = SHORT\(mtexture->patchcount\);",
              "texture->patchcount = RU_i16((byte *)mtexture + 20);")
    t = sub_n(t, r"memcpy \(texture->name, mtexture->name, sizeof\(texture->name\)\);",
              "memcpy (texture->name, (byte *)mtexture, sizeof(texture->name));")
    t = sub_n(t, r"mpatch = &mtexture->patches\[0\];",
              "mpatch = (mappatch_t *)((byte *)mtexture + 22);")
    t = sub_n(t, r"for \(j=0 ; j<texture->patchcount ; j\+\+, mpatch\+\+, patch\+\+\)",
              "for (j=0 ; j<texture->patchcount ; j++, "
              "mpatch = (mappatch_t *)((byte *)mpatch + 10), patch++)")
    t = sub_n(t, r"patch->originx = SHORT\(mpatch->originx\);",
              "patch->originx = RU_i16((byte *)mpatch);")
    t = sub_n(t, r"patch->originy = SHORT\(mpatch->originy\);",
              "patch->originy = RU_i16((byte *)mpatch + 2);")
    t = sub_n(t, r"patch->patch = patchlookup\[SHORT\(mpatch->patch\)\];",
              "patch->patch = patchlookup[RU_u16((byte *)mpatch + 4)];")

    # R_InitSpriteLumps: заголовок patch напрямую из сырых байтов
    t = sub_n(t, r"spritewidth\[i\] = SHORT\(patch->width\)<<FRACBITS;",
              "spritewidth[i] = RU_u16((byte *)patch)<<FRACBITS;")
    t = sub_n(t, r"spriteoffset\[i\] = SHORT\(patch->leftoffset\)<<FRACBITS;",
              "spriteoffset[i] = RU_i16((byte *)patch + 4)<<FRACBITS;")
    t = sub_n(t, r"spritetopoffset\[i\] = SHORT\(patch->topoffset\)<<FRACBITS;",
              "spritetopoffset[i] = RU_i16((byte *)patch + 6)<<FRACBITS;")

    # композиты/lookup читают реальные patch-лампы -> конвертер
    t = sub_n(t, r"realpatch = W_CacheLumpNum \(patch->patch, PU_CACHE\);",
              "realpatch = RVM_CachePatchNum (patch->patch, PU_CACHE);", 2)
    t = sub_n(t, r"x1 = patch->originx;", r"x1 = patch->originx;", 2)  # маркер
    return t


# --- r_things.c: спрайты в рендере ----------------------------------------

def fix_r_things(t: str) -> str:
    t = sub_n(t,
              r"patch = W_CacheLumpNum \(vis->patch\+firstspritelump, PU_CACHE\);",
              "patch = RVM_CachePatchNum (vis->patch+firstspritelump, PU_CACHE);")
    return t


# --- UI: все patch-лампы через конвертер -----------------------------------

def blanket_patch_cache(t: str, min_n: int) -> str:
    new, n1 = re.subn(r"W_CacheLumpName", "RVM_CachePatchName", t)
    new, n2 = re.subn(r"W_CacheLumpNum\b", "RVM_CachePatchNum", new)
    assert n1 + n2 >= min_n, f"мало замен: {n1}+{n2}"
    return new


def fix_st_stuff(t: str) -> str:
    return blanket_patch_cache(t, 2)


def fix_wi_stuff(t: str) -> str:
    return blanket_patch_cache(t, 2)


def fix_hu_stuff(t: str) -> str:
    return blanket_patch_cache(t, 1)


def fix_m_menu(t: str) -> str:
    return blanket_patch_cache(t, 2)


def fix_am_map(t: str) -> str:
    return blanket_patch_cache(t, 1)


def fix_d_main(t: str) -> str:
    # только страница-заставка (TITLEPIC и т.п.)
    t = sub_n(t,
              r"V_DrawPatch \(0, 0, W_CacheLumpName\(pagename, PU_CACHE\)\);",
              "V_DrawPatch (0, 0, RVM_CachePatchName(pagename, PU_CACHE));")
    return t


def fix_f_finale(t: str) -> str:
    # патчи каста/фона/бункера — конвертер; flat (finaleflat, стр. 240)
    # остаётся сырым
    t = sub_n(t, r"patch = W_CacheLumpNum \(lump\+firstspritelump, PU_CACHE\);",
              "patch = RVM_CachePatchNum (lump+firstspritelump, PU_CACHE);")
    t = sub_n(t, r'W_CacheLumpName \(DEH_String\("BOSSBACK"\), PU_CACHE\)',
              'RVM_CachePatchName (DEH_String("BOSSBACK"), PU_CACHE)')
    t = sub_n(t, r'p1 = W_CacheLumpName \(DEH_String\("PFUB2"\), PU_LEVEL\);',
              'p1 = RVM_CachePatchName (DEH_String("PFUB2"), PU_LEVEL);')
    t = sub_n(t, r'p2 = W_CacheLumpName \(DEH_String\("PFUB1"\), PU_LEVEL\);',
              'p2 = RVM_CachePatchName (DEH_String("PFUB1"), PU_LEVEL);')
    t = sub_n(t, r'W_CacheLumpName\(DEH_String\("END0"\), PU_CACHE\)',
              'RVM_CachePatchName(DEH_String("END0"), PU_CACHE)')
    t = sub_n(t, r"W_CacheLumpName \(name,PU_CACHE\)",
              "RVM_CachePatchName (name,PU_CACHE)")
    t = sub_n(t, r"W_CacheLumpName\(lumpname, PU_CACHE\)",
              "RVM_CachePatchName(lumpname, PU_CACHE)")
    return t


def fix_r_draw(t: str) -> str:
    # рамка вокруг вью — патчи brdr_*; flat-фон (строка src=) остаётся сырым
    t = sub_n(t, r'W_CacheLumpName\(DEH_String\("brdr_',
              'RVM_CachePatchName(DEH_String("brdr_', 8)
    return t



def main() -> None:
    print(f"патчим {ROOT}")
    patch_file("p_setup.c", fix_p_setup)
    patch_file("r_data.c", fix_r_data)
    patch_file("r_things.c", fix_r_things)
    patch_file("st_stuff.c", fix_st_stuff)
    patch_file("wi_stuff.c", fix_wi_stuff)
    patch_file("hu_stuff.c", fix_hu_stuff)
    patch_file("m_menu.c", fix_m_menu)
    patch_file("am_map.c", fix_am_map)
    patch_file("d_main.c", fix_d_main)
    patch_file("f_finale.c", fix_f_finale)
    patch_file("r_draw.c", fix_r_draw)
    print("готово")


if __name__ == "__main__":
    main()
