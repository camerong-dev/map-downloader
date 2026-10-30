"""Download verified Geofabrik extracts, or reuse a local PBF without network access."""
import hashlib
import json
import math
import os
from pathlib import Path
import time
import re
import threading
from contextlib import contextmanager
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from downloader import check_cancel

DEFAULT_SOURCE = 'https://download.geofabrik.de/europe/united-kingdom/england/west-sussex-latest.osm.pbf'
ATTRIBUTION = '© OpenStreetMap contributors'
LICENSE = 'https://opendatacommons.org/licenses/odbl/1-0/'
NETWORK_TIMEOUT = 90
RETRY_DELAYS = (5, 15)
HEADERS = {'User-Agent': 'ESARegionDownloader/1.0 (Geofabrik offline hybrid maps)'}

_cache_locks = {}
_cache_locks_guard = threading.Lock()


@contextmanager
def source_cache_lock(target, cancel, report):
    """Coordinate hybrid/OSM tab downloads without blocking cancellation."""
    key = os.path.normcase(str(target.resolve()))
    with _cache_locks_guard:
        lock = _cache_locks.setdefault(key, threading.Lock())
    waiting = False
    while not lock.acquire(timeout=0.2):
        check_cancel(cancel)
        if not waiting:
            report('Waiting for another export to prepare the same cached OSM source…')
            waiting = True
    try:
        check_cancel(cancel)
        yield
    finally:
        lock.release()


def retry_network(operation, stage, cancel, report):
    """Retry temporary network failures; cancellation also interrupts backoff."""
    for attempt in range(3):
        check_cancel(cancel)
        report(f'{stage}: attempt {attempt + 1}/3 (socket timeout {NETWORK_TIMEOUT}s)…')
        try:
            return operation()
        except HTTPError as error:
            error.close()
            if error.code not in (408, 500, 502, 503, 504):
                raise
            reason = f'HTTP {error.code}'
        except (URLError, TimeoutError, ConnectionError, IncompleteRead) as error:
            reason = str(error)
        check_cancel(cancel)
        if attempt == 2:
            raise RuntimeError(f'{stage} failed after 3 attempts: {reason}. '
                               'Retry later, or download the .osm.pbf in your browser and use Add PBF files. '
                               'Partial downloads and completed sources are kept for reuse.')
        delay = RETRY_DELAYS[attempt]
        report(f'{stage}: {reason}. Retrying in {delay}s…')
        if cancel.wait(delay):
            check_cancel(cancel)


def fetch_checksum(source, cancel, report):
    def read():
        with urlopen(Request(source + '.md5', headers=HEADERS), timeout=NETWORK_TIMEOUT) as response:
            tokens = response.read(1024).decode('ascii', errors='replace').split()
        checksum = tokens[0].lower() if tokens else ''
        if len(checksum) != 32 or any(c not in '0123456789abcdef' for c in checksum):
            raise ValueError('Geofabrik returned an invalid checksum.')
        return checksum
    return retry_network(read, f'Checking checksum for {Path(urlsplit(source).path).name}', cancel, report)


def transfer(source, partial, checksum, cancel, report):
    """Resume only a snapshot identified by the expected checksum; verify before use."""
    modified = ''
    def download():
        nonlocal modified
        size = partial.stat().st_size if partial.exists() else 0
        digest = hashlib.md5(usedforsecurity=False)
        if size:
            report(f'Checking {size / 1024**2:.1f} MiB of saved download before resuming…')
            with partial.open('rb') as saved:
                while chunk := saved.read(1024 * 1024):
                    check_cancel(cancel)
                    digest.update(chunk)
            if digest.hexdigest() == checksum:
                return size, modified
        headers = dict(HEADERS)
        if size:
            headers['Range'] = f'bytes={size}-'
        with urlopen(Request(source, headers=headers), timeout=NETWORK_TIMEOUT) as response:
            status = getattr(response, 'status', 200)
            if status == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if not match or int(match[1]) != size:
                    raise ValueError('Geofabrik returned an invalid resume range. No unverified file was published.')
                expected = int(match[3])
                mode = 'ab' if size else 'wb'
                report(f'Resuming Geofabrik download at {size / 1024**2:.1f} MiB…')
            else:
                if size:
                    report('Server did not support resume; restarting this file safely.')
                size = 0
                digest = hashlib.md5(usedforsecurity=False)
                expected = int(response.headers.get('Content-Length', 0))
                mode = 'wb'
            modified = response.headers.get('Last-Modified', modified)
            last_report = 0
            with partial.open(mode) as stream:
                while True:
                    check_cancel(cancel)
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if time.monotonic() - last_report >= 1:
                        report(f'Geofabrik download: {size / 1024**2:.1f} MiB' + (f' / {expected / 1024**2:.1f} MiB' if expected else ''))
                        last_report = time.monotonic()
        check_cancel(cancel)
        if expected and size < expected:
            raise ConnectionError(f'Incomplete transfer: received {size} of {expected} bytes')
        if (expected and size != expected) or digest.hexdigest() != checksum:
            raise ValueError('Geofabrik download failed its size/checksum check. Retry; the extract may have updated during download.')
        return size, modified
    try:
        return retry_network(download, f'Downloading {Path(urlsplit(source).path).name}', cancel, report)
    except ValueError:
        partial.unlink(missing_ok=True)
        raise


