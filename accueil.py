"""Fenetre d'accueil de FS25 Stream & Companion (tkinter + Pillow).

Elle s'ouvre au lancement de l'application (sauf si l'utilisateur a coche "Ne plus afficher")
et quand on clique sur l'icone pres de l'horloge.

Le dessin (coins arrondis, degrades, emojis en couleur, texte lisse) est fait par Pillow dans une
image, affichee dans un Canvas tkinter : tkinter seul ne sait pas faire de coins arrondis ni
d'emojis en couleur. Police : Inter (fournie avec l'app, licence OFL), sinon Segoe UI.

tkinter doit tourner dans le fil principal : app.py appelle Accueil.run() a la fin du demarrage,
l'icone pres de l'horloge tourne a cote (pystray run_detached). Les autres fils (icone, serveur)
passent par show() / call() / quit(), qui sont sans danger depuis n'importe quel fil.
"""
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

# ------------------------------------------------------------------ couleurs (maquette "version 2")
BG_TOP = (21, 36, 27)
BG_BOTTOM = (12, 17, 14)
CARD = (20, 32, 24)
CARD_HOVER = (27, 44, 33)
GREEN = (143, 228, 95)
GREEN_SOFT = (110, 190, 75)
TEXT = (238, 242, 238)
MUTED = (160, 172, 164)
ORANGE = (242, 181, 68)
GREY = (110, 122, 115)

PANEL_H = 214            # 1.4.4 : hauteur du panneau "Telephone / tablette" (QR code, Wi-Fi, code)
PILLS = {   # fond, bordure, texte
    "aide": ((28, 41, 33), (50, 66, 56), (232, 238, 233)),
    "discord": ((27, 31, 68), (88, 101, 242), (205, 210, 255)),
    "soutenir": ((44, 36, 18), (217, 168, 74), (245, 200, 110)),
}

W, H = 660, 632          # taille de la fenetre (pixels "logiques", avant mise a l'echelle Windows)
SS = 2                   # sur-echantillonnage pour des bords lisses

AIDE = {
    "Stream": [
        ("C'est quoi ?", "Un habillage (overlay) pour ton live : vitesse, carburant, outils attelés, "
                         "champ actuel, mini-carte, heure, météo, argent… Les infos viennent directement du jeu."),
        ("Mise en place (une seule fois)",
         "1. Lance le jeu une fois avec l'application ouverte : le mod est installé tout seul.\n"
         "2. Active le mod « FS25 Stream & Companion » dans ta sauvegarde.\n"
         "3. Dans OBS : Sources > + > Navigateur.\n"
         "    Adresse : http://127.0.0.1:8765   ·   taille de ta scène OBS (1920 × 1080, ou 2560 × 1440 en 2K).\n"
         "4. Clique sur « Stream » ici pour régler l'affichage (mode édition)."),
        ("Le mode édition",
         "Déplace et redimensionne les blocs à la souris. Le panneau à onglets permet de choisir les "
         "blocs affichés, la transparence et le style. Tout est enregistré tout seul, OBS se met à jour."),
        ("En multijoueur",
         "Le mod doit aussi être sur le serveur, dans la même version (sinon personne ne peut rejoindre). "
         "Chaque joueur qui veut l'overlay installe l'application."),
        ("Lancer le jeu",
         "La barre verte « Lancer Farming Simulator 25 » démarre le jeu. La 1re fois, choisis où tu l'as acheté "
         "(Steam, Epic Games, version Giants, Microsoft Store / Xbox, ou le fichier .exe du jeu). "
         "Pour changer plus tard : petit menu à droite de la barre."),
    ],
    "Compagnon": [
        ("C'est quoi ?", "Une page pour jouer (pas pour streamer) sur un téléphone, une tablette ou un "
                         "2e écran : carte des champs, état de tous les champs, liste « À faire », alertes "
                         "(champ prêt, remorque pleine…), qui fait quoi, notes par champ, tes engins, tes silos et les lieux de la carte."),
        ("Sur ce PC (2e écran)",
         "Dans la carte « Compagnon », clique sur « Ouvrir sur ce PC », ou va sur http://127.0.0.1:8765/compagnon."),
        ("Sur un téléphone ou une tablette",
         "1. Dans la carte « Compagnon », clique sur « Téléphone / tablette » et active « Ouvert au Wi-Fi de la maison ».\n"
         "2. Si le téléphone n'arrive pas à se connecter : « Autoriser dans le pare-feu » (Windows demande ton accord, une seule fois).\n"
         "3. Scanne le QR code avec l'appareil (il doit être sur le même Wi-Fi que le PC).\n"
         "Astuce : « Ajouter à l'écran d'accueil » sur le téléphone pour l'ouvrir comme une app."),
        ("Code d'accès",
         "Facultatif : si tu ne veux pas que n'importe qui sur ton Wi-Fi puisse ouvrir le compagnon, active « Protéger par un code » (Téléphone / tablette). Chaque appareil tape le code une seule fois."),
        ("Dans le jeu",
         "Le compagnon peut aussi afficher des infos directement dans le jeu (bouton « 🎮 En jeu » : choix des blocs, position, largeur, hauteur, taille du texte, transparence). "
         "Blocs disponibles : À faire, Qui fait quoi, Prévisions météo, Champ actuel, Dernières alertes. La touche F9 ouvre / ferme ceux réglés « Avec la touche » (modifiable dans Options > Commandes du jeu)."),
        ("Téléportation",
         "Double-clic sur un champ, un engin ou un lieu (🏠 maison, tes bâtiments, points de vente) : tu y vas directement dans le jeu "
         "(dans un engin : tu montes dedans, s'il est à ta ferme et libre). Sur téléphone : touche-le puis le bouton vert. Désactivable dans « 🎮 En jeu »."),
    ],
}


# ------------------------------------------------------------------ polices
def _win_fonts():
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


