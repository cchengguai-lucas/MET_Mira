"""Allowlist of built-in project cartoon badge ids (frontend renders the art)."""

from __future__ import annotations

PROJECT_ICONS = frozenset({
    "bot",
    "meka",
    "phoenix",
    "hammer",
    "chronos",
    "fox",
    "owl",
    "crystal",
    "rocket",
    "shield",
    "ramen",
    "squid",
    "dragon",
    "star",
    "fist",
    "wing",
    "bolt",
    "bloom",
    "comet",
    "wolf",
    # Semiconductor process badges (motif × color).
    "wafer-blue",
    "wafer-orange",
    "wafer-purple",
    "wafer-teal",
    "mdp-blue",
    "mdp-orange",
    "mdp-purple",
    "mdp-teal",
    "etch-blue",
    "etch-orange",
    "etch-purple",
    "etch-teal",
    "cvd-blue",
    "cvd-orange",
    "cvd-purple",
    "cvd-teal",
})


def normalize(icon: str | None) -> str | None:
    if icon is None or icon == "":
        return None
    key = icon.strip().lower()
    if key not in PROJECT_ICONS:
        raise ValueError(f"未知的项目图标：{icon}")
    return key
