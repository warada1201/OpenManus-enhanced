"""
OpenManus Verification Agent

本家Manusのように成果物の検証と品質保証を行う
専用の検証エージェント。
"""

from typing import Any, Dict, List, Optional

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.llm import LLM
from app.logger import logger
from app.schema import AgentState, Message
from app.tool import ToolCollection, Terminate
from app.tool.python_execute import PythonExecute
from app.tool.str_replace_editor import StrReplaceEditor


VERIFICATION_SYSTEM_PROMPT = """You are a Verification Agent for OpenManus, responsible for quality assurance.

Your role is to:
1. Verify that tasks have been completed correctly
2. Check for errors or issues in the output
3. Validate that the results match the original requirements
4. Suggest improvements or corrections if needed

Be concise and efficient to minimize token usage. Focus only on critical verification points.

Respond with a structured verification report:
- PASS: If the task was completed successfully
- FAIL: If there are issues that need to be addressed
- PARTIAL: If mostly complete but with minor issues

Include specific findings and recommendations."""

VERIFICATION_NEXT_STEP_PROMPT = """Analyze the task results and provide a verification report.
Be brief and focus on:
1. Does the output meet requirements?
2. Are there any errors or issues?
3. What is the overall quality assessment?

Use `terminate` when verification is complete."""


class VerificationResult:
    """検証結果"""

    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"

    def __init__(
        self,
        status: str,
        summary: str,
        findings: List[str] = None,
        recommendations: List[str] = None
    ):
        self.status = status
        self.summary = summary
        self.findings = findings or []
        self.recommendations = recommendations or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "findings": self.findings,
            "recommendations": self.recommendations
        }

    def __str__(self) -> str:
        parts = [f"[{self.status}] {self.summary}"]
        if self.findings:
            parts.append("Findings:")
            parts.extend([f"  - {f}" for f in self.findings])
        if self.recommendations:
            parts.append("Recommendations:")
            parts.extend([f"  - {r}" for r in self.recommendations])
        return "\n".join(parts)


class VerificationAgent(ToolCallAgent):
    """
    検証エージェント

    タスク完了後に自動的に成果物を検証し、
    品質保証を行う。
    """

    name: str = "verification"
    description: str = "An agent that verifies task results and ensures quality"

    system_prompt: str = VERIFICATION_SYSTEM_PROMPT
    next_step_prompt: str = VERIFICATION_NEXT_STEP_PROMPT

    # 検証に必要な最小限のツールのみ
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            PythonExecute(),
            StrReplaceEditor(),
            Terminate(),
        )
    )

    # 検証は短く終わらせる
    max_steps: int = 5
    max_observe: int = 2000  # トークン節約

    # 検証結果
    verification_result: Optional[VerificationResult] = None

    async def verify(
        self,
        task_description: str,
        task_output: str,
        artifacts: List[str] = None
    ) -> VerificationResult:
        """
        タスク結果の検証

        Args:
            task_description: 元のタスクの説明
            task_output: タスクの出力結果
            artifacts: 生成されたファイルパス等

        Returns:
            VerificationResult: 検証結果
        """
        # 検証プロンプトの構築（簡潔に）
        verification_prompt = f"""Verify the following task completion:

**Task:** {task_description[:500]}

**Output:** {task_output[:1000]}
"""

        if artifacts:
            verification_prompt += f"\n**Files:** {', '.join(artifacts[:5])}"

        try:
            # 検証を実行
            result = await self.run(verification_prompt)

            # 結果を解析
            return self._parse_verification_result(result)

        except Exception as e:
            logger.error(f"Verification failed: {e}")
            return VerificationResult(
                status=VerificationResult.FAIL,
                summary=f"Verification error: {str(e)}",
                findings=[str(e)]
            )

    def _parse_verification_result(self, result: str) -> VerificationResult:
        """検証結果の解析"""
        result_upper = result.upper()

        # ステータスの判定
        if "PASS" in result_upper and "FAIL" not in result_upper:
            status = VerificationResult.PASS
        elif "FAIL" in result_upper:
            status = VerificationResult.FAIL
        elif "PARTIAL" in result_upper:
            status = VerificationResult.PARTIAL
        else:
            # デフォルトはPASS（明確な失敗がなければ）
            status = VerificationResult.PASS

        # サマリーを抽出（最初の行または最初の100文字）
        lines = result.strip().split("\n")
        summary = lines[0][:200] if lines else "Verification completed"

        return VerificationResult(
            status=status,
            summary=summary,
            findings=self._extract_list(result, "finding"),
            recommendations=self._extract_list(result, "recommend")
        )

    def _extract_list(self, text: str, keyword: str) -> List[str]:
        """テキストからリスト項目を抽出"""
        items = []
        lines = text.split("\n")
        in_section = False

        for line in lines:
            line_lower = line.lower()
            if keyword in line_lower:
                in_section = True
                continue
            if in_section and line.strip().startswith("-"):
                items.append(line.strip()[1:].strip())
            elif in_section and not line.strip():
                in_section = False

        return items[:5]  # 最大5件


async def quick_verify(
    task_description: str,
    task_output: str,
    llm: Optional[LLM] = None
) -> VerificationResult:
    """
    軽量な検証（エージェントを使わない版）

    APIコスト削減のため、単純なLLM呼び出しで検証する。
    複雑な検証が必要な場合のみVerificationAgentを使用。
    """
    if llm is None:
        llm = LLM()

    prompt = f"""Quickly verify this task completion (respond in 50 words or less):
Task: {task_description[:200]}
Output: {task_output[:500]}

Reply with: PASS/FAIL/PARTIAL and one-line reason."""

    try:
        response = await llm.ask(
            messages=[Message.user_message(prompt)],
            stream=False,
            temperature=0.0
        )

        response_upper = response.upper()
        if "PASS" in response_upper:
            status = VerificationResult.PASS
        elif "FAIL" in response_upper:
            status = VerificationResult.FAIL
        else:
            status = VerificationResult.PARTIAL

        return VerificationResult(
            status=status,
            summary=response[:200]
        )

    except Exception as e:
        logger.error(f"Quick verification failed: {e}")
        return VerificationResult(
            status=VerificationResult.PARTIAL,
            summary=f"Could not verify: {str(e)}"
        )
