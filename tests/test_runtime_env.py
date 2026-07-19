import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path


def load_runtime_env_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "init_runtime_env.py"
    spec = importlib.util.spec_from_file_location("discord_init_runtime_env", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load init_runtime_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def values(document: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in document.splitlines() if line)


class RuntimeEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime_env = load_runtime_env_module()

    def test_standalone_scaffold_separates_service_and_provider_credentials(self):
        environment = values(self.runtime_env.build_environment("standalone"))

        self.assertEqual(environment["MCP_MODE"], "standalone")
        self.assertGreaterEqual(len(environment["MCP_ACCESS_TOKEN"]), 32)
        self.assertEqual(environment["MCP_PORTAL_GRANT_TOKEN"], "")
        self.assertEqual(environment["DISCORD_CREDENTIAL_MODE"], "server")
        self.assertEqual(environment["DISCORD_TOKEN"], "")
        self.assertEqual(environment["DISCORD_GUILD_ID"], "")
        self.assertEqual(environment["DISCORD_ALLOWED_CHANNEL_IDS"], "")

    def test_portal_scaffold_requires_request_byok_and_request_scope(self):
        environment = values(self.runtime_env.build_environment("portal"))

        self.assertEqual(environment["MCP_MODE"], "portal")
        self.assertEqual(environment["MCP_ACCESS_TOKEN"], "")
        self.assertGreaterEqual(len(environment["MCP_PORTAL_GRANT_TOKEN"]), 32)
        self.assertEqual(environment["DISCORD_CREDENTIAL_MODE"], "request")
        self.assertEqual(environment["DISCORD_TOKEN"], "")
        self.assertEqual(environment["DISCORD_ALLOWED_CHANNEL_IDS"], "ALL")

    def test_environment_creation_is_mode_0600_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            self.assertTrue(self.runtime_env.create_environment(env_path, "standalone"))
            first = env_path.read_text(encoding="utf-8")
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

            self.assertFalse(self.runtime_env.create_environment(env_path, "portal"))
            self.assertEqual(env_path.read_text(encoding="utf-8"), first)

    def test_invalid_mode_is_rejected_before_file_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            with self.assertRaises(ValueError):
                self.runtime_env.create_environment(env_path, "public")
            self.assertFalse(env_path.exists())


if __name__ == "__main__":
    unittest.main()
