"""Offline QGIS hybrid styling and bounded raster rendering subprocess."""
import json
import math
import os
from pathlib import Path
import sys
import time

from osgeo import gdal
from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsLineSymbol, QgsFillSymbol, QgsMapSettings,
                       QgsMapRendererSequentialJob, QgsPalLayerSettings, QgsProject,
                       QgsRasterLayer, QgsRectangle, QgsSingleSymbolRenderer, QgsGeometry, QgsFeature, QgsSpatialIndex, QgsFeatureRequest,
                       QgsTextBufferSettings, QgsTextFormat, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling, QgsReferencedRectangle)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QFont, QFontDatabase, QPainter, QImage

from raster_output import finish_output, write_rgb_style, compression_options
from hybrid_style import DEFAULT_ROADS, ROAD_GROUPS, ROAD_LABELS, osm_display_scale, OSM_COLOURS, OSM_ROADS

_application = None
FONT = 'Arial' if sys.platform == 'win32' else 'DejaVu Sans'


def hybrid_encoding(job):
    compression = job.get('compression', 'SOURCE').upper()
    quality = job.get('jpeg_quality', 85)
    if compression == 'SOURCE':
        compression = 'DEFLATE'
        if job.get('imagery'):
            source = gdal.Open(job['imagery'])
            metadata = source.GetMetadata('IMAGE_STRUCTURE')
            if 'JPEG' in metadata.get('COMPRESSION', '').upper():
                compression = 'JPEG'
                quality = int(metadata.get('JPEG_QUALITY', 85))
            source = None
    resolved = {'compression': compression, 'jpeg_quality': quality}
    compression_options(resolved)
    return resolved


def emit(message):
    print(json.dumps({'message': message}), flush=True)


def check(job):
    if Path(job['cancel']).exists():
        raise InterruptedError('Cancelled. Downloaded OSM data remains available.')


def inspect_imagery(path):
    """Read geographic coverage without reading or resampling raster pixels."""
    gdal.UseExceptions()
    source = gdal.Open(str(path))
    if source is None or not source.GetProjection():
        raise ValueError('Satellite TIFF must have a valid coordinate reference system.')
    if source.RasterCount not in (3, 4) or any(source.GetRasterBand(i).DataType != gdal.GDT_Byte for i in (1, 2, 3)):
        raise ValueError('Select a rendered 8-bit RGB TIFF from Colour balance & merge.')
    view = gdal.AutoCreateWarpedVRT(source, None, 'EPSG:4326')
    gt = view.GetGeoTransform()
    return {'west': gt[0], 'south': gt[3] + view.RasterYSize * gt[5],
            'east': gt[0] + view.RasterXSize * gt[1], 'north': gt[3]}


