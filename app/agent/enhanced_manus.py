"""
OpenManus Enhanced Manus Agent

本家Manusに近い機能を持つ強化版Manusエージェント。
- 永続メモリ
- 自己修正
- 検証
- APIコスト最適化
"""

from typing import Dict, List, Optional

from pydantic import Field, PrivateAttr, model_validator

from app.agent.browser import BrowserContextHelper
from app.agent.enhanced import CostOptimizationMixin, SelfCorrectionMixin
from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.logger import logger
from app.prompt.enhanced import (
    ENHANCED_NEXT_STEP_PROMPT,
    build_recovery_prompt,
    build_system_prompt,
)
from app.tool import Terminate, ToolCollection
from app.tool.ask_human import AskHuman
from app.tool.browser_use_tool import BrowserUseTool
from app.tool.mcp import MCPClients, MCPClientTool
from app.tool.python_execute import PythonExecute
from app.tool.str_replace_editor import StrReplaceEditor


class EnhancedManus(ToolCallAgent, SelfCorrectionMixin, CostOptimizationMixin):
    """
    強化版Manusエージェント

    本家Manusのような機能を提供:
    - 永続メモリによる学習
    - 自己修正メカニズム
    - タスク検証
    - APIコスト最適化
    """

    name: str = "EnhancedManus"
    description: str = "An enhanced AI agent with memory, self-correction, and verification capabilities"

    # 永続メモリコンテキスト
    memory_context: str = ""

    # MCP clients
    mcp_clients: MCPClients = Field(default_factory=MCPClients)

    # ツール
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            PythonExecute(),
            BrowserUseTool(),
            StrReplaceEditor(),
            AskHuman(),
            Terminate(),
        )
    )

    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])
    browser_context_helper: Optional[BrowserContextHelper] = None

    # MCP server tracking
    connected_servers: Dict[str, str] = Field(default_factory=dict)
    _initialized: bool = False

    # 自己修正設定
    error_history: List[Dict] = Field(default_factory=list)
    max_retries_per_step: int = 3
    current_retry_count: int = 0

    # コスト最適化設定
    max_observe: int = 2000  # トークン節約のため出力を制限
    max_steps: int = 15  # ステップ数を適切に制限
    max_context_messages: int = 15  # コンテキストメッセージ数

    # トークン追跡
    _total_tokens_used: int = PrivateAttr(default=0)
    _step_tokens: List[int] = PrivateAttr(default_factory=list)

    # cleanup は run() と呼び出し元の両方の finally から呼ばれ得るため冪等にする
    _cleaned_up: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def initialize_helper(self) -> "EnhancedManus":
        """Initialize components."""
        self.browser_context_helper = BrowserContextHelper(self)
        self._load_memory_context()
        self._build_prompts()
        return self

    def _load_memory_context(self):
        """永続メモリからコンテキストをロード"""
        try:
            from app.memory.persistent import persistent_memory

            self.memory_context = persistent_memory.get_context_for_task("general")
        except Exception as e:
            logger.debug(f"Could not load memory context: {e}")
            self.memory_context = ""

    def _build_prompts(self):
        """プロンプトを構築"""
        self.system_prompt = build_system_prompt(
            directory=str(config.workspace_root), context=self.memory_context
        )
        self.next_step_prompt = ENHANCED_NEXT_STEP_PROMPT

    @classmethod
    async def create(cls, **kwargs) -> "EnhancedManus":
        """Factory method to create and initialize instance."""
        instance = cls(**kwargs)
        await instance.initialize_mcp_servers()
        instance._initialized = True
        return instance

    async def initialize_mcp_servers(self) -> None:
        """Initialize MCP server connections."""
        for server_id, server_config in config.mcp_config.servers.items():
            try:
                if server_config.type == "sse":
                    if server_config.url:
                        await self.connect_mcp_server(server_config.url, server_id)
                        logger.info(f"Connected to MCP server {server_id}")
                elif server_config.type == "stdio":
                    if server_config.command:
                        await self.connect_mcp_server(
                            server_config.command,
                            server_id,
                            use_stdio=True,
                            stdio_args=server_config.args,
                        )
                        logger.info(f"Connected to MCP server {server_id}")
            except Exception as e:
                logger.error(f"Failed to connect to MCP server {server_id}: {e}")

    async def connect_mcp_server(
        self,
        server_url: str,
        server_id: str = "",
        use_stdio: bool = False,
        stdio_args: List[str] = None,
    ) -> None:
        """Connect to MCP server."""
        if use_stdio:
            await self.mcp_clients.connect_stdio(
                server_url, stdio_args or [], server_id
            )
        else:
            await self.mcp_clients.connect_sse(server_url, server_id)

        self.connected_servers[server_id or server_url] = server_url
        new_tools = [
            tool for tool in self.mcp_clients.tools if tool.server_id == server_id
        ]
        self.available_tools.add_tools(*new_tools)

    async def disconnect_mcp_server(self, server_id: str = "") -> None:
        """Disconnect from MCP server."""
        await self.mcp_clients.disconnect(server_id)
        if server_id:
            self.connected_servers.pop(server_id, None)
        else:
            self.connected_servers.clear()

        base_tools = [
            tool
            for tool in self.available_tools.tools
            if not isinstance(tool, MCPClientTool)
        ]
        self.available_tools = ToolCollection(*base_tools)
        self.available_tools.add_tools(*self.mcp_clients.tools)

    async def cleanup(self):
        """Clean up resources."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        if self.browser_context_helper:
            await self.browser_context_helper.cleanup_browser()
        if self._initialized:
            await self.disconnect_mcp_server()
            self._initialized = False

        # タスク完了時にメモリに保存
        self._save_task_to_memory()

    def _save_task_to_memory(self):
        """タスク結果を永続メモリに保存"""
        try:
            from app.memory.persistent import persistent_memory

            # 使用したツールを抽出
            tools_used = []
            for msg in self.memory.messages:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        if hasattr(tc, "function"):
                            tools_used.append(tc.function.name)

            # タスク内容を抽出
            task_summary = ""
            for msg in self.memory.messages:
                if hasattr(msg, "role") and msg.role == "user" and msg.content:
                    task_summary = msg.content[:200]
                    break

            if task_summary:
                token_count = (
                    self.llm.total_input_tokens + self.llm.total_completion_tokens
                    if hasattr(self.llm, "total_input_tokens")
                    else self._total_tokens_used
                )
                persistent_memory.save_task(
                    task_type="general",
                    task_summary=task_summary,
                    success=True,  # 完了時は成功とみなす
                    tools_used=list(set(tools_used)),
                    token_count=token_count,
                )
        except Exception as e:
            logger.debug(f"Could not save task to memory: {e}")

    async def think(self) -> bool:
        """Process and decide with self-correction."""
        if not self._initialized:
            await self.initialize_mcp_servers()
            self._initialized = True

        # コンテキスト最適化
        if len(self.memory.messages) > self.max_context_messages:
            self.memory.messages = self.optimize_context(self.memory.messages)

        original_prompt = self.next_step_prompt

        # ブラウザ使用時のプロンプト調整
        recent_messages = self.memory.messages[-3:] if self.memory.messages else []
        browser_in_use = any(
            tc.function.name == BrowserUseTool().name
            for msg in recent_messages
            if msg.tool_calls
            for tc in msg.tool_calls
        )

        if browser_in_use:
            self.next_step_prompt = (
                await self.browser_context_helper.format_next_step_prompt()
            )

        try:
            result = await super().think()
            self.reset_retry_count()  # 成功時はリトライカウントをリセット
            return result
        except Exception as e:
            # 自己修正を試行
            if self.should_continue_after_error(e):
                self.record_error(e, "think")
                logger.warning(f"🔧 Self-correcting after error: {e}")
                self.next_step_prompt = build_recovery_prompt(str(e))
                return True  # 続行
            raise
        finally:
            self.next_step_prompt = original_prompt

    async def run(self, request: Optional[str] = None) -> str:
        """Run with verification and memory."""
        try:
            result = await super().run(request)

            # 軽量検証（オプション）
            if config.run_flow_config and hasattr(
                config.run_flow_config, "enable_verification"
            ):
                if config.run_flow_config.enable_verification:
                    await self._quick_verify(request or "", result)

            return result
        finally:
            await self.cleanup()

    async def _quick_verify(self, task: str, result: str):
        """軽量な検証を実行"""
        try:
            from app.agent.verification import quick_verify

            verification = await quick_verify(task, result, self.llm)
            logger.info(
                f"✓ Verification: {verification.status} - {verification.summary}"
            )
        except Exception as e:
            logger.debug(f"Verification skipped: {e}")
