import subprocess
import sys
import tempfile
import unittest

from taxedo.paths import PROJECT_ROOT


class PackageLayoutTests(unittest.TestCase):
    def test_bundled_knowledge_db_is_found_from_another_directory(self):
        code = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
from taxedo.tax.knowledge import TaxKnowledgeDB
assert TaxKnowledgeDB().available
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-E", "-s", "-c", code],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_converter_import_does_not_load_bot_or_credentials(self):
        code = """
import sys
import taxedo.ingestion.worker
assert 'taxedo.config' not in sys.modules
assert 'taxedo.bot.app' not in sys.modules
assert 'anthropic' not in sys.modules
"""
        result = subprocess.run(
            [sys.executable, "-E", "-s", "-c", code],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
