"""
OpenManus Enhanced - 強化版OpenManusの実行スクリプト

本家Manusに近い機能を持つ強化版エージェントを使用して
タスクを実行します。

機能:
- 永続メモリ（過去の経験から学習）
- 自己修正（エラー時に自動回復）
- APIコスト最適化（トークン使用量を削減）
"""

import argparse
import asyncio
import sys

from app.agent.enhanced_manus import EnhancedManus
from app.logger import logger


if sys.platform == "win32":
    # Windows では Playwright のサブプロセスのパイプがイベントループ終了後に
    # GC されると、無害な "unclosed transport ... I/O operation on closed pipe"
    # が大量に表示される (CPython の既知の問題)。__del__ を包んで抑止する。
    from asyncio import base_subprocess, proactor_events

    def _silence_transport_del(cls):
        original_del = cls.__del__

        def __del__(self, *args, **kwargs):
            try:
                original_del(self, *args, **kwargs)
            except (ValueError, RuntimeError):
                pass

        cls.__del__ = __del__

    _silence_transport_del(proactor_events._ProactorBasePipeTransport)
    _silence_transport_del(base_subprocess.BaseSubprocessTransport)


async def main():
    parser = argparse.ArgumentParser(
        description="Run Enhanced OpenManus agent with improved capabilities"
    )
    parser.add_argument(
        "--prompt", type=str, required=False, help="Input prompt for the agent"
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=15,
        help="Maximum steps for execution (default: 15)",
    )
    args = parser.parse_args()

    # 強化版エージェントを作成
    agent = await EnhancedManus.create()
    agent.max_steps = args.max_steps

    try:
        # プロンプトを取得
        prompt = args.prompt if args.prompt else input("Enter your prompt: ")
        if not prompt.strip():
            logger.warning("Empty prompt provided.")
            return

        logger.info("🚀 Starting Enhanced OpenManus...")
        logger.info(f"   Max steps: {args.max_steps}")
        logger.info(
            f"   Memory context loaded: {'Yes' if agent.memory_context else 'No'}"
        )

        await agent.run(prompt)

        # トークン使用量のレポート
        token_summary = agent.get_token_summary()
        logger.info(f"📊 Token usage: {token_summary}")
        logger.info("✅ Task completed successfully.")

    except KeyboardInterrupt:
        logger.warning("Operation interrupted by user.")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await agent.cleanup()
        if sys.platform == "win32":
            # Playwright のブラウザサブプロセスのパイプが閉じ切る前に
            # イベントループが終了すると、GC 時に "unclosed transport"
            # (I/O operation on closed pipe) が大量に出力されるため、
            # クローズ処理が完了するまで少し待つ。
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
