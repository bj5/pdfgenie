"""PDFGenie CLI 入口 + 核心 pipeline

将所有模块串联起来：
PDF → 检测 → 版面分析 → 文本提取 → 翻译 → 格式回填 → 输出

默认: Google 翻译, EN→ZH, 双语对照
"""

import argparse
import logging
import os
import sys
import time
from typing import List, Optional

import fitz  # PyMuPDF
import numpy as np
from tqdm import tqdm

from pdfgenie.detector import detect_document, DocumentInfo
from pdfgenie.extractor import TextExtractor, PageContent
from pdfgenie.layout_analyzer import LayoutAnalyzer
from pdfgenie.renderer import PDFRenderer
from pdfgenie.translator import create_translator, translate_blocks, BaseTranslator

log = logging.getLogger(__name__)


def translate_pdf(
    input_path: str,
    output_path: Optional[str] = None,
    service: str = "google",
    lang_in: str = "en",
    lang_out: str = "zh-CN",
    model: str = "",
    pages: Optional[List[int]] = None,
    use_layout: bool = True,
    max_workers: int = 4,
    bilingual: bool = True,
    verbose: bool = False,
) -> str:
    """PDF 全文翻译主流程

    Args:
        input_path: 输入 PDF 路径
        output_path: 输出 PDF 路径
        service: 翻译服务 (google/openai/ollama/lmstudio)
        lang_in: 源语言
        lang_out: 目标语言
        model: 翻译模型（仅 openai/ollama/lmstudio）
        pages: 指定页码列表 (0-indexed)
        use_layout: 是否使用 PP-Structure 版面分析
        max_workers: 翻译并发线程数
        bilingual: True=双语对照, False=纯译文
        verbose: 详细日志
    Returns:
        输出文件路径
    """
    # 设置日志
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"文件不存在: {input_path}")

    if output_path is None:
        base, ext = os.path.splitext(input_path)
        suffix = "_bilingual" if bilingual else "_translated"
        output_path = f"{base}{suffix}{ext}"

    mode_str = "双语对照" if bilingual else "纯译文"
    start_time = time.time()
    log.info(f"=" * 60)
    log.info(f"PDFGenie 全文翻译")
    log.info(f"输入: {input_path}")
    log.info(f"输出: {output_path}")
    log.info(f"翻译: {service} ({lang_in} → {lang_out})")
    log.info(f"模式: {mode_str}")
    log.info(f"=" * 60)

    # ── 阶段 1: 文档加载与检测 ─────────────────────────────────────────
    log.info("📄 阶段 1: 文档加载与检测...")
    doc = fitz.open(input_path)
    if pages is not None:
        valid_pages = [p for p in pages if 0 <= p < len(doc)]
        if not valid_pages:
            raise ValueError(f"指定的页码超出文档范围: {pages} (总页数: {len(doc)})")
        doc.select(valid_pages)
        log.info(f"已选择指定页码处理，共 {len(doc)} 页")

    from pdfgenie.detector import detect_document_from_doc
    doc_info = detect_document_from_doc(doc, input_path)
    log.info(doc_info.summary())

    # ── 阶段 2: 初始化组件 ────────────────────────────────────────────
    log.info("⚙️  阶段 2: 初始化组件...")
    translator = create_translator(service, lang_out, lang_in, model)
    extractor = TextExtractor()
    layout_analyzer = LayoutAnalyzer() if use_layout else None
    renderer = PDFRenderer(lang_out, bilingual=bilingual)

    # ── 阶段 3~5: 逐页处理 ───────────────────────────────────────────
    all_pages_content: List[PageContent] = []

    for page_info in tqdm(doc_info.pages, desc="翻译进度", unit="页"):
        page_no = page_info.page_no
        page = doc[page_no]

        # 阶段 3: 版面分析
        layout = None
        if layout_analyzer and page_info.needs_ocr:
            log.info(f"🔍 Page {page_no}: 版面分析...")
            pix = page.get_pixmap(dpi=150)
            image = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:
                image = image[:, :, :3]
            layout = layout_analyzer.analyze(
                image, page_no, page.rect.width, page.rect.height
            )

        # 阶段 4: 文本提取
        log.info(f"📝 Page {page_no}: 文本提取 ({page_info.mode})...")
        page_content = extractor.extract_page(doc, page_no, page_info, layout)

        # 阶段 5: 翻译
        translatable_count = len(page_content.translatable_blocks)
        if translatable_count > 0:
            log.info(f"🌐 Page {page_no}: 翻译 {translatable_count} 个文本块...")
            translate_blocks(page_content.blocks, translator, max_workers)
        else:
            log.info(f"⏭️  Page {page_no}: 无需翻译")

        all_pages_content.append(page_content)

    # ── 阶段 6: 格式回填 ─────────────────────────────────────────────
    log.info(f"🎨 阶段 6: 格式回填 ({mode_str})...")
    result_path = renderer.render(doc, all_pages_content, output_path)

    doc.close()

    elapsed = time.time() - start_time
    total_blocks = sum(
        len(pc.translatable_blocks) for pc in all_pages_content
    )
    translated_blocks = sum(
        sum(1 for b in pc.blocks if b.is_translated)
        for pc in all_pages_content
    )

    log.info(f"=" * 60)
    log.info(f"✅ 翻译完成!")
    log.info(f"   模式: {mode_str}")
    log.info(f"   页数: {len(doc_info.pages)}")
    log.info(f"   文本块: {total_blocks} 个, 已翻译: {translated_blocks} 个")
    log.info(f"   耗时: {elapsed:.1f}s")
    log.info(f"   输出: {result_path}")
    log.info(f"=" * 60)

    return result_path


