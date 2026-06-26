"""PythonTool unwraps Cursor SDK JSON args and runs code."""

import json

import pytest

from src.agent_tools.subprocess_tools import PythonTool


@pytest.mark.asyncio
async def test_python_tool_unwraps_json_code():
    tool = PythonTool()
    payload = json.dumps({"code": "print('tool-ok')"})
    result = await tool.execute(payload, {"subproc_env": {}})
    assert result.get("exit_code") == 0
    assert "tool-ok" in result.get("output", "")


@pytest.mark.asyncio
async def test_python_tool_matplotlib_headless():
    pytest.importorskip("matplotlib")
    tool = PythonTool()
    code = (
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2, 3], [1, 4, 9])\n"
        "print('plot-ok')"
    )
    payload = json.dumps({"code": code})
    result = await tool.execute(payload, {"subproc_env": {}})
    assert result.get("exit_code") == 0, result
    assert "plot-ok" in result.get("output", "")
