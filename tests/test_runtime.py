import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mosaic_runtime import qgis_command


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def linux_layout(self):
        (self.root / 'bin').mkdir()
        (self.root / 'bin/python3').touch()
        (self.root / 'share/qgis/python').mkdir(parents=True)

    def test_native_linux_command_and_environment(self):
        self.linux_layout()
        with patch.dict(os.environ, {'PYTHONHOME': 'bundled', 'QT_PLUGIN_PATH': 'wrong-qt'}, clear=False):
            command, env = qgis_command(self.root, 'split_worker.py', 'job.json', platform='linux')
        self.assertEqual(command[0], str(self.root / 'bin/python3'))
        self.assertEqual(command[-2:], ['split_worker.py', 'job.json'])
        self.assertEqual(env['QGIS_PREFIX_PATH'], str(self.root))
        self.assertNotIn('PYTHONHOME', env)
        self.assertNotIn('QT_PLUGIN_PATH', env)
        self.assertEqual(env['QT_QPA_PLATFORM'], 'offscreen')

    def test_frozen_linux_restores_host_libraries(self):
        self.linux_layout()
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {
            'LD_LIBRARY_PATH': '/bundle/libraries', 'LD_LIBRARY_PATH_ORIG': '/host/libraries'}):
            _, env = qgis_command(self.root, 'split_worker.py', platform='linux')
        self.assertEqual(env['LD_LIBRARY_PATH'], '/host/libraries')

    def test_missing_linux_installation_gives_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'native QGIS prefix'):
            qgis_command(self.root, 'split_worker.py', platform='linux')

    def test_windows_command_still_uses_qgis_python(self):
        (self.root / 'bin').mkdir()
        (self.root / 'bin/python3.exe').touch()
        (self.root / 'apps/Python312/Lib').mkdir(parents=True)
        (self.root / 'apps/qgis/python').mkdir(parents=True)
        with patch.dict(os.environ, {}, clear=False):
            command, env = qgis_command(self.root, 'worker.py', platform='win32')
        self.assertEqual(command[0], str(self.root / 'bin/python3.exe'))
        self.assertEqual(env['PYTHONHOME'], str(self.root / 'apps/Python312'))
