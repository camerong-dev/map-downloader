"""Road display groups shared by the GUI, OSM conversion and QGIS worker."""
import re
import math

# Muted land colours leave contrast for streets, buildings and names.
OSM_COLOURS = {
    'land': '#f7f5ef', 'water': '#a8cee0', 'park': '#d9e8c6',
    'woodland': '#bad3b1', 'farmland': '#f2efd9', 'industrial': '#ebe4eb',
    'residential': '#f2f0eb', 'sand': '#f4e6bf', 'building': '#d3c6b8',
    'building_edge': '#baa997',
    'text': '#38434a', 'halo': '#ffffff', 'water_label': '#397a99',
    'park_label': '#587452', 'rail': '#8b8b86', 'coast': '#87b2c7', 'path': '#b49c83',
}
# Widths are millimetres at the renderer's DPI, independent of source imagery.
OSM_ROADS = {
    'motorway': ('#e9a1ad', '#bd7585', 3.0, 10),
    'a_road': ('#f5c98b', '#c29b62', 2.3, 10),
    'b_road': ('#f7e4ad', '#c3b17f', 1.9, 9),
    'main': ('#ffffff', '#c6beb0', 1.45, 9),
    'local': ('#ffffff', '#d1cabf', 1.0, 8),
}

OSM_DARK_COLOURS = {
    'land': '#151c24', 'water': '#102d42', 'park': '#1f3733',
    'woodland': '#19312d', 'farmland': '#293127', 'industrial': '#2b2835',
    'residential': '#1c252f', 'sand': '#3a3429', 'building': '#34414d',
    'building_edge': '#465563', 'text': '#dde6ee', 'halo': '#151c24',
    'water_label': '#83b9d1', 'park_label': '#92b8a1', 'rail': '#8e9aaa',
    'coast': '#376079', 'path': '#829087',
}
OSM_DARK_ROADS = {
    'motorway': ('#c88b9a', '#442f3e', 3.0, 10),
    'a_road': ('#ccaa76', '#493c2d', 2.3, 10),
    'b_road': ('#9eacb8', '#303e4a', 1.9, 9),
    'main': ('#637485', '#263340', 1.45, 9),
    'local': ('#455665', '#131e28', 1.0, 8),
}
OSM_STYLES = ('Light', 'Dark')


def osm_style(value='Light'):
    if value not in OSM_STYLES:
        raise ValueError('Choose Light or Dark OSM styling.')
    return (OSM_DARK_COLOURS, OSM_DARK_ROADS) if value == 'Dark' else (OSM_COLOURS, OSM_ROADS)


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
