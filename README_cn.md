# PDFGenie 🧞

基于 **PaddleOCR** 与 **PyMuPDF (fitz)** 的高质量 PDF 全文翻译工具，参考 [pdf2zh](https://github.com/Byaidu/PDFMathTranslate) 与 [博客指引](https://www.cnblogs.com/clnchanpin/p/19293657) 实现。

[English](README.md) | [中文]

---

## ✨ 核心特性

- 📄 **智能双模判别**：自动检测 PDF 是否含有文字层或为扫描件/乱码件，文字版走 PyMuPDF 高速抽取，扫描版自动走 PaddleOCR 识别。
- 🎨 **原版式克隆回填**：双语对照模式下完整克隆原版页面底图、图表、照片与线条框线，仅精确覆盖文字区域，杜绝白板页。
- 📐 **多栏与排版重构**：内置 XY-Cut 递归切分算法与段落连字符（Hyphenation）智能合并，恢复自然阅读段落。
- 🌐 **多翻译引擎支持**：
  - **Google 翻译**（默认，免配置即开即用）
  - **LMStudio**（本地大模型，支持自动发现已加载模型）
  - **Ollama**（本地大模型，支持自动发现已加载模型）
  - **OpenAI**（支持官方及 OpenAI 兼容 API）
- 📖 **中英双语对照**：默认输出双语逐页对照 PDF（原文页与克隆译文页交替），支持 `--mono` 纯译文模式。
- ⚡ **本地 SQLite 缓存**：翻译结果自动哈希持久化，相同句子秒级命中，避免重复计费与网络请求。
- 🧮 **LaTeX 公式保护**：自动识别 `$v*$`, `$...$`, `$$...$$` 等公式标记并使用占位符保护，翻译完成后无缝还原。

---

## 🚀 安装与依赖

Python 3.13。二选一：

```bash
# pip
pip install -r requirements.txt

# uv（推荐）
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

> 注意：首次运行扫描件时 PaddleOCR 会自动下载 PP-Structure 模型权重，请耐心等待。

---

## 📖 CLI 命令行使用

### 1. 默认翻译（Google 翻译，双语对照，EN → ZH）
```bash
python -m pdfgenie.cli input.pdf
# 输出: input_bilingual.pdf
```

### 2. 指定输出路径与页码范围
```bash
# 翻译第 1 至 5 页及第 10 页
python -m pdfgenie.cli input.pdf -o output_p1_5.pdf --pages 1-5,10
```

### 3. 纯译文模式（移除原文）
```bash
python -m pdfgenie.cli input.pdf --mono -o output_translated.pdf
```

### 4. 使用 LMStudio 本地大模型
```bash
# LMStudio 启动 Local Server (默认端口 1234)
python -m pdfgenie.cli input.pdf --service lmstudio
# 或指定模型名称
python -m pdfgenie.cli input.pdf --service lmstudio --model qwen2.5-7b-instruct
```

### 5. 使用 Ollama 本地大模型
```bash
python -m pdfgenie.cli input.pdf --service ollama --model qwen2.5:7b
```

### 6. 使用 OpenAI / DeepSeek / 兼容 API
```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com/v1"
python -m pdfgenie.cli input.pdf --service openai --model deepseek-chat
```

---

## 🛠️ 参数说明

| 参数 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `input` | 输入 PDF 文件路径 | 必填 |
| `-o, --output` | 输出 PDF 文件路径 | 自动附加 `_bilingual`/`_translated` |
| `--service` | 翻译服务：`google` / `lmstudio` / `ollama` / `openai` | `google` |
| `--lang-in` | 源语言代码 | `en` |
| `--lang-out` | 目标语言代码 | `zh-CN` |
| `--model` | 指定模型名称 (LMStudio/Ollama/OpenAI) | 自动探测/默认 |
| `--pages` | 页码过滤（如 `1-3,5`，从 1 起算） | 全部页面 |
| `--mono` | 纯译文模式（默认输出双语对照） | False |
| `--no-layout` | 跳过 PP-Structure 版面分析（扫描页建议保留） | False |
| `--workers` | 翻译并发线程数 | `4` |
| `-v, --verbose` | 打印详细调试日志 | False |

---

## 💾 翻译缓存

翻译结果以「文本 + 引擎 + 语言」为键持久化在 SQLite（`~/.cache/pdfgenie/translation_cache.db`），相同句子秒级命中。

- 仅成功译文会写入缓存；Google 引擎失败时返回占位串 `TRANSLATION_ERROR`，该串也会按"已翻译"回填页面——重跑前可先检查日志中是否有网络异常。
- 调试译者/渲染器改动时，旧缓存可能让代码变更看起来不生效，可用以下命令清空：

```python
from pdfgenie.cache import clear_cache
clear_cache()
```
