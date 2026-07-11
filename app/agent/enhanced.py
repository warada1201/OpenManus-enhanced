"""
OpenManus Enhanced Base Agent

本家Manusのような自己修正メカニズムとAPIコスト最適化を備えた
強化版ベースエージェント。

このファイルをインポートして既存のBaseAgentを拡張する
ミックスインとして使用する。
"""

from enum import Enum
from typing import Dict, List, Tuple

from pydantic import Field

from app.logger import logger


class ErrorType(str, Enum):
    """エラータイプの分類"""

    TOOL_EXECUTION = "tool_execution"
    API_ERROR = "api_error"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


class RecoveryStrategy(str, Enum):
    """リカバリー戦略"""

    RETRY = "retry"  # 同じアプローチで再試行
    ALTERNATIVE = "alternative"  # 別のアプローチを試す
    SIMPLIFY = "simplify"  # タスクを簡略化
    SKIP = "skip"  # このステップをスキップ
    ABORT = "abort"  # 実行を中止


# 既知のエラーパターンと対処法
ERROR_RECOVERY_MAP: Dict[str, Tuple[RecoveryStrategy, str]] = {
    "rate limit": (RecoveryStrategy.RETRY, "Wait and retry after delay"),
    "timeout": (RecoveryStrategy.RETRY, "Retry with increased timeout"),
    "invalid json": (RecoveryStrategy.ALTERNATIVE, "Try simpler format"),
    "tool not found": (RecoveryStrategy.ALTERNATIVE, "Use different tool"),
    "permission denied": (RecoveryStrategy.SKIP, "Skip this operation"),
    "file not found": (RecoveryStrategy.ALTERNATIVE, "Check path and retry"),
    "connection error": (RecoveryStrategy.RETRY, "Retry with backoff"),
    "token limit": (RecoveryStrategy.SIMPLIFY, "Reduce context size"),
}


class SelfCorrectionMixin:
    """
    自己修正機能のミックスイン

    エラー検出と自動リカバリーを提供する。
    BaseAgentに組み込んで使用する。
    """

    # エラー履歴
    error_history: List[Dict] = Field(default_factory=list)
    max_retries_per_step: int = 3
    current_retry_count: int = 0

    def classify_error(self, error: Exception) -> ErrorType:
        """エラーの分類"""
        error_str = str(error).lower()

        if "timeout" in error_str:
            return ErrorType.TIMEOUT
        elif "api" in error_str or "rate" in error_str:
            return ErrorType.API_ERROR
        elif "validation" in error_str or "invalid" in error_str:
            return ErrorType.VALIDATION
        elif "tool" in error_str or "execution" in error_str:
            return ErrorType.TOOL_EXECUTION
        else:
            return ErrorType.UNKNOWN

    def get_recovery_strategy(self, error: Exception) -> Tuple[RecoveryStrategy, str]:
        """エラーに対するリカバリー戦略を決定"""
        error_str = str(error).lower()

        # 既知のパターンをチェック
        for pattern, (strategy, hint) in ERROR_RECOVERY_MAP.items():
            if pattern in error_str:
                logger.info(f"🔧 Found recovery strategy for error: {strategy.value}")
                return strategy, hint

        # デフォルト戦略
        if self.current_retry_count < self.max_retries_per_step:
            return RecoveryStrategy.RETRY, "Retry with different approach"
        else:
            return RecoveryStrategy.SIMPLIFY, "Simplify the task and try again"

    def record_error(self, error: Exception, context: str = ""):
        """エラーを記録"""
        error_record = {
            "type": self.classify_error(error).value,
            "message": str(error)[:500],
            "context": context[:200],
            "retry_count": self.current_retry_count,
        }
        self.error_history.append(error_record)

        # メモリに保存（永続メモリがあれば）
        try:
            from app.memory.persistent import persistent_memory

            persistent_memory.save_error_pattern(
                error_type=error_record["type"], error_message=error_record["message"]
            )
        except ImportError:
            pass

    def get_correction_prompt(self, error: Exception) -> str:
        """自己修正用のプロンプトを生成"""
        strategy, hint = self.get_recovery_strategy(error)

        prompts = {
            RecoveryStrategy.RETRY: f"Previous attempt failed. {hint}. Try again with a different approach.",
            RecoveryStrategy.ALTERNATIVE: f"The previous method didn't work. {hint}. Use an alternative solution.",
            RecoveryStrategy.SIMPLIFY: f"Task is too complex. {hint}. Break it into simpler steps.",
            RecoveryStrategy.SKIP: f"Cannot complete this step. {hint}. Proceed to next step.",
            RecoveryStrategy.ABORT: "Critical error. Cannot continue execution.",
        }

        return prompts.get(strategy, "Error occurred. Try a different approach.")

    def should_continue_after_error(self, error: Exception) -> bool:
        """エラー後に続行すべきかを判断"""
        strategy, _ = self.get_recovery_strategy(error)

        if strategy == RecoveryStrategy.ABORT:
            return False

        if self.current_retry_count >= self.max_retries_per_step:
            logger.warning(f"Max retries ({self.max_retries_per_step}) reached")
            return False

        self.current_retry_count += 1
        return True

    def reset_retry_count(self):
        """リトライカウントをリセット"""
        self.current_retry_count = 0


