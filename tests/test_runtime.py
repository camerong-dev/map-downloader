import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from io import StringIO

from mosaic_runtime import qgis_command, run_qgis_worker, WorkerFailed, worker_event, inspect_tiff_bounds, request_cancel
from downloader import Bounds, Cancelled


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


class WorkerProtocolTests(unittest.TestCase):
    def process(self, output, code=0):
        process = Mock()
        process.stdout = StringIO(output)
        process.wait.return_value = code
        process.poll.return_value = code
        return process

    def test_mixed_json_and_native_diagnostics_are_forwarded(self):
        process = self.process('warning from GDAL\n{"message":"working","progress":42}\n[1,2]\n')
        events = []
        with patch('mosaic_runtime.start_worker', return_value=process):
            run_qgis_worker('qgis', 'worker.py', 'job.json', on_event=events.append)
        self.assertEqual(events[0]['message'], 'warning from GDAL')
        self.assertEqual(events[1]['progress'], 42)
        self.assertEqual(events[2]['message'], '[1,2]')
        self.assertTrue(process.stdout.closed)

    def test_cancellation_and_native_crash_are_distinct(self):
        for code, error in [(2, Cancelled), (3221225477, WorkerFailed)]:
            with self.subTest(code=code), patch('mosaic_runtime.start_worker', return_value=self.process('last stage\n', code)):
                with self.assertRaises(error) as caught:
                    run_qgis_worker('qgis', 'worker.py', on_event=lambda event: None)
                if code != 2:
                    self.assertIn('0xC0000005', str(caught.exception))
                    self.assertIn('last stage', str(caught.exception))

    def test_callback_failure_reaps_running_process(self):
        process = self.process('{"message":"hello"}\n')
        process.poll.return_value = None
        with patch('mosaic_runtime.start_worker', return_value=process):
            with self.assertRaisesRegex(RuntimeError, 'callback'):
                run_qgis_worker('qgis', 'worker.py', on_event=Mock(side_effect=RuntimeError('callback')))
        process.terminate.assert_called_once()
        process.wait.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_shared_tiff_inspection_validates_worker_response(self):
        process = self.process('{"bounds":{"west":34,"south":31,"east":35,"north":32}}\n')
        with patch('mosaic_runtime.start_worker', return_value=process):
            self.assertEqual(inspect_tiff_bounds('qgis', 'map.tif'), Bounds(34,31,35,32))
        with patch('mosaic_runtime.start_worker', return_value=self.process('no bounds\n')):
            with self.assertRaisesRegex(ValueError, 'did not return'):
                inspect_tiff_bounds('qgis', 'map.tif')

    def test_cancel_after_workspace_cleanup_is_harmless(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root = Path(temporary)
            self.assertFalse(request_cancel(root/'removed'/'cancel'))
            self.assertFalse((root/'removed').exists())
            self.assertTrue(request_cancel(root/'cancel'))
            self.assertTrue((root/'cancel').is_file())
            self.assertFalse(request_cancel(None))