def labels(layer, size, priority, road=False, mode='Numbers and names', display_scale=1, prefer_english=True, dark=False, area=False, colour=None):
    settings = QgsPalLayerSettings()
    settings.fieldName = 'coalesce(nullif("name",\'\'), "ref")'
    if road:
        if mode == 'Numbers only':
            settings.fieldName = '"ref"'
        elif mode == 'Names only':
            settings.fieldName = '"name"'
        else:
            settings.fieldName = 'CASE WHEN "ref" != \'\' AND "name" != \'\' AND "ref" != "name" THEN "ref" || \' · \' || "name" ELSE coalesce(nullif("ref",\'\'), "name") END'
    if prefer_english and layer.fields().indexFromName('name_en') >= 0:
        name = 'coalesce(nullif(trim("name_en"), \'\'), "name")'
        settings.fieldName = settings.fieldName.replace('"name"', name)
    settings.isExpression = True
    settings.priority = priority
    settings.placement = (Qgis.LabelPlacement.Horizontal if area else
                          Qgis.LabelPlacement.Curved if road else Qgis.LabelPlacement.OrderedPositionsAroundPoint)
    text = QgsTextFormat()
    size *= display_scale
    bold = not road and (not dark or priority >= 9)
    text.setFont(QFont(FONT, round(size), QFont.Weight.Bold if bold else QFont.Weight.Normal))
    text.setSize(size)
    text.setColor(QColor(colour or ('#38434a' if dark else '#ffffff')))
    halo = QgsTextBufferSettings()
    halo.setEnabled(True)
    halo.setSize((0.35 if dark else 0.7) * display_scale)
    halo.setColor(QColor('#ffffff' if dark else '#253127'))
    text.setBuffer(halo)
    settings.setFormat(text)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def prepare_render_data(folder, job):
    """Index source geometry once, so every block can query nearby features."""
    names = ('roads', 'places', 'map_lines', 'map_areas') if job.get('map_mode') == 'osm' else ('roads', 'places')
    sources = {}
    for name in names:
        check(job)
        path = folder / f'{name}.geojson'
        target = folder / f'{name}.fgb'
        if target.exists():
            raise FileExistsError(f'{target.name} already exists. Choose a new output folder.')
        if not path.is_file():
            raise ValueError(f'Missing OSM data: {path.name}. Reimport the regional extract.')
        dataset = gdal.OpenEx(str(path), gdal.OF_VECTOR)
        count = dataset.GetLayer(0).GetFeatureCount()
        dataset = None
        if not count:
            sources[name] = str(path)
            continue
        emit(f'Indexing {name}: {count:,} features for fast block rendering…')
        result = None
        try:
            result = gdal.VectorTranslate(str(target), str(path), format='FlatGeobuf', layerName=name,
                SQLStatement=f'SELECT FID AS render_order, * FROM "{name}"', SQLDialect='OGRSQL',
                layerCreationOptions=['SPATIAL_INDEX=YES'],
                callback=lambda f, m, d: 0 if Path(job['cancel']).exists() else 1)
            if result is None:
                check(job)
                raise RuntimeError(f'Could not index {name}.')
            result.Close()
            result = None
            check(job)
        except Exception:
            if result is not None:
                result.Close()
                result = None
            for suffix in ('', '-wal', '-shm', '-journal'):
                Path(str(target) + suffix).unlink(missing_ok=True)
            raise
        sources[name] = str(target)
    return sources


def write_rendered_image(result, image, x, y, jpeg):
    """Transfer unpremultiplied RGBA directly from Qt to GDAL, without PNG IO."""
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    pixels = image.constBits().asstring(image.sizeInBytes())
    result.WriteRaster(x, y, width, height, pixels, band_list=[1, 2, 3] if jpeg else [1, 2, 3, 4],
                       buf_pixel_space=4, buf_line_space=image.bytesPerLine(), buf_band_space=1)
    if jpeg:
        import numpy as np
        rgba = np.frombuffer(pixels, dtype=np.uint8).reshape(height, image.bytesPerLine())[:, :width * 4].reshape(height, width, 4)
        result.GetRasterBand(1).GetMaskBand().WriteArray((rgba[:, :, 3] > 0).astype(np.uint8) * 255, x, y)


def has_matching_features(uri, expression):
    path, _, name = uri.partition('|layername=')
    dataset = gdal.OpenEx(path, gdal.OF_VECTOR)
    try:
        layer = dataset.GetLayerByName(name) if name else dataset.GetLayer(0)
        if layer.GetFeatureCount() == 0:
            return False
        layer.SetAttributeFilter(expression)
        return layer.GetNextFeature() is not None
    finally:
        layer = dataset = None


def preserve_feature_order(layer):
    # A spatial index changes file traversal order. Keep original paint/label
    # order, sorting only the features requested for the current render block.
    renderer = layer.renderer()
    renderer.setOrderBy(QgsFeatureRequest.OrderBy([QgsFeatureRequest.OrderByClause('render_order')]))
    renderer.setOrderByEnabled(True)