def validate_sources(text):
    """Validate one URL/file per line, preserving source priority without repeats."""
    sources, seen = [], set()
    for line in text.splitlines():
        if not line.strip():
            continue
        source = validate_source(line.strip())
        key = source if source.startswith('https://') else os.path.normcase(source)
        if key not in seen:
            sources.append(source)
            seen.add(key)
    if not sources:
        raise ValueError('Add at least one Geofabrik URL or local PBF file (one per line).')
    return sources


def validate_source(source):
    if source.startswith(('https://', 'http://')):
        parsed = urlsplit(source)
        if (parsed.scheme != 'https' or parsed.hostname != 'download.geofabrik.de'
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.path.endswith('.osm.pbf')):
            raise ValueError('Use an HTTPS .osm.pbf URL from download.geofabrik.de, or select a local PBF file.')
        return source
    path = Path(source).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in ('.pbf', '.osm'):
        raise ValueError('Select a local .osm.pbf/.osm file, or paste a Geofabrik .osm.pbf URL.')
    return str(path)


def obtain(source, cache_dir, cancel, report, refresh=False):
    source = validate_source(source)
    check_cancel(cancel)
    if not source.startswith('https://'):
        stat = Path(source).stat()
        return Path(source), {'source': source, 'kind': 'local OSM extract', 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(source.encode()).hexdigest()[:16]
    target = cache_dir / f'{key}-{Path(urlsplit(source).path).name}'
    with source_cache_lock(target, cancel, report):
        return obtain_remote(source, target, cancel, report, refresh)


def obtain_remote(source, target, cancel, report, refresh):
    metadata_path = target.with_suffix('.json')
    if target.is_file() and metadata_path.is_file() and not refresh:
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        if target.stat().st_size == metadata.get('size') and metadata.get('source') == source:
            report(f'Reusing Geofabrik download from {metadata.get("downloaded_utc", "earlier run")}: {target}')
            return target, metadata
    checksum = fetch_checksum(source, cancel, report)
    partial = target.with_name(target.name + f'.{checksum}.part')
    size, modified = transfer(source, partial, checksum, cancel, report)
    from datetime import datetime, timezone
    metadata = {'source': source, 'kind': 'Geofabrik PBF', 'size': size, 'md5': checksum,
                'last_modified': modified, 'downloaded_utc': datetime.now(timezone.utc).isoformat()}
    check_cancel(cancel)
    partial.replace(target)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return target, metadata


def parse_region_polygon(text):
    """Read Geofabrik .poly rings, including islands and exclusion rings."""
    lines = iter(line.strip() for line in text.splitlines() if line.strip())
    next(lines)  # region name
    polygons, holes = [], []
    ended = False
    for name in lines:
        if name == 'END':
            ended = True
            break
        ring = []
        for line in lines:
            if line == 'END':
                break
            point = [float(value) for value in line.split()]
            if len(point) != 2 or not all(math.isfinite(value) for value in point) or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                raise ValueError('Invalid region boundary coordinates.')
            ring.append(point)
        else:
            raise ValueError('Unfinished region boundary ring.')
        if len(ring) < 3:
            raise ValueError('Region boundary ring is too short.')
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        (holes if name.startswith('!') else polygons).append(ring if name.startswith('!') else [ring])
    if not ended or not polygons:
        raise ValueError('Incomplete region boundary.')
    for hole in holes:
        x, y = hole[0]
        for polygon in polygons:
            inside = False
            for a, b in zip(polygon[0], polygon[0][1:]):
                if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
                    inside = not inside
            if inside:
                polygon.append(hole)
                break
        else:
            raise ValueError('Region exclusion ring is outside its boundary.')
    return {'type':'MultiPolygon', 'coordinates':polygons}


def obtain_region_polygon(info, cache_dir, cancel, report):
    """Reuse a cached exact extract footprint; local unknown extracts use a hull."""
    source = str(info.get('source', ''))
    if not source.startswith('https://'):
        if not source:
            return None
        local = Path(source)
        polygon = local.with_name(local.name.removesuffix('.osm.pbf').removesuffix('.pbf').removesuffix('.osm') + '.poly')
        if polygon.is_file():
            return parse_region_polygon(polygon.read_text(encoding='utf-8'))
        metadata = local.with_suffix('.json')
        if metadata.is_file():
            try:
                saved = json.loads(metadata.read_text(encoding='utf-8'))
                if isinstance(saved, dict) and str(saved.get('source','')).startswith('https://'):
                    return obtain_region_polygon(saved, cache_dir, cancel, report)
            except (OSError, ValueError):
                pass
        return None
    try:
        validate_source(source)
        url = source.removesuffix('-latest.osm.pbf') + '.poly'
        if not source.endswith('-latest.osm.pbf'):
            return None
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / (hashlib.sha256(url.encode()).hexdigest()[:16] + '.poly')
        with source_cache_lock(target, cancel, report):
            if target.is_file():
                return parse_region_polygon(target.read_text(encoding='utf-8'))
            report('Preparing the OSM region boundary for terrain trimming…')
            with urlopen(Request(url, headers=HEADERS), timeout=30) as response:
                data = response.read(2_000_001)
            check_cancel(cancel)
            if len(data) > 2_000_000:
                raise ValueError('Region boundary is too large.')
            geometry = parse_region_polygon(data.decode('utf-8'))
            target.write_bytes(data)
            return geometry
    except (OSError, ValueError, StopIteration):
        report('Exact extract boundary unavailable; terrain will follow the outline of the selected OSM features.')
        return None
