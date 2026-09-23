"""Export launcher-size derivatives of the approved, generated KH2 logo asset."""
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parent.parent / 'static' / 'mobile'
canvas = Image.open(root / 'kh2-icon-master.png').convert('RGB')
for size in (180, 192, 512):
    canvas.resize((size,size),Image.Resampling.LANCZOS).save(root / f'kh2-icon-{size}.png')