def render_raster(name, selected, settings, extent, width, height, resolution, folder, job):
    """Stream bounded QGIS renders into one full-resolution BigTIFF."""
    # Padding provides label-placement context around block edges. The crop writes
    # exact, non-overlapping pixel windows on the single output grid.
    block = int(job.get('render_block_size', 4096))
    if not 64 <= block <= 4096:
        raise ValueError('Render block size must be between 64 and 4096 pixels.')
    padding = math.ceil(256 * osm_display_scale(job.get('osm_scale', 1)))
    target = folder / f'{name}.tif'
    partial = folder / f'.{name}.part.tif'
    result = source = None
    cols, rows = math.ceil(width / block), math.ceil(height / block)
    encoding = hybrid_encoding(job) if name in ('hybrid', 'osm') else {'compression': 'DEFLATE'}
    jpeg = encoding['compression'] == 'JPEG'
    started = time.monotonic()
    emit(f'Exporting {name}: {width} × {height} pixels at {resolution:g} m/pixel in {cols * rows} render blocks. One GeoTIFF output; resolution is preserved.')
    try:
        emit(f'{name} compression: {encoding["compression"]}' + (f' quality {encoding["jpeg_quality"]}; transparency uses a lossless internal mask.' if jpeg else ' (lossless).'))
        result = gdal.GetDriverByName('GTiff').Create(str(partial), width, height, 3 if jpeg else 4, gdal.GDT_Byte,
                   options=compression_options(encoding) + [f'NUM_THREADS={min(4, os.cpu_count() or 1)}'])
        if jpeg:
            previous_mask = gdal.GetConfigOption('GDAL_TIFF_INTERNAL_MASK')
            try:
                gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', 'YES')
                result.CreateMaskBand(gdal.GMF_PER_DATASET)
            finally:
                gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', previous_mask)
            result.SetMetadataItem('EXPORT_JPEG_QUALITY', str(encoding['jpeg_quality']))
        result.SetProjection(settings.destinationCrs().toWkt())
        result.SetGeoTransform((extent.xMinimum(), resolution, 0, extent.yMaximum(), 0, -resolution))
        interpretations = (gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand) + (() if jpeg else (gdal.GCI_AlphaBand,))
        for index, interpretation in enumerate(interpretations, 1):
            result.GetRasterBand(index).SetColorInterpretation(interpretation)
        result.SetMetadataItem('ATTRIBUTION', '© OpenStreetMap contributors; https://www.openstreetmap.org/copyright')
        result.SetMetadataItem('OSM_LICENSE', 'https://opendatacommons.org/licenses/odbl/1-0/')
        result.SetMetadataItem('EXPORT_RESOLUTION_M', str(resolution))
        settings.setLayers(selected)
        for row, y in enumerate(range(0, height, block)):
            for col, x in enumerate(range(0, width, block)):
                check(job)
                w, h = min(block, width - x), min(block, height - y)
                x0, y0 = max(0, x - padding), max(0, y - padding)
                x1, y1 = min(width, x + w + padding), min(height, y + h + padding)
                settings.setExtent(QgsRectangle(extent.xMinimum() + x0 * resolution,
                    extent.yMaximum() - y1 * resolution, extent.xMinimum() + x1 * resolution,
                    extent.yMaximum() - y0 * resolution))
                settings.setOutputSize(QSize(x1 - x0, y1 - y0))
                emit(f'Rendering {name} block {row * cols + col + 1}/{rows * cols}…')
                block_started = time.monotonic()
                renderer = QgsMapRendererSequentialJob(settings)
                renderer.start()
                renderer.waitForFinished()
                if renderer.errors():
                    raise RuntimeError('; '.join(error.message for error in renderer.errors()))
                check(job)
                image = renderer.renderedImage().copy(x - x0, y - y0, w, h)
                # Attribution is placed once, at the lower-left of the whole map.
                if x < 560 and y + h > height - 24:
                    painter = QPainter(image)
                    painter.setFont(QFont(FONT, 9))
                    painter.setPen(QColor('white'))
                    painter.fillRect(-x, height - 24 - y, min(width, 560), 24, QColor(20, 25, 20, 210))
                    painter.drawText(8 - x, height - 8 - y, '© OpenStreetMap contributors · openstreetmap.org/copyright')
                    painter.end()
                write_rendered_image(result, image, x, y, jpeg)
                completed = row * cols + col + 1
                elapsed = time.monotonic() - started
                remaining = elapsed / completed * (rows * cols - completed)
                emit(f'Completed {name} block {completed}/{rows * cols} in {time.monotonic() - block_started:.1f}s; '
                     f'estimated rendering remaining {remaining / 60:.1f} min (excluding viewing overviews).')
        emit(f'Building {name} viewing overviews…')
        finish_output(result, lambda f, m, d: 0 if Path(job['cancel']).exists() else 1)
        result = None
        check(job)
        partial.rename(target)
        source = gdal.Open(str(target))
        write_rgb_style(target, source)
        source = None
    finally:
        result = source = None
        partial.unlink(missing_ok=True)




