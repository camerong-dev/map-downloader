"""Road display groups shared by the GUI, OSM conversion and QGIS worker."""
import re
import math


def osm_display_scale(value):
    scale = float(value)
    if not math.isfinite(scale) or not 1 <= scale <= 4:
        raise ValueError('OSM size must be between 100% and 400%.')
    return scale

ROAD_GROUPS = {
    'motorway': 'Motorways / M roads',
    'a_road': 'A roads / major roads',
    'b_road': 'B roads / secondary roads',
    'main': 'Other main roads',
    'local': 'Minor / residential / service roads',
}
DEFAULT_ROADS = ['motorway', 'a_road', 'b_road', 'main']
ROAD_LABELS = ['Numbers and names', 'Numbers only', 'Names only', 'Off']


def road_group(tags):
    refs = re.split(r'[;,]', tags.get('ref', '').upper())
    highway = tags.get('highway', '')
    def numbered(prefix):
        return any(re.match(rf'^\s*{prefix}\s*\d', ref) for ref in refs)
    if numbered('M') or highway in ('motorway', 'motorway_link'):
        return 'motorway'
    if numbered('A'):
        return 'a_road'
    if numbered('B'):
        return 'b_road'
    if highway in ('trunk', 'trunk_link', 'primary', 'primary_link'):
        return 'a_road'
    if highway in ('secondary', 'secondary_link'):
        return 'b_road'
    if highway in ('tertiary', 'tertiary_link'):
        return 'main'
    return 'local'
