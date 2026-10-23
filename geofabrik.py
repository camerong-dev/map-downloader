"""Download verified Geofabrik extracts, or reuse a local PBF without network access."""
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from downloader import check_cancel

DEFAULT_SOURCE = 'https://download.geofabrik.de/europe/united-kingdom/england/west-sussex-latest.osm.pbf'


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
    metadata_path = target.with_suffix('.json')
    if target.is_file() and metadata_path.is_file() and not refresh:
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            metadata = {}
        if target.stat().st_size == metadata.get('size') and metadata.get('source') == source:
            report(f'Reusing Geofabrik download from {metadata.get("downloaded_utc", "earlier run")}: {target}')
            return target, metadata
    report('Checking Geofabrik extract checksum…')
    headers = {'User-Agent': 'ESARegionDownloader/1.0 (Geofabrik offline hybrid maps)'}
    with urlopen(Request(source + '.md5', headers=headers), timeout=40) as response:
        checksum = response.read(1024).decode('ascii').split()[0].lower()
    if len(checksum) != 32 or any(c not in '0123456789abcdef' for c in checksum):
        raise ValueError('Geofabrik returned an invalid checksum.')
    partial = target.with_name(target.name + f'.{time.time_ns()}.part')
    digest = hashlib.md5(usedforsecurity=False)
    size = 0
    last_report = 0
    try:
        report(f'Downloading regional extract: {Path(urlsplit(source).path).name}…')
        with urlopen(Request(source, headers=headers), timeout=40) as response, partial.open('wb') as stream:
            expected = int(response.headers.get('Content-Length', 0))
            modified = response.headers.get('Last-Modified', '')
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
        if (expected and size != expected) or digest.hexdigest() != checksum:
            raise ValueError('Geofabrik download failed its size/checksum check. Retry; the extract may have updated during download.')
        from datetime import datetime, timezone
        metadata = {'source': source, 'kind': 'Geofabrik PBF', 'size': size, 'md5': checksum,
                    'last_modified': modified, 'downloaded_utc': datetime.now(timezone.utc).isoformat()}
        partial.replace(target)
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        return target, metadata
    finally:
        partial.unlink(missing_ok=True)
