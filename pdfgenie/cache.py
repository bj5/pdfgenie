"""翻译缓存模块：避免重复翻译，降低成本

参考 pdf2zh 的 cache 模块设计，使用 SQLite 存储翻译结果。
"""

import json
import logging
import os
import sqlite3
import threading
from typing import Optional

from pdfgenie.utils import deterministic_hash

log = logging.getLogger(__name__)

_DB_DIR = os.path.expanduser("~/.cache/pdfgenie")
_DB_PATH = os.path.join(_DB_DIR, "translation_cache.db")
_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    os.makedirs(_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            source_text TEXT,
            translated_text TEXT,
            translator TEXT,
            lang_in TEXT,
            lang_out TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


# 模块级连接（懒初始化）
_conn: Optional[sqlite3.Connection] = None


def _ensure_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _get_connection()
    return _conn


def cache_key(source_text: str, translator_name: str, lang_in: str, lang_out: str) -> str:
    """生成缓存 key"""
    return deterministic_hash(source_text, translator_name, lang_in, lang_out)


def load_translation(key: str) -> Optional[str]:
    """从缓存加载翻译结果"""
    with _lock:
        conn = _ensure_conn()
        cursor = conn.execute("SELECT translated_text FROM cache WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            log.debug(f"Cache hit: {key[:8]}...")
            return row[0]
        return None


def save_translation(key: str, source_text: str, translated_text: str,
                     translator_name: str, lang_in: str, lang_out: str) -> None:
    """保存翻译结果到缓存"""
    with _lock:
        conn = _ensure_conn()
        conn.execute(
            """INSERT OR REPLACE INTO cache
               (key, source_text, translated_text, translator, lang_in, lang_out)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key, source_text, translated_text, translator_name, lang_in, lang_out)
        )
        conn.commit()
        log.debug(f"Cache saved: {key[:8]}...")


def get_cache_stats() -> dict:
    """获取缓存统计信息"""
    with _lock:
        conn = _ensure_conn()
        cursor = conn.execute("SELECT COUNT(*), SUM(LENGTH(translated_text)) FROM cache")
        count, total_size = cursor.fetchone()
        return {
            "entries": count or 0,
            "total_chars": total_size or 0,
            "db_path": _DB_PATH,
        }


def clear_cache() -> int:
    """清除所有缓存，返回删除条目数"""
    with _lock:
        conn = _ensure_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM cache")
        count = cursor.fetchone()[0]
        conn.execute("DELETE FROM cache")
        conn.commit()
        return count