def coastline_water(folder, project, extent, job, sources):
    """Close OSM's directed coastline lines against the requested map boundary."""
    if not has_matching_features(sources['map_lines'], "natural = 'coastline'"):
        return None
    source = QgsVectorLayer(sources['map_lines'], 'Coastline source', 'ogr')
    if not source.isValid():
        return None
    source.setSubsetString("natural = 'coastline'")
    clip = QgsGeometry.fromRect(extent)
    edges = [QgsGeometry.fromPolylineXY(clip.asPolygon()[0])]
    index = QgsSpatialIndex(flags=QgsSpatialIndex.Flag.FlagStoreFeatureGeometries)
    transform = QgsCoordinateTransform(source.crs(), project.crs(), project)
    ident = 0
    for feature in source.getFeatures():
        check(job)
        line = feature.geometry()
        line.transform(transform)
        if not line.intersects(clip):
            continue
        parts = line.asMultiPolyline() if line.isMultipart() else [line.asPolyline()]
        for points in parts:
            if len(points) < 2:
                continue
            directed = QgsGeometry.fromPolylineXY(points)
            saved = QgsFeature(ident)
            saved.setGeometry(directed)
            index.addFeature(saved)
            ident += 1
            edges.append(directed.intersection(clip))
    if not ident:
        return None
    emit('Preparing sea areas from OSM coastlines…')
    faces = QgsGeometry.polygonize([QgsGeometry.unaryUnion(edges)])
    if faces.isEmpty():
        raise ValueError('Could not build coastal water areas from this extract.')
    polygons = faces.asGeometryCollection()
    if len(polygons) < 2:
        emit('Coastline does not enclose or divide the requested area; sea fill skipped. Check regional extract coverage.')
        return None
    features = []
    for face in polygons:
        check(job)
        point = face.pointOnSurface().asPoint()
        nearest = index.nearestNeighbor(point, 1)
        if not nearest:
            continue
        line = index.geometry(nearest[0])
        _, _, after, _ = line.closestSegmentWithContext(point)
        a, b = line.vertexAt(after-1), line.vertexAt(after)
        cross = (b.x()-a.x())*(point.y()-a.y()) - (b.y()-a.y())*(point.x()-a.x())
        # OSM coastlines run with land on the left and sea on the right.
        if cross < 0:
            features.append({'type': 'Feature', 'properties': {}, 'geometry': json.loads(face.asJson())})
    if not features:
        return None
    target = folder / 'sea.geojson'
    target.write_text(json.dumps({'type': 'FeatureCollection',
        'crs': {'type': 'name', 'properties': {'name': 'EPSG:3857'}}, 'features': features}), encoding='utf-8')
    layer = QgsVectorLayer(str(target), 'Sea', 'ogr')
    if not layer.isValid():
        raise RuntimeError('Could not load coastal water areas.')
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({'color': OSM_COLOURS['water'], 'outline_style': 'no'})))
    project.addMapLayer(layer)
    return layer


