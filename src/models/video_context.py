"""Снимок одного ролика в ленте (без изменения логики пайплайна)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VideoContext:
    username: str
    caption: str
    video_url: str
    video_src: str = ""  # префикс src <video> — меняется при смене ролика
