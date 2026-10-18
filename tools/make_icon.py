"""Build a small code-drawn Windows icon without an image library."""
import math
import struct
from pathlib import Path


def icon_image(size):
    pixels = bytearray()
    for y in reversed(range(size)):
        for x in range(size):
            red, green, blue, alpha = 54, 91, 221, 0
            # Supersample the circle and note for a smooth edge.
            for sy in range(4):
                for sx in range(4):
                    px, py = (x + (sx + 0.5) / 4) / size, (y + (sy + 0.5) / 4) / size
                    if math.hypot(px - 0.5, py - 0.5) < 0.47:
                        alpha += 1
            alpha = round(alpha / 16 * 255)
            px, py = (x + 0.5) / size, (y + 0.5) / size
            stem = 0.54 < px < 0.61 and 0.22 < py < 0.7
            flag = 0.57 < px < 0.78 and 0.22 < py < 0.29 + (px - 0.57) * 0.3
            note = ((px - 0.46) / 0.15) ** 2 + ((py - 0.7) / 0.105) ** 2 < 1
            if stem or flag or note:
                red, green, blue = 255, 255, 255
            pixels.extend((blue, green, red, alpha))
    mask_row = ((size + 31) // 32) * 4
    header = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    return header + pixels + bytes(mask_row * size)


root = Path(__file__).resolve().parent.parent
(root / 'assets').mkdir(exist_ok=True)
sizes = [32, 48, 64, 128]
images = [icon_image(size) for size in sizes]
offset = 6 + 16 * len(sizes)
entries = bytearray()
for size, data in zip(sizes, images):
    entries.extend(struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32, len(data), offset))
    offset += len(data)
(root / 'assets' / 'music-shelf.ico').write_bytes(struct.pack('<HHH', 0, 1, len(sizes)) + entries + b''.join(images))
