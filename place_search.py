"""Place lookup, bounded HTTP caches and map geometry, without GUI dependencies."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from downloader import Bounds, parse_region

USER_AGENT = 'ESAMapsDesktop/1.0 (interactive region preview)'
SEARCH_URL = 'https://photon.komoot.io/api/'
TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
_search_lock = threading.Lock()
_last_search = 0


def cache_root():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.cache')
    return base / 'ESA-Maps' / 'preview-cache'


class WebCache:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder is not None else cache_root()

    def get(self, url, lifetime, limit):
        if urlsplit(url).scheme != 'https':
            raise ValueError('Preview services must use HTTPS.')
        path = self.folder / hashlib.sha256(url.encode()).hexdigest()
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if record['expires'] > time.time():
                return bytes.fromhex(record['data'])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        with urlopen(Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Language': 'en'}), timeout=20) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Preview service response was too large.')
            match = re.search(r'max-age=(\d+)', response.headers.get('Cache-Control', ''))
            # Tile policy: keep at least seven days if not implementing the full
            # HTTP revalidation protocol. Longer advertised lifetimes are kept.
            expiry = time.time() + max(lifetime, int(match[1]) if match else 0)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f'.{threading.get_ident()}.tmp')
            temporary.write_text(json.dumps({'expires': expiry, 'data': data.hex()}), encoding='utf-8')
            temporary.replace(path)
        except OSError:
            pass  # A read-only cache must not prevent the preview from working.
        return data


@dataclass(frozen=True)
class Place:
    label: str
    bounds: Bounds
    approximate: bool = False


def buffered(bounds, kilometres):
    km = float(kilometres)
    if not math.isfinite(km) or not 0 <= km <= 1000:
        raise ValueError('Buffer must be from 0 to 1000 km.')
    lat = km / 111.32
    lon = lat / max(0.01, math.cos(math.radians((bounds.north + bounds.south) / 2)))
    result = Bounds(max(-180, bounds.west - lon), max(-90, bounds.south - lat),
                    min(180, bounds.east + lon), min(90, bounds.north + lat))
    return parse_region(','.join(map(str, vars(result).values())))


def parse_places(payload):
    result = []
    for feature in payload.get('features', []):
        try:
            properties = feature['properties']
            name = properties.get('name')
            if not name:
                continue
            extent = properties.get('extent')
            approximate = not extent
            if extent:
                west, north, east, south = map(float, extent)
                # Sorting a date-line extent would silently select the wrong area.
                if west >= east or south >= north:
                    continue
                bounds = parse_region(f'{west},{south},{east},{north}')
            else:
                kind = properties.get('type') or properties.get('osm_value')
                if kind in ('country', 'state', 'county'):
                    continue  # Never substitute a town-sized area for a country.
                lon, lat = map(float, feature['geometry']['coordinates'])
                if not (-180 < lon < 180 and -90 < lat < 90):
                    continue
                bounds = buffered(Bounds(lon, lat, lon, lat), 5)
            parts = list(dict.fromkeys(str(properties[k]) for k in
                        ('name', 'city', 'county', 'state', 'country') if properties.get(k)))
            label = ', '.join(parts)
            if approximate:
                label += ' (5 km around centre)'
            place = Place(label, bounds, approximate)
            if place not in result:
                result.append(place)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return result


def search(query, cache=None):
    global _last_search
    query = query.strip()
    if not 2 <= len(query) <= 200:
        raise ValueError('Enter a place name between 2 and 200 characters.')
    url = os.environ.get('ESA_MAPS_SEARCH_URL', SEARCH_URL)
    url += ('&' if '?' in url else '?') + urlencode({'q': query, 'limit': 8, 'lang': 'en'})
    # Only submitted searches, never keystroke autocomplete. Serialize requests.
    with _search_lock:
        time.sleep(max(0, 1.1 - (time.monotonic() - _last_search)))
        _last_search = time.monotonic()
        payload = json.loads((cache or WebCache()).get(url, 86400, 2_000_000))
    return parse_places(payload)


def tile_image(z, x, y, cache=None):
    url = os.environ.get('ESA_MAPS_TILE_URL', TILE_URL).format(z=z, x=x, y=y)
    return (cache or WebCache()).get(url, 7 * 86400, 1_000_000)


def project(lon, lat, zoom):
    lat = max(-85.05112878, min(85.05112878, lat))
    size = 256 * 2 ** zoom
    return ((lon + 180) / 360 * size,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size)


def unproject(x, y, zoom):
    size = 256 * 2 ** zoom
    return (max(-180, min(180, x / size * 360 - 180)),
            math.degrees(math.atan(math.sinh(math.pi * (1 - max(0, min(size, y)) / size * 2)))))


def tile_bounds(name):
    match = re.search(r'_([NS])(\d{2})([EW])(\d{3})_(Map|S2RGBNIR)\.tif$', name)
    if not match:
        raise ValueError('Unrecognized ESA tile name.')
    ns, lat, ew, lon, product = match.groups()
    south = int(lat) * (-1 if ns == 'S' else 1)
    west = int(lon) * (-1 if ew == 'W' else 1)
    step = 3 if product == 'Map' else 1
    return Bounds(west, south, west + step, south + step)
