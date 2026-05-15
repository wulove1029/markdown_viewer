from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PNG_PATH = ROOT / "ICON" / "icon.png"
ICO_PATH = ROOT / "ICON" / "icon.ico"
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]


def main() -> None:
    source = Image.open(PNG_PATH).convert("RGBA")
    source.save(ICO_PATH, format="ICO", sizes=SIZES)
    print(f"Wrote {ICO_PATH} with sizes: {', '.join(f'{w}x{h}' for w, h in SIZES)}")


if __name__ == "__main__":
    main()
