---
name: image-ocr
description: 当用户发送图片需要提取文字时，优先 vision_analyze，失败后用 tesseract/pytesseract 备用方案。
---

# Image OCR Processing

> Load when user shares an image and text extraction is needed.

## Core Workflow

### Step 1: Try vision_analyze

```
vision_analyze(image_path)
```

If successful, return the result. Done.

### Step 2: vision_analyze fails → fallback to tesseract

When `vision_analyze` is unavailable (tool not present or errors), use `tesseract` + `pytesseract`:

```bash
python3 -c "
from pytesseract import image_to_string
from PIL import Image
img = Image.open('/path/to/image.jpg')
text = image_to_string(img, lang='chi_sim+eng', config='--psm 6')
print(text)
"
```

### Step 3: Preprocessing (optional)

If direct OCR results are messy, preprocess first:

```python
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

img = Image.open(image_path).convert('L')  # grayscale
img = ImageEnhance.Contrast(img).enhance(2.0)  # boost contrast
img = ImageEnhance.Brightness(img).enhance(1.2)
img = img.filter(ImageFilter.SHARPEN)  # sharpen

# Try multiple PSM modes, pick clearest
for psm in ['3', '4', '6', '11']:
    text = pytesseract.image_to_string(img, lang='chi_sim+eng', config=f'--psm {psm}')
    print(f"PSM {psm}: {text[:200]}...")
```

## Notes

- **lang param**: Use `chi_sim+eng` for Chinese content, `eng` for pure English
- **PSM modes**:
  - `3` = automatic segmentation (default, works for most cases)
  - `4` = assume variable density text block
  - `6` = assume uniform text block
  - `11` = sparse text
- Screenshot-style images usually work best with PSM 6
- When presenting results to user, format OCR output into readable text, mark uncertain parts
- Image paths are typically in `/root/.hermes/image_cache/`

## Pitfalls

- `pytesseract` may not be installed → check first: `python3 -c "import pytesseract"`
- `tesseract` binary may not be installed → check first: `which tesseract`
- Chinese OCR requires language pack → ensure tesseract has `chi_sim` package
- `vision_analyze` is NOT available in all environments — always have tesseract fallback ready