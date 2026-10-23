"""Build portable GeoJSON fixtures without network or GIS dependencies."""
from hybrid_style import road_group


def collections(elements):
    roads, places = [], []
    for element in elements:
        tags = element.get('tags', {})
        properties = {key: tags.get(key, '') for key in ('name', 'ref', 'highway', 'place')}
        properties['osm_id'] = f"{element['type']}/{element['id']}"
        if element['type'] == 'node' and tags.get('place') and tags.get('name'):
            geometry = {'type': 'Point', 'coordinates': [element['lon'], element['lat']]}
            places.append({'type': 'Feature', 'properties': properties, 'geometry': geometry})
        elif element['type'] == 'way' and tags.get('highway'):
            properties['road_group'] = road_group(tags)
            geometry = {'type': 'LineString', 'coordinates': [[p['lon'], p['lat']] for p in element['geometry']]}
            roads.append({'type': 'Feature', 'properties': properties, 'geometry': geometry})
    return ({'type': 'FeatureCollection', 'features': roads},
            {'type': 'FeatureCollection', 'features': places})
