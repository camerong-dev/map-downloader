"""Cached, full-detail OSM ocean polygons, independent of regional PBF edges."""
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
import zipfile

SOURCE_URL = 'https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip'
SOURCE_PAGE = 'https://osmdata.openstreetmap.de/data/water-polygons.html'
MAX_DOWNLOAD = 2_000_000_000
MAX_EXTRACTED = 8_000_000_000
HEADERS = {'User-Agent': 'ESAMapsDesktop/1.0 (cached OSM coastline polygons)'}


def check(job):
    if Path(job['cancel']).exists():
        raise InterruptedError('Cancelled. Cached coastline data and partial downloads were kept.')


def download_archive(cache, job, emit):
    """Resume only the same server snapshot; publish only complete ZIPs."""
    target = cache / 'water-polygons.zip'
    if target.is_file():
        return target
    partial = cache / 'water-polygons.zip.part'
    stamp = cache / 'download.json'
    for attempt in range(3):
        check(job)
        try:
            with urlopen(Request(SOURCE_URL, headers=HEADERS, method='HEAD'), timeout=30) as response:
                size = int(response.headers.get('Content-Length', 0))
                snapshot = {'etag': response.headers.get('ETag'), 'size': size,
                            'modified': response.headers.get('Last-Modified')}
            if not 0 < size <= MAX_DOWNLOAD:
                raise ValueError('The coastline download has an unexpected size.')
            previous = json.loads(stamp.read_text()) if stamp.exists() else None
            offset = partial.stat().st_size if partial.exists() and previous == snapshot else 0
            if offset > size or not snapshot['etag']:
                offset = 0
            if not offset:
                # Discard an old snapshot before recording the new identity,
                # even if opening the subsequent GET fails.
                partial.unlink(missing_ok=True)
            stamp.write_text(json.dumps(snapshot), encoding='utf-8')
            if offset != size:
                headers = dict(HEADERS)
                if offset:
                    headers.update({'Range': f'bytes={offset}-', 'If-Range': snapshot['etag']})
                with urlopen(Request(SOURCE_URL, headers=headers), timeout=30) as response:
                    if offset and response.status == 206 and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                        partial.unlink(missing_ok=True)
                        raise OSError('The server returned an invalid resumed coastline range.')
                    if offset and response.status != 206:
                        offset = 0
                    received = offset
                    last_report = 0
                    with partial.open('ab' if offset else 'wb') as stream:
                        while True:
                            check(job)
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > size:
                                raise ValueError('The coastline download exceeded its expected size.')
                            stream.write(chunk)
                            if time.monotonic() - last_report >= 5:
                                emit(f'Downloading global coastlines: {received/1e6:.0f}/{size/1e6:.0f} MB (one-time cache)…')
                                last_report = time.monotonic()
                if received != size:
                    raise OSError('The coastline download ended early.')
            check(job)
            with zipfile.ZipFile(partial) as archive:
                if not any(member.filename.endswith('/water_polygons.shp') for member in archive.infolist()):
                    raise ValueError('The coastline archive does not contain water polygons.')
            partial.replace(target)
            return target
        except (OSError, zipfile.BadZipFile) as error:
            if isinstance(error, zipfile.BadZipFile):
                partial.unlink(missing_ok=True)
            if attempt == 2:
                raise RuntimeError('Could not download global coastline polygons. Check your connection and retry; partial downloads are kept.') from error
            emit('Coastline download interrupted; retrying from the saved position…')
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                check(job)
                time.sleep(0.1)


def process_alive(pid):
    if pid <= 0 or pid > 0xffffffff:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # access denied is not a stale lock
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        # A process owned by another user can exist without being signalable.
        return True


