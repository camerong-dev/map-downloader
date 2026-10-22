"""Executed by QGIS's Python (GDAL + NumPy), not the GUI's Python."""
import json
import math
from pathlib import Path
import sys
import tempfile
import os

import numpy as np
from osgeo import gdal, osr
from raster_output import (TARGET_CRS, projected_resolution, finish_output, write_rgb_style,
                           is_web_mercator, compression_options, export_geotiff)

gdal.UseExceptions()


class Cancelled(Exception):
    pass


def emit(message, progress=None):
    print(json.dumps({'message': message, 'progress': progress}), flush=True)


def check_cancel(path):
    if Path(path).exists():
        raise Cancelled('Processing cancelled. Source files were kept.')


_qgis_app = None


def render_fixed_qgis(path, target, bands, minimum, maximum, cancel):
    """Bake the user's actual PyQGIS renderer into RGBA pixels."""
    global _qgis_app
    from qgis.core import (QgsApplication, QgsRasterLayer, QgsMultiBandColorRenderer,
                           QgsContrastEnhancement, QgsRasterPipe, QgsRasterFileWriter,
                           QgsRasterBlockFeedback, QgsProject)
    if _qgis_app is None:
        QgsApplication.setPrefixPath(os.environ['QGIS_PREFIX_PATH'], True)
        _qgis_app = QgsApplication([], False)
        _qgis_app.initQgis()
    layer = QgsRasterLayer(str(path), path.stem, 'gdal')
    if not layer.isValid():
        raise ValueError(f'QGIS could not open {path}')
    provider = layer.dataProvider()
    renderer = QgsMultiBandColorRenderer(provider, *bands)
    mask_source = gdal.Open(str(path))
    alpha_band = next((i for i in range(1, mask_source.RasterCount + 1)
                       if mask_source.GetRasterBand(i).GetColorInterpretation() == gdal.GCI_AlphaBand), -1)
    if alpha_band == -1 and mask_source.GetRasterBand(1).GetMaskFlags() & gdal.GMF_PER_DATASET:
        alpha_band = mask_source.RasterCount + 1
    if alpha_band > 0:
        renderer.setAlphaBand(alpha_band)
    mask_source.Close()
    for band, setter in zip(bands, [renderer.setRedContrastEnhancement,
                                    renderer.setGreenContrastEnhancement,
                                    renderer.setBlueContrastEnhancement]):
        ce = QgsContrastEnhancement(provider.dataType(band))
        ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum)
        ce.setMinimumValue(minimum)
        ce.setMaximumValue(maximum)
        setter(ce)
    layer.setRenderer(renderer)
    pipe = QgsRasterPipe()
    if not pipe.set(provider.clone()) or not pipe.set(renderer.clone()):
        raise RuntimeError('Could not configure the QGIS rendering pipeline.')
    writer = QgsRasterFileWriter(str(target))
    writer.setOutputFormat('GTiff')
    options = ['TILED=YES', 'COMPRESS=DEFLATE', 'BIGTIFF=IF_SAFER', 'PHOTOMETRIC=RGB']
    # QGIS 3 LTR uses setCreateOptions; newer QGIS also has setCreationOptions.
    setter = getattr(writer, 'setCreationOptions', None) or writer.setCreateOptions
    setter(options)
    feedback = QgsRasterBlockFeedback()
    def progress(value):
        if Path(cancel).exists():
            feedback.cancel()
    feedback.progressChanged.connect(progress)
    check_cancel(cancel)
    result = writer.writeRaster(pipe, provider.xSize(), provider.ySize(), layer.extent(),
                                layer.crs(), QgsProject.instance().transformContext(), feedback)
    check_cancel(cancel)
    if int(result) != 0:
        raise RuntimeError(f'QGIS raster export failed (code {result}).')
    verification = gdal.Open(str(target))
    if (verification.RasterXSize, verification.RasterYSize) != (provider.xSize(), provider.ySize()):
        raise RuntimeError('QGIS rendered output dimensions do not match the full-resolution source.')
    verification = None


def read_rgb(ds, bands, x=0, y=0, width=None, height=None, sample=False):
    width, height = width or ds.RasterXSize, height or ds.RasterYSize
    kwargs = {'buf_xsize': min(width, 512), 'buf_ysize': min(height, 512)} if sample else {}
    arrays, valid = [], None
    for index in bands:
        band = ds.GetRasterBand(index)
        data = band.ReadAsArray(x, y, width, height, **kwargs)
        mask = band.GetMaskBand().ReadAsArray(x, y, width, height, **kwargs) != 0
        mask &= np.isfinite(data)
        valid = mask if valid is None else valid & mask
        arrays.append(data)
    # Honour a separate alpha band as well as GDAL masks/nodata.
    for index in range(1, ds.RasterCount + 1):
        band = ds.GetRasterBand(index)
        if band.GetColorInterpretation() == gdal.GCI_AlphaBand:
            valid &= band.ReadAsArray(x, y, width, height, **kwargs) > 0
    return np.stack(arrays), valid


