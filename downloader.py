"""Anonymous ESA WorldCover downloads; Python standard library only."""
from dataclasses import dataclass
import math
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = 'https://esa-worldcover.s3.eu-central-1.amazonaws.com'
IMAGERY_BASE = 'https://esa-worldcover-s2.s3.eu-central-1.amazonaws.com'
PRODUCTS = {'Satellite imagery (RGB + near infrared)': 'S2RGBNIR',
            'Land-cover classification': 'Map'}


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Bounds:
    west: float
    south: float
    east: float
    north: float

    def validate(self):
        if not all(math.isfinite(x) for x in (self.west, self.south, self.east, self.north)):
            raise ValueError('Coordinates must be finite decimal numbers.')
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError('Longitude must satisfy -180 ≤ west < east ≤ 180. Split regions crossing the date line.')
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError('Latitude must satisfy -90 ≤ south < north ≤ 90.')


@dataclass(frozen=True)
class Tile:
    name: str
    url: str
    size: int


def candidates(bounds, year, product='Map'):
    bounds.validate()
    if product not in ('Map', 'S2RGBNIR'):
        raise ValueError('Unknown WorldCover product.')
    step = 1 if product == 'S2RGBNIR' else 3
    version = {2020: 'v100', 2021: 'v200'}[year]
    names = []
    for lat in range(math.floor(bounds.south / step) * step, math.ceil(bounds.north / step) * step, step):
        for lon in range(math.floor(bounds.west / step) * step, math.ceil(bounds.east / step) * step, step):
            tile = f'{"N" if lat >= 0 else "S"}{abs(lat):02d}{"E" if lon >= 0 else "W"}{abs(lon):03d}'
            name = f'ESA_WorldCover_10m_{year}_{version}_{tile}_{product}.tif'
            url = (f'{IMAGERY_BASE}/rgbnir/{year}/{tile[:3]}/{name}' if product == 'S2RGBNIR'
                   else f'{BASE}/{version}/{year}/map/{name}')
            names.append((name, url))
    if len(names) > 400:
        raise ValueError('Region covers more than 400 tiles. Please choose a smaller region.')
    return names


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled()


def preview(bounds, year, cancel, report, product='Map'):
    result = []
    urls = candidates(bounds, year, product)
    for index, (name, url) in enumerate(urls, 1):
        check_cancel(cancel)
        report(f'Checking tile {index}/{len(urls)}: {name}')
        try:
            with urlopen(Request(url, method='HEAD'), timeout=30) as response:
                size = int(response.headers['Content-Length'])
                result.append(Tile(name, url, size))
        except HTTPError as error:
            if error.code != 404:
                raise
    check_cancel(cancel)
    return result


def download(tile, folder, cancel, progress):
    """Keep interrupted files separate; publish a file only after a complete transfer."""
    target = Path(folder) / tile.name
    partial = target.with_suffix('.tif.part')
    check_cancel(cancel)
    if target.exists():
        if target.stat().st_size == tile.size:
            progress(tile.size)
            return 'Already present'
        raise FileExistsError(f'{target.name} exists with a different size. Move or remove it before retrying.')
    received = 0
    try:
        with urlopen(tile.url, timeout=30) as response, partial.open('wb') as stream:
            while True:
                check_cancel(cancel)
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                received += len(chunk)
                progress(received)
        check_cancel(cancel)
        if received != tile.size:
            raise IOError(f'Incomplete download: expected {tile.size} bytes, received {received}.')
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return 'Downloaded'
