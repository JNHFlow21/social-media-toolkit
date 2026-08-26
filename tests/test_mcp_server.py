import importlib.metadata
import json
import subprocess
import sys
import unittest


class MCPServerStartupTests(unittest.TestCase):
    def test_mcp_v1_transport_completes_initialize_handshake(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "regression", "version": "0.1"},
            },
        }

        completed = subprocess.run(
            [sys.executable, "-m", "social_post_extractor_mcp.server"],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip()
        ]
        self.assertEqual(len(responses), 1, completed.stdout)
        response = responses[0]
        self.assertEqual(response["id"], 1)
        self.assertIn("result", response)
        self.assertEqual(response["result"]["serverInfo"]["name"], "Social Media Toolkit")
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")

        mcp_version = importlib.metadata.version("mcp")
        self.assertLess(int(mcp_version.split(".", 1)[0]), 2)


if __name__ == "__main__":
    unittest.main()
