import json
from typing import Any


def tool_result_is_error(result: Any) -> bool:
    if isinstance(result, str):
        if result.lstrip().lower().startswith("error:"):
            return True
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return False
    return isinstance(result, dict) and bool(result.get("isError") or result.get("error"))


def tool_search_results(result: Any) -> list[dict[str, str]]:
    if isinstance(result, dict) and "content" in result:
        contents = result["content"]
        result = contents[0].get("text", "") if contents else ""
    parsed = json.loads(result) if isinstance(result, str) else result
    if not isinstance(parsed, dict):
        raise ValueError("Tool search result must be an object")
    tools = parsed.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("Tool search tools must be a list")
    return [
        {
            "name": tool["name"],
            "description": (tool.get("description") or "").strip().split("\n")[0][:200],
        }
        for tool in tools
        if isinstance(tool, dict) and tool.get("name")
    ]
