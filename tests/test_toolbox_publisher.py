import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
CONNECTION_ID = (
    "/subscriptions/test/resourceGroups/fibey/providers/Microsoft.CognitiveServices"
    "/accounts/fibey/projects/fibey-project/connections/browser-automation"
)


class ToolboxPublisherTests(unittest.TestCase):
    def setUp(self):
        self.versions = []
        self.default_version = None
        self.connection_status = 200
        self.toolbox_status = None
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send_json(self, status, body):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/connections/browser-automation":
                    self.send_json(test.connection_status, {"id": CONNECTION_ID})
                elif path == "/toolboxes/fibey-toolbox":
                    self.send_json(
                        test.toolbox_status or (200 if test.default_version else 404),
                        {"default_version": test.default_version},
                    )
                elif path.startswith("/toolboxes/fibey-toolbox/versions/"):
                    version = int(path.rsplit("/", 1)[1])
                    self.send_json(200, test.versions[version - 1])
                else:
                    self.send_json(404, {"error": "Not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.split("?")[0] == "/toolboxes/fibey-toolbox/versions":
                    identifiers = [
                        tool.get("name") or tool.get("server_label")
                        or tool.get("openapi", {}).get("name")
                        for tool in body["tools"]
                    ]
                    if identifiers.count(None) > 1:
                        self.send_json(400, {"error": "Multiple tools without identifiers"})
                        return
                    test.versions.append(body)
                    self.send_json(200, {"version": str(len(test.versions))})
                else:
                    self.send_json(404, {"error": "Not found"})

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.default_version = body["default_version"]
                self.send_json(200, {"default_version": test.default_version})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        (directory / "skills").mkdir()
        # Replace only external commands; the publisher still uses real HTTP.
        commands = {
            "az": "#!/bin/sh\nprintf '%s\\n' test-token\n",
            "azd": "#!/bin/sh\nexit 1\n",
            "curl": (
                "#!/bin/sh\n"
                'case "$*" in\n'
                '  *https://orders.example/openapi.json*) printf \'%s\\n\' '
                '\'{"openapi":"3.0.0","info":{"title":"Orders","version":"1"},'
                '"paths":{}}\' ;;\n'
                f'  *) exec {subprocess.check_output(["which", "curl"], text=True).strip()} "$@" ;;\n'
                "esac\n"
            ),
        }
        for name, content in commands.items():
            path = directory / name
            path.write_text(content)
            path.chmod(0o755)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"
        self.env = {
            **os.environ,
            "PATH": f"{directory}:{os.environ['PATH']}",
            "FOUNDRY_PROJECT_ENDPOINT": self.endpoint,
            "TOOLBOX_NAME": "fibey-toolbox",
            "INVENTORY_FQDN": "inventory.example",
            "WORK_ORDERS_FQDN": "orders.example",
            "AZURE_SEARCH_ENDPOINT": "https://search.example",
            "KB_NAME": "fibey-field-ops-kb",
            "BROWSER_AUTOMATION_CONNECTION_NAME": "browser-automation",
            "SKILLS_DIR": str(directory / "skills"),
            "FORCE_TOOLBOX_UPDATE": "0",
        }

    def publish(self):
        return subprocess.run(
            ["bash", str(ROOT / "scripts/setup-toolbox.sh")],
            env=self.env, capture_output=True, text=True, timeout=30,
        )

    def default_toolbox(self):
        with urlopen(f"{self.endpoint}/toolboxes/fibey-toolbox?api-version=v1") as response:
            version = json.load(response)["default_version"]
        with urlopen(
            f"{self.endpoint}/toolboxes/fibey-toolbox/versions/{version}?api-version=v1"
        ) as response:
            return json.load(response)

    def test_new_toolbox_contains_browser_automation_and_rerun_is_a_noop(self):
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tools = self.default_toolbox()["tools"]
        self.assertIn({
            "type": "browser_automation",
            "name": "browser_automation",
            "browser_automation": {"connection": {"project_connection_id": CONNECTION_ID}},
        }, tools)
        self.assertEqual(
            [tool["type"] for tool in tools],
            ["toolbox_search_preview", "mcp", "openapi", "mcp", "browser_automation"],
        )
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.default_version, "1")
        self.assertEqual(len(self.versions), 1)

    def test_existing_toolbox_gets_browser_without_losing_other_tools(self):
        existing = {
            "description": "Existing field operations toolbox",
            "tools": [
                {"type": "toolbox_search_preview"},
                {
                    "type": "mcp",
                    "server_label": "inventory",
                    "server_url": "https://existing-inventory.example/mcp",
                    "require_approval": "never",
                },
                {"type": "openapi", "name": "custom_orders", "openapi": {"spec": {}}},
            ],
            "skills": [],
        }
        self.versions.append(existing)
        self.default_version = "1"

        result = self.publish()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        updated = self.default_toolbox()
        self.assertEqual(self.default_version, "2")
        self.assertEqual(self.versions[0], existing)
        self.assertEqual(updated["description"], existing["description"])
        self.assertEqual(updated["tools"][:-1], existing["tools"])
        self.assertEqual(updated["tools"][-1], {
            "type": "browser_automation",
            "name": "browser_automation",
            "browser_automation": {"connection": {"project_connection_id": CONNECTION_ID}},
        })
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.default_version, "2")
        self.assertEqual(len(self.versions), 2)

    def test_changed_browser_connection_creates_an_immutable_version(self):
        existing = {
            "tools": [
                {"type": "toolbox_search_preview"},
                {
                    "type": "browser_automation",
                    "name": "browser_automation",
                    "browser_automation": {
                        "connection": {"project_connection_id": CONNECTION_ID + "-old"},
                    },
                },
            ],
            "skills": [],
        }
        self.versions.append(existing)
        self.default_version = "1"

        result = self.publish()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.default_version, "2")
        self.assertEqual(self.versions[0], existing)
        updated = self.default_toolbox()
        self.assertEqual(updated["skills"], [])
        self.assertEqual(updated["tools"], [
            {"type": "toolbox_search_preview"},
            {
                "type": "browser_automation",
                "name": "browser_automation",
                "browser_automation": {"connection": {"project_connection_id": CONNECTION_ID}},
            },
        ])

    def test_unavailable_browser_connection_does_not_publish_a_version(self):
        self.connection_status = 404

        result = self.publish()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("404", result.stderr)
        self.assertEqual(self.versions, [])

    def test_toolbox_lookup_failure_does_not_publish_a_version(self):
        self.toolbox_status = 403

        result = self.publish()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("403", result.stderr)
        self.assertEqual(self.versions, [])


if __name__ == "__main__":
    unittest.main()
