import json
import os
from pathlib import Path
import re
import subprocess
import unittest


@unittest.skipUnless(
    os.environ.get("FIBEY_INFRA_TEST_RESOURCE_GROUP"),
    "Set FIBEY_INFRA_TEST_RESOURCE_GROUP to run read-only Azure naming checks",
)
class ContainerAppNameTests(unittest.TestCase):
    def planned_names(self, environment_name: str) -> list[str]:
        principal_id = subprocess.run(
            ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        result = subprocess.run(
            [
                "az", "deployment", "sub", "what-if",
                "--name", "fibey-container-app-name-test",
                "--location", "eastus2",
                "--template-file", str(Path(__file__).resolve().parents[1] / "infra/main.bicep"),
                "--parameters",
                f"environmentName={environment_name}",
                f"resourceGroupName={os.environ['FIBEY_INFRA_TEST_RESOURCE_GROUP']}",
                "location=eastus2",
                "searchServiceLocation=westus2",
                f"principalId={principal_id}",
                "--validation-level", "Template",
                "--no-pretty-print",
                "--only-show-errors",
                "--query", "changes[?changeType!='Ignore'].resourceId",
                "-o", "json",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        names = [
            match.group(1)
            for resource_id in json.loads(result.stdout)
            if (match := re.fullmatch(
                r"/subscriptions/[^/]+/resourceGroups/[^/]+"
                r"/providers/Microsoft.App/containerApps/([^/]+)",
                resource_id,
                re.IGNORECASE,
            ))
        ]
        self.assertEqual(len(names), 5, result.stdout)
        return names

    def test_all_app_names_fit_at_the_environment_name_boundary(self):
        for environment_name in ("fibey-clean-110", "fibey-clean-1101"):
            with self.subTest(environment_name=environment_name):
                names = self.planned_names(environment_name)
                for name in names:
                    self.assertLessEqual(len(name), 32, name)
                    self.assertRegex(name, r"^[a-z][a-z0-9-]*[a-z0-9]$")
                    self.assertNotIn("--", name)

                for service_name in ("ui", "gateway", "inventory-mcp", "work-orders-api"):
                    self.assertIn(f"{environment_name}-{service_name}", names)
                if environment_name == "fibey-clean-110":
                    self.assertIn("fibey-clean-110-status-dashboard", names)

    def test_long_names_stay_distinct_when_environment_prefixes_match(self):
        first = self.planned_names("fibey-" + "a" * 57 + "1")
        second = self.planned_names("fibey-" + "a" * 57 + "2")
        self.assertTrue(set(first).isdisjoint(second), (first, second))
        for name in first + second:
            self.assertLessEqual(len(name), 32, name)
            self.assertRegex(name, r"^[a-z][a-z0-9-]*[a-z0-9]$")
            self.assertNotIn("--", name)

    def test_invalid_environment_prefixes_produce_valid_app_names(self):
        for environment_name in ("Fibey-dev", "1" + "a" * 63, "fibey_qa"):
            with self.subTest(environment_name=environment_name):
                for name in self.planned_names(environment_name):
                    self.assertLessEqual(len(name), 32, name)
                    self.assertRegex(name, r"^[a-z][a-z0-9-]*[a-z0-9]$")
                    self.assertNotIn("--", name)


if __name__ == "__main__":
    unittest.main()
