"""Tests for the log collection module."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rest.log_collect import DiagnosticsCollector


class TestDiagnosticsCollector(unittest.TestCase):
    """Test DiagnosticsCollector functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.cfg = {
            "logging": {"file": {"path": "logs/changes_worker.log"}},
            "collect": {
                "max_log_size_mb": 200,
                "profile_seconds": 1,
                "system_command_timeout_seconds": 30,
            },
        }
        self.metrics = None
        self.redactor = None

    def test_collector_init(self):
        """Test collector initialization."""
        collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
        self.assertEqual(collector.cfg, self.cfg)
        self.assertIsNotNone(collector._hostname)
        self.assertIsNotNone(collector._timestamp)

    @patch("rest.log_collect.Path.glob")
    @patch("rest.log_collect.Path.is_file")
    def test_collect_project_logs_no_files(self, mock_is_file, mock_glob):
        """Test project logs collection when no files exist."""
        mock_glob.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
            collector._temp_dir = tmpdir

            async def run_test():
                await collector._collect_project_logs(tmpdir)

            asyncio.run(run_test())

    def test_get_system_commands_linux(self):
        """Test system commands for Linux."""
        with patch("platform.system", return_value="Linux"):
            collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
            commands = collector._get_system_commands()

            self.assertIn("uname", commands)
            self.assertIn("ps_aux", commands)
            self.assertIn("df", commands)
            self.assertIn("free", commands)
            # dmesg/lsof removed — require privileges in Docker containers
            self.assertNotIn("dmesg", commands)
            self.assertNotIn("lsof", commands)

    def test_get_system_commands_macos(self):
        """Test system commands for macOS."""
        with patch("platform.system", return_value="Darwin"):
            collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
            commands = collector._get_system_commands()

            self.assertIn("uname", commands)
            self.assertIn("ps_aux", commands)
            self.assertIn("df", commands)
            self.assertIn("vm_stat", commands)
            # lsof/sysctl removed — redundant with psutil, slow in containers
            self.assertNotIn("lsof", commands)

    def test_write_error_file(self):
        """Test error file writing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
            error = Exception("Test error")

            collector._write_error_file(tmpdir, "test_category", error)

            error_file = os.path.join(tmpdir, "test_category_error.txt")
            self.assertTrue(os.path.exists(error_file))

            with open(error_file) as f:
                content = f.read()
                self.assertIn("Test error", content)

    def test_write_collect_info(self):
        """Test collect info file writing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
            collector._write_collect_info(tmpdir, 10.5)

            info_file = os.path.join(tmpdir, "collect_info.json")
            self.assertTrue(os.path.exists(info_file))

            with open(info_file) as f:
                info = json.load(f)
                self.assertIn("timestamp", info)
                self.assertIn("hostname", info)
                self.assertIn("collection_duration_seconds", info)
                self.assertAlmostEqual(info["collection_duration_seconds"], 10.5)

    def test_get_version(self):
        """Test version retrieval."""
        collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
        version = collector._get_version()
        self.assertIsInstance(version, str)

    @patch("rest.log_collect.Path.glob")
    def test_create_zip_sync(self, mock_glob):
        """Test zip file creation."""
        mock_glob.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test directory structure
            collect_root = os.path.join(tmpdir, "csdb_collect_test_20240101_000000")
            os.makedirs(collect_root)

            # Create a dummy file
            test_file = os.path.join(collect_root, "test.txt")
            with open(test_file, "w") as f:
                f.write("test content")

            collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
            zip_path = collector._create_zip_sync(collect_root)

            self.assertTrue(os.path.exists(zip_path))
            self.assertTrue(zip_path.endswith(".zip"))

    def test_run_command_sync_success(self):
        """Test successful command execution."""
        collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)
        result = collector._run_command_sync(["echo", "hello"])

        self.assertIn("hello", result)

    def test_run_command_sync_failure(self):
        """Test command execution with failure."""
        collector = DiagnosticsCollector(self.cfg, self.metrics, self.redactor)

        # false command returns non-zero exit, which should be captured in stderr
        result = collector._run_command_sync(["false"])
        # On macOS/Linux, false command produces no output but has non-zero exit
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