def inspect(paths, bands, cancel):
    info, samples = [], []
    reference = None
    for i, path in enumerate(paths):
        check_cancel(cancel)
        ds = gdal.Open(str(path))
        if ds is None or max(bands) > ds.RasterCount:
            raise ValueError(f'{path}: not enough bands for the selected RGB channels.')
        if any(ds.GetRasterBand(b).GetColorTable() is not None for b in bands):
            raise ValueError('Colour balancing is for RGB imagery, not classified land-cover rasters.')
        gt, projection = ds.GetGeoTransform(can_return_null=True), ds.GetProjection()
        if not gt or not projection or gt[1] <= 0 or gt[5] >= 0 or gt[2] or gt[4]:
            raise ValueError(f'{path}: a georeferenced, north-up raster is required. Reproject in QGIS first.')
        srs = osr.SpatialReference(wkt=projection)
        if reference:
            ref_gt, ref_srs = reference
            if not srs.IsSame(ref_srs) or not math.isclose(gt[1], ref_gt[1], rel_tol=1e-6) or not math.isclose(gt[5], ref_gt[5], rel_tol=1e-6):
                raise ValueError('Inputs must use the same CRS and pixel size. Reproject/resample in QGIS first.')
        else:
            reference = gt, srs
        data, valid = read_rgb(ds, bands, sample=True)
        sample = data[:, valid].astype(np.float64)
        info.append((gt, projection, ds.RasterXSize, ds.RasterYSize))
        samples.append(sample)
        emit(f'Sampled {i + 1}/{len(paths)}: {Path(path).name}', 15 * (i + 1) / len(paths))
        ds = None
    if not any(s.shape[1] for s in samples):
        raise ValueError('No valid image pixels found in the selected bands.')
    return info, samples


