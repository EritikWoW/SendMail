#!/usr/bin/env python3
"""Generate SendMailOutreach app icons without external dependencies.

The mark is based on the official Lucide `mail` icon SVG.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
PNG_PATH = ASSETS / "sendmail_icon.png"
ICO_PATH = ASSETS / "sendmail_icon.ico"


def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _blend(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    return tuple(_clamp(a[i] + (b[i] - a[i]) * t) for i in range(4))


def _put_pixel(pixels: list[bytearray], x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if x < 0 or y < 0 or y >= len(pixels) or x >= len(pixels[0]) // 4:
        return
    row = pixels[y]
    idx = x * 4
    row[idx : idx + 4] = bytes(color)


def _fill_rect(pixels: list[bytearray], x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int, int]) -> None:
    for y in range(y0, y1):
        for x in range(x0, x1):
            _put_pixel(pixels, x, y, color)


def _draw_rounded_rect(pixels: list[bytearray], x0: int, y0: int, x1: int, y1: int, radius: int, color: tuple[int, int, int, int]) -> None:
    _fill_rect(pixels, x0 + radius, y0, x1 - radius, y1, color)
    _fill_rect(pixels, x0, y0 + radius, x1, y1 - radius, color)
    for y in range(y0, y1):
        for x in range(x0, x1):
            dx = min(abs(x - x0 - radius), abs(x - (x1 - radius - 1)))
            dy = min(abs(y - y0 - radius), abs(y - (y1 - radius - 1)))
            if dx * dx + dy * dy <= radius * radius:
                _put_pixel(pixels, x, y, color)


def _draw_circle(pixels: list[bytearray], cx: int, cy: int, radius: int, color: tuple[int, int, int, int]) -> None:
    r2 = radius * radius
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                _put_pixel(pixels, x, y, color)


def _draw_line(pixels: list[bytearray], x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int, int], width: int = 1) -> None:
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        for oy in range(-width // 2, width // 2 + 1):
            for ox in range(-width // 2, width // 2 + 1):
                _put_pixel(pixels, x0 + ox, y0 + oy, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def build_pixels(size: int = 256) -> list[bytearray]:
    bg_top = (11, 18, 32, 255)
    bg_bottom = (30, 41, 59, 255)
    pixels = [bytearray(size * 4) for _ in range(size)]
    for y in range(size):
        t = y / (size - 1)
        row_color = _blend(bg_top, bg_bottom, t)
        for x in range(size):
            # Subtle radial highlight.
            dx = (x - size * 0.35) / size
            dy = (y - size * 0.3) / size
            glow = max(0.0, 1.0 - math.sqrt(dx * dx + dy * dy) * 3.5)
            r = _clamp(row_color[0] + glow * 18)
            g = _clamp(row_color[1] + glow * 12)
            b = _clamp(row_color[2] + glow * 2)
            _put_pixel(pixels, x, y, (r, g, b, 255))

    # Main rounded card.
    _draw_rounded_rect(pixels, 38, 42, 218, 214, 26, (15, 23, 42, 240))

    # Envelope body from Lucide mail icon proportions.
    envelope = (248, 250, 252, 255)
    accent = (245, 158, 11, 255)
    outline = (203, 213, 225, 255)
    _draw_rounded_rect(pixels, 72, 82, 184, 164, 16, (0, 0, 0, 0))
    _draw_line(pixels, 72, 90, 128, 124, envelope, 5)
    _draw_line(pixels, 184, 90, 136, 124, envelope, 5)
    _draw_line(pixels, 72, 154, 126, 116, outline, 3)
    _draw_line(pixels, 184, 154, 138, 116, outline, 3)
    _draw_rounded_rect(pixels, 70, 84, 186, 162, 14, (0, 0, 0, 0))
    _draw_line(pixels, 70, 84, 128, 121, envelope, 4)
    _draw_line(pixels, 186, 84, 136, 121, envelope, 4)
    _draw_line(pixels, 70, 84, 70, 160, envelope, 3)
    _draw_line(pixels, 186, 84, 186, 160, envelope, 3)
    _draw_line(pixels, 70, 160, 186, 160, envelope, 3)
    _draw_circle(pixels, 178, 56, 13, accent)
    _draw_circle(pixels, 178, 56, 6, (15, 23, 42, 255))

    return pixels


def encode_png(pixels: list[bytearray]) -> bytes:
    height = len(pixels)
    width = len(pixels[0]) // 4
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        raw.extend(row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", ihdr),
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
            chunk(b"IEND", b""),
        ]
    )


def encode_ico(png_bytes: bytes, width: int = 256, height: int = 256) -> bytes:
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        width if width < 256 else 0,
        height if height < 256 else 0,
        0,
        0,
        1,
        32,
        len(png_bytes),
        6 + 16,
    )
    return header + entry + png_bytes


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    pixels = build_pixels(256)
    png_bytes = encode_png(pixels)
    PNG_PATH.write_bytes(png_bytes)
    ICO_PATH.write_bytes(encode_ico(png_bytes))
    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {ICO_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
