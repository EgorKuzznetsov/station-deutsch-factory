"""Звуковые эффекты анимаций — синтезируются кодом (никаких чужих сэмплов → никаких претензий по авторским правам).

Звуки привязаны к тем же моментам, что и анимации в render.py:
  whoosh  — смена сцены            pop     — появление элемента
  tick    — секунды таймера        tick_hi — последние 3 секунды
  correct — правильный ответ       sparkle — конфетти
  wrong   — зачёркивание ошибки    rise    — «апгрейд» A1 → B2
  chime   — финальная сцена с ботом
"""
import wave

import numpy as np

SR = 44100
_rng = np.random.default_rng(7)


def _t(dur):
    return np.arange(int(SR * dur)) / SR


def _env(n, attack=0.004, decay=8.0):
    t = np.arange(n) / SR
    a = np.clip(t / attack, 0, 1) if attack > 0 else 1
    return a * np.exp(-t * decay)


def _tone(freq, dur, decay=6.0, harmonics=(1.0, 0.35, 0.12), attack=0.004):
    t = _t(dur)
    s = sum(a * np.sin(2 * np.pi * freq * (i + 1) * t) for i, a in enumerate(harmonics))
    return s * _env(len(t), attack, decay)


def _lowpass(x, k):
    k = max(1, int(k))
    return np.convolve(x, np.ones(k) / k, mode="same")


def pop():
    t = _t(0.09)
    f = 950 * np.exp(-t * 18) + 380
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(len(t), 0.002, 38)
    return 0.55 * s


def tick(high=False):
    t = _t(0.05)
    f = 2300 if high else 1700
    s = np.sin(2 * np.pi * f * t) * _env(len(t), 0.001, 90)
    s[:60] += _rng.uniform(-1, 1, 60) * np.linspace(0.6, 0, 60)
    return (0.5 if high else 0.3) * s


def whoosh():
    n = int(SR * 0.38)
    noise = _rng.uniform(-1, 1, n)
    out = np.zeros(n)
    for i, k in enumerate((40, 22, 12)):  # «раскрывающийся» фильтр → эффект пролёта
        seg = slice(i * n // 3, (i + 1) * n // 3)
        out[seg] = _lowpass(noise, k)[seg]
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    return 0.45 * _lowpass(out, 3) * env


def correct():
    s = np.zeros(int(SR * 0.75))
    for i, f in enumerate((1046.5, 1318.5, 1568.0)):  # до-ми-соль (мажор)
        tone = _tone(f, 0.75 - i * 0.07, decay=5.5)
        start = int(SR * 0.07 * i)
        s[start:start + len(tone)] += tone
    return 0.33 * s


def wrong():
    t = _t(0.28)
    sq = np.sign(np.sin(2 * np.pi * 150 * t)) * 0.6 + np.sin(2 * np.pi * 155 * t) * 0.4
    gate = ((t % 0.14) < 0.1).astype(float)
    return 0.22 * _lowpass(sq, 6) * gate * _env(len(t), 0.003, 4)


def sparkle():
    s = np.zeros(int(SR * 0.7))
    for _ in range(9):
        f = _rng.uniform(2600, 5200)
        tone = _tone(f, 0.18, decay=26, harmonics=(1.0,))
        st = int(_rng.uniform(0, 0.5) * SR)
        s[st:st + len(tone)] += tone * _rng.uniform(0.3, 0.7)
    return 0.16 * s


def rise():
    t = _t(0.35)
    f = 420 + 900 * (t / t[-1]) ** 1.5
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.sin(np.linspace(0, np.pi, len(t)))
    out = np.zeros(int(SR * 0.9))
    out[:len(s)] += 0.25 * s
    c = _tone(1568.0, 0.55, decay=5)
    out[int(SR * 0.3):int(SR * 0.3) + len(c)] += 0.3 * c
    return out


def chime():
    s = np.zeros(int(SR * 1.6))
    for i, f in enumerate((783.99, 987.77, 1174.66, 1567.98)):  # соль-си-ре-соль
        tone = _tone(f, 1.2, decay=3.2, harmonics=(1.0, 0.25))
        st = int(SR * 0.11 * i)
        s[st:st + len(tone)] += tone
    return 0.26 * s


SOUNDS = {"pop": pop, "tick": lambda: tick(False), "tick_hi": lambda: tick(True), "whoosh": whoosh,
          "correct": correct, "wrong": wrong, "sparkle": sparkle, "rise": rise, "chime": chime}


def events(spec):
    """Расписание звуков по сценам — синхронно с анимациями в render.py."""
    ev, acc = [], 0.0
    for i, sc in enumerate(spec["scenes"]):
        t = sc["type"]
        if i > 0:
            ev.append((acc - 0.08, "whoosh"))
        if t == "hook":
            ev += [(acc + 0.12, "pop"), (acc + 0.3, "pop")]
        elif t in ("mcq", "error"):
            think = sc["think"]
            if t == "mcq":
                n = len(sc["options"])
                ev += [(acc + 0.3 + k * (0.08 if sc["style"] == "article" else 0.09), "pop") for k in range(n)]
            else:
                ev.append((acc + 0.15, "pop"))
            for k in range(1, int(think)):
                ev.append((acc + k, "tick_hi" if think - k <= 3 else "tick"))
            r = acc + think
            if t == "mcq":
                ev += [(r, "correct"), (r + 0.05, "sparkle")]
                if sc.get("explain"):
                    ev.append((r + 0.4, "pop"))
            else:
                ev += [(r, "wrong"), (r + 0.45, "correct"), (r + 1.0, "pop")]
        elif t == "compare":
            ev.append((acc + 0.05, "pop"))
            if sc["mode"] == "fix":
                ev += [(acc + 1.3, "correct"), (acc + 1.4, "wrong"), (acc + 1.35, "sparkle")]
            else:
                ev.append((acc + 1.3, "rise"))
        elif t == "card":
            ev += [(acc + 0.05, "pop"), (acc + 0.7, "tick")]
        elif t == "result":
            ev += [(acc + 0.3 + k * 0.15, "pop") for k in range(len(sc["lines"]))]
        elif t == "cta":
            ev += [(acc + 0.05, "chime"), (acc + 0.6, "pop")]
        acc += sc["dur"]
    return ev, acc


def build_wav(spec, path, volume=70):
    ev, total = events(spec)
    mix = np.zeros(int(SR * (total + 0.05)) + SR)
    cache = {}
    for t, name in ev:
        if name not in cache:
            cache[name] = SOUNDS[name]()
        s = cache[name]
        st = max(0, int(t * SR))
        end = min(len(mix), st + len(s))
        mix[st:end] += s[:end - st]
    mix = mix[:int(SR * total)]
    peak = np.max(np.abs(mix)) or 1.0
    mix = np.tanh(mix / peak * 1.2) / np.tanh(1.2)  # мягкий лимитер
    mix *= 0.89 * max(0, min(100, volume)) / 100
    pcm = (mix * 32767).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())
    return path
