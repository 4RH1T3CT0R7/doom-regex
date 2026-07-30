# -*- coding: utf-8 -*-
"""Композит для промо: слева кадр DOOM с зелёной подсветкой только что
записанных пикселей, справа терминал с бегущими настоящими правилами
подстановки (имя + фрагмент паттерна), синхронизировано с прогрессом
покраски. Источники: timelapse_writes/*.png+*.msk и rules_seq.txt."""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 320, 200
SCALE = 3
FW, FH = W * SCALE, H * SCALE            # 960x600 кадр
FEEDW = 760                               # ширина терминала справа
PAD = 24
CANW = PAD + FW + PAD + FEEDW + PAD
CANH = PAD + FH + PAD

BG = (10, 9, 8)
GRN = (123, 201, 111)
GRN2 = (60, 255, 100)
DIM = (120, 120, 110)
AMB = (224, 164, 88)
RED = (228, 87, 46)

R = Path(r"C:/dev/doom-regex-out/run")
SRC = R / "timelapse_v21"


def font(sz, bold=False):
    for n in (["consolab.ttf"] if bold else ["consola.ttf"]):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + n, sz)
        except OSError:
            pass
    return ImageFont.load_default()


F = font(22)
FB = font(24, bold=True)
FS = font(19)


def load_mask(p):
    bits = np.frombuffer(p.read_bytes(), dtype=np.uint8)
    return np.unpackbits(bits, bitorder="little")[:W * H].reshape(H, W)


def dilate(m):
    d = m.copy()
    d[1:, :] |= m[:-1, :]; d[:-1, :] |= m[1:, :]
    d[:, 1:] |= m[:, :-1]; d[:, :-1] |= m[:, 1:]
    return d


def shorten(pat, n=52):
    # свернуть гигантские повторы, чтобы строка читалась
    import re
    pat = re.sub(r"(\.\{65535\})(\1)+", r"\1x…", pat)
    pat = pat.replace(r"(?:[^#]*+#)+?", "…HOP…")
    if len(pat) > n:
        pat = pat[:n] + "…"
    return pat


def main():
    rules_seq = (R / "rules_seq.txt").read_text().split("\n")
    pat = json.load(open(R / "pat_full.json"))
    pngs = sorted(SRC.glob("*.png"))
    total_pass = len(rules_seq)

    frames = []
    heat = np.zeros((H, W), dtype=np.float32)
    n = len(pngs)
    for idx, p in enumerate(pngs):
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.float32)
        mp = p.with_suffix(".msk")
        mask = load_mask(mp) if mp.exists() else np.zeros((H, W), np.uint8)
        mask = dilate(mask.astype(bool)).astype(np.float32)
        heat *= 0.80
        heat = np.maximum(heat, mask)
        if heat.max() < 0.05 and idx < n - 1:
            continue
        a = np.clip(heat, 0, 1)[..., None] * 0.9
        comp = (img * (1 - a) + np.array(GRN2, np.float32) * a)
        left = Image.fromarray(comp.clip(0, 255).astype(np.uint8)).resize(
            (FW, FH), Image.NEAREST)

        # холст
        can = Image.new("RGB", (CANW, CANH), BG)
        can.paste(left, (PAD, PAD))
        d = ImageDraw.Draw(can)
        d.rectangle([PAD - 2, PAD - 2, PAD + FW + 1, PAD + FH + 1],
                    outline=(44, 37, 30), width=2)

        # терминал: какие правила «идут» в этот момент
        fx = PAD + FW + PAD
        d.text((fx, PAD), "substitutions firing right now",
               font=FB, fill=AMB)
        d.text((fx, PAD + 34), "rule            pattern it searches for",
               font=FS, fill=DIM)
        # окно трейса вокруг текущего прогресса
        cur = int(total_pass * (idx + 1) / n)
        lo = max(0, cur - 15)
        y = PAD + 64
        for k in range(lo, min(cur, lo + 20)):
            name = rules_seq[k] if k < len(rules_seq) else ""
            if not name:
                continue
            frag = shorten(pat.get(name, ""))
            fresh = k >= cur - 2
            col = GRN2 if fresh else (90, 130, 85)
            d.text((fx, y), f"{name:<15}", font=F, fill=col)
            d.text((fx + 15 * 12, y), frag, font=F,
                   fill=(200, 200, 190) if fresh else (110, 110, 100))
            y += 26

        # низ: прогресс и цифра
        painted = int((heat > 0.02).sum()) if idx == n - 1 else None
        prog = int(100 * (idx + 1) / n)
        d.text((fx, CANH - PAD - 6),
               f"frame {prog}% painted   |   {cur:,} substitutions   |   "
               f"544 rules, one 96 MB string", font=FS, fill=DIM)
        frames.append(can)

    # финал: чистый кадр без зелени, пауза
    clean = Image.open(pngs[-1]).convert("RGB").resize((FW, FH), Image.NEAREST)
    can = Image.new("RGB", (CANW, CANH), BG)
    can.paste(clean, (PAD, PAD))
    d = ImageDraw.Draw(can)
    d.rectangle([PAD - 2, PAD - 2, PAD + FW + 1, PAD + FH + 1],
                outline=(44, 37, 30), width=2)
    fx = PAD + FW + PAD
    d.text((fx, PAD), "one frame of E1M1", font=FB, fill=AMB)
    d.text((fx, PAD + 40), "13,994,067 substitutions", font=font(30, True),
           fill=GRN2)
    d.text((fx, PAD + 84), "byte-identical to native DOOM", font=F, fill=DIM)
    d.text((fx, PAD + 118), "(SHA-256 match)", font=FS, fill=DIM)
    frames.append(can)

    gif = R / "composite.gif"
    dur = [90] * (len(frames) - 1) + [3000]
    frames[0].save(gif, save_all=True, append_images=frames[1:],
                   duration=dur, loop=0, optimize=True)
    print("GIF:", gif, f"{gif.stat().st_size/1e6:.1f}МБ, {len(frames)} кадров")
    frames[len(frames) // 2].save(R / "composite_check.png")


if __name__ == "__main__":
    main()
