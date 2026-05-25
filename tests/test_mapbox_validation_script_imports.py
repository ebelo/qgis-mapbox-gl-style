import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests import _path  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[1]


class MapboxValidationScriptImportTests(unittest.TestCase):
    def test_runtime_helper_imports_support_direct_script_execution(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)

        for script_path in (
            "validation/mapbox_outdoors_comparison.py",
            "validation/mapbox_outdoors_label_settings.py",
        ):
            with self.subTest(script_path=script_path):
                result = subprocess.run(
                    [sys.executable, script_path, "--help"],
                    check=False,
                    cwd=REPO_ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
