"""Runs the whole test suite:   python -m unittest discover -s tests -v

The suites in this folder (check_*.py) simulate the Internet Archive, so they need no
internet, no keys and no account. They also use a fake clock, so the waits and pauses the
script does (minutes) take a fraction of a second.
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE_TIMEOUT_SECONDS = 60  # each suite takes about a second; this only catches a hang


class Suites(unittest.TestCase):
    def run_suite(self, filename):
        try:
            proc = subprocess.run([sys.executable, "-I", os.path.join(HERE, filename)],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=SUITE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.fail(f"{filename} did not finish in {SUITE_TIMEOUT_SECONDS} s (an infinite wait?)")
        self.assertEqual(proc.returncode, 0, f"{filename} failed:\n{proc.stdout}\n{proc.stderr}")

    def test_verification(self):
        """Job checks: progress, error reasons, pauses when the Archive refuses connections."""
        self.run_suite("check_verification.py")

    def test_resume(self):
        """Resuming an interrupted run, including stuck jobs."""
        self.run_suite("check_resume.py")

    def test_window(self):
        """Sending in a window, a slow or stalled Archive, retries, account limits."""
        self.run_suite("check_window.py")

    def test_record(self):
        """record_archive.py: the public record and the optional method.md."""
        self.run_suite("check_record.py")

    def test_site_address(self):
        """The site address prompt refuses empty or invalid answers."""
        self.run_suite("check_site_url.py")


if __name__ == "__main__":
    unittest.main()