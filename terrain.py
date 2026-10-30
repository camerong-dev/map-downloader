"""Optional elevation relief for OSM maps; GUI-safe options, lazy GIS imports."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HALF_WORLD = 20037508.342789244
NODATA = -32768
TILE_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
CREDIT_URL = 'https://raw.githubusercontent.com/tilezen/joerd/master/docs/attribution.md'
TERRAIN_CREDIT = 'Terrain Tiles: Mapzen and source contributors'
PNG_HEADER = b'\x89PNG\r\n\x1a\n'


@dataclass(frozen=True)
class TerrainOptions:
    enabled: bool = False
    dem: str = ''
    strength: float = 0.4
    contours: bool = False
    interval: float = 50

    @classmethod
    def from_job(cls, job):
        if not job.get('terrain_enabled', False):
            return cls()
        dem = str(job.get('terrain_dem', '')).strip()
        strength = float(job.get('terrain_strength', 0.4))
        interval = float(job.get('terrain_interval', 50))
        if not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError('Terrain shading strength must be from 0% to 100%.')
        if not math.isfinite(interval) or not 10 <= interval <= 1000:
            raise ValueError('Contour spacing must be from 10 to 1000 metres.')
        if dem:
            path = Path(dem).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() not in ('.tif', '.tiff'):
                raise ValueError('Choose an elevation GeoTIFF, or leave the elevation file blank for automatic terrain.')
            dem = str(path)
        return cls(True, dem, strength, bool(job.get('terrain_contours', False)), interval)

    def job_options(self):
        return {'terrain_enabled': self.enabled, 'terrain_dem': self.dem,
                'terrain_strength': self.strength, 'terrain_contours': self.contours,
                'terrain_interval': self.interval}


def check_cancel(job):
    if Path(job['cancel']).exists():
        raise InterruptedError('Cancelled. Cached terrain tiles were kept.')


def tile_range(bounds, zoom):
    """Include one surrounding tile for derivatives at the map boundary."""
    west, south, east, north = bounds
    size = 2**zoom
    span = 2 * HALF_WORLD / size
    x0 = max(0, math.floor((west + HALF_WORLD) / span) - 1)
    x1 = min(size - 1, math.ceil((east + HALF_WORLD) / span))
    y0 = max(0, math.floor((HALF_WORLD - north) / span) - 1)
    y1 = min(size - 1, math.ceil((HALF_WORLD - south) / span))
    return x0, y0, x1, y1


def terrain_tiles(bounds, max_tiles=256):
    for zoom in range(12, -1, -1):
        area = tile_range(bounds, zoom)
        x0, y0, x1, y1 = area
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= max_tiles:
            return zoom, area
    raise ValueError('The terrain area is too large.')


def cached_resource(url, path, job, png=False):
    """Bounded HTTPS transfers with atomic publication and cancellable retries."""
    check_cancel(job)
    if path.is_file():
        data = path.read_bytes()
        if data and (not png or data.startswith(PNG_HEADER)):
            return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + '.part')
    try:
        for attempt in range(3):
            check_cancel(job)
            try:
                request = Request(url, headers={'User-Agent': 'ESAMapsDesktop/1.0 (terrain relief)'})
                with urlopen(request, timeout=30) as response:
                    data = response.read(1_000_001)
                if not data or len(data) > 1_000_000 or (png and not data.startswith(PNG_HEADER)):
                    raise ValueError('The terrain service returned invalid or oversized data.')
                check_cancel(job)
                partial.write_bytes(data)
                partial.replace(path)
                return path
            except HTTPError as error:
                if error.code not in (408, 429, 500, 502, 503, 504):
                    raise
            except (URLError, TimeoutError, ConnectionError):
                pass
            if attempt == 2:
                raise RuntimeError('Terrain download failed after three attempts. Check your connection and retry; cached tiles are kept.')
            deadline = time.monotonic() + attempt + 1
            while time.monotonic() < deadline:
                check_cancel(job)
                time.sleep(0.1)
    finally:
        partial.unlink(missing_ok=True)


def automatic_dem(folder, bounds, job, emit):
    import numpy as np
    from osgeo import gdal, osr
    zoom, (x0, y0, x1, y1) = terrain_tiles(bounds)
    cache = folder.parent / '.terrain-cache'
    credits = cached_resource(CREDIT_URL, cache / 'attribution.md', job)
    (folder / 'TERRAIN_ATTRIBUTION.md').write_bytes(credits.read_bytes())
    span = 2 * HALF_WORLD / 2**zoom
    pixel = span / 256
    width, height = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
    path = folder / 'terrain_elevation.tif'
    result = gdal.GetDriverByName('GTiff').Create(str(path), width, height, 1, gdal.GDT_Float32,
        options=['TILED=YES', 'COMPRESS=DEFLATE', 'BIGTIFF=IF_SAFER'])
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(3857)
    result.SetProjection(crs.ExportToWkt())
    result.SetGeoTransform((-HALF_WORLD + x0 * span, pixel, 0, HALF_WORLD - y0 * span, 0, -pixel))
    band = result.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    band.SetUnitType('m')
    band.Fill(NODATA)
    total, valid_tiles = (x1 - x0 + 1) * (y1 - y0 + 1), 0
    try:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                check_cancel(job)
                index = (y - y0) * (x1 - x0 + 1) + x - x0 + 1
                emit(f'Terrain elevation tile {index}/{total} (cached downloads are reused)…')
                try:
                    tile_path = cached_resource(TILE_URL.format(z=zoom, x=x, y=y),
                                                cache / str(zoom) / str(x) / f'{y}.png', job, png=True)
                except HTTPError as error:
                    if error.code == 404:
                        continue
                    raise
                tile = gdal.Open(str(tile_path))
                try:
                    if tile.RasterXSize != 256 or tile.RasterYSize != 256 or tile.RasterCount < 3:
                        raise ValueError('The terrain tile has an unexpected format.')
                    rgb = tile.ReadAsArray()[:3].astype(np.float32)
                    values = rgb[0] * 256 + rgb[1] + rgb[2] / 256 - 32768
                    values[rgb[0] == 0] = NODATA
                    if np.any(values != NODATA):
                        valid_tiles += 1
                    band.WriteArray(values, (x - x0) * 256, (y - y0) * 256)
                finally:
                    tile.Close()
        if not valid_tiles:
            raise ValueError('No elevation coverage was available for this area.')
    finally:
        band = None
        result.Close()
    return path, {'source': 'Mapzen Terrain Tiles on AWS', 'zoom': zoom, 'pixel_spacing_m': pixel,
                  'attribution': TERRAIN_CREDIT, 'source_url': 'https://registry.opendata.aws/terrain-tiles/',
                  'license_url': 'https://github.com/tilezen/joerd/blob/master/docs/attribution.md',
                  'accessed_utc': datetime.now(timezone.utc).isoformat()}


def local_dem(folder, bounds, path, job, emit):
    import numpy as np
    from osgeo import gdal
    source = gdal.Open(path)
    view = result = None
    try:
        if source.RasterCount != 1 or source.GetRasterBand(1).GetColorTable() is not None or not source.GetProjection():
            raise ValueError('Terrain needs a georeferenced single-band elevation TIFF, not imagery or a coloured map.')
        band = source.GetRasterBand(1)
        unit = (band.GetUnitType() or '').lower()
        if unit not in ('', 'm', 'metre', 'metres', 'meter', 'meters'):
            raise ValueError('Elevation values must be in metres. Convert this DEM to metres first.')
        scale = 1 if band.GetScale() is None else band.GetScale()
        offset = 0 if band.GetOffset() is None else band.GetOffset()
        if not math.isfinite(scale) or not math.isfinite(offset):
            raise ValueError('The elevation TIFF has an invalid height scale or offset.')
        view = gdal.AutoCreateWarpedVRT(source, None, 'EPSG:3857')
        gt = view.GetGeoTransform()
        west, south, east, north = bounds
        source_bounds = (gt[0], gt[3] + view.RasterYSize * gt[5], gt[0] + view.RasterXSize * gt[1], gt[3])
        if east <= source_bounds[0] or west >= source_bounds[2] or north <= source_bounds[1] or south >= source_bounds[3]:
            raise ValueError('The elevation TIFF does not overlap the selected map area.')
        pixel = max(abs(gt[1]), abs(gt[5]), math.sqrt((east-west)*(north-south)/16_000_000))
        target = folder / 'terrain_elevation.tif'
        emit(f'Preparing local elevation at {pixel:g} projected metres/pixel…')
        result = gdal.Warp(str(target), source, dstSRS='EPSG:3857', format='GTiff',
            outputBounds=[west-2*pixel, south-2*pixel, east+2*pixel, north+2*pixel],
            xRes=pixel, yRes=pixel, resampleAlg='bilinear', outputType=gdal.GDT_Float32, dstNodata=NODATA,
            creationOptions=['TILED=YES', 'COMPRESS=DEFLATE', 'BIGTIFF=IF_SAFER'],
            callback=lambda f,m,d: 0 if Path(job['cancel']).exists() else 1)
        out = result.GetRasterBand(1)
        valid_count = 0
        for y in range(0, result.RasterYSize, 512):
            check_cancel(job)
            h = min(512, result.RasterYSize-y)
            for x in range(0, result.RasterXSize, 512):
                w = min(512, result.RasterXSize-x)
                values = out.ReadAsArray(x,y,w,h)
                valid = (values != NODATA) & np.isfinite(values)
                valid_count += int(np.count_nonzero(valid))
                out.WriteArray(np.where(valid, values*scale+offset, NODATA), x,y)
        if not valid_count:
            raise ValueError('The elevation TIFF has no valid heights inside the map area.')
        out.SetNoDataValue(NODATA)
        out.SetScale(1)
        out.SetOffset(0)
        out.SetUnitType('m')
        return target, {'source': str(Path(path).resolve()), 'pixel_spacing_m': pixel,
                        'attribution': source.GetMetadataItem('ATTRIBUTION') or f'Local elevation: {Path(path).name}',
                        'license': source.GetMetadataItem('LICENSE') or ''}
    finally:
        band = out = None
        if result is not None:
            result.Close()
        if view is not None:
            view.Close()
        source.Close()


def relief_raster(folder, dem_path, job, emit):
    import numpy as np
    from osgeo import gdal
    from raster_output import write_rgb_style, finish_output
    source = gdal.Open(str(dem_path))
    gray = result = None
    scratch = folder / '.terrain_hillshade.tif'
    path = folder / 'terrain_relief.tif'
    try:
        latitude = (job['bounds']['north'] + job['bounds']['south']) / 2
        emit('Computing multidirectional terrain shading…')
        gray = gdal.DEMProcessing(str(scratch), source, 'hillshade', format='GTiff',
            multiDirectional=True, computeEdges=True, scale=1, zFactor=1/math.cos(math.radians(latitude)),
            creationOptions=['TILED=YES', 'COMPRESS=DEFLATE'],
            callback=lambda f,m,d: 0 if Path(job['cancel']).exists() else 1)
        result = gdal.GetDriverByName('GTiff').Create(str(path), source.RasterXSize, source.RasterYSize, 4, gdal.GDT_Byte,
            options=['TILED=YES', 'COMPRESS=DEFLATE', 'PHOTOMETRIC=RGB'])
        result.SetProjection(source.GetProjection())
        result.SetGeoTransform(source.GetGeoTransform())
        for i, interpretation in enumerate((gdal.GCI_RedBand,gdal.GCI_GreenBand,gdal.GCI_BlueBand,gdal.GCI_AlphaBand),1):
            result.GetRasterBand(i).SetColorInterpretation(interpretation)
        for y in range(0, source.RasterYSize, 512):
            check_cancel(job)
            h = min(512, source.RasterYSize-y)
            for x in range(0, source.RasterXSize, 512):
                w = min(512, source.RasterXSize-x)
                values = gray.GetRasterBand(1).ReadAsArray(x,y,w,h).astype(np.float32)
                delta = values - 181  # Flat ground at the default 45-degree sun angle.
                delta[np.abs(delta) <= 1] = 0
                # Make gentle terrain legible without changing measured heights.
                alpha = np.where(delta < 0, -delta*3, delta*1.7)
                heights = source.GetRasterBand(1).ReadAsArray(x,y,w,h)
                alpha[(values == 0) | (heights == NODATA)] = 0
                colour = np.where(delta > 0, 255, 0).astype(np.uint8)
                for i in (1,2,3):
                    result.GetRasterBand(i).WriteArray(colour,x,y)
                result.GetRasterBand(4).WriteArray(np.clip(alpha,0,255).astype(np.uint8),x,y)
        finish_output(result, lambda f,m,d: 0 if Path(job['cancel']).exists() else 1)
        write_rgb_style(path,result)
        return path
    finally:
        if result is not None:
            result.Close()
        if gray is not None:
            gray.Close()
        source.Close()
        scratch.unlink(missing_ok=True)


def contour_data(folder, dem_path, interval, job, emit):
    from osgeo import gdal, ogr, osr
    source = gdal.Open(str(dem_path))
    target = folder / 'terrain_contours.fgb'
    temporary = folder / '.terrain_contours.gpkg'
    database = None
    try:
        emit(f'Generating terrain contours every {interval:g} m…')
        crs = osr.SpatialReference(wkt=source.GetProjection())
        database = ogr.GetDriverByName('GPKG').CreateDataSource(str(temporary))
        layer = database.CreateLayer('contours', crs, ogr.wkbLineString)
        for name, kind in [('id',ogr.OFTInteger),('elevation',ogr.OFTReal),('major',ogr.OFTInteger)]:
            layer.CreateField(ogr.FieldDefn(name,kind))
        gdal.ContourGenerate(source.GetRasterBand(1),interval,0,[],1,NODATA,layer,0,1,
            callback=lambda f,m,d: 0 if Path(job['cancel']).exists() else 1)
        check_cancel(job)
        count = layer.GetFeatureCount()
        database.StartTransaction()
        for feature in layer:
            check_cancel(job)
            level = feature.GetField('elevation') / (interval*5)
            feature.SetField('major',int(abs(level-round(level)) < 1e-6))
            layer.SetFeature(feature)
        database.CommitTransaction()
        feature = layer = None
        database = None
        if not count:
            return None
        result = gdal.VectorTranslate(str(target),str(temporary),format='FlatGeobuf',layerName='contours',
            layerCreationOptions=['SPATIAL_INDEX=YES'])
        result.Close()
        return target
    finally:
        feature = layer = database = None
        source.Close()
        temporary.unlink(missing_ok=True)


def mask_water(dem_path, water_layers, job, emit):
    from osgeo import gdal
    from qgis.core import QgsProviderRegistry
    if not water_layers:
        return
    emit('Keeping water surfaces clear of hillshade and elevation contours…')
    dem = gdal.Open(str(dem_path), gdal.GA_Update)
    try:
        for water in water_layers:
            check_cancel(job)
            uri = QgsProviderRegistry.instance().decodeUri('ogr', water.source())
            path = uri.get('path', water.source().split('|')[0])
            name = uri.get('layerName', '')
            source = gdal.OpenEx(path, gdal.OF_VECTOR)
            try:
                layer = source.GetLayerByName(name) if name else source.GetLayer(0)
                if water.subsetString():
                    layer.SetAttributeFilter(water.subsetString())
                gdal.RasterizeLayer(dem,[1],layer,burn_values=[NODATA],options=['ALL_TOUCHED=TRUE'],
                    callback=lambda f,m,d: 0 if Path(job['cancel']).exists() else 1)
            finally:
                layer = source = None
    finally:
        dem.Close()


def feature_coverage(folder, sources, job, emit):
    """Approximate unknown local-extract coverage with a bounded-memory hull."""
    from osgeo import gdal, ogr, osr
    emit('Finding the terrain boundary from the selected OSM features…')
    combined = ogr.Geometry(ogr.wkbGeometryCollection)
    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(4326)
    target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    for name in ('roads','places','map_areas','map_lines'):
        if name not in sources:
            continue
        check_cancel(job)
        source = gdal.OpenEx(sources[name], gdal.OF_VECTOR)
        layer = source.GetLayer(0)
        cloud = ogr.Geometry(ogr.wkbGeometryCollection)
        try:
            for position, feature in enumerate(layer):
                if position % 2000 == 0:
                    check_cancel(job)
                    if not cloud.IsEmpty():
                        hull = cloud.ConvexHull()
                        cloud = ogr.Geometry(ogr.wkbGeometryCollection)
                        cloud.AddGeometry(hull)
                geometry = feature.GetGeometryRef()
                if geometry is None or geometry.IsEmpty():
                    continue
                west,east,south,north = geometry.GetEnvelope()
                for x,y in ((west,south),(west,north),(east,south),(east,north)):
                    point = ogr.Geometry(ogr.wkbPoint)
                    point.AddPoint_2D(x,y)
                    cloud.AddGeometry(point)
            if not cloud.IsEmpty():
                hull = cloud.ConvexHull()
                source_srs = layer.GetSpatialRef().Clone()
                source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
                hull.Transform(osr.CoordinateTransformation(source_srs,target_srs))
                combined.AddGeometry(hull)
        finally:
            feature = layer = source = None
    if combined.IsEmpty():
        raise ValueError('Cannot determine an OSM region for terrain shading.')
    hull = combined.ConvexHull()
    if hull.GetDimension() < 2:
        hull = hull.Buffer(0.001)
    path = folder/'terrain-coverage.geojson'
    path.write_text(json.dumps({'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{'source':'OSM feature hull'},'geometry':json.loads(hull.ExportToJson())}]}),encoding='utf-8')
    return path


def mask_coverage(dem_path, coverage, job, emit):
    """Keep relief inside the union of region polygons, preserving holes."""
    from osgeo import gdal
    check_cancel(job)
    emit('Trimming terrain to the OSM region boundary…')
    dem = gdal.Open(str(dem_path),gdal.GA_Update)
    source = mask = layer = None
    try:
        mask = gdal.GetDriverByName('MEM').Create('',dem.RasterXSize,dem.RasterYSize,1,gdal.GDT_Byte)
        mask.SetGeoTransform(dem.GetGeoTransform())
        mask.SetProjection(dem.GetProjection())
        source = gdal.OpenEx(str(coverage),gdal.OF_VECTOR)
        layer = source.GetLayer(0)
        gdal.RasterizeLayer(mask,[1],layer,burn_values=[1],
            callback=lambda f,m,d: 0 if Path(job['cancel']).exists() else 1)
        band = dem.GetRasterBand(1)
        for y in range(0,dem.RasterYSize,256):
            check_cancel(job)
            height = min(256,dem.RasterYSize-y)
            inside = mask.GetRasterBand(1).ReadAsArray(0,y,dem.RasterXSize,height)
            values = band.ReadAsArray(0,y,dem.RasterXSize,height)
            values[inside == 0] = NODATA
            band.WriteArray(values,0,y)
    finally:
        layer = source = mask = None
        dem.Close()


def terrain_layers(folder, extent, project, job, emit, water_layers=(), coverage_sources=None):
    from qgis.core import (Qgis, QgsRasterLayer, QgsVectorLayer, QgsLineSymbol,
                           QgsSingleSymbolRenderer, QgsPalLayerSettings, QgsTextFormat,
                           QgsTextBufferSettings, QgsVectorLayerSimpleLabeling, QgsBilinearRasterResampler)
    from qgis.PyQt.QtGui import QColor, QFont
    options = TerrainOptions.from_job(job)
    if not options.enabled:
        return []
    check_cancel(job)
    bounds = (extent.xMinimum(),extent.yMinimum(),extent.xMaximum(),extent.yMaximum())
    if options.dem:
        dem, info = local_dem(folder,bounds,options.dem,job,emit)
    else:
        dem, info = automatic_dem(folder,bounds,job,emit)
    coverage = folder/'terrain-coverage.geojson'
    if not coverage.is_file() and coverage_sources:
        coverage = feature_coverage(folder,coverage_sources,job,emit)
    if coverage.is_file():
        mask_coverage(dem,coverage,job,emit)
        info['coverage'] = coverage.name
    mask_water(dem, water_layers, job, emit)
    info.update(options.job_options())
    info['elevation_units'] = 'metres'
    info['terrain_elevation'] = dem.name
    info['water_masked'] = bool(water_layers)
    layers = []
    dark = job.get('osm_style','Light') == 'Dark'
    display_scale = float(job.get('osm_scale',1))
    if options.contours:
        contours = contour_data(folder,dem,options.interval,job,emit)
        if contours:
            for major in (1,0):
                layer = QgsVectorLayer(str(contours), 'Index contours (m)' if major else 'Contours (m)', 'ogr')
                layer.setSubsetString(f'major = {major}')
                if not layer.featureCount():
                    continue
                symbol = QgsLineSymbol.createSimple({'line_color': '#9b9280' if dark else '#9c896d',
                    'line_width': str((0.23 if major else 0.12)*display_scale)})
                layer.setRenderer(QgsSingleSymbolRenderer(symbol))
                layer.setOpacity(0.65 if major else 0.4)
                layer.setScaleBasedVisibility(True)
                layer.setMinimumScale(250000)
                if major:
                    settings = QgsPalLayerSettings()
                    settings.fieldName = 'round("elevation") || \' m\''
                    settings.isExpression = True
                    settings.placement = Qgis.LabelPlacement.Curved
                    settings.priority = 2
                    text = QgsTextFormat()
                    text.setFont(QFont('Arial',round(8*display_scale)))
                    text.setSize(8*display_scale)
                    text.setColor(QColor('#c3b8a4' if dark else '#877251'))
                    buffer = QgsTextBufferSettings()
                    buffer.setEnabled(True)
                    buffer.setSize(0.3*display_scale)
                    buffer.setColor(QColor('#151c24' if dark else '#f7f5ef'))
                    text.setBuffer(buffer)
                    settings.setFormat(text)
                    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
                    layer.setLabelsEnabled(True)
                project.addMapLayer(layer)
                layers.append(layer)
            info['contours'] = contours.name
    if options.strength > 0:
        shade = relief_raster(folder,dem,job,emit)
        layer = QgsRasterLayer(str(shade),'Terrain relief')
        if not layer.isValid():
            raise RuntimeError('Could not load terrain shading.')
        layer.loadDefaultStyle()
        layer.renderer().setOpacity(options.strength)
        layer.resampleFilter().setZoomedInResampler(QgsBilinearRasterResampler())
        layer.resampleFilter().setZoomedOutResampler(QgsBilinearRasterResampler())
        project.addMapLayer(layer)
        layers.append(layer)
        info['relief'] = shade.name
    (folder/'terrain-info.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    return layers
