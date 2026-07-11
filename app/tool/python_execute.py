import asyncio
import subprocess
import sys
from typing import Dict

from app.config import config
from app.tool.base import BaseTool


class PythonExecute(BaseTool):
    """A tool for executing Python code with timeout and safety restrictions."""

    name: str = "python_execute"
    description: str = "Executes Python code string. Note: Only print outputs are visible, function return values are not captured. Use print statements to see results."
    parameters: dict = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute.",
            },
        },
        "required": ["code"],
    }

    async def execute(
        self,
        code: str,
        timeout: int = 30,
    ) -> Dict:
        """
        Executes the provided Python code with a timeout.

        Runs the code in a fresh interpreter via subprocess rather than
        multiprocessing: on Windows, spawned multiprocessing children re-import
        the heavy application entrypoint, which alone can exceed the timeout.

        Args:
            code (str): The Python code to execute.
            timeout (int): Execution timeout in seconds.

        Returns:
            Dict: Contains 'observation' with execution output or error message and 'success' status.
        """

        def _run() -> Dict:
            try:
                result = subprocess.run(
                    [sys.executable, "-X", "utf8", "-c", code],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    cwd=str(config.workspace_root),
                )
                observation = result.stdout
                if result.returncode != 0:
                    observation += result.stderr
                return {"observation": observation, "success": result.returncode == 0}
            except subprocess.TimeoutExpired:
                return {
                    "observation": f"Execution timeout after {timeout} seconds",
                    "success": False,
                }

        return await asyncio.to_thread(_run)
