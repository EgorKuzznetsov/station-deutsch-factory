"""Видеодвижок: рисует кадры 1080×1920 (Pillow) и кодирует в MP4 (FFmpeg).

Без нейросетей: всё — программная анимация (easing, pop-эффекты, таймер, конфетти, переходы).
"""
import math
import os
import random
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .themes import ARTICLE_COLORS, hex2rgb

W, H, FPS = 1080, 1920, 30
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "fonts")
WEIGHTS = {"regular": "Inter-Regular.otf", "medium": "Inter-Medium.otf", "semibold": "Inter-SemiBold.otf",
           "bold": "Inter-Bold.otf", "extrabold": "Inter-ExtraBold.otf", "black": "Inter-Black.otf"}
_FONTS = {}

# ---------------------------------------------------------------- утилиты


def font(size, weight="bold"):
    size = max(8, int(round(size)))
    k = (size, weight)
    if k not in _FONTS:
        _FONTS[k] = ImageFont.truetype(os.path.join(FONT_DIR, WEIGHTS[weight]), size)
    return _FONTS[k]


def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = clamp(t)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_back(t, s=1.70158):
    t = clamp(t) - 1
    return t * t * ((s + 1) * t + s) + 1


def prog(t, start, dur):
    return clamp((t - start) / dur) if dur > 0 else (1.0 if t >= start else 0.0)


def mix(c1, c2, k):
    return tuple(int(a + (b - a) * k) for a, b in zip(c1, c2))


