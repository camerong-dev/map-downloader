"""QGIS GDAL worker: import a regional PBF once, then select roads/places locally."""
import hashlib
import json
from pathlib import Path
import sys
import time

from osgeo import gdal, ogr, osr
from hybrid_style import road_group

gdal.UseExceptions()
ogr.UseExceptions()


def emit(message):
    print(json.dumps({'message': message}), flush=True)


def check(job):
    if Path(job['cancel']).exists():
        raise InterruptedError('Cancelled. Completed regional downloads and imports are kept.')


def import_extract(source, cache_dir, job):
    check(job)
    source = Path(source).resolve()
    stat = source.stat()
    pure_osm = job.get('map_mode') == 'osm'
    version = 'v4-basemap' if pure_osm else 'v2-english'
    key = hashlib.sha256(f'{version}:{source}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()[:24]
    target = Path(cache_dir) / f'{key}.gpkg'
    if target.exists():
        emit(f'Reusing local road/place database: {target}')
        return target
    partial = target.with_name(f'{key}.{time.time_ns()}.part.gpkg')
    config = partial.with_suffix('.ini')
    header = '[general]\n' if int(gdal.VersionInfo('VERSION_NUM')) >= 3100000 else ''
    # Closed coastlines must remain directed lines so islands can be separated
    # from sea; other natural areas are polygons.
    polygon_keys = ('building,landuse,amenity,leisure,water,natural=water,natural=wood,'
                    'natural=scrub,natural=beach,natural=sand,natural=heath,natural=grassland,'
                    'natural=wetland,natural=bare_rock,natural=glacier' if pure_osm else
                    'building,landuse,natural,amenity')
    config.write_text(header + f'closed_ways_are_polygons={polygon_keys}\n'
                      '[points]\nosm_id=yes\nattributes=name,name:en,place\nother_tags=no\n'
                      '[lines]\nosm_id=yes\nattributes=name,name:en,ref,highway,waterway,railway,natural\nother_tags=no\n'
                      '[multipolygons]\nosm_id=yes\nattributes=name,name:en,natural,landuse,leisure,building,water,amenity\nother_tags=no\n[multilinestrings]\nother_tags=no\n[other_relations]\nother_tags=no\n', encoding='utf-8')
    previous = gdal.GetConfigOption('OSM_CONFIG_FILE')
    gdal.SetConfigOption('OSM_CONFIG_FILE', str(config))
    roads = "'motorway','motorway_link','trunk','trunk_link','primary','primary_link','secondary','secondary_link','tertiary','tertiary_link','unclassified','residential','living_street','service'"
    if pure_osm:
        roads += ", 'track','path','footway','cycleway','pedestrian','steps'"
    places = "'city','town','village','hamlet','suburb'"
    queries = [
            ('roads', f'SELECT osm_id, name, name_en, ref, highway FROM lines WHERE highway IN ({roads})'),
            ('places', f"SELECT osm_id, name, name_en, place FROM points WHERE place IN ({places}) AND (name IS NOT NULL OR name_en IS NOT NULL)")]
    if pure_osm:
        queries.extend([
            ('map_lines', 'SELECT osm_id, name, name_en, waterway, railway, natural FROM lines WHERE waterway IS NOT NULL OR railway IS NOT NULL OR natural = \'coastline\''),
            ('map_areas', 'SELECT osm_id, osm_way_id, name, name_en, natural, landuse, leisure, building, water, amenity FROM multipolygons')])
    try:
        for index, (name, query) in enumerate(queries):
            check(job)
            emit(f'Importing {name} from regional OSM file (first import only)…')
            result = gdal.VectorTranslate(str(partial), str(source), format='GPKG',
                                          accessMode='update' if index else None,
                                          SQLStatement=query, layerName=name,
                                          layerCreationOptions=['SPATIAL_INDEX=YES'],
                                          callback=lambda fraction, message, data: 0 if Path(job['cancel']).exists() else 1)
            if result is None:
                check(job)
                raise RuntimeError(f'Could not import {name} from OSM file.')
            result = None
        check(job)
        database = ogr.Open(str(partial), 1)
        layer = database.GetLayerByName('roads')
        layer.CreateField(ogr.FieldDefn('road_group', ogr.OFTString))
        database.StartTransaction()
        for index, feature in enumerate(layer):
            if index % 1000 == 0:
                check(job)
            feature.SetField('road_group', road_group({'ref': feature.GetField('ref') or '', 'highway': feature.GetField('highway') or ''}))
            layer.SetFeature(feature)
        database.CommitTransaction()
        feature = layer = database = None
        check(job)
        partial.replace(target)
        emit(f'Regional import complete: {target}')
        return target
    except Exception:
        check(job)
        raise
    finally:
        gdal.SetConfigOption('OSM_CONFIG_FILE', previous)
        config.unlink(missing_ok=True)
        # Close references before removing an interrupted SQLite database on Windows.
        result = feature = layer = database = None
        for suffix in ('', '-wal', '-shm', '-journal'):
            Path(str(partial) + suffix).unlink(missing_ok=True)


def extract(job):
    check(job)
    cache_dir = Path(job['cache_dir'])
    cache_dir.mkdir(parents=True, exist_ok=True)
    sources = job.get('sources', [job.get('source')])
    if not isinstance(sources, list) or not sources or any(not source for source in sources):
        raise ValueError('Provide at least one OSM source file.')
    sources = list(dict.fromkeys(str(Path(source).resolve()) for source in sources))
    cached = []
    for index, source in enumerate(sources, 1):
        check(job)
        emit(f'Preparing OSM dataset {index}/{len(sources)}: {Path(source).name}')
        cached.append(import_extract(source, cache_dir, job))
    b = job['bounds']
    folder = Path(job['folder'])
    counts = {}
    duplicates = {}
    names = ('roads', 'places', 'map_lines', 'map_areas') if job.get('map_mode') == 'osm' else ('roads', 'places')
    extents = []
    for path in cached:
        check(job)
        database = ogr.Open(str(path))
        try:
            for name in names:
                layer = database.GetLayerByName(name)
                if layer.GetFeatureCount() > 0:
                    extents.append(layer.GetExtent())
        finally:
            layer = database = None
    requested = f"west {b['west']:.5f}, south {b['south']:.5f}, east {b['east']:.5f}, north {b['north']:.5f}"
    emit(f'Export area (WGS84): {requested}')
    if extents:
        available = (min(e[0] for e in extents), max(e[1] for e in extents),
                     min(e[2] for e in extents), max(e[3] for e in extents))
        coverage = f'west {available[0]:.5f}, south {available[2]:.5f}, east {available[1]:.5f}, north {available[3]:.5f}'
        emit(f'OSM source feature bounds (WGS84): {coverage}')
        if not any(b['west'] <= e[1] and b['east'] >= e[0] and
                   b['south'] <= e[3] and b['north'] >= e[2] for e in extents):
            raise ValueError(f'The chosen export area does not overlap the OSM data. Export area: {requested}. '
                             f'OSM data bounds: {coverage}. Choose Use TIFF area or Load saved map area, '
                             'correct the coordinates, or choose OSM sources covering the requested area.')
    fields = ('name', 'name_en', 'ref', 'highway', 'place', 'road_group', 'waterway', 'railway', 'natural', 'landuse', 'leisure', 'building', 'water', 'amenity')
    for name in names:
        check(job)
        emit(f'Extracting {name} for selected TIFF/coordinate bounds…')
        target = folder / f'{name}.geojson'
        if target.exists():
            raise FileExistsError(f'{target.name} already exists.')
        partial = folder / f'.{name}.part.geojson'
        output = None
        try:
            output = ogr.GetDriverByName('GeoJSON').CreateDataSource(str(partial))
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            destination = output.CreateLayer(name, srs, ogr.wkbUnknown)
            for field in ('osm_id', *fields):
                destination.CreateField(ogr.FieldDefn(field, ogr.OFTString))
            count = 0
            duplicates[name] = 0
            seen = set()
            for index, path in enumerate(cached, 1):
                check(job)
                emit(f'Selecting {name} from dataset {index}/{len(cached)}…')
                database = layer = feature = None
                try:
                    database = ogr.Open(str(path))
                    layer = database.GetLayerByName(name)
                    layer.SetSpatialFilterRect(b['west'], b['south'], b['east'], b['north'])
                    for position, feature in enumerate(layer):
                        if position % 1000 == 0:
                            check(job)
                        osm_id = feature.GetField('osm_id')
                        prefix = 'node/' if name == 'places' else 'way/'
                        if name == 'map_areas':
                            way_id = feature.GetField('osm_way_id')
                            if way_id is not None:
                                osm_id = way_id
                            else:
                                prefix = 'relation/'
                        osm_id = prefix + str(osm_id)
                        if osm_id in seen:
                            duplicates[name] += 1
                            continue
                        seen.add(osm_id)
                        out = ogr.Feature(destination.GetLayerDefn())
                        for field in fields:
                            field_index = feature.GetFieldIndex(field)
                            out.SetField(field, (feature.GetField(field_index) or '') if field_index >= 0 else '')
                        out.SetField('osm_id', osm_id)
                        out.SetGeometry(feature.GetGeometryRef())
                        destination.CreateFeature(out)
                        out = None
                        count += 1
                finally:
                    feature = layer = database = None
            destination = output = None
            check(job)
            partial.rename(target)
            counts[name] = count
        finally:
            destination = output = None
            partial.unlink(missing_ok=True)
    if not any(counts.values()):
        raise ValueError(f'No OSM features found inside the export area: {requested}. '
                         'The OSM import completed. Check the export coordinates and source coverage; '
                         'use a map TIFF or saved map area to select the intended region.')
    emit('Extracted OSM features: ' + ', '.join(f'{name}: {count}' for name, count in counts.items()))
    emit(f"Removed {duplicates['roads']} duplicate roads and {duplicates['places']} duplicate places across datasets.")
    info = {'counts': counts, 'databases': [str(path) for path in cached], 'duplicates_removed': duplicates}
    if len(cached) == 1:
        info['database'] = str(cached[0])
    (folder / 'extract-info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')


if __name__ == '__main__':
    try:
        extract(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception as error:
        emit(str(error))
        sys.exit(2 if isinstance(error, InterruptedError) else 1)
