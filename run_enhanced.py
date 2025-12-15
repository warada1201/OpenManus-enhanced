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

from app.agent.enhanced_manus import EnhancedManus
from app.logger import logger


async def main():
    parser = argparse.ArgumentParser(
        description="Run Enhanced OpenManus agent with improved capabilities"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        required=False,
        help="Input prompt for the agent"
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=15,
        help="Maximum steps for execution (default: 15)"
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
        logger.info(f"   Memory context loaded: {'Yes' if agent.memory_context else 'No'}")

        result = await agent.run(prompt)

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


if __name__ == "__main__":
    asyncio.run(main())