def wrap(text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if fnt.getlength(test) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(text, max_w, max_size, min_size=34, weight="bold", max_lines=3):
    size = max_size
    while size >= min_size:
        f = font(size, weight)
        lines = wrap(text, f, max_w)
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            return size, lines
        size -= 4
    f = font(min_size, weight)
    return min_size, wrap(text, f, max_w)


# ---------------------------------------------------------------- контекст рисования

class Ctx:
    def __init__(self, img, theme):
        self.img = img
        self.d = ImageDraw.Draw(img, "RGBA")
        self.th = theme
        self.ga = 1.0   # глобальная прозрачность сцены (переходы)
        self.dy = 0     # глобальный сдвиг сцены по Y
        self.c = {k: hex2rgb(v) for k, v in theme.items() if isinstance(v, str) and v.startswith("#")}

    def col(self, name_or_rgb):
        return self.c[name_or_rgb] if isinstance(name_or_rgb, str) else name_or_rgb

    def rect(self, x0, y0, x1, y1, r=36, fill="card", alpha=255, outline=None, width=0, oalpha=255):
        a = int(alpha * self.ga)
        o = None
        if outline is not None:
            o = self.col(outline) + (int(oalpha * self.ga),)
        if a <= 0 and o is None:
            return
        self.d.rounded_rectangle([x0, y0 + self.dy, x1, y1 + self.dy], radius=r,
                                 fill=self.col(fill) + (a,) if a > 0 else None, outline=o, width=width)

    def text(self, x, y, s, size, weight="bold", color="text", alpha=255, anchor="mm"):
        a = int(alpha * self.ga)
        if a <= 0 or not s:
            return
        self.d.text((x, y + self.dy), s, font=font(size, weight), fill=self.col(color) + (a,), anchor=anchor)

    def lines(self, cx, cy, lines, size, weight="bold", color="text", alpha=255, gap=1.18):
        lh = size * gap
        top = cy - lh * (len(lines) - 1) / 2
        for i, l in enumerate(lines):
            self.text(cx, top + i * lh, l, size, weight, color, alpha)

    def circle(self, cx, cy, r, fill, alpha=255):
        a = int(alpha * self.ga)
        if a > 0:
            self.d.ellipse([cx - r, cy - r + self.dy, cx + r, cy + r + self.dy], fill=self.col(fill) + (a,))

    def poly(self, pts, fill, alpha=255):
        a = int(alpha * self.ga)
        if a > 0:
            self.d.polygon([(x, y + self.dy) for x, y in pts], fill=self.col(fill) + (a,))

    def line(self, pts, fill, width, alpha=255):
        a = int(alpha * self.ga)
        if a > 0:
            self.d.line([(x, y + self.dy) for x, y in pts], fill=self.col(fill) + (a,), width=width, joint="curve")

    def arc(self, cx, cy, r, start, end, fill, width, alpha=255):
        a = int(alpha * self.ga)
        if a > 0 and end > start:
            self.d.arc([cx - r, cy - r + self.dy, cx + r, cy + r + self.dy], start, end,
                       fill=self.col(fill) + (a,), width=width)

    def text_on_pill(self, cx, cy, s, size, fill, color, alpha=255, padx=34, pady=18, weight="extrabold"):
        f = font(size, weight)
        w = f.getlength(s)
        h = size + pady * 2
        self.rect(cx - w / 2 - padx, cy - h / 2, cx + w / 2 + padx, cy + h / 2, r=h / 2, fill=fill, alpha=alpha)
        self.text(cx, cy, s, size, weight, color, alpha)
        return w + padx * 2


def check_icon(ctx, cx, cy, s, color, alpha=255):
    ctx.line([(cx - s * 0.45, cy), (cx - s * 0.12, cy + s * 0.33), (cx + s * 0.5, cy - s * 0.35)],
             color, max(4, int(s * 0.16)), alpha)


def cross_icon(ctx, cx, cy, s, color, alpha=255):
    w = max(4, int(s * 0.16))
    ctx.line([(cx - s * 0.38, cy - s * 0.38), (cx + s * 0.38, cy + s * 0.38)], color, w, alpha)
    ctx.line([(cx + s * 0.38, cy - s * 0.38), (cx - s * 0.38, cy + s * 0.38)], color, w, alpha)


# ---------------------------------------------------------------- фон

class Background:
    def __init__(self, theme, seed):
        top, bot = hex2rgb(theme["bg"][0]), hex2rgb(theme["bg"][1])
        grad = Image.new("RGB", (1, 256))
        for y in range(256):
            grad.putpixel((0, y), mix(top, bot, ease_in_out(y / 255)))
        self.base = grad.resize((W, H), Image.BILINEAR)
        # точечная сетка для «дорогого» вида
        dots = ImageDraw.Draw(self.base, "RGBA")
        txt = hex2rgb(theme["text"])
        for y in range(40, H, 60):
            for x in range(40, W, 60):
                dots.ellipse([x - 2, y - 2, x + 2, y + 2], fill=txt + (14,))
        rnd = random.Random(seed)
        self.blobs = []
        for i, c in enumerate(theme["blobs"]):
            size = rnd.randint(620, 860)
            spr = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(spr).ellipse([size * 0.3, size * 0.3, size * 0.7, size * 0.7],
                                        fill=hex2rgb(c) + (150,))
            spr = spr.filter(ImageFilter.GaussianBlur(size * 0.09))
            self.blobs.append({"spr": spr, "x": rnd.uniform(0.1, 0.9) * W, "y": rnd.uniform(0.15, 0.85) * H,
                               "ax": rnd.uniform(80, 200), "ay": rnd.uniform(100, 260),
                               "sp": rnd.uniform(0.15, 0.35), "ph": rnd.uniform(0, 6.28)})

    def frame(self, t):
        img = self.base.copy()
        for b in self.blobs:
            s = b["spr"]
            x = b["x"] + math.sin(t * b["sp"] * 2 + b["ph"]) * b["ax"] - s.width / 2
            y = b["y"] + math.cos(t * b["sp"] * 1.6 + b["ph"]) * b["ay"] - s.height / 2
            img.paste(s, (int(x), int(y)), s)
        return img


# ---------------------------------------------------------------- общие элементы

def header(ctx, spec, t, total, brand):
    ctx.text(90, 200, brand.upper(), 30, "extrabold", "text", 230, anchor="lm")
    lv = spec.get("level")
    if lv:
        ctx.text_on_pill(W - 150, 200, lv, 30, "accent", "accent_text", 255, padx=26, pady=12)
    # прогресс всего ролика
    ctx.rect(90, 252, W - 90, 262, r=5, fill="text", alpha=40)
    ctx.rect(90, 252, 90 + (W - 180) * clamp(t / total), 262, r=5, fill="accent", alpha=255)


def timer(ctx, cx, cy, r, t, think, alpha=255):
    left = max(0.0, think - t)
    k = clamp(t / think)
    urgent = left <= 3 and left > 0
    pulse = 1 + (0.07 * math.sin(t * 14) if urgent else 0)
    ctx.circle(cx, cy, r * pulse, "card", 30 * alpha / 255)
    ctx.arc(cx, cy, r * pulse, -90, -90 + 360 * (1 - k), "bad" if urgent else "accent", 12, alpha)
    ctx.text(cx, cy, str(int(math.ceil(left))) if left > 0 else "0", r * 0.85, "black",
             "bad" if urgent else "text", alpha)


def confetti(ctx, cx, cy, t, seed, colors):
    if t <= 0 or t > 1.6:
        return
    rnd = random.Random(seed)
    for i in range(46):
        ang = rnd.uniform(-math.pi, 0)
        sp = rnd.uniform(500, 1100)
        vx, vy = math.cos(ang) * sp, math.sin(ang) * sp
        x = cx + vx * t
        y = cy + vy * t + 1300 * t * t
        rot = rnd.uniform(0, 6.28) + t * rnd.uniform(4, 12)
        s = rnd.uniform(9, 16)
        a = 255 * (1 - clamp((t - 0.9) / 0.7))
        pts = [(x + math.cos(rot + k * math.pi / 2) * s * (1 if k % 2 == 0 else 0.5),
                y + math.sin(rot + k * math.pi / 2) * s * (1 if k % 2 == 0 else 0.5)) for k in range(4)]
        ctx.poly(pts, hex2rgb(rnd.choice(colors)), a)


def scene_alpha(lt, dur):
    """Переход: появление 0.35с (снизу вверх), исчезновение 0.25с."""
    a_in = ease_out(lt / 0.35)
    a_out = 1 - ease_in_out((lt - (dur - 0.25)) / 0.25) if lt > dur - 0.25 else 1
    dy = (1 - a_in) * 70 - (1 - a_out) * 50
    return min(a_in, a_out), dy


# ---------------------------------------------------------------- сцены

def s_hook(ctx, sc, lt):
    p = ease_back(prog(lt, 0.0, 0.45))
    ctx.text_on_pill(W / 2, 700 - (1 - p) * 60, sc["badge"], 40, "accent", "accent_text", 255 * clamp(p))
    size, lines = fit(sc["title"], 900, 112, 60, "black", 4)
    lh = size * 1.12
    top = 960 - lh * (len(lines) - 1) / 2
    for i, l in enumerate(lines):
        k = ease_back(prog(lt, 0.15 + i * 0.12, 0.45))
        ctx.text(W / 2, top + i * lh + (1 - k) * 50, l, size * (0.85 + 0.15 * clamp(k)), "black", "text",
                 255 * clamp(k * 1.4))
    k = ease_out(prog(lt, 0.6, 0.5))
    y = top + lh * len(lines) - lh / 2 + 50
    ctx.rect(W / 2 - 160 * k, y, W / 2 + 160 * k, y + 10, r=5, fill="accent", alpha=255)


def big_card(ctx, sc, lt, top=330, height=560):
    """Карточка с главным текстом задания."""
    ctx.rect(80, top, W - 80, top + height, r=48, fill="card", alpha=ctx.th["card_alpha"],
             outline="text", width=2, oalpha=40)


def s_mcq(ctx, sc, lt, seed):
    think, opts, ans, style = sc["think"], sc["options"], sc["answer"], sc["style"]
    rev = lt - think
    # строка: счётчик + вопрос + таймер
    if sc.get("counter"):
        ctx.text(110, 370, sc["counter"], 40, "black", "accent", 255, anchor="lm")
    ctx.text(W / 2, 370, sc["prompt"], 44, "extrabold", "muted", 255)
    if rev < 0:
        timer(ctx, W - 150, 370, 52, lt, think)
    else:
        k = ease_back(prog(rev, 0, 0.4))
        ctx.circle(W - 150, 370, 52 * k, "good")
        check_icon(ctx, W - 150, 372, 50 * k, (255, 255, 255))

    # главная карточка
    ct, chh = 450, 470
    big_card(ctx, sc, lt, ct, chh)
    pop = ease_back(prog(lt, 0.1, 0.5))
    cy = ct + chh / 2
    if style == "blank":
        txt = sc["big"]
        if rev >= 0:
            txt = txt.replace("___", sc["options"][ans])
        size, lines = fit(txt.replace("___", "______"), 800, 76, 44, "extrabold", 4)
        lh = size * 1.25
        top = cy - lh * (len(lines) - 1) / 2
        f = font(size, "extrabold")
        for i, l in enumerate(lines):
            y = top + i * lh
            if rev < 0 and "______" in l:  # рисуем «пустое место»
                pre, post = l.split("______", 1)
                gapw = f.getlength("______")
                x = W / 2 - f.getlength(l) / 2
                ctx.text(x, y, pre, size, "extrabold", "text", 255 * clamp(pop), anchor="lm")
                x0 = x + f.getlength(pre)
                blink = 150 + 105 * (0.5 + 0.5 * math.sin(lt * 6))
                ctx.rect(x0 + 4, y - size * 0.55, x0 + gapw - 4, y + size * 0.55, r=14, fill="accent",
                         alpha=blink * 0.35 * clamp(pop), outline="accent", width=4, oalpha=255 * clamp(pop))
                ctx.text(x0 + gapw, y, post, size, "extrabold", "text", 255 * clamp(pop), anchor="lm")
            else:
                ctx.text(W / 2, y, l, size, "extrabold", "text", 255 * clamp(pop))
            if rev >= 0:
                word = sc["options"][ans]
                if word in l.split() or word in l:
                    idx = l.find(word)
                    x0 = W / 2 - f.getlength(l) / 2 + f.getlength(l[:idx])
                    k = ease_out(prog(rev, 0, 0.35))
                    ctx.rect(x0 - 10, y + size * 0.5, x0 - 10 + (f.getlength(word) + 20) * k, y + size * 0.62,
                             r=4, fill="good", alpha=255)
    else:
        art = sc.get("big_article")
        size, lines = fit(sc["big"], 820, 140 if style == "article" else 120, 50, "black", 3)
        sz = size * (0.8 + 0.2 * clamp(pop))
        yy = cy - (50 if (style == "article" and sc.get("sub")) else 0)
        if art and style != "article":
            ctx.text(W / 2, yy - sz * 0.95, art, 50, "extrabold", hex2rgb(ARTICLE_COLORS[art]), 255 * clamp(pop))
            yy += 20
        if style == "article" and rev >= 0:
            k = ease_back(prog(rev, 0, 0.45))
            ctx.text(W / 2, yy - sz * 0.95 - 10, opts[ans], 60 * clamp(k), "black",
                     hex2rgb(ARTICLE_COLORS[opts[ans]]), 255 * clamp(k))
        ctx.lines(W / 2, yy, lines, sz, "black", "text", 255 * clamp(pop))
        if sc.get("sub"):
            ctx.text(W / 2, yy + sz * 0.5 * len(lines) + 60, sc["sub"], 42, "semibold", "muted", 255 * clamp(pop))

    # варианты ответа
    if style == "article":
        y = 1110
        bw, gap = 270, 30
        x0 = (W - (bw * 3 + gap * 2)) / 2
        for i, o in enumerate(opts):
            col = hex2rgb(ARTICLE_COLORS[o])
            k = ease_back(prog(lt, 0.25 + i * 0.08, 0.45))
            x = x0 + i * (bw + gap)
            yy = y + (1 - clamp(k)) * 80
            fade, s = 1.0, 1.0
            if rev >= 0:
                r = ease_out(prog(rev, 0, 0.3))
                if i == ans:
                    s = 1 + 0.08 * math.sin(clamp(rev / 0.4) * math.pi)
                else:
                    fade = 1 - 0.65 * r
            w2, h2 = bw * s / 2, 100 * s
            filled = rev >= 0 and i == ans
            ctx.rect(x + bw / 2 - w2, yy - h2, x + bw / 2 + w2, yy + h2, r=46, fill=col,
                     alpha=(255 if filled else 40) * fade * clamp(k), outline=col, width=6, oalpha=255 * fade * clamp(k))
            ctx.text(x + bw / 2, yy, o, 78 * s, "black", (255, 255, 255) if filled else col, 255 * fade * clamp(k))
        if rev >= 0:
            confetti(ctx, x0 + ans * (bw + gap) + bw / 2, y, rev, seed, ctx.th["blobs"] + [ctx.th["accent"]])
    else:
        oy, oh, og = 1010, 128, 26
        for i, o in enumerate(opts):
            k = ease_back(prog(lt, 0.3 + i * 0.09, 0.45))
            y0 = oy + i * (oh + og) + (1 - clamp(k)) * 90
            fade, fill, falpha, tcol = 1.0, "card", ctx.th["card_alpha"] + 14, "text"
            letter_bg, letter_tc = "accent", "accent_text"
            if rev >= 0:
                r = ease_out(prog(rev, 0, 0.3))
                if i == ans:
                    fill, falpha, letter_bg = "good", 255 * r + (ctx.th["card_alpha"] + 14) * (1 - r), "text"
                    letter_tc = "good"
                    tcol = mix(hex2rgb(ctx.th["text"]), (255, 255, 255), r)
                else:
                    fade = 1 - 0.6 * r
            sx = 0
            if rev >= 0 and i != ans:
                sx = math.sin(clamp(rev / 0.35) * math.pi * 4) * 10 * (1 - clamp(rev / 0.35))
            ctx.rect(90 + sx, y0, W - 90 + sx, y0 + oh, r=34, fill=fill, alpha=falpha * clamp(k) * fade,
                     outline="text", width=2, oalpha=50 * fade * clamp(k))
            ctx.circle(160 + sx, y0 + oh / 2, 36, letter_bg, 255 * clamp(k) * fade)
            ctx.text(160 + sx, y0 + oh / 2, "ABC"[i], 38, "black", letter_tc, 255 * clamp(k) * fade)
            size, lines = fit(o, 720, 52, 30, "bold", 2)
            lh = size * 1.12
            top = y0 + oh / 2 - lh * (len(lines) - 1) / 2
            for j, l in enumerate(lines):
                ctx.text(222 + sx, top + j * lh, l, size, "bold", tcol, 255 * clamp(k) * fade, anchor="lm")
            if rev >= 0 and i == ans:
                kk = ease_back(prog(rev, 0.1, 0.4))
                check_icon(ctx, W - 150, y0 + oh / 2, 46 * clamp(kk), (255, 255, 255))
        if rev >= 0:
            confetti(ctx, W / 2, oy + ans * (oh + og) + oh / 2, rev, seed, ctx.th["blobs"] + [ctx.th["accent"]])

    # подсказка / объяснение
    if rev < 0:
        a = 200 * clamp(prog(lt, 0.8, 0.4))
        ctx.text(W / 2, 1500, sc.get("hint", ""), 34, "semibold", "muted", a)
    elif sc.get("explain"):
        k = ease_back(prog(rev, 0.35, 0.45))
        size, lines = fit(sc["explain"], 820, 42, 28, "extrabold", 2)
        ctx.rect(110, 1480 - (1 - clamp(k)) * 30, W - 110, 1480 + 50 + size * 1.2 * len(lines) - (1 - clamp(k)) * 30,
                 r=30, fill="accent", alpha=255 * clamp(k))
        ctx.lines(W / 2, 1505 + size * 0.6 * len(lines) - (1 - clamp(k)) * 30, lines, size, "extrabold",
                  "accent_text", 255 * clamp(k))


def layout_tokens(tokens, fnt, max_w, gap):
    rows, cur, cw = [], [], 0
    for i, tk in enumerate(tokens):
        w = fnt.getlength(tk)
        if cur and cw + gap + w > max_w:
            rows.append((cur, cw))
            cur, cw = [], 0
        cur.append((i, tk, w))
        cw += (gap if len(cur) > 1 else 0) + w
    if cur:
        rows.append((cur, cw))
    return rows


def find_span(tokens, bad):
    b = bad.split()
    for i in range(len(tokens) - len(b) + 1):
        if tokens[i:i + len(b)] == b:
            return set(range(i, i + len(b)))
    return set()


def draw_tokens(ctx, tokens, cy, size, color, alpha, highlight=None, hcolor="bad", mode="strike", k=1.0, max_w=820):
    f = font(size, "extrabold")
    gap = size * 0.32
    rows = layout_tokens(tokens, f, max_w, gap)
    lh = size * 1.35
    top = cy - lh * (len(rows) - 1) / 2
    for r, (row, rw) in enumerate(rows):
        x = W / 2 - rw / 2
        y = top + r * lh
        for i, tk, w in row:
            hl = highlight and i in highlight
            c = hcolor if (hl and k > 0) else color
            if hl and k > 0:
                pad = 10
                ctx.rect(x - pad, y - size * 0.62, x + w + pad, y + size * 0.62, r=14, fill=hcolor,
                         alpha=70 * k)
            ctx.text(x, y, tk, size, "extrabold", c, alpha, anchor="lm")
            if hl and mode == "strike" and k > 0:
                ctx.line([(x - 6, y + 2), (x - 6 + (w + 12) * k, y + 2)], hcolor, 7, alpha)
            x += w + gap
    return top + lh * len(rows)


def s_error(ctx, sc, lt, seed):
    think, rev = sc["think"], lt - sc["think"]
    ctx.text(W / 2, 370, sc["prompt"], 44, "extrabold", "muted", 255)
    if rev < 0:
        timer(ctx, W - 150, 370, 52, lt, think)
    ct, chh = 450, 440
    big_card(ctx, sc, lt, ct, chh)
    pop = ease_out(prog(lt, 0.1, 0.4))
    wtok = sc["wrong"].split()
    span = find_span(wtok, sc["bad"])
    k = ease_out(prog(rev, 0, 0.4)) if rev >= 0 else 0
    size, _ = fit(sc["wrong"], 820, 72, 40, "extrabold", 4)
    sh = math.sin(clamp(rev / 0.4) * math.pi * 5) * 8 * (1 - clamp(rev / 0.4)) if rev >= 0 else 0
    ctx.dy += sh
    draw_tokens(ctx, wtok, ct + chh / 2, size, "text", 255 * pop, span if rev >= 0 else None, "bad", "strike", k)
    ctx.dy -= sh
    if rev >= 0:
        k2 = ease_back(prog(rev, 0.45, 0.5))
        y0 = 950 + (1 - clamp(k2)) * 60
        ctx.rect(80, y0, W - 80, y0 + 400, r=48, fill="good", alpha=46 * clamp(k2), outline="good", width=4,
                 oalpha=255 * clamp(k2))
        check_icon(ctx, 150, y0 + 60, 50 * clamp(k2), "good", 255 * clamp(k2))
        rtok = sc["right"].split()
        fixspan = find_span(rtok, sc["fix"]) if sc["fix"] else set()
        draw_tokens(ctx, rtok, y0 + 220, min(size, 66), "text", 255 * clamp(k2), fixspan, "good", "box", clamp(k2))
        k3 = ease_back(prog(rev, 1.0, 0.45))
        if k3 > 0:
            ssz, lines = fit(sc["explain"], 820, 42, 28, "extrabold", 2)
            ctx.rect(110, 1440, W - 110, 1490 + ssz * 1.2 * len(lines), r=30, fill="accent", alpha=255 * clamp(k3))
            ctx.lines(W / 2, 1465 + ssz * 0.6 * len(lines), lines, ssz, "extrabold", "accent_text", 255 * clamp(k3))
    else:
        ctx.text(W / 2, 1050, "?", 220, "black", "accent", 120 + 80 * math.sin(lt * 4))
        ctx.text(W / 2, 1500, sc.get("hint", ""), 34, "semibold", "muted", 200 * clamp(prog(lt, 0.8, 0.4)))


def s_compare(ctx, sc, lt, seed):
    if sc.get("counter"):
        ctx.text(110, 370, sc["counter"], 40, "black", "accent", 255, anchor="lm")
    fix = sc["mode"] == "fix"
    top_col = "bad" if fix else "muted"
    bot_col = "good" if fix else "accent"
    k1 = ease_back(prog(lt, 0.05, 0.45))
    y1 = 450 + (1 - clamp(k1)) * 60
    ctx.rect(80, y1, W - 80, y1 + 380, r=48, fill="card", alpha=ctx.th["card_alpha"] * clamp(k1),
             outline=top_col, width=4, oalpha=200 * clamp(k1))
    ctx.text_on_pill(W / 2, y1, sc["top_label"], 34, top_col, (255, 255, 255) if fix else "accent_text",
                     255 * clamp(k1), padx=28, pady=12)
    size, lines = fit(sc["top"], 800, 68, 38, "extrabold", 3)
    ctx.lines(W / 2, y1 + 200, lines, size, "extrabold", "text", (255 if not fix or lt < 1.4 else 170) * clamp(k1))
    if fix and lt > 1.4:  # зачёркиваем неправильный вариант
        kk = ease_out(prog(lt, 1.4, 0.35))
        f = font(size, "extrabold")
        lh = size * 1.18
        top = y1 + 200 - lh * (len(lines) - 1) / 2
        for i, l in enumerate(lines):
            w = f.getlength(l)
            ctx.line([(W / 2 - w / 2 - 10, top + i * lh), (W / 2 - w / 2 - 10 + (w + 20) * kk, top + i * lh)],
                     "bad", 8)
    k2 = ease_back(prog(lt, 1.3, 0.5))
    if k2 > 0:
        y2 = 900 + (1 - clamp(k2)) * 80
        ctx.rect(80, y2, W - 80, y2 + 420, r=48, fill=bot_col, alpha=40 * clamp(k2),
                 outline=bot_col, width=5, oalpha=255 * clamp(k2))
        ctx.text_on_pill(W / 2, y2, sc["bottom_label"], 34, bot_col,
                         (255, 255, 255) if fix else "accent_text", 255 * clamp(k2), padx=28, pady=12)
        size2, lines2 = fit(sc["bottom"], 800, 70, 38, "black", 3)
        ctx.lines(W / 2, y2 + 215, lines2, size2 * (0.9 + 0.1 * clamp(k2)), "black", "text", 255 * clamp(k2))
        if fix:
            confetti(ctx, W / 2, y2 + 60, lt - 1.3, seed, [ctx.th["good"], ctx.th["accent"]])
    k3 = ease_out(prog(lt, 2.1, 0.4))
    if k3 > 0 and sc.get("note"):
        ssz, nl = fit(sc["note"], 820, 42, 28, "bold", 2)
        ctx.lines(W / 2, 1420 + (1 - k3) * 20, nl, ssz, "bold", "muted", 255 * k3)


def s_card(ctx, sc, lt, seed):
    ctx.text(110, 370, sc["counter"], 40, "black", "accent", 255, anchor="lm")
    n_all = int(sc["counter"].split("/")[1])
    cur = int(sc["counter"].split("/")[0])
    for i in range(n_all):
        x = W - 110 - (n_all - 1 - i) * 36
        ctx.circle(x, 370, 10 if i + 1 == cur else 7, "accent" if i + 1 <= cur else "muted",
                   255 if i + 1 <= cur else 120)
    k = ease_back(prog(lt, 0.0, 0.5))
    y0 = 520 + (1 - clamp(k)) * 80
    ctx.rect(80, y0, W - 80, y0 + 820, r=56, fill="card", alpha=ctx.th["card_alpha"] * clamp(k), outline="text",
             width=2, oalpha=40 * clamp(k))
    yy = y0 + 330
    if sc.get("article"):
        col = hex2rgb(ARTICLE_COLORS[sc["article"]])
        ctx.text_on_pill(W / 2, y0 + 150, sc["article"], 52, col, (255, 255, 255), 255 * clamp(k), padx=40)
    size, lines = fit(sc["de"], 820, 118 if sc.get("article") else 92, 48, "black", 3)
    ctx.lines(W / 2, yy, lines, size * (0.85 + 0.15 * clamp(k)), "black", "text", 255 * clamp(k))
    k2 = ease_out(prog(lt, 0.7, 0.4))
    ctx.rect(W / 2 - 60 * k2, yy + size * 0.6 * len(lines) + 40, W / 2 + 60 * k2,
             yy + size * 0.6 * len(lines) + 48, r=4, fill="accent", alpha=255)
    s2, l2 = fit(sc["tr"], 820, 60, 34, "bold", 2)
    ctx.lines(W / 2, yy + size * 0.6 * len(lines) + 130 + (1 - k2) * 30, l2, s2, "bold", "accent", 255 * k2)


def s_result(ctx, sc, lt, seed):
    k = ease_back(prog(lt, 0, 0.45))
    ctx.text(W / 2, 600 - (1 - clamp(k)) * 40, sc["title"], 80, "black", "text", 255 * clamp(k))
    for i, l in enumerate(sc["lines"]):
        kk = ease_back(prog(lt, 0.3 + i * 0.15, 0.4))
        y = 800 + i * 150
        ctx.rect(150 + (1 - clamp(kk)) * 120, y - 55, W - 150 + (1 - clamp(kk)) * 120, y + 55, r=55,
                 fill="accent" if i == len(sc["lines"]) - 1 else "card",
                 alpha=(255 if i == len(sc["lines"]) - 1 else ctx.th["card_alpha"] + 20) * clamp(kk))
        ctx.text(W / 2 + (1 - clamp(kk)) * 120, y, l, 50, "extrabold",
                 "accent_text" if i == len(sc["lines"]) - 1 else "text", 255 * clamp(kk))


def plane(ctx, cx, cy, s, alpha):
    ctx.poly([(cx - s, cy - s * 0.1), (cx + s, cy - s * 0.8), (cx + s * 0.45, cy + s * 0.85)], (255, 255, 255), alpha)
    ctx.poly([(cx - s * 0.1, cy + s * 0.15), (cx + s * 0.45, cy + s * 0.85), (cx + s, cy - s * 0.8)],
             (210, 228, 255), alpha)


def s_cta(ctx, sc, lt, seed):
    k = ease_back(prog(lt, 0, 0.55))
    cy = 640
    tg = (42, 171, 238)
    for i in range(3):  # волны
        ph = ((lt * 0.8) + i / 3) % 1
        ctx.circle(W / 2, cy, 120 + ph * 160, tg, 70 * (1 - ph) * clamp(k))
    ctx.circle(W / 2, cy, 125 * clamp(k), tg)
    plane(ctx, W / 2 - 8, cy + 4 + math.sin(lt * 3) * 6, 62 * clamp(k), 255)
    k2 = ease_out(prog(lt, 0.3, 0.45))
    size, lines = fit(sc["line1"], 880, 62, 40, "black", 2)
    ctx.lines(W / 2, 920 + (1 - k2) * 30, lines, size, "black", "text", 255 * k2)
    k3 = ease_back(prog(lt, 0.55, 0.5))
    pulse = 1 + 0.03 * math.sin(lt * 5)
    hs = fit(sc["handle"], 820, 64, 36, "black", 1)[0] * pulse
    f = font(hs, "black")
    w = f.getlength(sc["handle"])
    y = 1120
    ctx.rect(W / 2 - w / 2 - 50, y - 70, W / 2 + w / 2 + 50, y + 70, r=70, fill="accent", alpha=255 * clamp(k3))
    ctx.text(W / 2, y, sc["handle"], hs * clamp(k3) if k3 > 0 else 1, "black", "accent_text", 255 * clamp(k3))
    k4 = ease_out(prog(lt, 0.9, 0.4))
    if sc.get("line3"):
        ctx.text(W / 2, 1270 + (1 - k4) * 20, sc["line3"], 40, "bold", "muted", 255 * k4)
    # стрелка вниз (к описанию/профилю)
    ay = 1390 + math.sin(lt * 6) * 14
    ctx.line([(W / 2 - 30, ay), (W / 2, ay + 30), (W / 2 + 30, ay)], "accent", 9, 255 * k4)


SCENES = {"hook": s_hook, "mcq": s_mcq, "error": s_error, "compare": s_compare, "card": s_card,
          "result": s_result, "cta": s_cta}


# ---------------------------------------------------------------- рендер

def total_duration(spec):
    return sum(s["dur"] for s in spec["scenes"])


def frame_at(spec, theme, bg, t, total, brand):
    img = bg.frame(t)
    ctx = Ctx(img, theme)
    header(ctx, spec, t, total, brand)
    acc = 0.0
    for i, sc in enumerate(spec["scenes"]):
        if acc <= t < acc + sc["dur"] or (i == len(spec["scenes"]) - 1 and t >= acc):
            lt = t - acc
            ctx.ga, ctx.dy = scene_alpha(lt, sc["dur"]) if sc["type"] != "cta" else (ease_out(lt / 0.35), 0)
            if sc["type"] == "hook":
                SCENES["hook"](ctx, sc, lt)
            else:
                SCENES[sc["type"]](ctx, sc, lt, hash(spec["id"]) + i)
            break
        acc += sc["dur"]
    return img


def render(spec, theme, out_path, brand="Station Deutsch", cover_path=None, preview_every=None, sound=None):
    total = total_duration(spec)
    n = int(total * FPS)
    bg = Background(theme, spec["id"])
    sound = sound or {"enabled": True, "volume": 70}
    wav = None
    if sound.get("enabled", True):
        from .audio import build_wav
        wav = build_wav(spec, out_path[:-4] + ".wav", sound.get("volume", 70))
        audio_in = ["-i", wav]
    else:
        audio_in = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           *audio_in, "-map", "0:v", "-map", "1:a",
           "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cover_t = spec["scenes"][0]["dur"] + 1.2
    try:
        for i in range(n):
            t = i / FPS
            img = frame_at(spec, theme, bg, t, total, brand)
            if cover_path and abs(t - cover_t) < 0.5 / FPS:
                img.save(cover_path, quality=88)
            if preview_every and i % preview_every == 0:
                img.save(out_path.replace(".mp4", f"_f{i:04d}.jpg"), quality=80)
            proc.stdin.write(img.tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
        if wav and os.path.exists(wav):
            os.remove(wav)
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg завершился с ошибкой")
    return total
