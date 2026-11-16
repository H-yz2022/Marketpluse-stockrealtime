"""Draws desktop/icon.ico: three candlesticks (red up, green down - the mainland convention) on a dark tile."""

from pathlib import Path

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=48, fill=(26, 26, 25, 255))
red, green = (224, 64, 64, 255), (24, 170, 90, 255)
# (x centre, wick top, body top, body bottom, wick bottom, colour)
CANDLES = ((70, 120, 140, 196, 214, green), (128, 70, 92, 170, 188, red), (186, 40, 58, 132, 150, red))
for x, wt, bt, bb, wb, c in CANDLES:
    d.line([x, wt, x, wb], fill=c, width=8)
    d.rounded_rectangle([x - 20, bt, x + 20, bb], radius=6, fill=c)
out = Path(__file__).with_name("icon.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)
