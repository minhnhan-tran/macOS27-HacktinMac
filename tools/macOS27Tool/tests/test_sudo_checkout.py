"""A user's checkout read as root (sudo ./macOS27Tool create-installer): git still names its commit and its files.
GIT_TEST_ASSUME_DIFFERENT_OWNER makes git see another owner, as root sees the user's checkout."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
TOP = TOOL.parents[1]
sys.path.insert(0, str(TOOL / 'src'))
import builder as BUILD  # noqa: E402
import report as REPORT  # noqa: E402
import stick as STICK    # noqa: E402


class SudoCheckout(unittest.TestCase):
    def setUp(self):
        if not (TOP / '.git').exists():
            self.skipTest('not a checkout')
        patch = mock.patch.dict(os.environ, GIT_TEST_ASSUME_DIFFERENT_OWNER='1', GIT_CONFIG_COUNT='0')
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop('M27_STICK', None)

    def test_stick_copies_the_tracked_files(self):
        self.assertIn('tools/macOS27Tool/macOS27Tool', STICK.m27_files(TOP))

    def test_release_of_names_the_checkout_commit(self):
        if (TOP / 'tools/macOS27Tool/release.json').exists():
            self.skipTest('release.json beside m27')
        self.assertRegex(BUILD.release_of()[1] or '', '^[0-9a-f]{40}$')

    def test_report_names_the_commit(self):
        self.assertRegex(REPORT.version().get('commit', ''), '[0-9a-f]{12}')


if __name__ == '__main__':
    unittest.main()
