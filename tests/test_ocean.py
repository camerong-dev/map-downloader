import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ocean import download_archive, prepare_ocean_source, process_alive


class Response(io.BytesIO):
    def __init__(self, body=b'', headers=None, status=200):
        super().__init__(body)
        self.headers = headers or {}
        self.status = status


def archive_bytes():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for suffix in ('shp','shx','dbf','prj','qix'):
            archive.writestr(f'water-polygons-split-3857/water_polygons.{suffix}', suffix.encode())
    return data.getvalue()


class OceanTests(unittest.TestCase):
    def test_download_extract_and_cache_without_network_on_second_export(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root = Path(temporary)
            job = {'cancel':str(root/'cancel')}
            body = archive_bytes()
            headers = {'Content-Length':str(len(body)), 'ETag':'snapshot', 'Last-Modified':'today'}
            with patch('ocean.urlopen', side_effect=[Response(headers=headers), Response(body)]) as network:
                target = prepare_ocean_source(root, job, lambda _:None)
                self.assertEqual(target.read_bytes(),b'shp')
                self.assertEqual(prepare_ocean_source(root,job,lambda _:None),target)
                self.assertEqual(network.call_count,2)
                self.assertFalse((root/'prepare.lock').exists())
                self.assertFalse((root/'water-polygons.zip').exists())
            with patch('ocean.urlopen',side_effect=AssertionError('Must use cache')):
                self.assertEqual(prepare_ocean_source(root,job,lambda _:None),target)
            (root/'cancel').touch()
            with self.assertRaises(InterruptedError):
                prepare_ocean_source(root,job,lambda _:None)

    def test_resume_only_matching_snapshot(self):
        for same in (True,False):
            with self.subTest(same=same), tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
                root = Path(temporary)
                body = archive_bytes()
                (root/'water-polygons.zip.part').write_bytes(body[:50] if same else b'old')
                (root/'download.json').write_text(json.dumps({'etag':'same' if same else 'old','size':len(body),'modified':'today'}))
                headers = {'Content-Length':str(len(body)), 'ETag':'same', 'Last-Modified':'today'}
                response = Response(body[50:],{'Content-Range':f'bytes 50-{len(body)-1}/{len(body)}'},206) if same else Response(body)
                with patch('ocean.urlopen', side_effect=[Response(headers=headers),response]) as network:
                    target = download_archive(root,{'cancel':str(root/'cancel')},lambda _:None)
                    self.assertEqual(target.read_bytes(),body)
                    self.assertEqual(network.call_args_list[1].args[0].get_header('Range'), 'bytes=50-' if same else None)

    def test_cancel_during_extraction_does_not_publish_cache(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root = Path(temporary)
            (root/'water-polygons.zip').write_bytes(archive_bytes())
            def cancel(_):
                (root/'cancel').touch()
            with self.assertRaises(InterruptedError):
                prepare_ocean_source(root,{'cancel':str(root/'cancel')},cancel)
            self.assertFalse((root/'ready.json').exists())
            self.assertFalse((root/'prepare.lock').exists())

    def test_new_snapshot_never_resumes_old_bytes_after_failed_get(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root = Path(temporary)
            (root/'water-polygons.zip.part').write_bytes(b'old bytes')
            (root/'download.json').write_text(json.dumps({'etag':'old','size':999,'modified':'old'}))
            def cancel(_):
                (root/'cancel').touch()
            headers = {'Content-Length':'1000','ETag':'new','Last-Modified':'new'}
            with patch('ocean.urlopen',side_effect=[Response(headers=headers),OSError('interrupted')]):
                with self.assertRaises(InterruptedError):
                    download_archive(root,{'cancel':str(root/'cancel')},cancel)
            self.assertFalse((root/'water-polygons.zip.part').exists())

    def test_process_probe_does_not_terminate_current_worker(self):
        self.assertTrue(process_alive(os.getpid()))
        self.assertFalse(process_alive(4294967294))

    def test_posix_process_probe_handles_pid_overflow_and_permissions(self):
        with patch('ocean.os.name','posix'):
            for error, expected in ((OverflowError('signed integer is greater than maximum'),False),
                                    (ProcessLookupError(),False),(PermissionError(),True)):
                with self.subTest(error=type(error).__name__), patch('ocean.os.kill',side_effect=error) as kill:
                    self.assertEqual(process_alive(4294967294),expected)
                    kill.assert_called_once_with(4294967294,0)
            with patch('ocean.os.kill',return_value=None) as kill:
                self.assertTrue(process_alive(123))
                kill.assert_called_once_with(123,0)

    def test_invalid_pids_never_probe_process_groups_or_wrap_on_windows(self):
        with patch('ocean.os.kill') as kill:
            for pid in (0,-1,-123,2**32,2**64):
                self.assertFalse(process_alive(pid))
            kill.assert_not_called()
