import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class PortalComposePolicyTests(unittest.TestCase):
    def test_portal_profile_enables_admin_server_ceiling(self):
        compose = (REPOSITORY_ROOT / "fastmcp" / "docker-compose.yaml").read_text()

        self.assertIn('MCP_ADMIN_TOOLS_ENABLED: "true"', compose)
        self.assertIn("DISCORD_CREDENTIAL_MODE: request", compose)


if __name__ == "__main__":
    unittest.main()