def prepare_ocean_source(cache, job, emit):
    """Extract once for local queries, with cancellable cross-process lock."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    ready = cache / 'ready.json'
    target = cache / 'water_polygons.shp'
    def complete():
        return ready.is_file() and all((cache/f'water_polygons.{suffix}').is_file()
                                      for suffix in ('shp','shx','dbf','prj'))
    check(job)
    if complete():
        return target
    lock = cache / 'prepare.lock'
    waiting = False
    while True:
        check(job)
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if not waiting:
                emit('Waiting for another export to finish preparing cached coastline data…')
                waiting = True
            # Recover a lock left by a terminated worker after the next start.
            owner = lock / 'owner'
            if owner.exists():
                try:
                    pid = int(owner.read_text())
                    if not process_alive(pid):
                        owner.unlink(missing_ok=True)
                        try:
                            lock.rmdir()
                        except OSError:
                            pass
                        continue
                except (OSError, ValueError):
                    pass
            elif time.time() - lock.stat().st_mtime > 30:
                try:
                    lock.rmdir()
                except OSError:
                    pass
            time.sleep(0.2)
    (lock/'owner').write_text(str(os.getpid()))
    try:
        if complete():
            return target
        archive = download_archive(cache, job, emit)
        emit('Preparing global coastline data for reuse…')
        with zipfile.ZipFile(archive) as zipped:
            members = [m for m in zipped.infolist() if Path(m.filename).stem == 'water_polygons'
                       and Path(m.filename).suffix.lower() in ('.shp','.shx','.dbf','.prj','.qix','.cpg')]
            if sum(m.file_size for m in members) > MAX_EXTRACTED:
                raise ValueError('The coastline archive is unexpectedly large.')
            for member in members:
                check(job)
                final = cache / Path(member.filename).name
                partial = final.with_suffix(final.suffix + '.part')
                try:
                    with zipped.open(member) as source, partial.open('wb') as stream:
                        while chunk := source.read(1024 * 1024):
                            check(job)
                            stream.write(chunk)
                    partial.replace(final)
                finally:
                    partial.unlink(missing_ok=True)
        if not all((cache/f'water_polygons.{suffix}').exists() for suffix in ('shp','shx','dbf','prj')):
            raise ValueError('The coastline archive is missing required shapefile data.')
        ready.write_text(json.dumps({'url': SOURCE_URL, 'license': 'ODbL',
                                    'attribution': '© OpenStreetMap contributors'}), encoding='utf-8')
        # Extraction is verified by ZIP CRCs. Keep the extracted data, not two copies.
        archive.unlink()
        return target
    finally:
        (lock/'owner').unlink(missing_ok=True)
        lock.rmdir()


def ocean_polygons(folder, extent, job, emit):
    """Clip trusted global polygons to the map, never infer sea from open ways."""
    from osgeo import gdal
    check(job)
    cache = folder.parent / '.coastline-cache'
    source = prepare_ocean_source(cache, job, emit)
    target = folder / 'sea.fgb'
    partial = folder / '.sea.part.fgb'
    bounds = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    emit('Clipping full-detail OSM ocean and harbour polygons to the map…')
    result = None
    try:
        try:
            result = gdal.VectorTranslate(str(partial), str(source), format='FlatGeobuf',
                spatFilter=bounds, clipDst=str(folder/'map-coverage.geojson') if (folder/'map-coverage.geojson').exists() else bounds, dstSRS='EPSG:3857',
                geometryType='MULTIPOLYGON', layerCreationOptions=['SPATIAL_INDEX=YES'],
                callback=lambda *args: 0 if Path(job['cancel']).exists() else 1)
        except RuntimeError:
            check(job)
            raise
        check(job)
        if result is None:
            raise RuntimeError('Could not prepare ocean polygons.')
        count = result.GetLayer(0).GetFeatureCount()
        result = None
        partial.replace(target)
    finally:
        result = None
        partial.unlink(missing_ok=True)
    info = {'source': SOURCE_PAGE, 'download': SOURCE_URL,
            'attribution': '© OpenStreetMap contributors', 'license': 'ODbL',
            'features': count, 'data': target.name}
    (folder/'coastline-info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    return target if count else None