class Fonts:
    def __init__(self, font_dir=None):
        self.font_dir = Path(font_dir) if font_dir else None
        self._cache = {}

    def _first(self, names):
        dirs = [d for d in (self.font_dir, _win_fonts(), Path("/usr/share/fonts/opentype/inter"),
                            Path("/usr/share/fonts/truetype/dejavu")) if d]
        for n in names:
            for d in dirs:
                p = d / n
                if p.is_file():
                    return str(p)
        return None

    def get(self, weight, size):
        key = (weight, size)
        if key not in self._cache:
            names = {
                "regular": ["Inter-Regular.otf", "segoeui.ttf", "DejaVuSans.ttf"],
                "semibold": ["Inter-SemiBold.otf", "seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"],
                "bold": ["Inter-Bold.otf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"],
            }[weight]
            path = self._first(names)
            try:
                self._cache[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
            except Exception:
                self._cache[key] = ImageFont.load_default()
        return self._cache[key]


_emoji_cache = {}


def emoji_image(char, size):
    """Emoji en couleur (Segoe UI Emoji sur Windows, Noto Color Emoji sous Linux), ou None."""
    key = (char, size)
    if key in _emoji_cache:
        return _emoji_cache[key]
    img = None
    for path, native in ((_win_fonts() / "seguiemj.ttf", None),
                         (Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"), 109)):
        if not path.is_file():
            continue
        try:
            fsize = native or size
            font = ImageFont.truetype(str(path), fsize)
            tmp = Image.new("RGBA", (fsize * 2, fsize * 2), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text((fsize // 2, fsize // 2), char, font=font, embedded_color=True)
            box = tmp.getbbox()
            if not box:
                continue
            tmp = tmp.crop(box)
            r = size / max(tmp.size)
            img = tmp.resize((max(1, round(tmp.width * r)), max(1, round(tmp.height * r))), Image.LANCZOS)
            break
        except Exception:
            continue
    _emoji_cache[key] = img
    return img


# ------------------------------------------------------------------ dessin
def _background(w, h):
    grad = Image.linear_gradient("L").resize((w, h))
    top = Image.new("RGB", (w, h), BG_TOP)
    bot = Image.new("RGB", (w, h), BG_BOTTOM)
    img = Image.composite(bot, top, grad)
    glow = Image.new("L", (w, h), 0)                            # halo vert discret derriere le logo
    ImageDraw.Draw(glow).ellipse((w * 0.25, -h * 0.25, w * 0.75, h * 0.35), fill=60)
    glow = glow.filter(ImageFilter.GaussianBlur(w // 8))
    img = Image.composite(Image.new("RGB", (w, h), (40, 70, 45)), img, glow)
    return img.convert("RGBA")


def _text_center(d, cx, y, text, font, fill):
    w = d.textlength(text, font=font)
    d.text((cx - w / 2, y), text, font=font, fill=fill)


def _draw_icon_camera(d, cx, cy, s):
    """Icone de secours (si pas d'emoji) : camera."""
    k = s / 64
    d.rounded_rectangle((cx - 26 * k, cy - 12 * k, cx + 12 * k, cy + 20 * k), radius=6 * k, fill=(90, 98, 110))
    d.polygon([(cx + 12 * k, cy), (cx + 28 * k, cy - 10 * k), (cx + 28 * k, cy + 18 * k), (cx + 12 * k, cy + 8 * k)],
              fill=(70, 78, 90))
    d.ellipse((cx - 26 * k, cy - 34 * k, cx - 6 * k, cy - 14 * k), fill=(70, 78, 90))
    d.ellipse((cx - 6 * k, cy - 34 * k, cx + 14 * k, cy - 14 * k), fill=(70, 78, 90))
    d.ellipse((cx - 22 * k, cy + 2 * k, cx - 14 * k, cy + 10 * k), fill=(229, 72, 77))


def _draw_icon_phone(d, cx, cy, s):
    k = s / 64
    d.rounded_rectangle((cx - 18 * k, cy - 30 * k, cx + 18 * k, cy + 30 * k), radius=7 * k, fill=(40, 44, 52))
    d.rounded_rectangle((cx - 14 * k, cy - 24 * k, cx + 14 * k, cy + 22 * k), radius=3 * k, fill=(70, 120, 200))
    cols = [(240, 90, 90), (250, 190, 70), (110, 200, 110), (90, 170, 240)]
    for i in range(12):
        x = cx - 11 * k + (i % 3) * 8 * k
        y = cy - 20 * k + (i // 3) * 9 * k
        d.rounded_rectangle((x, y, x + 6 * k, y + 6 * k), radius=1.5 * k, fill=cols[i % 4])


def _check(d, x, y, s, fill):
    d.line([(x, y + s * 0.55), (x + s * 0.38, y + s * 0.9), (x + s, y + s * 0.1)], fill=fill,
           width=max(2, round(s / 5)), joint="curve")


class Layout:
    """Positions des zones cliquables (pixels logiques)."""
    def __init__(self):
        self.cards = {"stream": (30, 214, 320, 452), "compagnon": (340, 214, 630, 452)}
        self.pills = {}
        self.play = (30, 470, 630, 516)      # grande barre "Lancer Farming Simulator 25"
        self.platform = None                 # petit "Steam ▾" a droite de la barre (changer de plateforme)
        self.check = None
        self.choice = None
        self.upd = None                      # bandeau "nouvelle version" (en haut a gauche)
        self.upd_btn = None
        self.comp_btns = {}                  # 1.4.4 : boutons dans la carte Compagnon (comp_pc, comp_phone)
        self.phone = {}                      # 1.4.4 : zones du panneau "Telephone / tablette"
        self.dy = 0                          # hauteur ajoutee par le panneau quand il est ouvert


def render(state, *, title_a, title_b, version, status, logo, fonts, scale, discord_ok):
    """Dessine toute la fenetre. state : {"hover": cle|None, "skip": bool, "open": "Stream"|...}."""
    k = scale * SS
    ph = state.get("phone")                  # None, ou {"open": bool, "info": {...}}
    dy = PANEL_H + 14 if ph and ph.get("open") else 0
    HH = H + dy
    w, h = round(W * k), round(HH * k)
    img = _background(w, h)
    d = ImageDraw.Draw(img)
    layout = Layout()
    layout.dy = dy

    def f(weight, size):
        return fonts.get(weight, round(size * k))

    def P(*v):
        return tuple(x * k for x in v)

    cx = w / 2

    # --- bandeau "nouvelle version" (en haut a gauche, a cote du logo)
    upd = state.get("upd")
    if upd:
        ust, uver, upct = upd
        bx1, by1, bx2, by2 = 20, 18, 234, 108
        hov_box = state.get("hover") == "upd"
        d.rounded_rectangle(P(bx1, by1, bx2, by2), radius=14 * k, fill=CARD_HOVER if hov_box else CARD,
                            outline=GREEN_SOFT, width=round(1.5 * k))
        e = emoji_image("🔄", round(16 * k))
        tx = bx1 + 12
        if e is not None:
            img.alpha_composite(e, (round(tx * k), round((by1 + 10) * k)))
            tx += 22
        d.text((tx * k, (by1 + 9) * k), "Nouvelle version", font=f("bold", 15), fill=TEXT)
        d.text(((bx1 + 12) * k, (by1 + 31) * k), f"v{uver} · voir les nouveautés", font=f("regular", 12),
               fill=GREEN if hov_box else MUTED)
        ux1, uy1, ux2, uy2 = bx1 + 10, by1 + 54, bx2 - 10, by2 - 9
        if ust == "downloading":
            d.rounded_rectangle(P(ux1, uy1, ux2, uy2), radius=9 * k, fill=(30, 46, 35))
            if upct > 0:
                d.rounded_rectangle(P(ux1, uy1, ux1 + max(18, (ux2 - ux1) * upct / 100), uy2), radius=9 * k, fill=GREEN_SOFT)
            lab, tcol = f"Téléchargement… {upct} %", TEXT
        elif ust == "launching":
            d.rounded_rectangle(P(ux1, uy1, ux2, uy2), radius=9 * k, fill=GREEN_SOFT)
            lab, tcol = "Installation…", (13, 20, 16)
        elif ust == "error":
            d.rounded_rectangle(P(ux1, uy1, ux2, uy2), radius=9 * k, fill=(70, 40, 30) if state.get("hover") != "upd_btn" else (90, 50, 36))
            lab, tcol = "Échec · réessayer", ORANGE
        else:
            hv = state.get("hover") == "upd_btn"
            d.rounded_rectangle(P(ux1, uy1, ux2, uy2), radius=9 * k, fill=tuple(min(255, c + 18) for c in GREEN) if hv else GREEN)
            lab, tcol = "Mettre à jour", (13, 20, 16)
        fb2 = f("bold", 14)
        tb2 = fb2.getbbox("Ag")
        _text_center(d, (ux1 + ux2) / 2 * k, (uy1 + uy2) / 2 * k - (tb2[1] + tb2[3]) / 2, lab, fb2, tcol)
        layout.upd = (bx1, by1, bx2, by2)
        layout.upd_btn = (ux1, uy1, ux2, uy2) if ust in ("available", "error") else None

    # --- logo
    if logo is not None:
        lw = round(118 * k)
        lh = min(round(lw * logo.height / logo.width), round(92 * k))
        lw = round(lh * logo.width / logo.height)
        img.alpha_composite(logo.resize((lw, lh), Image.LANCZOS), (round(cx - lw / 2), round(24 * k + (92 * k - lh) / 2)))

    # --- titre : "FS25 " blanc + "Stream & Companion" vert
    ft = f("bold", 27)
    wa, wb = d.textlength(title_a, font=ft), d.textlength(title_b, font=ft)
    x0 = cx - (wa + wb) / 2
    d.text((x0, 124 * k), title_a, font=ft, fill=TEXT)
    d.text((x0 + wa, 124 * k), title_b, font=ft, fill=GREEN)

    # --- ligne d'etat : v1.4.1 · partie en cours ✓ · mod a jour ✓ · Wi-Fi ✓
    fs = f("regular", 14)
    parts = [("text", f"v{version}", MUTED)]
    for st, txt in status:
        parts.append(("text", "  ·  ", GREY))
        parts.append(("text", txt, {"ok": MUTED, "warn": ORANGE}.get(st, GREY)))
        if st == "ok":
            parts.append(("check", None, GREEN))
        elif st == "warn":
            parts.append(("text", " !", ORANGE))
    cs = 11 * k
    total = sum(cs + 6 * k if kind == "check" else d.textlength(t, font=fs) for kind, t, _c in parts)
    x, y = cx - total / 2, 165 * k
    for kind, t, col in parts:
        if kind == "check":
            _check(d, x + 5 * k, y + 5 * k, cs, col)
            x += cs + 6 * k
        else:
            d.text((x, y), t, font=fs, fill=col)
            x += d.textlength(t, font=fs)

    # --- 2 grandes cartes
    cards = {"stream": ("🎥", "Stream", "Ouvrir le mode édition / OBS", _draw_icon_camera),
             "compagnon": ("📱", "Compagnon", "Sur ce PC ou QR code\ntéléphone", _draw_icon_phone)}
    for key, (emo, name, sub, fallback) in cards.items():
        x1, y1, x2, y2 = layout.cards[key]
        hov = state.get("hover") == key
        if hov:                                              # halo vert
            glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(glow).rounded_rectangle(P(x1 - 2, y1 - 2, x2 + 2, y2 + 2), radius=26 * k,
                                                   outline=GREEN + (150,), width=round(8 * k))
            img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(8 * k)))
            d = ImageDraw.Draw(img)
        d.rounded_rectangle(P(x1, y1, x2, y2), radius=24 * k, fill=CARD_HOVER if hov else CARD,
                            outline=GREEN if hov else GREEN_SOFT, width=round(2.5 * k))
        mx = (x1 + x2) / 2 * k
        if key == "compagnon" and ph is not None:            # 1.4.4 : 2 boutons (sur ce PC / telephone)
            es = round(54 * k)
            e = emoji_image(emo, es)
            if e is not None:
                img.alpha_composite(e, (round(mx - e.width / 2), round(y1 * k + 20 * k + (es - e.height) / 2)))
            else:
                fallback(d, mx, y1 * k + 47 * k, es)
            _text_center(d, mx, y1 * k + 84 * k, name, f("bold", 25), TEXT)
            fbb = f("bold", 14)
            tbb = fbb.getbbox("Ag")
            for bkey, (by1, by2), label in (("comp_pc", (y1 + 128, y1 + 166), "🖥  Ouvrir sur ce PC"),
                                             ("comp_phone", (y1 + 176, y1 + 214),
                                              "📱  Téléphone / tablette")):
                bx1, bx2 = x1 + 18, x2 - 18
                bh = state.get("hover") == bkey
                if bkey == "comp_pc":
                    bfill = tuple(min(255, c + 18) for c in GREEN) if bh else GREEN
                    d.rounded_rectangle(P(bx1, by1, bx2, by2), radius=10 * k, fill=bfill)
                    tcol = (13, 20, 16)
                else:
                    bfill = (38, 64, 44) if (bh or ph.get("open")) else (26, 42, 31)
                    d.rounded_rectangle(P(bx1, by1, bx2, by2), radius=10 * k, fill=bfill, outline=GREEN_SOFT, width=round(1.5 * k))
                    tcol = (207, 233, 195)
                ei = emoji_image(label[0:2].strip(), round(15 * k))
                txt = label[3:] if ei is not None else label
                tw = d.textlength(txt, font=fbb) + ((15 + 7) * k if ei is not None else 0) + (16 * k if bkey == "comp_phone" else 0)
                tx = (bx1 + bx2) / 2 * k - tw / 2
                if ei is not None:
                    img.alpha_composite(ei, (round(tx), round(((by1 + by2) / 2 - 7.5) * k)))
                    tx += (15 + 7) * k
                d.text((tx, (by1 + by2) / 2 * k - (tbb[1] + tbb[3]) / 2), txt, font=fbb, fill=tcol)
                if bkey == "comp_phone":                       # petit triangle (ouvert = vers le haut)
                    tri = (tx + d.textlength(txt, font=fbb)) / k + 8
                    cy0 = (by1 + by2) / 2
                    pts = [(tri, cy0 + 3), (tri + 8, cy0 + 3), (tri + 4, cy0 - 2)] if ph.get("open") else \
                          [(tri, cy0 - 2), (tri + 8, cy0 - 2), (tri + 4, cy0 + 3)]
                    d.polygon([P(*p) for p in pts], fill=tcol)
                layout.comp_btns[bkey] = (bx1, by1, bx2, by2)
            continue
        es = round(70 * k)
        e = emoji_image(emo, es)
        if e is not None:
            img.alpha_composite(e, (round(mx - e.width / 2), round(y1 * k + 36 * k + (es - e.height) / 2)))
        else:
            fallback(d, mx, y1 * k + 71 * k, es)
        _text_center(d, mx, y1 * k + 122 * k, name, f("bold", 27), TEXT)
        ff = f("regular", 15)
        for i, line in enumerate(sub.split("\n")):
            _text_center(d, mx, y1 * k + 170 * k + i * 22 * k, line, ff, MUTED)

    # --- 1.4.4 : panneau "Telephone / tablette" (sous les cartes, quand il est ouvert)
    if dy:
        _draw_phone_panel(img, d, layout, ph.get("info") or {}, state.get("hover"), f, P, k)
        d = ImageDraw.Draw(img)

    # --- grande barre "Lancer le jeu" (choix de Luxo : sous les 2 cartes)
    layout.play = (layout.play[0], layout.play[1] + dy, layout.play[2], layout.play[3] + dy)
    x1, y1, x2, y2 = layout.play
    mode = state.get("play", "ready")                 # ready | launching | running
    hov = state.get("hover") == "play" and mode == "ready"
    if mode == "running":
        fill, tcol, label = CARD, MUTED, "✓  Partie en cours"
    elif mode == "launching":
        fill, tcol, label = GREEN_SOFT, (13, 20, 16), "Lancement du jeu…"
    else:
        fill, tcol, label = (tuple(min(255, c + 18) for c in GREEN) if hov else GREEN), (13, 20, 16), "▶  Lancer Farming Simulator 25"
    d.rounded_rectangle(P(x1, y1, x2, y2), radius=14 * k, fill=fill, outline=GREEN_SOFT if mode == "running" else None,
                        width=round(1.5 * k))
    fb = f("bold", 19)
    tb = fb.getbbox("Ag")
    _text_center(d, (x1 + x2) / 2 * k, (y1 + y2) / 2 * k - (tb[1] + tb[3]) / 2, label, fb, tcol)
    plat = state.get("platform")
    if mode != "running" and plat:
        fpl = f("semibold", 13)
        ptxt = plat if len(plat) <= 14 else plat[:13] + "…"
        pw = d.textlength(ptxt, font=fpl) / k
        px2 = x2 - 16
        px1 = px2 - pw - 14
        pcol = (13, 20, 16) if state.get("hover") != "platform" else (40, 70, 30)
        pb = fpl.getbbox("Ag")
        d.text((px1 * k, (y1 + y2) / 2 * k - (pb[1] + pb[3]) / 2), ptxt, font=fpl, fill=pcol)
        tri = px2 - 9
        cy0 = (y1 + y2) / 2
        d.polygon([P(tri, cy0 - 2), P(tri + 8, cy0 - 2), P(tri + 4, cy0 + 3)], fill=pcol)
        layout.platform = (px1 - 6, y1 + 6, x2 - 6, y2 - 6)

    # --- petits boutons en pilule
    fp = f("semibold", 15)
    pills = [("aide", "❓", "Aide"),
             ("discord", "💬", "Discord" if discord_ok else "Discord (bientôt)"),
             ("soutenir", "☕", "Soutenir")]
    pad, gap, ph, es = 16, 10, 36, 17
    widths = [pad * 2 + es + 7 + d.textlength(t, font=fp) / k for _key, _e, t in pills]
    x = W / 2 - (sum(widths) + gap * (len(pills) - 1)) / 2
    py = 532 + dy
    th = fp.getbbox("Ag")
    for (key, emo, txt), pw in zip(pills, widths):
        fill, border, tcol = PILLS[key]
        dim = key == "discord" and not discord_ok
        if state.get("hover") == key and not dim:
            fill = tuple(min(255, c + 16) for c in fill)
        if dim:
            fill, border, tcol = (24, 28, 44), (60, 66, 120), (130, 136, 170)
        d.rounded_rectangle(P(x, py, x + pw, py + ph), radius=ph / 2 * k, fill=fill, outline=border,
                            width=round(1.5 * k))
        e = emoji_image(emo, round(es * k))
        tx = (x + pad) * k
        if e is not None:
            img.alpha_composite(e, (round(tx), round((py + (ph - es) / 2) * k)))
        tx += (es + 7) * k
        d.text((tx, (py + ph / 2) * k - (th[1] + th[3]) / 2), txt, font=fp, fill=tcol)
        layout.pills[key] = (x, py, x + pw, py + ph)
        x += pw + gap

    # --- case "ne plus afficher" + choix
    fc, fcb = f("regular", 14), f("semibold", 14)
    lab = "Ne plus afficher au lancement (ouvrir directement : "
    choice = state.get("open", "Stream")
    bs = 15
    wl = d.textlength(lab, font=fc) / k
    wc = d.textlength(choice, font=fcb) / k
    wend = d.textlength(")", font=fc) / k
    total = bs + 8 + wl + wc + 4 + 9 + wend
    x = W / 2 - total / 2
    cy = 596 + dy
    on = state.get("skip")
    d.rounded_rectangle(P(x, cy - bs / 2, x + bs, cy + bs / 2), radius=4 * k,
                        fill=GREEN if on else None, outline=GREEN if on else MUTED, width=round(1.5 * k))
    if on:
        _check(d, (x + 3) * k, (cy - 5) * k, 9 * k, (16, 32, 10))
    layout.check = (x - 4, cy - 12, x + bs + 8 + wl, cy + 12)
    x += bs + 8
    tb = fc.getbbox("Ag")
    ty = cy * k - (tb[1] + tb[3]) / 2
    d.text((x * k, ty), lab, font=fc, fill=MUTED)
    x += wl
    ccol = (GREEN if state.get("hover") == "choice" else TEXT) if on else GREY
    d.text((x * k, ty), choice, font=fcb, fill=ccol)
    tri = x + wc + 4
    d.polygon([P(tri, cy - 2), P(tri + 8, cy - 2), P(tri + 4, cy + 3)], fill=ccol)
    layout.choice = (x - 4, cy - 12, tri + 12, cy + 12)
    d.text(((tri + 9) * k, ty), ")", font=fc, fill=MUTED)

    return img.resize((round(W * scale), round(HH * scale)), Image.LANCZOS), layout


def _qr_image(matrix, size):
    """QR code (matrice de 0/1) -> image PIL carree noir sur blanc, avec la marge blanche obligatoire."""
    n, q = len(matrix), 4
    mod = max(1, size // (n + 2 * q))
    im = Image.new("L", ((n + 2 * q) * mod,) * 2, 255)
    px = ImageDraw.Draw(im)
    for r, row in enumerate(matrix):
        for c, v in enumerate(row):
            if v:
                px.rectangle(((c + q) * mod, (r + q) * mod, (c + q + 1) * mod - 1, (r + q + 1) * mod - 1), fill=0)
    return im.resize((size, size), Image.NEAREST).convert("RGBA")


def _switch(d, P, k, x, y, on):
    d.rounded_rectangle(P(x, y, x + 40, y + 22), radius=11 * k, fill=GREEN if on else (58, 74, 63))
    cx = x + (29 if on else 11)
    d.ellipse(P(cx - 8, y + 3, cx + 8, y + 19), fill=(255, 255, 255))


def _draw_phone_panel(img, d, layout, info, hover, f, P, k):
    """QR code + Wi-Fi + code d'acces + pare-feu, dans le lanceur (1.4.4, choix A de Luxo)."""
    x1, y1, x2, y2 = 30, 466, 630, 466 + PANEL_H
    d.rounded_rectangle(P(x1, y1, x2, y2), radius=16 * k, fill=(18, 32, 24), outline=GREEN_SOFT, width=round(1.5 * k))
    lan, running, url, code = info.get("lan"), info.get("running"), info.get("url"), info.get("code")
    # QR code (ou message)
    qs = 176
    qx, qy = x1 + 16, y1 + (PANEL_H - qs) / 2
    d.rounded_rectangle(P(qx, qy, qx + qs, qy + qs), radius=12 * k, fill=(255, 255, 255))
    if lan and running and info.get("matrix"):
        q = _qr_image(info["matrix"], round((qs - 12) * k))
        img.alpha_composite(q, (round((qx + 6) * k), round((qy + 6) * k)))
    else:
        msg = ("Wi-Fi impossible :\n" + str(info.get("error"))[:40]) if (lan and info.get("error")) else \
              ("Démarrage…" if lan else "Active le Wi-Fi\npour afficher\nle QR code")
        fm = f("semibold", 13)
        for i, line in enumerate(msg.split("\n")):
            _text_center(d, (qx + qs / 2) * k, (qy + qs / 2 - 26 + i * 19) * k, line, fm, (70, 80, 74))
    # colonne de droite
    cx1, cx2 = qx + qs + 18, x2 - 16
    fb, fr, fs_ = f("bold", 15), f("regular", 13), f("regular", 12)
    y = y1 + 16
    d.text((cx1 * k, y * k), "Ouvert au Wi-Fi de la maison", font=fb, fill=TEXT)
    _switch(d, P, k, cx2 - 40, y - 2, lan)
    layout.phone["ph_lan"] = (cx1 - 4, y - 6, cx2 + 4, y + 24)
    y += 30
    if lan and running and url:
        d.text((cx1 * k, y * k), "Scanne le QR code (même Wi-Fi), ou tape :", font=fr, fill=MUTED)
        y += 19
        fu = f("semibold", 14)
        d.text((cx1 * k, y * k), url.replace("http://", ""), font=fu, fill=GREEN)
    else:
        d.text((cx1 * k, y * k), "Pour ouvrir le compagnon sur ton téléphone", font=fr, fill=MUTED)
        y += 19
        d.text((cx1 * k, y * k), "ou ta tablette, par le Wi-Fi de la maison.", font=fr, fill=MUTED)
    y += 32
    d.text((cx1 * k, y * k), "Protéger par un code", font=fb, fill=TEXT)
    _switch(d, P, k, cx2 - 40, y - 2, bool(code))
    layout.phone["ph_code"] = (cx1 - 4, y - 6, cx2 + 4, y + 24)
    y += 24
    if code:
        d.text((cx1 * k, y * k), f"Code : {code}  (à taper une fois sur chaque appareil)", font=fs_, fill=GREEN)
    else:
        d.text((cx1 * k, y * k), "Facultatif : empêche les autres personnes de ton", font=fs_, fill=MUTED)
        y += 16
        d.text((cx1 * k, y * k), "Wi-Fi d'ouvrir le compagnon.", font=fs_, fill=MUTED)
    # boutons du bas
    by1, by2 = y2 - 44, y2 - 14
    fbt = f("semibold", 13)
    tb = fbt.getbbox("Ag")
    bx = cx1
    for key, label in (("ph_fw", "Autoriser dans le pare-feu"), ("ph_copy", "Copier l'adresse")):
        if key == "ph_copy" and not (lan and running and url):
            continue
        bw = d.textlength(label, font=fbt) / k + 24
        hv = hover == key
        d.rounded_rectangle(P(bx, by1, bx + bw, by2), radius=8 * k, fill=(40, 62, 46) if hv else (28, 44, 33),
                            outline=(70, 96, 78), width=round(1 * k))
        d.text(((bx + 12) * k, (by1 + by2) / 2 * k - (tb[1] + tb[3]) / 2), label, font=fbt, fill=TEXT)
        layout.phone[key] = (bx, by1, bx + bw, by2)
        bx += bw + 10


# ------------------------------------------------------------------ la fenetre
def _dpi_aware():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def _dark_title_bar(win):
    """Barre de titre sombre (Windows 10 20H1+ / 11)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


def _hex(c):
    return "#%02x%02x%02x" % c


class Accueil:
    def __init__(self, *, title, version, icon_path=None, logo_path=None, font_dir=None,
                 get_prefs, set_prefs, status, open_stream, open_companion,
                 discord_url="", donate_url="", game=None, update=None, phone=None):
        self.title = title
        self.version = version
        self.icon_path = icon_path
        self.logo_path = logo_path
        self.fonts = Fonts(font_dir)
        self.get_prefs = get_prefs          # () -> {"skip": bool, "open": "stream"|"compagnon"|"les2"|"rien"}
        self.set_prefs = set_prefs          # (dict) -> None
        self.status = status                # () -> [(etat "ok"|"warn"|"off", texte), ...]
        self.open_stream = open_stream
        self.open_companion = open_companion
        self.discord_url = discord_url
        self.donate_url = donate_url
        self.game = game                    # {"get": () -> (cle, libelle)|None, "set": (cle, chemin), "launch": () -> (ok, message), "choices": [(cle, libelle)]}
        self.update = update                # {"get": () -> {"status","version","notes","pct","error"}, "start": () -> bool}
        self.phone = phone                  # 1.4.4 : {"info", "set_lan", "set_code", "firewall"} (panneau Telephone)
        self._phone_open = False
        self._ph_info = {}
        self.upd_win = None
        self._launch_at = 0.0
        self._q = queue.Queue()
        self.root = None
        self.win = None
        self.help_win = None
        self.ready = threading.Event()
        self.scale = 1.0
        self._logo = None
        self._frames = {}

    # ---------------------------------------------------------------- depuis n'importe quel fil
    def show(self):
        self._q.put(("show", None, None))

    def quit(self):
        self._q.put(("quit", None, None))

    def call(self, fn, timeout=None):
        """Execute fn() dans le fil de la fenetre et renvoie son resultat (pour les fenetres de choix)."""
        if threading.current_thread() is threading.main_thread() and self.root is not None:
            return fn()
        box, done = {}, threading.Event()
        self._q.put(("call", (fn, box), done))
        done.wait(timeout)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    # ---------------------------------------------------------------- fil principal
    def run(self, show_now=True):
        _dpi_aware()
        self.root = tk.Tk()
        self.root.withdraw()                # fenetre racine invisible : elle sert de parent aux dialogues
        self._set_icon(self.root)
        try:
            self.scale = max(1.0, min(3.0, self.root.winfo_fpixels("1i") / 96.0))
        except Exception:
            self.scale = 1.0
        if self.logo_path:
            try:
                self._logo = Image.open(self.logo_path).convert("RGBA")
            except Exception:
                self._logo = None
        self.ready.set()
        if show_now:
            self._show()
        self.root.after(100, self._poll)
        self.root.mainloop()

    def _poll(self):
        try:
            while True:
                what, arg, done = self._q.get_nowait()
                if what == "show":
                    self._show()
                elif what == "quit":
                    self.root.quit()
                    return
                elif what == "call":
                    fn, box = arg
                    try:
                        box["result"] = fn()
                    except Exception as exc:
                        box["error"] = exc
                    done.set()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _set_icon(self, w):
        if self.icon_path:
            try:
                w.iconbitmap(default=str(self.icon_path)) if w is self.root else w.iconbitmap(str(self.icon_path))
            except Exception:
                pass

    def _show(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self._flash_top()
            return
        self._build()

    def _flash_top(self):
        try:
            self.win.attributes("-topmost", True)
            self.win.after(400, lambda: self.win.attributes("-topmost", False))
        except Exception:
            pass

    # ---------------------------------------------------------------- construction
    def _play_mode(self):
        if any(st == "ok" and "partie en cours" in txt for st, txt in self._status_now):
            return "running"
        import time as _t
        if _t.time() - self._launch_at < 90:
            return "launching"
        return "ready"

    def _platform_label(self):
        try:
            g = self.game["get"]() if self.game else None
        except Exception:
            g = None
        return g[1] if g else None

    def _upd(self):
        try:
            u = self.update["get"]() if self.update else None
        except Exception:
            u = None
        if not u or u.get("status") in (None, "none"):
            return None
        return (u["status"], u.get("version") or "?", int(u.get("pct") or 0))

    def _state(self):
        return {"upd": self._upd(), "hover": self._hover, "skip": self._prefs["skip"],
                "open": {"stream": "Stream", "compagnon": "Compagnon", "les2": "Les 2"}.get(self._prefs["open"], "Rien"),
                "play": self._play_mode(), "platform": self._platform_label(),
                "phone": ({"open": self._phone_open, "info": self._ph_info} if self.phone else None)}

    def _redraw(self):
        st = self._state()
        pi = self._ph_info
        key = (st["hover"], st["skip"], st["open"], tuple(self._status_now), st["play"], st["platform"], st["upd"],
               self._phone_open, pi.get("lan"), pi.get("running"), pi.get("url"), pi.get("code"), pi.get("error"))
        if key not in self._frames:
            if len(self._frames) > 24:
                self._frames.clear()
            img, layout = render(st, title_a="FS25 ", title_b=self.title.replace("FS25 ", "", 1),
                                 version=self.version, status=self._status_now, logo=self._logo,
                                 fonts=self.fonts, scale=self.scale, discord_ok=bool(self.discord_url))
            self._frames[key] = (ImageTk.PhotoImage(img, master=self.win), layout)
        photo, self._layout = self._frames[key]
        ch = photo.height()
        if int(self.canvas.cget("height")) != ch:          # panneau Telephone ouvert / ferme : la fenetre grandit
            self.canvas.configure(height=ch)
        self.canvas.itemconfigure(self._img_id, image=photo)
        self._photo = photo

    def _build(self):
        self._prefs = dict(self.get_prefs())
        self._hover = None
        self._status_now = self._read_status()
        self._frames = {}
        w = self.win = tk.Toplevel(self.root, bg=_hex(BG_BOTTOM))
        w.title(self.title)
        w.resizable(False, False)
        self._set_icon(w)
        w.protocol("WM_DELETE_WINDOW", self._close)
        w.bind("<Escape>", lambda e: self._close())
        cw, ch = round(W * self.scale), round(H * self.scale)
        self.canvas = tk.Canvas(w, width=cw, height=ch, bd=0, highlightthickness=0, bg=_hex(BG_BOTTOM))
        self.canvas.pack()
        self._img_id = self.canvas.create_image(0, 0, anchor="nw")
        self._redraw()
        self.canvas.bind("<Motion>", self._on_move)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<Button-1>", self._on_click)
        _dark_title_bar(w)

        w.update_idletasks()
        x = (w.winfo_screenwidth() - w.winfo_reqwidth()) // 2
        y = (w.winfo_screenheight() - w.winfo_reqheight()) // 3
        w.geometry(f"+{max(0, x)}+{max(0, y)}")
        w.deiconify()
        w.lift()
        w.focus_force()
        self._flash_top()
        w.after(2000, self._tick)

    def _close(self):
        if self.help_win is not None and self.help_win.winfo_exists():
            self.help_win.destroy()
        if self.upd_win is not None and self.upd_win.winfo_exists():
            self.upd_win.destroy()
        self.win.destroy()
        self.win = None
        self._frames = {}

    def _read_status(self):
        try:
            return list(self.status())
        except Exception:
            return [("warn", "état indisponible")]

    def _tick(self):
        if self.win is None or not self.win.winfo_exists():
            return
        now = self._read_status()
        if self._phone_open:
            self._refresh_phone(redraw=True)
        u = self._upd()
        if now != self._status_now or self._launch_at or u != getattr(self, "_upd_last", None):
            self._status_now = now
            self._upd_last = u
            self._redraw()
        self.win.after(400 if u and u[0] in ("downloading", "launching") else 2000, self._tick)

    # ---------------------------------------------------------------- souris
    def _hit(self, ev):
        x, y = ev.x / self.scale, ev.y / self.scale
        L = self._layout
        if L.upd_btn and L.upd_btn[0] <= x <= L.upd_btn[2] and L.upd_btn[1] <= y <= L.upd_btn[3]:
            return "upd_btn"
        if L.upd and L.upd[0] <= x <= L.upd[2] and L.upd[1] <= y <= L.upd[3]:
            return "upd"
        for key, (x1, y1, x2, y2) in list(L.comp_btns.items()) + list(L.phone.items()):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        for key, (x1, y1, x2, y2) in list(L.cards.items()) + list(L.pills.items()):
            if x1 <= x <= x2 and y1 <= y <= y2:
                if key == "discord" and not self.discord_url:
                    return None
                return key
        if self.game and self._play_mode() != "running":
            if L.platform and L.platform[0] <= x <= L.platform[2] and L.platform[1] <= y <= L.platform[3]:
                return "platform"
            x1, y1, x2, y2 = L.play
            if x1 <= x <= x2 and y1 <= y <= y2:
                return "play" if self._play_mode() == "ready" else None
        for key, box in (("choice", L.choice), ("check", L.check)):
            if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                if key == "choice" and not self._prefs["skip"]:
                    return "check"
                return key
        return None

    def _set_hover(self, key):
        if key != self._hover:
            self._hover = key
            self.canvas.configure(cursor="hand2" if key else "")
            self._redraw()

    def _on_move(self, ev):
        self._set_hover(self._hit(ev))

    def _on_click(self, ev):
        key = self._hit(ev)
        if key == "upd_btn":
            self._start_update()
        elif key == "upd":
            self._show_update()
        elif key == "stream":
            self.open_stream()
        elif key in ("compagnon", "comp_pc"):
            self.open_companion()
        elif key == "comp_phone":
            self._phone_open = not self._phone_open
            if self._phone_open:
                self._refresh_phone(redraw=False)
            self._redraw()
        elif key in ("ph_lan", "ph_code", "ph_fw", "ph_copy"):
            self._phone_action(key)
        elif key == "aide":
            self._show_help()
        elif key == "discord" and self.discord_url:
            webbrowser.open(self.discord_url)
        elif key == "soutenir" and self.donate_url:
            webbrowser.open(self.donate_url)
        elif key == "play":
            if self._platform_label() is None:
                self._platform_menu(ev, then_launch=True)
            else:
                self._launch()
        elif key == "platform":
            self._platform_menu(ev, then_launch=False)
        elif key == "check":
            self._prefs["skip"] = not self._prefs["skip"]
            self._save_prefs()
        elif key == "choice":
            m = tk.Menu(self.win, tearoff=0, bg=_hex(CARD), fg=_hex(TEXT), activebackground=_hex(GREEN_SOFT),
                        activeforeground="#0d1a08", bd=0, font=("Segoe UI", 10))
            for label, val in (("Stream", "stream"), ("Compagnon", "compagnon"), ("Les 2 (Stream + Compagnon)", "les2"), ("Rien", "rien")):
                m.add_command(label=label, command=lambda v=val: self._set_choice(v))
            m.tk_popup(ev.x_root, ev.y_root + 8)

    # ---------------------------------------------------------------- telephone / tablette (1.4.4)
    def _refresh_phone(self, redraw=True):
        try:
            info = dict(self.phone["info"]())
        except Exception as exc:
            info = {"error": str(exc)}
        changed = {k: v for k, v in info.items() if k != "matrix"} != {k: v for k, v in self._ph_info.items() if k != "matrix"}
        self._ph_info = info
        if changed and redraw:
            self._redraw()

    def _phone_action(self, key):
        from tkinter import messagebox, simpledialog
        info = self._ph_info or {}
        try:
            if key == "ph_lan":
                self.phone["set_lan"](not info.get("lan"))
            elif key == "ph_code":
                if info.get("code"):
                    self.phone["set_code"]("")
                else:
                    import random
                    c = simpledialog.askstring(self.title, "Choisis un code de 4 à 8 chiffres.\n"
                                               "Il sera demandé une fois sur chaque téléphone / tablette :",
                                               initialvalue=str(random.randint(1000, 9999)), parent=self.win)
                    if c is None:
                        return
                    self.phone["set_code"](c)
            elif key == "ph_fw":
                ok, msg = self.phone["firewall"]()
                (messagebox.showinfo if ok else messagebox.showwarning)(
                    self.title, ("Le pare-feu Windows laisse maintenant passer le compagnon." if ok else
                                 "Le pare-feu n'a pas été modifié : " + str(msg)), parent=self.win)
            elif key == "ph_copy" and info.get("url"):
                self.win.clipboard_clear()
                self.win.clipboard_append(info["url"])
        except Exception as exc:
            messagebox.showwarning(self.title, str(exc), parent=self.win)
        self._refresh_phone(redraw=False)
        self._redraw()

    # ---------------------------------------------------------------- lancer le jeu
    def _platform_menu(self, ev, then_launch):
        m = tk.Menu(self.win, tearoff=0, bg=_hex(CARD), fg=_hex(TEXT), activebackground=_hex(GREEN_SOFT),
                    activeforeground="#0d1a08", bd=0, font=("Segoe UI", 10))
        m.add_command(label="Où as-tu acheté le jeu ?", state="disabled")
        for key, label in self.game.get("choices", []):
            m.add_command(label=label, command=lambda k=key: self._pick_platform(k, then_launch))
        m.add_separator()
        m.add_command(label="Autre : choisir le fichier du jeu (.exe)…", command=lambda: self._pick_platform("exe", then_launch))
        m.tk_popup(ev.x_root, ev.y_root + 8)

    def _pick_platform(self, key, then_launch):
        path = None
        if key == "exe":
            from tkinter import filedialog
            path = filedialog.askopenfilename(parent=self.win, title="Choisis le fichier du jeu (FarmingSimulator2025.exe)",
                                              filetypes=[("Programme", "*.exe")])
            if not path:
                return
        try:
            self.game["set"](key, path)
        except Exception as exc:
            print("[Accueil] plateforme non enregistree :", exc)
        self._frames = {}
        self._redraw()
        if then_launch:
            self._launch()

    def _launch(self):
        try:
            ok, msg = self.game["launch"]()
        except Exception as exc:
            ok, msg = False, str(exc)
        if ok:
            import time as _t
            self._launch_at = _t.time()
            self._redraw()
            return
        from tkinter import messagebox
        if msg == "need-exe":
            if messagebox.askyesno(self.title, "Je n'ai pas trouvé le jeu sur ce PC.\nVeux-tu choisir le fichier FarmingSimulator2025.exe ?", parent=self.win):
                self._pick_platform("exe", True)
            return
        messagebox.showwarning(self.title, "Le jeu n'a pas pu être lancé :\n" + str(msg) +
                               "\n\nTu peux changer de plateforme avec le petit menu à droite de la barre verte.", parent=self.win)

    def _set_choice(self, val):
        self._prefs["open"] = val
        self._save_prefs()

    def _save_prefs(self):
        try:
            self.set_prefs({"skip": bool(self._prefs["skip"]), "open": self._prefs["open"]})
        except Exception as exc:
            print("[Accueil] reglage non enregistre :", exc)
        self._redraw()

    # ---------------------------------------------------------------- mise a jour
    def _start_update(self):
        # 1.4.4 : si la nouvelle version change le mod, on previent avant (serveur multi a mettre a jour aussi)
        try:
            u = self.update["get"]() if self.update else {}
        except Exception:
            u = {}
        if u and u.get("mod"):
            from tkinter import messagebox
            ok = messagebox.askyesno(
                self.title,
                f"Cette version change le mod ({u['mod']}).\n\n"
                "En multi, le serveur doit avoir la même version, sinon tu ne pourras pas le rejoindre.\n"
                "Vérifie avec l'admin de ton serveur qu'il est (ou va être) mis à jour. En solo, aucun souci.\n\n"
                "Oui = mettre à jour maintenant\nNon = plus tard (le cadre « Nouvelle version » reviendra au prochain lancement)",
                parent=self.win)
            if not ok:
                return
        try:
            self.update["start"]()
        except Exception as exc:
            print("[Accueil] mise a jour impossible :", exc)
        if self.upd_win is not None and self.upd_win.winfo_exists():
            self.upd_win.destroy()
        self._frames = {}
        self._redraw()
        if self.win is not None:
            self.win.after(300, self._tick)

    def _show_update(self):
        if self.upd_win is not None and self.upd_win.winfo_exists():
            self.upd_win.lift()
            self.upd_win.focus_force()
            return
        try:
            u = self.update["get"]()
        except Exception:
            return
        bg, card, fg, muted, green = _hex(BG_TOP), _hex(CARD_HOVER), _hex(TEXT), _hex(MUTED), _hex(GREEN)
        font = "Segoe UI"
        h = self.upd_win = tk.Toplevel(self.win or self.root, bg=bg)
        h.title("Nouvelle version - " + self.title)
        h.resizable(False, False)
        self._set_icon(h)
        h.bind("<Escape>", lambda e: h.destroy())
        top = tk.Frame(h, bg=bg, padx=22, pady=16)
        top.pack(fill="x")
        tk.Label(top, text=f"🔄  Version {u.get('version') or '?'} disponible", bg=bg, fg=green, font=(font, 13, "bold")).pack(anchor="w")
        tk.Label(top, text=f"Tu as la version {self.version}. Tes réglages sont gardés.", bg=bg, fg=muted, font=(font, 9)).pack(anchor="w", pady=(2, 0))
        body = tk.Frame(h, bg=bg, padx=22)
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, width=62, height=16, bg=card, fg=fg, bd=0, highlightthickness=0, wrap="word",
                      font=(font, 10), padx=12, pady=10, spacing1=1, spacing3=3, cursor="arrow")
        txt.pack(fill="both", expand=True)
        txt.insert("end", u.get("notes") or "Pas de notes pour cette version.")
        txt.configure(state="disabled")
        foot = tk.Frame(h, bg=bg, padx=22, pady=16)
        foot.pack(fill="x")
        tk.Label(foot, text="L'application se ferme, s'installe puis se relance toute seule.", bg=bg, fg=muted, font=(font, 9)).pack(side="left")
        go = tk.Label(foot, text="Mettre à jour", bg=green, fg="#10200a", font=(font, 10, "bold"), padx=16, pady=6, cursor="hand2")
        go.pack(side="right")
        go.bind("<Button-1>", lambda _e: self._start_update())
        later = tk.Label(foot, text="Plus tard", bg=card, fg=fg, font=(font, 10), padx=14, pady=6, cursor="hand2")
        later.pack(side="right", padx=(0, 8))
        later.bind("<Button-1>", lambda _e: h.destroy())
        _dark_title_bar(h)
        h.update_idletasks()
        if self.win is not None and self.win.winfo_exists():
            h.geometry(f"+{self.win.winfo_rootx() + 40}+{self.win.winfo_rooty() + 40}")
        h.lift()
        h.focus_force()

    # ---------------------------------------------------------------- aide
    def _show_help(self):
        if self.help_win is not None and self.help_win.winfo_exists():
            self.help_win.lift()
            self.help_win.focus_force()
            return
        bg, card, fg, muted, green = _hex(BG_TOP), _hex(CARD_HOVER), _hex(TEXT), _hex(MUTED), _hex(GREEN)
        font = "Segoe UI"
        h = self.help_win = tk.Toplevel(self.win or self.root, bg=bg)
        h.title("Aide - " + self.title)
        h.resizable(False, False)
        self._set_icon(h)
        h.bind("<Escape>", lambda e: h.destroy())

        tabs = tk.Frame(h, bg=bg, padx=22, pady=18)
        tabs.pack(fill="x")
        body = tk.Frame(h, bg=bg, padx=22)
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, width=66, height=30, bg=bg, fg=fg, bd=0, highlightthickness=0, wrap="word",
                      font=(font, 10), padx=2, pady=4, spacing1=1, spacing3=3, cursor="arrow")
        txt.pack(fill="both", expand=True)
        txt.tag_configure("h", font=(font, 11, "bold"), foreground=green, spacing1=12, spacing3=4)
        buttons = {}

        def select(name):
            for n, b in buttons.items():
                b.configure(bg=green if n == name else card, fg="#10200a" if n == name else fg)
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            for title, para in AIDE[name]:
                txt.insert("end", title + "\n", "h")
                txt.insert("end", para + "\n")
            txt.configure(state="disabled")

        for name in AIDE:
            b = tk.Label(tabs, text=name, bg=card, fg=fg, font=(font, 10, "bold"), padx=18, pady=7, cursor="hand2")
            b.pack(side="left", padx=(0, 8))
            b.bind("<Button-1>", lambda _e, n=name: select(n))
            buttons[name] = b

        foot = tk.Frame(h, bg=bg, padx=22, pady=16)
        foot.pack(fill="x")
        tk.Label(foot, text="Un problème ? Clic droit sur l'icône > Diagnostic, puis envoie le rapport.",
                 bg=bg, fg=muted, font=(font, 9)).pack(side="left")
        close = tk.Label(foot, text="Fermer", bg=card, fg=fg, font=(font, 10), padx=16, pady=6, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: h.destroy())
        select("Stream")
        _dark_title_bar(h)

        h.update_idletasks()
        if self.win is not None and self.win.winfo_exists():
            h.geometry(f"+{self.win.winfo_rootx() + 40}+{self.win.winfo_rooty() + 40}")
        h.lift()
        h.focus_force()
