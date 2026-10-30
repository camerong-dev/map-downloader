"""Shared map dimensions and cost estimates, independent of Tk and QGIS."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MapPlan:
    width: int
    height: int
    block_size: int = 4096
    bytes_per_pixel: int = 4

    def __post_init__(self):
        if not 64 <= self.block_size <= 4096:
            raise ValueError('Render block size must be between 64 and 4096 pixels.')
        if not 1 <= self.width <= 2**31 - 1 or not 1 <= self.height <= 2**31 - 1:
            raise ValueError('Map dimensions exceed TIFF limits. Use a larger metres/pixel value or a smaller area.')

    @property
    def columns(self):
        return math.ceil(self.width / self.block_size)

    @property
    def rows(self):
        return math.ceil(self.height / self.block_size)

    @property
    def blocks(self):
        return self.columns * self.rows

    @property
    def raw_bytes(self):
        return self.width * self.height * self.bytes_per_pixel

    def summary(self):
        unit, divisor = ('GiB', 1024**3) if self.raw_bytes >= 1024**3 else ('MiB', 1024**2)
        return (f'{self.width:,} × {self.height:,} pixels • {self.blocks:,} render blocks • '
                f'~{self.raw_bytes / divisor:,.1f} {unit} before compression, excluding overviews')


def projected_plan(width, height, resolution, block_size=4096, bytes_per_pixel=4):
    values = (width, height, resolution)
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise ValueError('Map extent and metres/pixel must be positive finite numbers.')
    pixels = width / resolution, height / resolution
    if any(not math.isfinite(value) or value > 2**31 - 1 for value in pixels):
        raise ValueError('Map dimensions exceed TIFF limits. Use a larger metres/pixel value or a smaller area.')
    return MapPlan(math.ceil(pixels[0]), math.ceil(pixels[1]), block_size, bytes_per_pixel)


def geographic_plan(bounds, resolution, block_size=4096, bytes_per_pixel=4):
    bounds.validate()
    if bounds.south < -85 or bounds.north > 85:
        raise ValueError('Map exports support latitudes from -85 to 85 degrees.')
    radius = 6378137
    width = radius * math.radians(bounds.east - bounds.west)
    def northing(latitude):
        return radius * math.asinh(math.tan(math.radians(latitude)))
    height = northing(bounds.north) - northing(bounds.south)
    return projected_plan(width, height, resolution, block_size, bytes_per_pixel)
