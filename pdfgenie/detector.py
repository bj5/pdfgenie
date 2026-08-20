"""自动判别模块：扫描件 vs 文本 PDF

解决博文难点 1️⃣：扫描版 PDF 无文本层，OCR 是第一关。
逐页检测文本层密度，判断需要走文本提取还是 OCR 通道。
"""

import logging
from dataclasses import dataclass, field
from typing import List

import fitz  # PyMuPDF

from pdfgenie.utils import is_garbled

log = logging.getLogger(__name__)


@dataclass
class PageInfo:
    """单页检测结果"""
    page_no: int
    width: float
    height: float
    has_text_layer: bool = False       # 是否有可提取的文本层
    text_char_count: int = 0           # 文本层字符数
    text_is_garbled: bool = False      # 文本层是否乱码
    image_count: int = 0              # 页面图片数量
    is_scanned: bool = False          # 最终判定：是否为扫描页
    needs_ocr: bool = False           # 最终判定：是否需要 OCR

    @property
    def mode(self) -> str:
        if self.needs_ocr:
            return "ocr"
        return "text"


@dataclass
class DocumentInfo:
    """整个文档的检测结果"""
    path: str
    total_pages: int
    pages: List[PageInfo] = field(default_factory=list)

    @property
    def ocr_pages(self) -> List[int]:
        return [p.page_no for p in self.pages if p.needs_ocr]

    @property
    def text_pages(self) -> List[int]:
        return [p.page_no for p in self.pages if not p.needs_ocr]

    @property
    def is_fully_scanned(self) -> bool:
        return all(p.needs_ocr for p in self.pages)

    def summary(self) -> str:
        ocr_count = len(self.ocr_pages)
        text_count = len(self.text_pages)
        return (f"文档: {self.path}\n"
                f"总页数: {self.total_pages}\n"
                f"文本页: {text_count}, OCR页: {ocr_count}\n"
                f"{'全扫描件' if self.is_fully_scanned else '混合文档'}")


# 文本层字符密度阈值：低于此值认为文本层不可靠
# 一般一页 A4 至少有 50+ 个字符
MIN_TEXT_CHARS_PER_PAGE = 30


def detect_page(page: fitz.Page, page_no: int) -> PageInfo:
    """检测单页 PDF 的类型

    Args:
        page: PyMuPDF Page 对象
        page_no: 页码 (0-indexed)
    Returns:
        PageInfo
    """
    info = PageInfo(
        page_no=page_no,
        width=page.rect.width,
        height=page.rect.height,
    )

    # 1. 提取文本层
    text = page.get_text("text").strip()
    info.text_char_count = len(text)
    info.has_text_layer = info.text_char_count > 0

    # 2. 检测乱码
    if info.has_text_layer:
        info.text_is_garbled = is_garbled(text)

    # 3. 统计图片
    info.image_count = len(page.get_images(full=True))

    # 4. 综合判定
    if not info.has_text_layer:
        # 完全没有文本层 → 扫描件
        info.is_scanned = True
        info.needs_ocr = True
        log.info(f"Page {page_no}: 无文本层 → OCR")
    elif info.text_is_garbled:
        # 文本层乱码 → 需要 OCR
        info.needs_ocr = True
        log.info(f"Page {page_no}: 文本乱码 → OCR")
    elif info.text_char_count < MIN_TEXT_CHARS_PER_PAGE:
        # 文本过少（可能只有页码）→ 需要 OCR
        if info.image_count > 0:
            info.is_scanned = True
            info.needs_ocr = True
            log.info(f"Page {page_no}: 文本稀少({info.text_char_count}字) + 有图片 → OCR")
        else:
            # 可能是空白页或只有少量文字
            info.needs_ocr = False
            log.info(f"Page {page_no}: 文本稀少({info.text_char_count}字)但无图片 → 文本模式")
    else:
        # 正常文本页
        info.needs_ocr = False
        log.debug(f"Page {page_no}: 正常文本页 ({info.text_char_count}字)")

    return info


def detect_document_from_doc(doc: fitz.Document, doc_path: str = "") -> DocumentInfo:
    """从已打开的 PyMuPDF Document 检测所有页面类型"""
    doc_info = DocumentInfo(path=doc_path or doc.name, total_pages=len(doc))
    for page_no in range(len(doc)):
        page = doc[page_no]
        page_info = detect_page(page, page_no)
        doc_info.pages.append(page_info)
    return doc_info


def detect_document(pdf_path: str, page_range: List[int] = None) -> DocumentInfo:
    """检测整个 PDF 文档

    Args:
        pdf_path: PDF 文件路径
        page_range: 可选的页码范围 (0-indexed)
    Returns:
        DocumentInfo
    """
    doc = fitz.open(pdf_path)
    if page_range:
        valid_pages = [p for p in page_range if 0 <= p < len(doc)]
        doc.select(valid_pages)

    doc_info = detect_document_from_doc(doc, pdf_path)
    doc.close()

    log.info(doc_info.summary())
    return doc_info