def parse_page_range(page_str: str) -> List[int]:
    """解析页码范围字符串，如 '1-5,10,15-20'

    注意：用户输入 1-indexed，内部转为 0-indexed
    """
    pages = []
    for part in page_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start) - 1, int(end)))
        else:
            pages.append(int(part) - 1)
    return sorted(set(pages))


def main():
    parser = argparse.ArgumentParser(
        prog="pdfgenie",
        description="PDFGenie - PDF 全文翻译工具 (PaddleOCR + PyMuPDF)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  %(prog)s input.pdf                                  # 默认: Google 双语对照 EN→ZH
  %(prog)s input.pdf -o output.pdf                    # 指定输出文件
  %(prog)s input.pdf --mono                           # 纯译文模式（移除原文）
  %(prog)s input.pdf --service openai --model gpt-4o-mini
  %(prog)s input.pdf --service ollama --model qwen2.5:7b
  %(prog)s input.pdf --service lmstudio               # LMStudio 本地模型
  %(prog)s input.pdf --service lmstudio --model qwen2.5-7b-instruct
  %(prog)s input.pdf --pages 1-5,10                   # 仅翻译指定页码
  %(prog)s input.pdf -v                               # 详细日志

环境变量:
  OPENAI_API_KEY      OpenAI API 密钥
  OPENAI_BASE_URL     OpenAI 兼容 API 地址
  LMSTUDIO_BASE_URL   LMStudio API 地址 (默认: http://localhost:1234/v1)
        """,
    )

    parser.add_argument("input", help="输入 PDF 文件路径")
    parser.add_argument("-o", "--output", help="输出 PDF 文件路径")
    parser.add_argument("--service", default="google",
                        choices=["google", "openai", "ollama", "lmstudio"],
                        help="翻译服务 (默认: google)")
    parser.add_argument("--lang-in", default="en", help="源语言 (默认: en)")
    parser.add_argument("--lang-out", default="zh-CN", help="目标语言 (默认: zh-CN)")
    parser.add_argument("--model", default="", help="翻译模型 (openai/ollama/lmstudio)")
    parser.add_argument("--pages", default=None,
                        help="页码范围，如 1-5,10 (1-indexed)")
    parser.add_argument("--mono", action="store_true",
                        help="纯译文模式（移除原文，仅显示译文）")
    parser.add_argument("--no-layout", action="store_true",
                        help="跳过 PP-Structure 版面分析")
    parser.add_argument("--workers", type=int, default=4,
                        help="翻译并发线程数 (默认: 4)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="详细日志输出")

    args = parser.parse_args()

    pages = parse_page_range(args.pages) if args.pages else None

    try:
        result = translate_pdf(
            input_path=args.input,
            output_path=args.output,
            service=args.service,
            lang_in=args.lang_in,
            lang_out=args.lang_out,
            model=args.model,
            pages=pages,
            use_layout=not args.no_layout,
            max_workers=args.workers,
            bilingual=not args.mono,
            verbose=args.verbose,
        )
        print(f"\n✅ 翻译完成: {result}")
    except KeyboardInterrupt:
        print("\n⚠️  用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