def basemap_layers(folder, project, job, display_scale, sources):
    """Offline street-map background; layer order is topmost first."""
    layers = []
    # Draw polygons below streets, with buildings above land use and water.
    styles = [
        ('map_lines', 'Railways', "railway IN ('rail','light_rail','subway','tram')", '#8b8b86', 0.28, 300000, True),
        ('map_lines', 'Waterways', "waterway IN ('river','stream','canal','drain')", OSM_COLOURS['water'], 0.55, 750000, False),
        ('map_lines', 'Coastlines', "natural = 'coastline'", '#87b2c7', 0.16, 20000000, False),
        ('roads', 'Paths and tracks', "highway IN ('path','footway','cycleway','track','steps','pedestrian')", '#b49c83', 0.3, 60000, True),
        ('map_areas', 'Buildings', "building != '' AND building != 'no'", OSM_COLOURS['building'], 0.1, 35000, False),
        ('map_areas', 'Water', "natural IN ('water','bay') OR water != '' OR landuse = 'reservoir'", OSM_COLOURS['water'], 0, 20000000, False),
        ('map_areas', 'Parks and grass', "leisure IN ('park','garden','golf_course','pitch','recreation_ground') OR landuse IN ('grass','meadow','recreation_ground','village_green') OR natural IN ('grassland','heath')", OSM_COLOURS['park'], 0, 3000000, False),
        ('map_areas', 'Woodland', "landuse = 'forest' OR natural IN ('wood','scrub')", OSM_COLOURS['woodland'], 0, 10000000, False),
        ('map_areas', 'Farmland', "landuse IN ('farmland','farmyard','orchard','vineyard')", OSM_COLOURS['farmland'], 0, 3000000, False),
        ('map_areas', 'Industrial and commercial', "landuse IN ('industrial','commercial','retail')", OSM_COLOURS['industrial'], 0, 1000000, False),
        ('map_areas', 'Residential areas', "landuse = 'residential'", OSM_COLOURS['residential'], 0, 2000000, False),
        ('map_areas', 'Sand and beaches', "natural IN ('sand','beach')", OSM_COLOURS['sand'], 0, 3000000, False),
    ]
    for source, name, expression, colour, stroke, scale, dashed in styles:
        check(job)
        if name == 'Paths and tracks' and 'local' not in job.get('road_groups', list(ROAD_GROUPS)):
            continue
        path = folder / f'{source}.geojson'
        if not path.is_file():
            raise ValueError('OSM map needs full map data. Reimport the regional extract in OSM maps.')
        if not has_matching_features(sources[source], expression):
            continue
        layer = QgsVectorLayer(sources[source], name, 'ogr')
        if not layer.isValid():
            # Empty GeoJSON collections have no geometry schema in older GDAL.
            if not json.loads(path.read_text(encoding='utf-8'))['features']:
                continue
            raise RuntimeError(f'Cannot open map data: {name}')
        if not layer.setSubsetString(expression):
            raise RuntimeError(f'Cannot filter map data: {name}')
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(scale * display_scale)
        if source == 'map_areas':
            symbol = QgsFillSymbol.createSimple({'color': colour, 'outline_color': OSM_COLOURS['building_edge'],
                'outline_style': 'solid' if stroke else 'no', 'outline_width': str(stroke * display_scale)})
        else:
            symbol = QgsLineSymbol.createSimple({'line_color': colour, 'line_width': str(stroke * display_scale),
                                                'line_style': 'dash' if dashed else 'solid'})
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        preserve_feature_order(layer)
        if name in ('Water', 'Parks and grass', 'Woodland'):
            labels(layer, 9, 4 if name == 'Water' else 3, display_scale=display_scale,
                   prefer_english=job.get('prefer_english', True), dark=True, area=True,
                   colour='#397a99' if name == 'Water' else '#587452')
        project.addMapLayer(layer)
        layers.append(layer)
    return layers


