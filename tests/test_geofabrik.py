import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from downloader import Cancelled
from geofabrik import DEFAULT_SOURCE, obtain, validate_source, validate_sources, retry_network, NETWORK_TIMEOUT
from urllib.error import HTTPError


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.headers = {'Content-Length': str(len(value)), 'Last-Modified': 'Test date'}
        self.status = 200


class InterruptedResponse(Response):
    def __init__(self, value):
        super().__init__(value)
        self.reads = 0

    def read(self, size=-1):
        self.reads += 1
        if self.reads == 1:
            return super().read(4)
        raise TimeoutError('The read operation timed out')


class GeofabrikTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def responses(self, body=b'PBF fixture'):
        digest = hashlib.md5(body, usedforsecurity=False).hexdigest().encode()
        return [Response(digest + b' file.osm.pbf'), Response(body)]

    def test_download_verification_offline_reuse_and_refresh(self):
        with patch('geofabrik.urlopen', side_effect=self.responses()) as request:
            target, metadata = obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)
            self.assertEqual(request.call_count, 2)
        self.assertEqual(target.read_bytes(), b'PBF fixture')
        self.assertEqual(metadata['size'], 11)
        with patch('geofabrik.urlopen') as request:
            cached, _ = obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)
            request.assert_not_called()
            self.assertEqual(cached, target)
        with patch('geofabrik.urlopen', side_effect=self.responses(b'New PBF')):
            obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None, refresh=True)
        self.assertEqual(target.read_bytes(), b'New PBF')

    def test_bad_checksum_never_publishes_file(self):
        responses = [Response(b'0' * 32), Response(b'Incomplete or changed file')]
        with patch('geofabrik.urlopen', side_effect=responses):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_empty_checksum_is_reported_cleanly(self):
        with patch('geofabrik.urlopen', return_value=Response(b'')):
            with self.assertRaisesRegex(ValueError, 'invalid checksum'):
                obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)

    def test_invalid_cache_metadata_triggers_verified_download(self):
        with patch('geofabrik.urlopen', side_effect=self.responses()):
            target, _ = obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)
        target.with_suffix('.json').write_text('[]', encoding='utf-8')
        with patch('geofabrik.urlopen', side_effect=self.responses(b'Refreshed PBF')) as request:
            obtain(DEFAULT_SOURCE, self.folder, threading.Event(), lambda m: None)
            self.assertEqual(request.call_count, 2)
        self.assertEqual(target.read_bytes(), b'Refreshed PBF')

    def test_local_file_is_offline_and_cancel_is_checked(self):
        source = self.folder / 'region.osm.pbf'
        source.write_bytes(b'fixture')
        with patch('geofabrik.urlopen') as request:
            result, metadata = obtain(str(source), self.folder, threading.Event(), lambda m: None)
            self.assertEqual(result, source)
            self.assertEqual(metadata['kind'], 'local OSM extract')
            request.assert_not_called()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            obtain(DEFAULT_SOURCE, self.folder, cancel, lambda m: None)

    def test_source_validation(self):
        for url in ('http://download.geofabrik.de/x.osm.pbf', 'https://example.com/x.osm.pbf',
                    'https://download.geofabrik.de/x.zip', 'https://download.geofabrik.de/x.osm.pbf?a=b'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_source(url)

    def test_multiple_sources_keep_order_and_remove_repeated_entries(self):
        local = self.folder / 'second country.osm.pbf'
        local.write_bytes(b'fixture')
        text = f' {DEFAULT_SOURCE}\n\n{local}\n{DEFAULT_SOURCE}\n{local}\n'
        self.assertEqual(validate_sources(text), [DEFAULT_SOURCE, str(local)])

    def test_multiple_sources_reject_empty_or_invalid_input(self):
        for text in ('\n ', DEFAULT_SOURCE + '\nhttps://example.com/wrong.osm.pbf'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_sources(text)

    def test_checksum_timeout_is_retried(self):
        cancel = threading.Event()
        with patch('geofabrik.urlopen', side_effect=[TimeoutError('read timed out'), *self.responses()]) as request, patch.object(cancel, 'wait', return_value=False):
            target, _ = obtain(DEFAULT_SOURCE, self.folder, cancel, lambda m: None)
        self.assertEqual(target.read_bytes(), b'PBF fixture')
        self.assertEqual(request.call_count, 3)
        self.assertTrue(all(call.kwargs['timeout'] == NETWORK_TIMEOUT for call in request.call_args_list))

    def test_transfer_timeout_resumes_verified_bytes(self):
        body = b'Complete PBF fixture'
        checksum = hashlib.md5(body, usedforsecurity=False).hexdigest().encode()
        resumed = Response(body[4:])
        resumed.status = 206
        resumed.headers['Content-Range'] = f'bytes 4-{len(body) - 1}/{len(body)}'
        cancel = threading.Event()
        with patch('geofabrik.urlopen', side_effect=[Response(checksum), InterruptedResponse(body), resumed]) as request, patch.object(cancel, 'wait', return_value=False):
            target, _ = obtain(DEFAULT_SOURCE, self.folder, cancel, lambda m: None)
        self.assertEqual(target.read_bytes(), body)
        self.assertEqual(request.call_args_list[-1].args[0].get_header('Range'), 'bytes=4-')
        self.assertFalse(list(self.folder.glob('*.part')))

    def test_failed_transfer_is_resumed_on_next_run(self):
        body = b'Complete PBF fixture'
        checksum = hashlib.md5(body, usedforsecurity=False).hexdigest().encode()
        cancel = threading.Event()
        responses = [Response(checksum), InterruptedResponse(body), TimeoutError('timeout'), TimeoutError('timeout')]
        with patch('geofabrik.urlopen', side_effect=responses), patch.object(cancel, 'wait', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Downloading.*3 attempts'):
                obtain(DEFAULT_SOURCE, self.folder, cancel, lambda m: None)
        self.assertEqual(next(self.folder.glob('*.part')).read_bytes(), body[:4])
        resumed = Response(body[4:])
        resumed.status = 206
        resumed.headers['Content-Range'] = f'bytes 4-{len(body) - 1}/{len(body)}'
        with patch('geofabrik.urlopen', side_effect=[Response(checksum), resumed]):
            target, _ = obtain(DEFAULT_SOURCE, self.folder, cancel, lambda m: None)
        self.assertEqual(target.read_bytes(), body)

    def test_resume_restarts_when_server_ignores_range(self):
        body = b'Complete PBF fixture'
        checksum = hashlib.md5(body, usedforsecurity=False).hexdigest().encode()
        cancel = threading.Event()
        with patch('geofabrik.urlopen', side_effect=[Response(checksum), InterruptedResponse(body), Response(body)]), patch.object(cancel, 'wait', return_value=False):
            target, _ = obtain(DEFAULT_SOURCE, self.folder, cancel, lambda m: None)
        self.assertEqual(target.read_bytes(), body)

    def test_invalid_resume_never_publishes_data(self):
        body = b'Complete PBF fixture'
        checksum = hashlib.md5(body, usedforsecurity=False).hexdigest().encode()
        resumed = Response(body)
        resumed.status = 206
        resumed.headers['Content-Range'] = f'bytes 0-{len(body) - 1}/{len(body)}'
        cancel = threading.Event()
        with patch('geofabrik.urlopen', side_effect=[Response(checksum), InterruptedResponse(body), resumed]), patch.object(cancel, 'wait', return_value=False):
            with self.assertRaisesRegex(ValueError, 'invalid resume range'):
                obtain(DEFAULT_SOURCE, self.folder, cancel, lambda m: None)
        self.assertFalse(list(self.folder.glob('*.pbf')))
        self.assertFalse(list(self.folder.glob('*.part')))

    def test_retry_cancellation_and_permanent_http_errors(self):
        cancel = threading.Event()
        def stop(delay):
            cancel.set()
            return True
        def timeout():
            raise TimeoutError('Read timed out')
        with patch.object(cancel, 'wait', side_effect=stop):
            with self.assertRaises(Cancelled):
                retry_network(timeout, 'Checksum', cancel, lambda m: None)
        cancel.clear()
        operation = Mock(side_effect=HTTPError(DEFAULT_SOURCE, 404, 'Not found', {}, None))
        with self.assertRaises(HTTPError):
            retry_network(operation, 'Checksum', cancel, lambda m: None)
        operation.assert_called_once()
