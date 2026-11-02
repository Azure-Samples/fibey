"""Read the deployed Status Dashboard through the hosted agent (incurs model usage).

Run with: uv run --no-sync python scripts/smoke-network-status.py -e fibey-dev
Only safe evidence summaries are printed; browser session URLs are never logged.
"""

import argparse
import json
import logging
import re
import subprocess
import time

import httpx
from azure.identity import AzureCliCredential, AzureDeveloperCliCredential, ChainedTokenCredential

from fibey.tool_results import tool_result_is_error


QUESTION = (
    "What's the network status of NE-3 Access Ring and OLT-NE-201? "
    "Include any NE-3 alert and the OLT's recent incident."
)
FACTS = {
    "NE-3 outage": r"NE-3[\s\S]{0,160}\boutage\b",
    "fiber cut alert": r"fiber cut",
    "OLT-NE-201 degraded": r"OLT-NE-201[\s\S]{0,160}\bdegraded\b",
    "uplink incident": r"uplink flap",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-e", "--environment", required=True, help="Existing azd environment")
    parser.add_argument("--timeout", type=float, default=600, help="Overall request deadline in seconds")
    args = parser.parse_args()
    logging.disable(logging.INFO)
    env = json.loads(subprocess.check_output(
        ["azd", "env", "get-values", "-e", args.environment, "-o", "json"], text=True,
    ))
    endpoint = env["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    dashboard = env["STATUS_DASHBOARD_URL"]
    agent = env.get("HOSTED_AGENT_NAME", "fibey-agent")
    url = (
        f"{endpoint}/agents/{agent}/endpoint/protocols/openai/responses"
        "?api-version=2025-11-15-preview"
    )
    calls = {}
    outputs = {}
    text = []
    completed = False
    started = time.monotonic()
    first_text = None
    with (
        ChainedTokenCredential(AzureCliCredential(), AzureDeveloperCliCredential()) as credential,
        httpx.Client(timeout=args.timeout) as client,
    ):
        token = credential.get_token("https://ai.azure.com/.default").token
        with client.stream(
            "POST", url,
            headers={"Authorization": f"Bearer {token}"},
            json={"input": QUESTION, "stream": True},
        ) as response:
            if response.status_code != 200:
                raise SystemExit(f"FAIL: hosted Responses HTTP {response.status_code}")
            for line in response.iter_lines():
                if time.monotonic() - started > args.timeout:
                    raise SystemExit("FAIL: hosted smoke exceeded its deadline")
                if not line.startswith("data: "):
                    continue
                raw = line.removeprefix("data: ")
                if raw == "[DONE]":
                    break
                event = json.loads(raw)
                kind = event.get("type", "")
                if kind in {"error", "response.failed", "response.incomplete"}:
                    raise SystemExit(f"FAIL: hosted stream emitted {kind}")
                if kind == "response.output_item.done":
                    item = event["item"]
                    if item["type"] == "function_call":
                        arguments = json.loads(item.get("arguments") or "{}")
                        name = item["name"]
                        if name == "call_tool" and "name" in arguments:
                            name = arguments["name"]
                            arguments = arguments.get("arguments", {})
                        calls[item["call_id"]] = (name, arguments)
                    elif item["type"] == "function_call_output":
                        outputs[item["call_id"]] = item.get("output", "")
                elif kind == "response.output_text.delta":
                    if first_text is None:
                        first_text = time.monotonic() - started
                    text.append(event["delta"])
                elif kind == "response.completed":
                    completed = event["response"].get("status") == "completed"

    answer = "".join(text)
    browser_calls = {
        call_id: (name, arguments) for call_id, (name, arguments) in calls.items()
        if "browser" in name.lower()
    }
    successful_reads = [
        str(outputs[call_id]) for call_id, (name, _) in browser_calls.items()
        if name.endswith(("___browser_navigate", "___browser_snapshot"))
        and call_id in outputs and not tool_result_is_error(outputs[call_id])
    ]
    checks = {
        "completed response": completed,
        "Browser Automation invoked": bool(browser_calls),
        "configured dashboard visited": any(
            name.endswith("___browser_navigate") and arguments.get("url") == dashboard
            for name, arguments in browser_calls.values()
        ),
        "no other sites visited": all(
            arguments.get("url") == dashboard
            for name, arguments in browser_calls.values()
            if name.endswith("___browser_navigate")
        ),
        "read-only browser operations": all(
            name.rsplit("___", 1)[-1] in {
                "create_browser_session", "browser_navigate", "browser_snapshot", "close_browser_session",
            } for name, _ in browser_calls.values()
        ),
        "dashboard content returned": any(
            "NE-3" in output and "OLT-NE-201" in output for output in successful_reads
        ),
        "Status Dashboard citation": bool(re.search(
            r"\[Status Dashboard\]\(" + re.escape(dashboard) + r"/?\)", answer,
        )),
        "no browser session links in answer": all(
            link.rstrip("/") == dashboard.rstrip("/")
            for link in re.findall(r"https?://[^\s)<>]+", answer)
        ),
        **{
            label: bool(re.search(pattern, answer, re.IGNORECASE))
            for label, pattern in FACTS.items()
        },
    }
    for label, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    print(f"Elapsed: {time.monotonic() - started:.1f}s")
    if first_text is not None:
        print(f"First answer text: {first_text:.1f}s")
    print(f"Browser calls: {len(browser_calls)}; successful reads: {len(successful_reads)}")
    if not checks["Status Dashboard citation"]:
        safe_answer = re.sub(r"https?://[^\s\"<>]+", "[URL redacted]", answer)
        print(f"Answer (URLs redacted): {safe_answer[:4000]}")
    for call_id, (name, arguments) in calls.items():
        if call_id not in outputs:
            print(f"FAIL: result missing for {name}")
        elif tool_result_is_error(outputs[call_id]):
            error = re.sub(r"https?://[^\s\"<>]+", "[URL redacted]", str(outputs[call_id]))
            shape = {key: type(value).__name__ for key, value in arguments.items()}
            print(f"Tool error ({name}, argument types {shape}): {error[:1000]}")
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
