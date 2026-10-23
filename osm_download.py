"""Bounded Overpass downloads and portable OSM GeoJSON, using only the stdlib."""
import json
import math
import hashlib
from pathlib import Path
import time
from http.client import IncompleteRead
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from downloader import Bounds, check_cancel
from hybrid_style import road_group

ENDPOINT = 'https://overpass-api.de/api/interpreter'
ATTRIBUTION = '© OpenStreetMap contributors'
LICENSE = 'https://opendatacommons.org/licenses/odbl/1-0/'
ROADS = 'motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|unclassified|residential|living_street|service'
PLACES = 'city|town|village|hamlet|suburb'


def parse_region(text):
    """Read two longitude/latitude corners and normalize reversed edges."""
    try:
        values = [float(value.strip()) for value in text.split(',')]
    except ValueError:
        raise ValueError('Enter four decimal numbers: west, south, east, north.') from None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        raise ValueError('Enter four finite decimal numbers: west, south, east, north.')
    lon1, lat1, lon2, lat2 = values
    if abs(lon2 - lon1) > 180:
        raise ValueError('Longitude span exceeds 180°. For a date-line region, split it into two downloads.')
    bounds = Bounds(min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2))
    bounds.validate()
    return bounds


def cells(bounds, step=0.5):
    bounds.validate()
    if bounds.south < -85 or bounds.north > 85:
        raise ValueError('Hybrid maps support latitudes from -85 to 85 degrees.')
    # Warp-derived extents often exceed an exact grid edge by a few metres.
    cols = max(1, math.ceil((bounds.east - bounds.west) / step - 0.0001))
    rows = max(1, math.ceil((bounds.north - bounds.south) / step - 0.0001))
    if rows * cols > (64 if step == 0.5 else 256):
        raise ValueError('OSM region exceeds 64 half-degree requests. Select a smaller region.')
    return [Bounds(bounds.west + col * step, bounds.south + row * step,
                   bounds.east if col == cols - 1 else bounds.west + (col + 1) * step,
                   bounds.north if row == rows - 1 else bounds.south + (row + 1) * step)
            for row in range(rows) for col in range(cols)]


def query(bounds):
    box = f'{bounds.south},{bounds.west},{bounds.north},{bounds.east}'
    return (f'[out:json][timeout:25][maxsize:67108864];('
            f'node[place~"^({PLACES})$"][name]({box});'
            f'way[highway~"^({ROADS})$"]({box}););out body geom;')


def collections(elements):
    roads, places = [], []
    seen = set()
    for element in elements:
        key = element['type'], element['id']
        if key in seen:
            continue
        seen.add(key)
        tags = element.get('tags', {})
        properties = {key: tags.get(key, '') for key in ('name', 'ref', 'highway', 'place')}
        properties['osm_id'] = f"{element['type']}/{element['id']}"
        if element['type'] == 'node' and tags.get('place') and tags.get('name'):
            geometry = {'type': 'Point', 'coordinates': [element['lon'], element['lat']]}
            places.append({'type': 'Feature', 'properties': properties, 'geometry': geometry})
        elif element['type'] == 'way' and tags.get('highway'):
            properties['road_group'] = road_group(tags)
            points = element.get('geometry', [])
            if len(points) < 2 or any('lon' not in p or 'lat' not in p for p in points):
                raise ValueError('Overpass returned an incomplete road geometry. Retry the download.')
            geometry = {'type': 'LineString', 'coordinates': [[p['lon'], p['lat']] for p in points]}
            roads.append({'type': 'Feature', 'properties': properties, 'geometry': geometry})
    def collection(features):
        return {'type': 'FeatureCollection', 'features': features}
    return collection(roads), collection(places)


def fetch(bounds, cancel, report, cache_dir=None, endpoint=ENDPOINT):
    cells(bounds)  # Keep the original geographic-size limit.
    requests = cells(bounds, step=0.25)
    cache_dir = Path(cache_dir) if cache_dir else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    elements = []
    for index, cell in enumerate(requests, 1):
        check_cancel(cancel)
        report(f'Downloading OSM request {index}/{len(requests)}…')
        key = hashlib.sha256((endpoint + query(cell)).encode()).hexdigest()
        cached = cache_dir / f'{key}.json' if cache_dir else None
        payload = None
        if cached and cached.exists() and time.time() - cached.stat().st_mtime < 86400:
            try:
                payload = json.loads(cached.read_text(encoding='utf-8'))
                if not isinstance(payload.get('elements'), list) or payload.get('remark'):
                    payload = None
            except (OSError, ValueError):
                payload = None
            if payload is not None:
                report(f'Reusing saved OSM request {index}/{len(requests)} (less than 24 hours old).')
        if payload is None:
            payload = request_payload(cell, cancel, report, endpoint)
            if cached:
                partial = cached.with_suffix(f'.{time.time_ns()}.part')
                try:
                    partial.write_text(json.dumps(payload), encoding='utf-8')
                    partial.replace(cached)
                finally:
                    partial.unlink(missing_ok=True)
        elements.extend(payload['elements'])
        if len(elements) > 300000:
            raise ValueError('Too many OSM features. Choose a smaller region. Completed requests are cached.')
    check_cancel(cancel)
    return collections(elements)


def request_payload(cell, cancel, report, endpoint):
    for attempt in range(3):
        check_cancel(cancel)
        request = Request(endpoint, data=urlencode({'data': query(cell)}).encode('utf-8'),
                          headers={'User-Agent': 'ESARegionDownloader/1.0 (OSM hybrid overlay)',
                                   'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            response = urlopen(request, timeout=40)
            payload = read_payload(response, cancel)
        except HTTPError as error:
            if error.code == 429:
                raise RuntimeError('Overpass rate limit (HTTP 429). Retry later; completed requests are cached.') from error
            if error.code not in (502, 503, 504):
                raise
            failure = f'HTTP {error.code}'
        except (URLError, TimeoutError, ConnectionError, IncompleteRead) as error:
            failure = str(error)
        else:
            return payload
        if attempt == 2:
            raise RuntimeError(f'Overpass failed after 3 attempts ({failure}); retry later or select another Overpass HTTPS endpoint. Completed requests are cached.')
        delay = (5, 15)[attempt]
        report(f'Overpass {failure}; retry {attempt + 2}/3 in {delay} seconds…')
        if cancel.wait(delay):
            check_cancel(cancel)


def read_payload(response, cancel):
    with response:
        chunks, size = [], 0
        while True:
            check_cancel(cancel)
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 64 * 1024**2:
                raise ValueError('OSM response too large. Choose a smaller region.')
            chunks.append(chunk)
    payload = json.loads(b''.join(chunks))
    if payload.get('remark'):
        raise RuntimeError(f"Overpass could not complete the request: {payload['remark']}")
    if not isinstance(payload.get('elements'), list):
        raise ValueError('Invalid Overpass response: missing elements.')
    return payload
