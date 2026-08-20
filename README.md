# PDFGenie 🧞

A high-quality full-document PDF translation tool built on **PaddleOCR** and **PyMuPDF (fitz)**, inspired by [pdf2zh](https://github.com/Byaidu/PDFMathTranslate) and a [blog walkthrough](https://www.cnblogs.com/clnchanpin/p/19293657).

[English] | [中文](README_cn.md)

---

## ✨ Key Features

- 📄 **Smart dual-mode detection**: automatically determines whether each page has a text layer or is a scanned/garbled image — text pages use fast PyMuPDF extraction, scanned pages go through PaddleOCR.
- 🎨 **Layout-preserving backfill**: in bilingual mode the original page artwork (backgrounds, charts, photos, rules and boxes) is fully cloned and only the text regions are overwritten — no blank white pages.
- 📐 **Multi-column & typography reconstruction**: built-in recursive XY-Cut segmentation plus intelligent hyphenation merging to restore natural reading paragraphs.
- 🌐 **Multiple translation engines**:
  - **Google Translate** (default, zero configuration)
  - **LMStudio** (local LLMs, auto-discovers loaded models)
  - **Ollama** (local LLMs, auto-discovers listed models)
  - **OpenAI** (official and OpenAI-compatible APIs)
- 📖 **Side-by-side bilingual output**: by default produces a page-interleaved PDF (original page followed by its translated clone); `--mono` switches to translation-only mode.
- ⚡ **Local SQLite cache**: translations are hash-keyed and persisted; identical sentences hit instantly, avoiding duplicate billing and network requests.
- 🧮 **LaTeX formula protection**: markers like `$...$` and `$$...$$` are swapped for placeholders before translation and seamlessly restored afterwards.

---

## 🚀 Installation

Python 3.13. Pick one:

```bash
# pip
pip install -r requirements.txt

# uv (recommended)
uv venv && uv pip install -r requirements.txt
```

`requirements.txt`:
```text
paddlepaddle==3.3.0
paddleocr
PyMuPDF>=1.27.0
numpy
Pillow
requests
openai
ollama
tqdm
tenacity
```

> Note: on the first run over scanned pages, PaddleOCR downloads PP-Structure model weights automatically — expect a long delay once.

---

## 📖 CLI Usage

### 1. Default translation (Google Translate, bilingual, EN → ZH)
```bash
python -m pdfgenie.cli input.pdf
# output: input_bilingual.pdf
```

### 2. Custom output path and page range
```bash
# translate pages 1–5 and page 10 only
python -m pdfgenie.cli input.pdf -o output_p1_5.pdf --pages 1-5,10
```

### 3. Translation-only mode (original removed)
```bash
python -m pdfgenie.cli input.pdf --mono -o output_translated.pdf
```

### 4. LMStudio local LLM
```bash
# start the LMStudio Local Server first (default port 1234)
python -m pdfgenie.cli input.pdf --service lmstudio
# or pin a specific model
python -m pdfgenie.cli input.pdf --service lmstudio --model qwen2.5-7b-instruct
```

### 5. Ollama local LLM
```bash
python -m pdfgenie.cli input.pdf --service ollama --model qwen2.5:7b
```

### 6. OpenAI / DeepSeek / compatible API
```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com/v1"
python -m pdfgenie.cli input.pdf --service openai --model deepseek-chat
```

---

## 🛠️ Options

| Flag | Description | Default |
| :--- | :--- | :--- |
| `input` | Input PDF path | required |
| `-o, --output` | Output PDF path | auto-appends `_bilingual`/`_translated` |
| `--service` | Translation engine: `google` / `lmstudio` / `ollama` / `openai` | `google` |
| `--lang-in` | Source language code | `en` |
| `--lang-out` | Target language code | `zh-CN` |
| `--model` | Model name (LMStudio/Ollama/OpenAI) | auto-detect/default |
| `--pages` | Page filter (e.g. `1-3,5`, 1-indexed) | all pages |
| `--mono` | Translation-only mode (default is bilingual side-by-side) | False |
| `--no-layout` | Skip PP-Structure layout analysis (keep it for scanned pages) | False |
| `--workers` | Translation worker threads | `4` |
| `-v, --verbose` | Verbose debug logging | False |

---

## 💾 Translation Cache

Translations are persisted in SQLite (`~/.cache/pdfgenie/translation_cache.db`) keyed by text + engine + languages; identical sentences hit instantly.

- Only successful translations are cached. When the Google endpoint fails it returns the sentinel string `TRANSLATION_ERROR`, which gets rendered on the page as if translated — check the log for network errors before re-running.
- Stale cache entries can make translator/renderer changes appear to have no effect; clear with:

```python
from pdfgenie.cache import clear_cache
clear_cache()
```
