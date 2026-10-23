"""Offline QGIS hybrid styling and bounded raster rendering subprocess."""
import json
import math
import os
from pathlib import Path
import sys

from osgeo import gdal
from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsLineSymbol, QgsMapSettings,
                       QgsMapRendererSequentialJob, QgsPalLayerSettings, QgsProject,
                       QgsRasterLayer, QgsRectangle, QgsSingleSymbolRenderer,
                       QgsTextBufferSettings, QgsTextFormat, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling, QgsReferencedRectangle)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QFont, QFontDatabase, QPainter

from raster_output import finish_output, write_rgb_style, compression_options
from hybrid_style import DEFAULT_ROADS, ROAD_GROUPS, ROAD_LABELS

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


def labels(layer, size, priority, road=False, mode='Numbers and names'):
    settings = QgsPalLayerSettings()
    settings.fieldName = 'coalesce(nullif("name",\'\'), "ref")'
    if road:
        if mode == 'Numbers only':
            settings.fieldName = '"ref"'
        elif mode == 'Names only':
            settings.fieldName = '"name"'
        else:
            settings.fieldName = 'CASE WHEN "ref" != \'\' AND "name" != \'\' AND "ref" != "name" THEN "ref" || \' · \' || "name" ELSE coalesce(nullif("ref",\'\'), "name") END'
    settings.isExpression = True
    settings.priority = priority
    settings.placement = Qgis.LabelPlacement.Curved if road else Qgis.LabelPlacement.OrderedPositionsAroundPoint
    text = QgsTextFormat()
    text.setFont(QFont(FONT, size, QFont.Weight.Bold if not road else QFont.Weight.Normal))
    text.setSize(size)
    text.setColor(QColor('#ffffff'))
    halo = QgsTextBufferSettings()
    halo.setEnabled(True)
    halo.setSize(0.7)
    halo.setColor(QColor('#253127'))
    text.setBuffer(halo)
    settings.setFormat(text)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def render_raster(name, selected, settings, extent, width, height, resolution, folder, job):
    """Stream bounded QGIS renders into one full-resolution BigTIFF."""
    # Padding provides label-placement context around block edges. The crop writes
    # exact, non-overlapping pixel windows on the single output grid.
    block = int(job.get('render_block_size', 4096))
    if not 64 <= block <= 4096:
        raise ValueError('Render block size must be between 64 and 4096 pixels.')
    padding = 256
    target = folder / f'{name}.tif'
    partial = folder / f'.{name}.part.tif'
    png = folder / f'.{name}.block.png'
    result = source = None
    cols, rows = math.ceil(width / block), math.ceil(height / block)
    encoding = hybrid_encoding(job) if name == 'hybrid' else {'compression': 'DEFLATE'}
    jpeg = encoding['compression'] == 'JPEG'
    emit(f'Exporting {name}: {width} × {height} pixels at {resolution:g} m/pixel in {cols * rows} render blocks. One GeoTIFF output; resolution is preserved.')
    try:
        emit(f'{name} compression: {encoding["compression"]}' + (f' quality {encoding["jpeg_quality"]}; transparency uses a lossless internal mask.' if jpeg else ' (lossless).'))
        result = gdal.GetDriverByName('GTiff').Create(str(partial), width, height, 3 if jpeg else 4, gdal.GDT_Byte,
                   options=compression_options(encoding))
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
                if not image.save(str(png)):
                    raise RuntimeError('Could not save rendered block.')
                source = gdal.Open(str(png))
                bands = 3 if jpeg else min(source.RasterCount, 4)
                band_list = list(range(1, bands + 1))
                result.WriteRaster(x, y, w, h, source.ReadRaster(band_list=band_list), band_list=band_list)
                if jpeg:
                    import numpy as np
                    alpha = source.GetRasterBand(4).ReadAsArray() if source.RasterCount == 4 else np.full((h, w), 255, dtype=np.uint8)
                    result.GetRasterBand(1).GetMaskBand().WriteArray((alpha > 0).astype(np.uint8) * 255, x, y)
                elif bands == 3:
                    result.GetRasterBand(4).WriteRaster(x, y, w, h, b'\xff' * (w * h))
                source = None
                png.unlink(missing_ok=True)
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
        png.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def run(job):
    folder = Path(job['folder'])
    for name in ('hybrid.qgz', 'overlay.tif', 'hybrid.tif', 'overlay.qml', 'hybrid.qml'):
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
    crs = QgsCoordinateReferenceSystem('EPSG:3857')
    project.setCrs(crs)
    b = job['bounds']
    extent = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), crs, project).transformBoundingBox(
        QgsRectangle(b['west'], b['south'], b['east'], b['north']))
    resolution = float(job['resolution'])
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError('Resolution must be a positive finite number.')
    width, height = math.ceil(extent.width() / resolution), math.ceil(extent.height() / resolution)
    # Exact square pixel spacing, extending by less than one pixel east/south.
    extent.setXMaximum(extent.xMinimum() + width * resolution)
    extent.setYMinimum(extent.yMaximum() - height * resolution)
    layers = []
    has_features = {}
    selected_roads = job.get('road_groups', DEFAULT_ROADS)
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
            specifications.append(('places', category.title(), f'"place" = \'{category}\'', scale, '', 0, size, priority))
    for source, name, expression, scale, color, stroke, size, priority in specifications:
        check(job)
        path = folder / f'{source}.geojson'
        if source not in has_features:
            has_features[source] = bool(json.loads(path.read_text(encoding='utf-8'))['features'])
        if not has_features[source]:
            continue
        layer = QgsVectorLayer(str(path), name, 'ogr')
        if not layer.isValid() or not layer.setSubsetString(expression):
            raise RuntimeError(f'Cannot load OSM layer: {name}')
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(scale)
        if source == 'roads':
            symbol = QgsLineSymbol.createSimple({'line_color': '#30352e', 'line_width': str(stroke + 0.35)})
            foreground = QgsLineSymbol.createSimple({'line_color': color, 'line_width': str(stroke)})
            symbol.appendSymbolLayer(foreground.takeSymbolLayer(0))
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        else:
            layer.renderer().symbol().setOpacity(0)
        if source != 'roads' or label_mode != 'Off':
            labels(layer, size, priority, source == 'roads', label_mode)
        project.addMapLayer(layer)
        layers.insert(0, layer)
    satellite = None
    if job.get('imagery'):
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
    if not project.write(str(folder / 'hybrid.qgz')):
        raise RuntimeError('Cannot save QGIS hybrid project.')
    if not job.get('render_rasters', True):
        emit(f'Complete: {folder}. Open hybrid.qgz for sharp vector roads and labels at every zoom.')
        return
    settings = QgsMapSettings()
    settings.setDestinationCrs(crs)
    settings.setTransformContext(project.transformContext())
    settings.setExtent(extent)
    settings.setOutputDpi(96)
    settings.setBackgroundColor(QColor(0, 0, 0, 0))
    for name, selected in [('overlay', layers)] + ([('hybrid', ordered)] if satellite else []):
        render_raster(name, selected, settings, extent, width, height, resolution, folder, job)
    emit(f'Complete: {folder}. Open hybrid.qgz for labels that adapt as you zoom.')


if __name__ == '__main__':
    try:
        if sys.argv[1] == '--inspect':
            print(json.dumps({'bounds': inspect_imagery(sys.argv[2])}), flush=True)
        else:
            run(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception as error:
        emit(str(error))
        sys.exit(2 if isinstance(error, InterruptedError) else 1)