def run(job):
    compression_options(job)
    paths = [Path(p).resolve() for p in job['inputs']]
    output = Path(job['output']).resolve()
    cancel = job['cancel']
    bands = job.get('bands', [1, 2, 3])
    mode = job.get('mode', 'fixed')
    minimum, maximum = float(job.get('minimum', 0.005)), float(job.get('maximum', 0.40))
    if mode == 'fixed' and (not math.isfinite(minimum) or not math.isfinite(maximum) or minimum >= maximum):
        raise ValueError('The fixed stretch minimum must be less than its maximum, and both must be finite.')
    if mode not in ('fixed', 'shared', 'match') or len(bands) != 3 or min(bands) < 1:
        raise ValueError('Select a valid RGB mode and three positive band numbers.')
    if not paths or any(not p.is_file() for p in paths):
        raise ValueError('Select existing input TIFF files.')
    if output in paths or output.exists():
        raise ValueError('Choose a new output file. Existing files are never overwritten.')
    output.parent.mkdir(parents=True, exist_ok=True)
    info, samples = inspect(paths, bands, cancel)
    if mode == 'match' and samples[0].shape[1] == 0:
        raise ValueError('The reference tile contains no valid sampled pixels. Choose another reference.')
    low = high = None
    if mode != 'fixed':
        pooled = np.concatenate([s for s in samples if s.shape[1]], axis=1) if mode == 'shared' else samples[0]
        low, high = np.percentile(pooled, [2, 98], axis=1)
        # Handle constant or nearly uniform bands without division by zero.
        high = np.maximum(high, low + 1)
    percentiles = np.linspace(0, 100, 257)
    reference_quantiles = np.percentile(samples[0], percentiles, axis=1) if mode == 'match' else None
    normalized = []
    with tempfile.TemporaryDirectory(prefix='esa-mosaic-', dir=output.parent) as temp:
        temp = Path(temp)
        for i, (path, meta, sample) in enumerate(zip(paths, info, samples)):
            check_cancel(cancel)
            emit(f'Balancing tile {i + 1}/{len(paths)}: {path.name}')
            src = gdal.Open(str(path))
            gt, projection, width, height = meta
            target_path = temp / f'tile-{i}.tif'
            if mode == 'fixed':
                src = None
                render_fixed_qgis(path, target_path, bands, minimum, maximum, cancel)
                normalized.append(str(target_path))
                emit(f'Rendered tile {i + 1}/{len(paths)} using your QGIS stretch', 15 + 65 * (i + 1) / len(paths))
                continue
            dst = gdal.GetDriverByName('GTiff').Create(str(target_path), width, height, 4, gdal.GDT_Byte,
                       options=['TILED=YES', 'COMPRESS=DEFLATE', 'BIGTIFF=IF_SAFER', 'PHOTOMETRIC=RGB', 'ALPHA=YES'])
            dst.SetGeoTransform(gt)
            dst.SetProjection(projection)
            for channel, interpretation in enumerate([gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand, gdal.GCI_AlphaBand], 1):
                dst.GetRasterBand(channel).SetColorInterpretation(interpretation)
            mapping = []
            if mode == 'match' and sample.shape[1]:
                quantiles = np.percentile(sample, percentiles, axis=1)
                for c in range(3):
                    unique, indexes = np.unique(quantiles[:, c], return_index=True)
                    mapping.append((unique, reference_quantiles[indexes, c]))
            try:
                for y in range(0, height, 512):
                    for x in range(0, width, 512):
                        check_cancel(cancel)
                        data, valid = read_rgb(src, bands, x, y, min(512, width - x), min(512, height - y))
                        for c in range(3):
                            values = data[c].astype(np.float64)
                            if mapping:
                                values = np.interp(values, *mapping[c])
                            scaled = np.clip((values - low[c]) * 255 / (high[c] - low[c]), 0, 255)
                            scaled[~valid] = 0
                            dst.GetRasterBand(c + 1).WriteArray(np.rint(scaled).astype(np.uint8), x, y)
                        dst.GetRasterBand(4).WriteArray(valid.astype(np.uint8) * 255, x, y)
                    emit(f'Balancing tile {i + 1}/{len(paths)}', 15 + 65 * (i + min(y + 512, height) / height) / len(paths))
                dst.FlushCache()
            finally:
                dst = src = None
            normalized.append(str(target_path))
        check_cancel(cancel)
        emit('Merging balanced tiles…', 80)
        # Warp honours alpha in overlaps; transparent pixels do not hide valid imagery.
        merged_path = temp / 'merged.tif'
        def callback(fraction, message, data):
            if Path(cancel).exists():
                return 0
            emit('Merging balanced tiles…', 80 + 15 * fraction)
            return 1
        sources = [gdal.Open(path) for path in normalized]
        resolution = min(projected_resolution(source) for source in sources)
        try:
            merged = gdal.Warp(str(merged_path), sources, format='GTiff',
                               dstSRS=TARGET_CRS, xRes=resolution, yRes=resolution,
                               overviewLevel='NONE', errorThreshold=0.0,
                               srcAlpha=True, dstAlpha=True, resampleAlg='near',
                               creationOptions=['TILED=YES', 'COMPRESS=DEFLATE', 'BIGTIFF=IF_SAFER', 'PHOTOMETRIC=RGB'],
                               callback=callback)
        except Exception:
            check_cancel(cancel)
            raise
        finally:
            for source in sources:
                source.Close()
            sources.clear()
        if merged is None:
            raise RuntimeError('GDAL could not create the mosaic.')
        if job.get('compression', 'DEFLATE').upper() == 'JPEG':
            emit(f'Encoding JPEG-compressed RGB at quality {job.get("jpeg_quality", 85)}…', 95)
            merged.FlushCache()
            raw = merged
            encoding_progress = 95
            def encoding_callback(fraction, message, data):
                nonlocal encoding_progress
                if Path(cancel).exists():
                    return 0
                encoding_progress = max(encoding_progress, 95 + fraction)
                emit('Encoding JPEG-compressed RGB…', encoding_progress)
                return 1
            try:
                merged = export_geotiff(raw, temp / 'compressed.tif', job, callback=encoding_callback)
            finally:
                raw.Close()
                raw = None
            check_cancel(cancel)
            merged_path = temp / 'compressed.tif'
            if merged is None:
                raise RuntimeError('JPEG encoding failed.')
        try:
            check_cancel(cancel)
            emit('Building viewing overviews…', 96)
            try:
                finish_output(merged, lambda f, m, d: 0 if Path(cancel).exists() else 1)
            except Exception:
                check_cancel(cancel)
                raise
            merged.SetMetadataItem('COLOUR_PROCESSING', mode)
            merged.SetMetadataItem('SOURCE_RGB_BANDS', ','.join(map(str, bands)))
            if mode == 'fixed':
                merged.SetMetadataItem('QGIS_STRETCH_MINIMUM', str(minimum))
                merged.SetMetadataItem('QGIS_STRETCH_MAXIMUM', str(maximum))
                merged.SetMetadataItem('QGIS_CONTRAST_ALGORITHM', 'StretchToMinimumMaximum')
            else:
                merged.SetMetadataItem('STRETCH_PERCENTILES', '2,98 (sampled)')
            merged.FlushCache()
        finally:
            merged = None
        check_cancel(cancel)
        verification = gdal.Open(str(merged_path))
        expected_bands = 3 if job.get('compression', 'DEFLATE').upper() == 'JPEG' else 4
        if verification is None or verification.RasterCount != expected_bands or not is_web_mercator(verification):
            raise RuntimeError('Output verification failed.')
        write_rgb_style(output, verification)
        verification = None
        # rename refuses an existing destination on Windows.
        merged_path.rename(output)
    emit(f'Complete: {output}', 100)


if __name__ == '__main__':
    try:
        run(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception as error:
        emit(str(error))
        sys.exit(2 if isinstance(error, Cancelled) else 1)
