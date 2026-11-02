import json
from typing import Any


def is_browser_tool(name: str) -> bool:
    return name == "browser_automation" or name.startswith("browser_automation___")


def tool_result_is_error(result: Any) -> bool:
    if isinstance(result, list):
        return any(tool_result_is_error(item) for item in result)
    if getattr(result, "type", None) == "text":
        return tool_result_is_error(result.text)
    if isinstance(result, str):
        if result.lstrip().lower().startswith("error:"):
            return True
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return False
    if not isinstance(result, dict):
        return False
    return bool(result.get("isError") or result.get("error")) or (
        result.get("type") == "text" and tool_result_is_error(result.get("text"))
    ) or tool_result_is_error(result.get("content"))


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
