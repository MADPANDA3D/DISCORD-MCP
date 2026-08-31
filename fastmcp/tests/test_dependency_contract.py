import pathlib
import unittest


class DependencyContractTests(unittest.TestCase):
    def test_fastmcp_runtime_stays_on_mcp_v1(self):
        requirements = pathlib.Path(__file__).parents[1] / "requirements.txt"
        lines = {
            line.strip()
            for line in requirements.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("mcp>=1.9.0,<2", lines)


if __name__ == "__main__":
    unittest.main()
