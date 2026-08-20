# AGENTS.md

PDFGenie: PDF full-text translation CLI (EN→ZH by default). PaddleOCR for scanned pages, PyMuPDF (fitz) for text extraction/rendering. Single package `pdfgenie/`, no tests, no CI, not yet under git.

## Commands

Run from repo root (`pip install -r requirements.txt` first; Python 3.13):

```bash
python -m pdfgenie.cli input.pdf                 # default: google engine, bilingual output
python -m pdfgenie input.pdf --pages 1 -v       # fast smoke test on one page
```

- `--mono` = translation-only mode; default is bilingual (original page + cloned translated page interleaved).
- `--no-layout` skips PP-Structure layout analysis. Not documented in README's flag table — argparse in `pdfgenie/cli.py:184` is the source of truth for flags.
- `--pages 1-5,10` is **1-indexed**; internally converted to 0-indexed (`parse_page_range`, cli.py:168).

## Verification (no test suite)

Run against sample files in repo root — real scanned FT/WSJ PDFs and text-layer WSJ PDF. Use `--pages 1` (full scans are slow on CPU PaddleOCR) and inspect the output PDF visually. Preview PNGs at root (`preview_*.png`, `test_*.png`) are prior run artifacts, not fixtures to edit.

## Pipeline & architecture

`cli.py:29 translate_pdf()` orchestrates: detect → layout (OCR pages only) → extract → translate (threaded, 4 workers) → render.

- `detector.py`: per-page decides text-layer vs scanned (`needs_ocr`). Text pages use fitz extraction; scanned pages go through PaddleOCR.
- `layout_analyzer.py`: PP-Structure via PaddleOCR, **lazy init on first OCR page** (downloads model weights on first run — expect a long delay once). Only invoked when `needs_ocr`.
- `translator.py`: translation unit is the paragraph/block, not lines. Formula markers ($...$, $$...$$) are replaced with `__MATH_n__` placeholders before translating and restored after (`utils.protect_formulas`).
- `renderer.py`: bilingual mode clones original page artwork (images/lines) and overlays translated text only; mono mode redacts + replaces in place.

## Translation engines & gotchas

- `google` (default): free unofficial web endpoint `http://translate.google.com/m`, no API key, network-dependent/flaky. Text is **truncated to 5000 chars**; HTTP 400 returns sentinel string `"TRANSLATION_ERROR"` (not an exception).
- `openai`: reads `OPENAI_API_KEY` / `OPENAI_BASE_URL`; default model `gpt-4o-mini`.
- `lmstudio`: OpenAI-compatible at `http://localhost:1234/v1` (override with `LMSTUDIO_BASE_URL`). Without `--model`, auto-picks the first loaded model. Requires LMStudio Local Server running.
- `ollama`: without `--model`, picks first listed model (fallback `qwen2.5:7b`).

## Gotchas

- **Translation cache masks changes**: SQLite at `~/.cache/pdfgenie/translation_cache.db` is keyed by text+engine+langs. When debugging translator/renderer output, stale cached translations can make code changes appear ineffective. Clear via `pdfgenie.cache.clear_cache()` (no CLI flag exists).
- Cache only stores successful translations; `"TRANSLATION_ERROR"` sentinel passes through as if it were translated text.
- Code comments, docstrings, log messages, and user-facing strings are in **Chinese** — follow this convention in new code.