class CostOptimizationMixin:
    """
    APIコスト最適化のミックスイン

    トークン使用量の追跡と最適化を提供する。
    """

    # トークン追跡（pydanticモデル側で PrivateAttr として再宣言される）
    _total_tokens_used: int = 0
    _step_tokens = None

    # コスト最適化設定
    max_context_messages: int = 10  # 保持するメッセージ数
    truncate_long_outputs: bool = True
    max_output_length: int = 2000

    @staticmethod
    def _get_role(msg) -> str:
        if hasattr(msg, "role"):
            return msg.role
        if isinstance(msg, dict):
            return msg.get("role", "")
        return ""

    def optimize_context(self, messages: List) -> List:
        """コンテキストメッセージを最適化

        上限を超えたときだけ、上限の約半分まで一気に削る（ヒステリシス方式）。
        毎ステップ先頭を1つずつ削るとメッセージ列のプレフィックスが常に変化し、
        DeepSeek等の自動プロンプトキャッシュ（ヒット時は入力単価が大幅に安い）が
        全く効かなくなるため、削減の頻度を下げてプレフィックスを安定させる。
        """
        if len(messages) <= self.max_context_messages:
            return messages

        # システムメッセージは保持
        important_msgs = [m for m in messages if self._get_role(m) == "system"]
        regular_msgs = [m for m in messages if self._get_role(m) != "system"]

        # 上限の半分まで削り、次の削減まで履歴の先頭を安定させる
        keep_count = max(self.max_context_messages // 2 - len(important_msgs), 1)
        recent_msgs = regular_msgs[-keep_count:]

        # 先頭が tool メッセージだと対応する assistant の tool_calls を失って
        # APIエラーになるため、先頭の tool メッセージは取り除く
        while recent_msgs and self._get_role(recent_msgs[0]) == "tool":
            recent_msgs.pop(0)

        logger.info(
            f"📉 Optimized context: {len(messages)} -> {len(important_msgs) + len(recent_msgs)} messages"
        )
        return important_msgs + recent_msgs

    def truncate_output(self, output: str) -> str:
        """長い出力を切り詰め"""
        if not self.truncate_long_outputs:
            return output

        if len(output) <= self.max_output_length:
            return output

        # 先頭と末尾を保持
        head_len = self.max_output_length // 2
        tail_len = self.max_output_length // 2 - 50

        truncated = output[:head_len] + "\n...[truncated]...\n" + output[-tail_len:]
        logger.info(f"📏 Truncated output: {len(output)} -> {len(truncated)} chars")
        return truncated

    def track_tokens(self, token_count: int):
        """トークン使用量を追跡"""
        self._total_tokens_used += token_count
        if not isinstance(self._step_tokens, list):
            self._step_tokens = []
        self._step_tokens.append(token_count)

    def get_token_summary(self) -> Dict:
        """トークン使用量のサマリー（LLMクライアントのAPI実測値を優先）"""
        llm = getattr(self, "llm", None)
        if llm is not None and hasattr(llm, "total_input_tokens"):
            summary = {
                "input_tokens": llm.total_input_tokens,
                "completion_tokens": llm.total_completion_tokens,
                "total": llm.total_input_tokens + llm.total_completion_tokens,
                "cache_hit_tokens": getattr(llm, "total_cache_hit_tokens", 0),
            }
            if llm.total_input_tokens:
                summary[
                    "cache_hit_rate"
                ] = f"{summary['cache_hit_tokens'] / llm.total_input_tokens * 100:.0f}%"
            cost = (
                llm.get_cost_estimate() if hasattr(llm, "get_cost_estimate") else None
            )
            if cost is not None:
                summary["estimated_cost_usd"] = round(cost, 4)
            return summary

        step_tokens = self._step_tokens if isinstance(self._step_tokens, list) else []
        if not step_tokens:
            return {"total": 0, "avg_per_step": 0, "steps": 0}

        return {
            "total": self._total_tokens_used,
            "avg_per_step": self._total_tokens_used // len(step_tokens),
            "steps": len(step_tokens),
        }

    def should_optimize(self) -> bool:
        """最適化が必要かを判断"""
        if len(self._step_tokens) < 3:
            return False

        # 平均より大幅に多いトークンを使用している場合
        avg = sum(self._step_tokens) / len(self._step_tokens)
        recent = self._step_tokens[-1] if self._step_tokens else 0

        return recent > avg * 1.5


def get_efficient_system_prompt(base_prompt: str, max_length: int = 1000) -> str:
    """システムプロンプトを効率化"""
    if len(base_prompt) <= max_length:
        return base_prompt

    # 重要な部分を保持して圧縮
    lines = base_prompt.split("\n")
    important_lines = [l for l in lines if l.strip()][:10]

    return "\n".join(important_lines)[:max_length]
