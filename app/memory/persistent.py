"""
OpenManus Persistent Memory System

本家Manusのように過去のインタラクションを記憶し、
セッション間で学習・適応するためのメモリシステム。
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.config import WORKSPACE_ROOT
from app.logger import logger


class MemoryEntry(BaseModel):
    """単一のメモリエントリ"""

    id: str
    timestamp: str
    task_type: str  # "code", "search", "browser", "analysis" など
    task_summary: str
    success: bool
    tools_used: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class UserPreference(BaseModel):
    """ユーザー設定の保存"""

    key: str
    value: Any
    updated_at: str


class PersistentMemory:
    """
    SQLiteベースの永続メモリシステム

    機能:
    - タスク履歴の保存と検索
    - 成功/失敗パターンの学習
    - ユーザー設定の保存
    - よく使うツールの追跡
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.db_path = WORKSPACE_ROOT / ".openmanus_memory.db"
        self._init_database()
        self._initialized = True
        logger.info(f"Persistent memory initialized at {self.db_path}")

    @contextmanager
    def _get_connection(self):
        """データベース接続のコンテキストマネージャ"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def _init_database(self):
        """データベーステーブルの初期化"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # タスク履歴テーブル
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS task_history (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    task_summary TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    tools_used TEXT,
                    notes TEXT,
                    token_count INTEGER DEFAULT 0
                )
            """
            )

            # ユーザー設定テーブル
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """
            )

            # ツール使用統計テーブル
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_statistics (
                    tool_name TEXT PRIMARY KEY,
                    use_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    last_used TEXT
                )
            """
            )

            # エラーパターンテーブル
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS error_patterns (
                    error_hash TEXT PRIMARY KEY,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    recovery_strategy TEXT,
                    occurrence_count INTEGER DEFAULT 1,
                    last_occurred TEXT
                )
            """
            )

    def generate_task_id(self, task_summary: str) -> str:
        """タスクIDの生成"""
        timestamp = datetime.now().isoformat()
        content = f"{timestamp}:{task_summary}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def save_task(
        self,
        task_type: str,
        task_summary: str,
        success: bool,
        tools_used: List[str],
        notes: Optional[str] = None,
        token_count: int = 0,
    ) -> str:
        """タスクの保存"""
        task_id = self.generate_task_id(task_summary)
        timestamp = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO task_history
                (id, timestamp, task_type, task_summary, success, tools_used, notes, token_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    task_id,
                    timestamp,
                    task_type,
                    task_summary,
                    1 if success else 0,
                    json.dumps(tools_used),
                    notes,
                    token_count,
                ),
            )

            # ツール統計の更新
            for tool in tools_used:
                self._update_tool_statistics(cursor, tool, success)

        logger.info(
            f"Saved task {task_id}: {task_type} - {'success' if success else 'failed'}"
        )
        return task_id

    def _update_tool_statistics(self, cursor, tool_name: str, success: bool):
        """ツール使用統計の更新"""
        cursor.execute(
            """
            INSERT INTO tool_statistics (tool_name, use_count, success_count, last_used)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(tool_name) DO UPDATE SET
                use_count = use_count + 1,
                success_count = success_count + ?,
                last_used = ?
        """,
            (
                tool_name,
                1 if success else 0,
                datetime.now().isoformat(),
                1 if success else 0,
                datetime.now().isoformat(),
            ),
        )

    def get_similar_tasks(self, task_type: str, limit: int = 5) -> List[MemoryEntry]:
        """類似タスクの取得（学習用）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM task_history
                WHERE task_type = ? AND success = 1
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (task_type, limit),
            )

            rows = cursor.fetchall()
            return [
                MemoryEntry(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    task_type=row["task_type"],
                    task_summary=row["task_summary"],
                    success=bool(row["success"]),
                    tools_used=json.loads(row["tools_used"] or "[]"),
                    notes=row["notes"],
                )
                for row in rows
            ]

    def get_preferred_tools(self, limit: int = 10) -> List[Dict[str, Any]]:
        """よく使用される成功率の高いツールを取得"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    tool_name,
                    use_count,
                    success_count,
                    CAST(success_count AS FLOAT) / use_count AS success_rate
                FROM tool_statistics
                WHERE use_count >= 3
                ORDER BY success_rate DESC, use_count DESC
                LIMIT ?
            """,
                (limit,),
            )

            return [dict(row) for row in cursor.fetchall()]

    def save_error_pattern(
        self,
        error_type: str,
        error_message: str,
        recovery_strategy: Optional[str] = None,
    ):
        """エラーパターンの保存（自己修正用）"""
        error_hash = hashlib.sha256(
            f"{error_type}:{error_message[:100]}".encode()
        ).hexdigest()[:16]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO error_patterns (error_hash, error_type, error_message, recovery_strategy, last_occurred)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(error_hash) DO UPDATE SET
                    occurrence_count = occurrence_count + 1,
                    recovery_strategy = COALESCE(?, recovery_strategy),
                    last_occurred = ?
            """,
                (
                    error_hash,
                    error_type,
                    error_message[:500],
                    recovery_strategy,
                    datetime.now().isoformat(),
                    recovery_strategy,
                    datetime.now().isoformat(),
                ),
            )

    def get_recovery_strategy(
        self, error_type: str, error_message: str
    ) -> Optional[str]:
        """既知のエラーに対するリカバリー戦略を取得"""
        error_hash = hashlib.sha256(
            f"{error_type}:{error_message[:100]}".encode()
        ).hexdigest()[:16]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT recovery_strategy FROM error_patterns
                WHERE error_hash = ? AND recovery_strategy IS NOT NULL
            """,
                (error_hash,),
            )

            row = cursor.fetchone()
            return row["recovery_strategy"] if row else None

    def set_preference(self, key: str, value: Any):
        """ユーザー設定の保存"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO user_preferences (key, value, updated_at)
                VALUES (?, ?, ?)
            """,
                (key, json.dumps(value), datetime.now().isoformat()),
            )

    def get_preference(self, key: str, default: Any = None) -> Any:
        """ユーザー設定の取得"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM user_preferences WHERE key = ?", (key,))
            row = cursor.fetchone()
            return json.loads(row["value"]) if row else default

    def get_token_usage_summary(self) -> Dict[str, int]:
        """トークン使用量のサマリー"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_tasks,
                    SUM(token_count) as total_tokens,
                    AVG(token_count) as avg_tokens_per_task
                FROM task_history
            """
            )
            row = cursor.fetchone()
            return dict(row) if row else {}

    def get_context_for_task(self, task_type: str) -> str:
        """
        タスク実行時に参照するコンテキストを生成
        APIコスト削減のため、簡潔にまとめる
        """
        similar_tasks = self.get_similar_tasks(task_type, limit=3)
        preferred_tools = self.get_preferred_tools(limit=5)

        if not similar_tasks and not preferred_tools:
            return ""

        context_parts = []

        if similar_tasks:
            context_parts.append("Based on past successful tasks:")
            for task in similar_tasks[:2]:  # 最大2件
                context_parts.append(f"- {task.task_summary[:100]}")

        if preferred_tools:
            tool_names = [t["tool_name"] for t in preferred_tools[:3]]
            context_parts.append(f"Preferred tools: {', '.join(tool_names)}")

        return "\n".join(context_parts)


# シングルトンインスタンス
persistent_memory = PersistentMemory()
