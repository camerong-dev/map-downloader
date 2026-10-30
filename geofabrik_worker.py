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
    key = hashlib.sha256(f'v2-english:{source}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()[:24]
    target = Path(cache_dir) / f'{key}.gpkg'
    if target.exists():
        emit(f'Reusing local road/place database: {target}')
        return target
    partial = target.with_name(f'{key}.{time.time_ns()}.part.gpkg')
    config = partial.with_suffix('.ini')
    header = '[general]\n' if int(gdal.VersionInfo('VERSION_NUM')) >= 3100000 else ''
    config.write_text(header + 'closed_ways_are_polygons=building,landuse,natural,amenity\n'
                      '[points]\nosm_id=yes\nattributes=name,name:en,place\nother_tags=no\n'
                      '[lines]\nosm_id=yes\nattributes=name,name:en,ref,highway\nother_tags=no\n'
                      '[multipolygons]\nother_tags=no\n[multilinestrings]\nother_tags=no\n[other_relations]\nother_tags=no\n', encoding='utf-8')
    previous = gdal.GetConfigOption('OSM_CONFIG_FILE')
    gdal.SetConfigOption('OSM_CONFIG_FILE', str(config))
    roads = "'motorway','motorway_link','trunk','trunk_link','primary','primary_link','secondary','secondary_link','tertiary','tertiary_link','unclassified','residential','living_street','service'"
    places = "'city','town','village','hamlet','suburb'"
    try:
        for index, (name, query) in enumerate([
            ('roads', f'SELECT osm_id, name, name_en, ref, highway FROM lines WHERE highway IN ({roads})'),
            ('places', f"SELECT osm_id, name, name_en, place FROM points WHERE place IN ({places}) AND (name IS NOT NULL OR name_en IS NOT NULL)")]):
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
    for name in ('roads', 'places'):
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
            destination = output.CreateLayer(name, srs, ogr.wkbLineString if name == 'roads' else ogr.wkbPoint)
            for field in ('osm_id', 'name', 'name_en', 'ref', 'highway', 'place', 'road_group'):
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
                        osm_id = str(feature.GetField('osm_id'))
                        if osm_id in seen:
                            duplicates[name] += 1
                            continue
                        seen.add(osm_id)
                        out = ogr.Feature(destination.GetLayerDefn())
                        for field in ('name', 'name_en', 'ref', 'highway', 'place', 'road_group'):
                            field_index = feature.GetFieldIndex(field)
                            out.SetField(field, (feature.GetField(field_index) or '') if field_index >= 0 else '')
                        out.SetField('osm_id', ('way/' if name == 'roads' else 'node/') + osm_id)
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
    if not counts['roads'] and not counts['places']:
        raise ValueError('No roads or settlements found in this area. Choose a Geofabrik extract that covers the selected TIFF/coordinates.')
    emit(f"Extracted {counts['roads']} roads and {counts['places']} places from local Geofabrik data.")
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
