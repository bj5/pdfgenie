"""工具函数：文本清理、坐标运算、阅读顺序排序等"""

import hashlib
import logging
import re
import unicodedata
from typing import List, Tuple

import numpy as np

log = logging.getLogger(__name__)

# ── 文本清理 ─────────────────────────────────────────────────────────

def remove_control_characters(s: str) -> str:
    """移除 Unicode 控制字符"""
    return "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")


def is_garbled(text: str, threshold: float = 0.3) -> bool:
    """判断文本是否为乱码（高比例替换字符 / PUA 字符）

    Args:
        text: 待检测文本
        threshold: 乱码字符比例阈值
    Returns:
        True 表示文本很可能是乱码
    """
    if not text.strip():
        return True
    bad = 0
    for ch in text:
        cat = unicodedata.category(ch)
        cp = ord(ch)
        # 替换字符、PUA 区域、未定义字符
        if ch == '\ufffd' or cat in ('Co', 'Cn') or (0xE000 <= cp <= 0xF8FF):
            bad += 1
    return bad / max(len(text), 1) > threshold


def deterministic_hash(*args) -> str:
    """生成确定性 hash，用于翻译缓存的 key"""
    data = "|".join(str(a) for a in args)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


# ── 坐标运算 ─────────────────────────────────────────────────────────

Bbox = Tuple[float, float, float, float]  # (x0, y0, x1, y1)


def bbox_area(box: Bbox) -> float:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def bbox_overlap(a: Bbox, b: Bbox) -> float:
    """计算两个 bbox 重叠面积"""
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    return max(0, x1 - x0) * max(0, y1 - y0)


def bbox_iou(a: Bbox, b: Bbox) -> float:
    overlap = bbox_overlap(a, b)
    area_a = bbox_area(a)
    area_b = bbox_area(b)
    union = area_a + area_b - overlap
    return overlap / max(union, 1e-6)


def bbox_contains(outer: Bbox, inner: Bbox, tolerance: float = 5.0) -> bool:
    """判断 outer 是否包含 inner（允许容差）"""
    return (inner[0] >= outer[0] - tolerance and
            inner[1] >= outer[1] - tolerance and
            inner[2] <= outer[2] + tolerance and
            inner[3] <= outer[3] + tolerance)


def merge_bboxes(boxes: List[Bbox]) -> Bbox:
    """合并多个 bbox 为最小外接矩形"""
    if not boxes:
        return (0, 0, 0, 0)
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return (x0, y0, x1, y1)


# ── XY-Cut 阅读顺序算法 ──────────────────────────────────────────────

def xy_cut_sort(blocks: list, key_func=None) -> list:
    """XY-Cut 递归排序，恢复多栏布局的自然阅读顺序

    Args:
        blocks: 待排序的文本块列表
        key_func: 从 block 中提取 bbox 的函数，默认直接取 block['bbox']
    Returns:
        按阅读顺序排列的 blocks
    """
    if len(blocks) <= 1:
        return blocks

    if key_func is None:
        key_func = lambda b: b['bbox']

    # 尝试水平切分（找到一条水平线将 blocks 分为上下两组）
    bboxes = [key_func(b) for b in blocks]
    sorted_by_y = sorted(range(len(blocks)), key=lambda i: bboxes[i][1])

    # 找最大的垂直间隙做水平切分
    max_gap = 0
    split_idx = -1
    for i in range(len(sorted_by_y) - 1):
        curr_bottom = bboxes[sorted_by_y[i]][3]
        next_top = bboxes[sorted_by_y[i + 1]][1]
        gap = next_top - curr_bottom
        if gap > max_gap:
            max_gap = gap
            split_idx = i

    # 如果找到有意义的间隙（> 5pt），水平切分
    if max_gap > 5 and split_idx >= 0:
        top_indices = set(sorted_by_y[:split_idx + 1])
        top = [blocks[i] for i in range(len(blocks)) if i in top_indices]
        bottom = [blocks[i] for i in range(len(blocks)) if i not in top_indices]
        return xy_cut_sort(top, key_func) + xy_cut_sort(bottom, key_func)

    # 尝试垂直切分（找到一条竖直线将 blocks 分为左右两组）
    sorted_by_x = sorted(range(len(blocks)), key=lambda i: bboxes[i][0])
    max_gap = 0
    split_idx = -1
    for i in range(len(sorted_by_x) - 1):
        curr_right = bboxes[sorted_by_x[i]][2]
        next_left = bboxes[sorted_by_x[i + 1]][0]
        gap = next_left - curr_right
        if gap > max_gap:
            max_gap = gap
            split_idx = i

    if max_gap > 20 and split_idx >= 0:  # 垂直间隙要更大才算分栏
        left_indices = set(sorted_by_x[:split_idx + 1])
        left = [blocks[i] for i in range(len(blocks)) if i in left_indices]
        right = [blocks[i] for i in range(len(blocks)) if i not in left_indices]
        return xy_cut_sort(left, key_func) + xy_cut_sort(right, key_func)

    # 无法进一步切分，按 y 坐标排序
    return [blocks[i] for i in sorted_by_y]


def int_to_rgb(color_int: int) -> Tuple[float, float, float]:
    """将 PyMuPDF 整数颜色值转换为归一化 RGB 元组 (0.0~1.0)"""
    if color_int == 0:
        return (0.0, 0.0, 0.0)
    r = ((color_int >> 16) & 255) / 255.0
    g = ((color_int >> 8) & 255) / 255.0
    b = (color_int & 255) / 255.0
    return (r, g, b)


def protect_formulas(text: str) -> Tuple[str, dict]:
    """保护 LaTeX 公式和特殊符号，避免被翻译引擎破坏

    Returns:
        (protected_text, formula_dict)
    """
    formulas = {}
    counter = 0

    def repl(match):
        nonlocal counter
        token = f"__MATH_{counter}__"
        formulas[token] = match.group(0)
        counter += 1
        return token

    # 保护 $$...$$、$...$ 和 \(...\)
    text = re.sub(r'\$\$.*?\$\$', repl, text, flags=re.DOTALL)
    text = re.sub(r'\$[^\$\n]+?\$', repl, text)
    text = re.sub(r'\\\(.*?\\\)', repl, text)
    return text, formulas


def restore_formulas(text: str, formulas: dict) -> str:
    """还原被保护的公式标记"""
    for token, formula in formulas.items():
        # 兼容翻译引擎可能在 token 周围添加的空格
        text = text.replace(token, formula)
        text = re.sub(re.escape(token.replace("_", " ")), formula, text, flags=re.IGNORECASE)
    return text


def clean_hyphenation(parts: List[str]) -> str:
    """智能合并多行英文文本并处理行尾连字符 (hyphenation)

    如 "inter-" + "national" -> "international"
    """
    if not parts:
        return ""
    merged = parts[0]
    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue
        if (merged.endswith("-") and not merged.endswith(" -")
                and len(merged) > 1 and merged[-2].isalpha() and part[0].isalpha()):
            merged = merged[:-1] + part
        else:
            merged += " " + part
    return merged

