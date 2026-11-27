"""Дизайн-темы. Цвета в HEX. Можно добавлять свои — панель управления их подхватит."""

THEMES = {
    "midnight": {
        "name": "Midnight", "bg": ["#0B1026", "#2A1659"], "blobs": ["#5B3DF5", "#FF4D8D", "#00C2FF"],
        "card": "#FFFFFF", "card_alpha": 22, "text": "#FFFFFF", "muted": "#B9B6D9",
        "accent": "#FFD23F", "accent_text": "#14112B", "good": "#2BD97C", "bad": "#FF5A6E",
    },
    "sunset": {
        "name": "Sunset", "bg": ["#2B0F3A", "#FF6A3D"], "blobs": ["#FFB347", "#FF3D7F", "#7A3DFF"],
        "card": "#FFFFFF", "card_alpha": 26, "text": "#FFFFFF", "muted": "#FFD9C7",
        "accent": "#FFF15A", "accent_text": "#2B0F3A", "good": "#3DFFA2", "bad": "#FF2E4D",
    },
    "mint": {
        "name": "Mint", "bg": ["#03241F", "#0E6B5C"], "blobs": ["#22E3B5", "#00A3FF", "#B6FF3D"],
        "card": "#FFFFFF", "card_alpha": 20, "text": "#FFFFFF", "muted": "#BFE9DF",
        "accent": "#B6FF3D", "accent_text": "#03241F", "good": "#42F5A1", "bad": "#FF6B6B",
    },
    "berlin": {
        "name": "Berlin", "bg": ["#0A0A0A", "#262626"], "blobs": ["#DD0000", "#FFCE00", "#3A3A3A"],
        "card": "#FFFFFF", "card_alpha": 16, "text": "#FFFFFF", "muted": "#BDBDBD",
        "accent": "#FFCE00", "accent_text": "#0A0A0A", "good": "#2ECC71", "bad": "#FF3B30",
    },
    "paper": {
        "name": "Paper", "bg": ["#FFF7E8", "#FFE3C2"], "blobs": ["#FFB86B", "#FF7AA8", "#7FD1FF"],
        "card": "#1B1B2F", "card_alpha": 14, "text": "#1B1B2F", "muted": "#6B5F55",
        "accent": "#1B1B2F", "accent_text": "#FFF7E8", "good": "#14A85A", "bad": "#E0344B",
    },
}

ARTICLE_COLORS = {"der": "#3D8BFF", "die": "#FF4D6D", "das": "#2BD97C"}


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def get_theme(name, rng=None):
    if name == "rotate" and rng is not None:
        name = rng.choice(list(THEMES))
    return name if name in THEMES else "midnight", THEMES.get(name, THEMES["midnight"])
