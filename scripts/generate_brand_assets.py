#!/usr/bin/env python3
"""Génère les déclinaisons raster du logo Veliora (boîte à outils immobilière).

Source de vérité = le tracé du logo doré/vert (cf. vitrine/favicon.svg et
crm/assets/img/veliora-logo.svg). On le redessine ici avec Pillow pour produire
les formats que les réseaux sociaux / iOS exigent en bitmap :

  - vitrine/assets/img/veliora-icon-180.png   apple-touch-icon (fond blanc)
  - vitrine/assets/img/veliora-icon-512.png   icône PWA / maskable
  - vitrine/assets/img/veliora-og.png         partage social 1200×630

Lancer :  python scripts/generate_brand_assets.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = ROOT / "vitrine" / "assets" / "img"

# Palette (identique aux SVG)
GOLD_TOP = (235, 208, 138)
GOLD_MID = (205, 164, 88)
GOLD_BOT = (154, 123, 79)
GOLD_DARK = (138, 110, 69)
GREEN = (90, 125, 106)
INK = (30, 51, 64)
INK_SOFT = (74, 92, 104)
PLATE = (255, 255, 255)
PLATE_BORDER = (231, 222, 203)

SS = 4  # suréchantillonnage anti-crénelage


def _vgradient(size: int, top, bot) -> Image.Image:
    """Dégradé vertical top→bot sur un carré size×size."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel(
            (0, y),
            tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3)),
        )
    return grad.resize((size, size))


def _three_stop_gradient(width: int, height: int):
    """Dégradé doré 3 arrêts (haut/milieu/bas) sur width×height."""
    grad = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        if t < 0.5:
            tt = t / 0.5
            col = tuple(round(GOLD_TOP[i] + (GOLD_MID[i] - GOLD_TOP[i]) * tt) for i in range(3))
        else:
            tt = (t - 0.5) / 0.5
            col = tuple(round(GOLD_MID[i] + (GOLD_BOT[i] - GOLD_MID[i]) * tt) for i in range(3))
        grad.putpixel((0, y), col)
    return grad.resize((width, height))


def draw_mark(canvas: Image.Image, box: tuple[float, float, float]) -> None:
    """Dessine la marque (maison/boîte dorée + porte verte) dans `box` (px).

    `box` = (x, y, taille) ; le tracé est en repère viewBox 64 puis collé.
    Le logo est rendu dans une tuile carrée locale pour que le dégradé doré
    reste proportionné quelle que soit la forme du canevas final.
    """
    ox, oy, span = box
    side = max(1, round(span))
    s = side / 64.0  # échelle viewBox→px

    def P(x, y):
        return (x * s, y * s)

    def R(x, y, w, h):
        return [P(x, y), P(x + w, y + h)]

    tile = Image.new("RGBA", (side, side), (0, 0, 0, 0))

    # Masque des surfaces dorées (corps + toit) → rempli par dégradé doré.
    mask = Image.new("L", tile.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle(R(14, 31, 36, 20.5), radius=3.4 * s, fill=255)
    md.polygon([P(11, 31.6), P(32, 16), P(53, 31.6)], fill=255)
    grad = _three_stop_gradient(side, side).convert("RGBA")
    tile.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(tile)
    # Porte verte + bouton doré
    d.rounded_rectangle(R(28.7, 43, 6.6, 8.5), radius=3.3 * s, fill=GREEN)
    d.rectangle(R(28.7, 47, 6.6, 4.5), fill=GREEN)  # base droite de la porte
    kr = 1.0 * s
    kx, ky = P(33.8, 47)
    d.ellipse([kx - kr, ky - kr, kx + kr, ky + kr], fill=GOLD_TOP)
    # Poignée « boîte à outils » (demi-arc supérieur)
    hb = R(25.8, 11.2, 12.4, 10.8)  # bbox ellipse
    d.arc([hb[0][0], hb[0][1], hb[1][0], hb[1][1]], start=180, end=360,
          fill=GOLD_DARK, width=max(1, round(3 * s)))
    # Loquet / clip central
    d.rounded_rectangle(R(28.8, 29, 6.4, 4), radius=1.1 * s, fill=GOLD_DARK)

    canvas.alpha_composite(tile, (round(ox), round(oy)))


def render_icon(out: Path, size: int, *, plate: bool, pad_ratio: float = 0.0) -> None:
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if plate:
        d = ImageDraw.Draw(img)
        r = big * 0.22
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=r, fill=PLATE)
        d.rounded_rectangle([SS, SS, big - 1 - SS, big - 1 - SS], radius=r - SS,
                            outline=PLATE_BORDER, width=max(1, SS))
    pad = big * pad_ratio
    span = big - 2 * pad
    draw_mark(img, (pad, pad, span))
    img = img.resize((size, size), Image.LANCZOS)
    img.save(out)
    print("écrit", out.relative_to(ROOT), f"{size}×{size}")


def _font(serif: bool, size: int) -> ImageFont.FreeTypeFont:
    name = "DejaVuSerif-Bold.ttf" if serif else "DejaVuSans-Bold.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def render_og(out: Path, w: int = 1200, h: int = 630) -> None:
    big_w, big_h = w * 2, h * 2
    img = Image.new("RGBA", (big_w, big_h), PLATE + (255,))
    # Mark à gauche
    mark = big_h * 0.46
    mx = big_w * 0.16
    my = (big_h - mark) / 2
    draw_mark(img, (mx, my, mark))

    d = ImageDraw.Draw(img)
    tx = mx + mark + big_h * 0.08
    title_f = _font(True, int(big_h * 0.20))
    tag_f = _font(False, int(big_h * 0.052))
    # Titre
    bbox = d.textbbox((0, 0), "Veliora", font=title_f)
    th = bbox[3] - bbox[1]
    ty = big_h / 2 - th * 0.78
    d.text((tx, ty), "Veliora", font=title_f, fill=INK)
    ink_bottom = ty + bbox[3]  # bas réel des glyphes (bbox[1] > 0 → décalage)
    filet_y = ink_bottom + big_h * 0.035
    d.rectangle([tx + 4, filet_y, tx + big_h * 0.40, filet_y + max(3, big_h * 0.006)],
                fill=GOLD_MID)
    d.text((tx + 2, filet_y + big_h * 0.03),
           "La boîte à outils immobilière", font=tag_f, fill=GOLD_BOT)

    img = img.convert("RGB").resize((w, h), Image.LANCZOS)
    img.save(out)
    print("écrit", out.relative_to(ROOT), f"{w}×{h}")


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    render_icon(IMG_DIR / "veliora-icon-180.png", 180, plate=True, pad_ratio=0.04)
    render_icon(IMG_DIR / "veliora-icon-512.png", 512, plate=True, pad_ratio=0.04)
    render_icon(IMG_DIR / "veliora-maskable-512.png", 512, plate=True, pad_ratio=0.14)
    render_og(IMG_DIR / "veliora-og.png")


if __name__ == "__main__":
    main()