def run(job):
    folder = Path(job['folder'])
    pure_osm = job.get('map_mode') == 'osm'
    for name in ('osm.qgz', 'osm.tif', 'osm.qml', 'hybrid.qgz', 'overlay.tif', 'hybrid.tif', 'overlay.qml', 'hybrid.qml'):
        if (folder / name).exists():
            raise FileExistsError(f'{name} already exists. Choose a new output folder.')
    global _application
    if QgsApplication.instance() is None:
        _application = QgsApplication([], False)
        _application.initQgis()
    if sys.platform == 'win32':
        # Offscreen Qt installations may not discover the system font collection.
        fonts = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts'
        for name in ('arial.ttf', 'arialbd.ttf'):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
    gdal.UseExceptions()
    project = QgsProject.instance()
    project_file = 'osm.qgz' if pure_osm else 'hybrid.qgz'
    project.setFileName(str(folder / project_file))
    crs = QgsCoordinateReferenceSystem('EPSG:3857')
    project.setCrs(crs)
    project.setBackgroundColor(QColor(OSM_COLOURS['land']) if pure_osm else QColor('white'))
    b = job['bounds']
    extent = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), crs, project).transformBoundingBox(
        QgsRectangle(b['west'], b['south'], b['east'], b['north']))
    resolution = float(job['resolution'])
    display_scale = osm_display_scale(job.get('osm_scale', 1))
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError('Resolution must be a positive finite number.')
    width, height = math.ceil(extent.width() / resolution), math.ceil(extent.height() / resolution)
    # Exact square pixel spacing, extending by less than one pixel east/south.
    extent.setXMaximum(extent.xMinimum() + width * resolution)
    extent.setYMinimum(extent.yMaximum() - height * resolution)
    layers = []
    sources = prepare_render_data(folder, job)
    selected_roads = job.get('road_groups', list(ROAD_GROUPS) if pure_osm else DEFAULT_ROADS)
    if any(group not in ROAD_GROUPS for group in selected_roads):
        raise ValueError('Unknown road display group.')
    label_mode = job.get('road_labels', 'Numbers and names')
    if label_mode not in ROAD_LABELS:
        raise ValueError('Unknown road label mode.')
    appearances = [('motorway', 2000000, '#e8bd77', 0.75, 10, 6),
                   ('a_road', 1500000, '#eecb86', 0.65, 9, 5),
                   ('b_road', 400000, '#f2e7cb', 0.45, 9, 4),
                   ('main', 150000, '#ebe9df', 0.35, 8, 3),
                   ('local', 40000, '#deded6', 0.28, 8, 2)]
    specifications = [('roads', ROAD_GROUPS[group], f'"road_group" = \'{group}\'', scale, color, stroke, size, priority)
                      for group, scale, color, stroke, size, priority in appearances if group in selected_roads]
    selected_places = job.get('place_types', ['city', 'town'])
    for category, scale, size, priority in [('city', 15000000, 14, 10), ('town', 2500000, 12, 9),
                                            ('village', 350000, 10, 8), ('hamlet', 80000, 9, 6), ('suburb', 60000, 9, 5)]:
        if category in selected_places:
            if pure_osm:
                size = {'city': 17, 'town': 14, 'village': 11, 'hamlet': 9, 'suburb': 10}[category]
            specifications.append(('places', category.title(), f'"place" = \'{category}\'', scale, '', 0, size, priority))
    for source, name, expression, scale, color, stroke, size, priority in specifications:
        check(job)
        path = folder / f'{source}.geojson'
        if not path.is_file():
            raise ValueError(f'Missing OSM data: {path.name}. Reimport the regional extract.')
        if pure_osm and source == 'roads' and name == ROAD_GROUPS['local']:
            expression += " AND highway NOT IN ('path','footway','cycleway','track','steps','pedestrian')"
        if not has_matching_features(sources[source], expression):
            continue
        layer = QgsVectorLayer(sources[source], name, 'ogr')
        if not layer.isValid() or not layer.setSubsetString(expression):
            raise RuntimeError(f'Cannot load OSM layer: {name}')
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(scale * display_scale)
        if source == 'roads':
            casing = '#30352e'
            casing_extra = 0.35
            if pure_osm:
                group = next(group for group, title in ROAD_GROUPS.items() if title == name)
                color, casing, stroke, size = OSM_ROADS[group]
                casing_extra = 0.5 if group in ('motorway', 'a_road') else 0.35
            joins = {'capstyle': 'round', 'joinstyle': 'round'} if pure_osm else {}
            symbol = QgsLineSymbol.createSimple({'line_color': casing,
                'line_width': str((stroke + casing_extra) * display_scale), **joins})
            foreground = QgsLineSymbol.createSimple({'line_color': color, 'line_width': str(stroke * display_scale),
                                                     **joins})
            symbol.appendSymbolLayer(foreground.takeSymbolLayer(0))
            renderer = QgsSingleSymbolRenderer(symbol)
            if pure_osm:
                symbol.symbolLayer(0).setRenderingPass(0)
                symbol.symbolLayer(1).setRenderingPass(1)
                renderer.setUsingSymbolLevels(True)
            layer.setRenderer(renderer)
        else:
            layer.renderer().symbol().setOpacity(0)
        preserve_feature_order(layer)
        if source != 'roads' or label_mode != 'Off':
            labels(layer, size, priority, source == 'roads', label_mode, display_scale,
                   job.get('prefer_english', True), dark=pure_osm)
        project.addMapLayer(layer)
        layers.insert(0, layer)
    if pure_osm:
        road_rank = {title: i for i, title in enumerate(ROAD_GROUPS.values())}
        layers.sort(key=lambda layer: road_rank.get(layer.name(), -1))
        layers += basemap_layers(folder, project, job, display_scale, sources)
        sea = coastline_water(folder, project, extent, job, sources)
        if sea is not None:
            layers.append(sea)
    satellite = None
    if job.get('imagery') and not pure_osm:
        source = gdal.Open(job['imagery'])
        if source is None or source.RasterCount not in (3, 4) or any(
                source.GetRasterBand(i).DataType != gdal.GDT_Byte for i in (1, 2, 3)):
            raise ValueError('Select a rendered 8-bit RGB satellite TIFF from Colour balance & merge, rather than raw reflectance or land-cover data.')
        source = None
        satellite = QgsRasterLayer(job['imagery'], 'Satellite imagery')
        if not satellite.isValid():
            raise ValueError('Cannot open satellite TIFF.')
        satellite.loadDefaultStyle()
        project.addMapLayer(satellite)
    ordered = layers + ([satellite] if satellite else [])
    project.layerTreeRoot().setHasCustomLayerOrder(True)
    project.layerTreeRoot().setCustomLayerOrder(ordered)
    project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, crs))
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    if not project.write(str(folder / project_file)):
        raise RuntimeError('Cannot save QGIS hybrid project.')
    if not job.get('render_rasters', True):
        emit(f'Complete: {folder}. Open {project_file} for sharp vector roads and labels at every zoom.')
        return
    settings = QgsMapSettings()
    settings.setDestinationCrs(crs)
    settings.setTransformContext(project.transformContext())
    settings.setExtent(extent)
    settings.setOutputDpi(96)
    settings.setBackgroundColor(QColor(OSM_COLOURS['land']) if pure_osm else QColor(0, 0, 0, 0))
    outputs = [('osm', layers)] if pure_osm else [('overlay', layers)] + ([('hybrid', ordered)] if satellite else [])
    for name, selected in outputs:
        render_raster(name, selected, settings, extent, width, height, resolution, folder, job)
    emit(f'Complete: {folder}. Open {project_file} for labels that adapt as you zoom.')


if __name__ == '__main__':
    try:
        if sys.argv[1] == '--inspect':
            print(json.dumps({'bounds': inspect_imagery(sys.argv[2])}), flush=True)
        else:
            run(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception as error:
        emit(str(error))
        sys.exit(2 if isinstance(error, InterruptedError) else 1)
