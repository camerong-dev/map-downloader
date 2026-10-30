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
from geofabrik import parse_region_polygon, obtain_region_polygon


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
    def test_region_outline_preserves_islands_and_holes(self):
        polygon = parse_region_polygon('region\n1\n0 0\n4 0\n4 4\n0 4\nEND\n2\n8 0\n9 0\n9 1\nEND\n!1\n1 1\n2 1\n2 2\nEND\nEND')
        self.assertEqual(len(polygon['coordinates']),2)
        self.assertEqual(len(polygon['coordinates'][0]),2)
        self.assertEqual(polygon['coordinates'][0][0][0],polygon['coordinates'][0][0][-1])
        for bad in ('name\nEND','name\n1\n0 0\n1 0\n1 1','name\n1\nnan 0\n1 0\n1 1\nEND\nEND'):
            with self.assertRaises((ValueError,StopIteration)):
                parse_region_polygon(bad)

    def test_region_outline_is_cached_and_local_unknown_sources_stay_offline(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root = Path(temporary)
            cancel = threading.Event()
            body = b'region\n1\n0 0\n1 0\n1 1\n0 1\nEND\nEND'
            with patch('geofabrik.urlopen',return_value=Response(body)) as network:
                geometry = obtain_region_polygon({'source':DEFAULT_SOURCE},root,cancel,lambda _:None)
                self.assertEqual(obtain_region_polygon({'source':DEFAULT_SOURCE},root,cancel,lambda _:None),geometry)
                network.assert_called_once()
                self.assertTrue(network.call_args.args[0].full_url.endswith('/west-sussex.poly'))
            with patch('geofabrik.urlopen',side_effect=AssertionError('Local extract must stay offline')):
                self.assertIsNone(obtain_region_polygon({'source':str(root/'local.osm.pbf')},root,cancel,lambda _:None))

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


    def test_concurrent_exports_share_one_completed_download(self):
        entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
        body = b'one verified cached source'
        digest = hashlib.md5(body, usedforsecurity=False).hexdigest()
        results, errors = [], []
        def transfer(source, partial, checksum, cancel, report):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test download was not released')
            partial.write_bytes(body)
            return len(body), 'Test date'
        def report(message):
            if message.startswith('Waiting for another export'):
                waiting.set()
        def obtain_in_thread():
            try:
                results.append(obtain(DEFAULT_SOURCE, self.folder, threading.Event(), report))
            except Exception as error:
                errors.append(error)
        with patch('geofabrik.fetch_checksum', return_value=digest), patch('geofabrik.transfer', side_effect=transfer) as download:
            first = threading.Thread(target=obtain_in_thread)
            second = threading.Thread(target=obtain_in_thread)
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            try:
                self.assertTrue(waiting.wait(2))
            finally:
                release.set()
                first.join(3)
                second.join(3)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0][0], results[1][0])
            download.assert_called_once()
            self.assertEqual(results[0][0].read_bytes(), body)

    def test_waiting_for_cached_source_can_be_cancelled(self):
        from geofabrik import source_cache_lock
        cancel = threading.Event()
        errors = []
        waiting = threading.Event()
        def worker():
            try:
                with source_cache_lock(self.folder/'same.pbf', cancel, lambda message: waiting.set()):
                    errors.append('Unexpectedly acquired lock')
            except Cancelled:
                pass
        with source_cache_lock(self.folder/'same.pbf', threading.Event(), lambda message: None):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(waiting.wait(2))
                cancel.set()
            finally:
                thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
