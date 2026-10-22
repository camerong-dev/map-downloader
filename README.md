# ESA Region Downloader

A Windows and Linux desktop GUI using Python and Tkinter. Downloads ESA WorldCover
2021 v200 or 2020 v100 satellite composites or land-cover GeoTIFFs from public S3 over anonymous HTTPS.
No AWS account, API keys, boto3, or runtime pip packages are needed.
Processing tabs require a native local QGIS installation with Python bindings,
NumPy and GDAL 3.8 or newer. QGIS 3 LTR and QGIS 4 APIs are supported.

## Run

Install Python 3.11 or newer from python.org, including Tcl/Tk and the PATH option.
Open PowerShell in this folder:

```powershell
python app.py
```

On Linux (native Debian/Ubuntu installation; Ubuntu 24.04 or newer is a suitable base):

```bash
sudo apt update
sudo apt install qgis python3-qgis python3-gdal python3-numpy python3-tk python3-venv
/usr/bin/python3 app.py
```

Use packages appropriate to your distribution; see the [official QGIS installation
guide](https://qgis.org/resources/installation-guide/). The processing prefix is
automatically detected, usually `/usr`; select that prefix in the QGIS folder field.
Linux uses the native prefix's `bin/python3`, not a Windows executable. For a custom
native installation, set `QGIS_PREFIX_PATH` and, if necessary, `ESA_QGIS_PYTHON` to
the compatible interpreter. Flatpak/Snap-only QGIS installations are not supported
by this external Python launcher; install native QGIS Python packages instead.
The desktop GUI needs a graphical desktop session. Only workers run offscreen.

Paste a comma-separated bounding box in **west, south, east, north** order and click
**Use these coordinates**, or edit the four labelled edges individually.
For example, `-61.528931,-52.484453,-57.615051,-50.934200` sets west to
`-61.528931`, south to `-52.484453`, east to `-57.615051`, and north to `-50.934200`.
The supplied example is preloaded on startup.
Choose the product, year and output
folder, click **Preview region**, then **Download tiles**. Negative longitudes
are west of Greenwich; negative latitudes are south of the equator.
Split a region crossing longitude ±180 into two downloads.
Bounds must have nonzero width and height. Preview is limited to 400 candidate
tiles to prevent accidental worldwide requests.

The preview checks availability and reports total transfer size before downloading.
Ocean-only tiles may not exist. Network errors are reported; retry the operation
after restoring connectivity. Cancellation can take up to the current network
timeout (30 seconds). Completed files are kept; files of the expected size are
skipped on retry. This is a size check, not cryptographic integrity verification.
An interrupted tile is removed and restarts from the beginning on retry.
Existing files with conflicting sizes are not overwritten. Use a separate output
folder per running instance. Successful batches include a JSON region manifest
with source URLs, requested bounds, tile sizes, attribution, and license.

## Build a single Windows executable

On Windows with Python installed:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The build creates an isolated `.venv`, installs PyInstaller, and outputs
`dist\ESARegionDownloader.exe`. Distribute that executable; users do not need
Python. Internet is needed for data downloads. Build on the same operating system
and architecture you intend to distribute to. Unsigned executables may trigger
Windows SmartScreen; code signing is a separate distribution step.

## Build a Linux executable

On the target Linux system, with native QGIS and the dependencies above installed:

```bash
bash build.sh
./dist/ESARegionDownloader
```

`build.sh` creates `.venv-build` for PyInstaller. The portable `build.py` selects
the correct resource separator for the build OS. Build Windows executables on
Windows and Linux executables on Linux; there is no single binary for both systems.
The Linux executable still requires native QGIS/Python/GDAL on the destination
machine. Build on the oldest Linux distribution you intend to support because
system library compatibility matters.

External workers use their host QGIS environment rather than bundled GUI libraries.
In frozen Linux builds, `LD_LIBRARY_PATH` is restored to its original host value,
as described in [PyInstaller's external-program guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html).
`.github/workflows/linux.yml` supplies Ubuntu raster tests and a GUI smoke test.
Local verification so far is on Windows; Linux launcher behaviour is unit tested
and the Linux workflow has been added, but a native Linux run has not been completed here.

## Products and QGIS

The default **Satellite imagery (RGB + near infrared)** product downloads annual
Sentinel-2 RGBNIR composites (`S2RGBNIR`) from `esa-worldcover-s2/rgbnir`.
These use a **1° × 1°** grid, matching S2RGBNIR results in the ESA viewer.
They are annual composites, rather than individual dated satellite scenes.
Load the four-band GeoTIFFs into QGIS and use multiband colour, with bands
1/2/3 for red/green/blue; band 4 is near infrared. Adjust the display stretch
as needed. Select **Land-cover classification** for the categorical map instead.

## Colour balance and merge

Open the **Colour balance & merge** tab in the same application:

1. Check the detected QGIS installation folder (for example `C:\Program Files\QGIS 4.2.3`).
2. Add TIFF files, or add a folder containing the downloaded imagery.
3. Leave **Your QGIS colours — fixed RGB stretch** selected to reproduce the supplied
   QGIS script's method: RGB bands 1/2/3, `StretchToMinimumMaximum`, minimum **0.005**,
   with your updated desert-friendly maximum **0.40** for each channel. Both limits
   are editable (the original script used 0.20). This uses the actual
   `QgsMultiBandColorRenderer` and `QgsContrastEnhancement`, then exports the rendered
   pixels through `QgsRasterFileWriter` before merging. QGIS honours band scale/offset
   metadata (the downloaded WorldCover RGBNIR files use a 0.0001 scale), so the limits
   are applied to reflectance values rather than raw integer storage values.
4. The earlier **Shared colour stretch** remains available for a sampled 2nd–98th
   percentile range per channel across all tiles. For tiles with different
   brightness/colour distributions, choose **Match colours
   to reference tile** and select a representative tile with **Use selected as reference**.
   This applies sampled, per-channel quantile matching before the shared reference
   stretch. It can reduce seams but may change genuine differences in terrain or water;
   it is not overlap-based radiometric calibration and cannot guarantee invisible seams.
5. Choose a **new** output `.tif` and click **Balance colours & merge**.

The app runs PyQGIS and the GDAL/NumPy tools bundled with QGIS in a separate background process;
QGIS Desktop does not need to be open. No extra GIS pip packages are installed.
The fixed mode exports each tile with QGIS's raster renderer; the two statistical
modes balance full-resolution tiles in 512-pixel blocks. It merges the results and builds viewing
overviews. The result is a compressed, tiled, georeferenced **8-bit RGB + alpha GeoTIFF**,
in **EPSG:3857**, with BigTIFF support for large outputs. RGB bands default to 1/2/3. Near infrared and
original scientific reflectance values are not included in this display product.
Source files remain unchanged. In the two statistical modes, colour statistics are
approximate (up to 512 × 512 sample pixels per tile, excluding nodata and masked pixels).
The fixed mode uses the specified limits, without percentile matching. Its output
has the desired appearance baked into byte RGB values: view RGB bands 1/2/3 with
**No enhancement** in QGIS rather than applying the reflectance limits again.

Use RGB imagery, not the categorical WorldCover land-cover map. Inputs must have
the same CRS and pixel size and be north-up. The worker reports incompatible inputs
instead of silently skipping them. Where valid imagery overlaps, later files take
priority. Alpha/nodata transparency is honoured. Geographic gaps remain transparent.
The output covers all selected tiles, without clipping to the downloader's region.
Reprojection uses the base-resolution image, with output pixel spacing chosen from
the finer projected source-pixel dimension. Native downloaded ESA source files stay
in their original CRS; all merged outputs and split outputs are EPSG:3857. The
download bounds remain WGS84 longitude/latitude degrees.

Each rendered byte RGB output includes a matching `.qml` sidecar for QGIS with RGB
bands 1/2/3, alpha transparency and no additional contrast enhancement. Keep it beside
the TIFF and add the new file as a fresh QGIS layer. Viewing overviews are built for
both mosaics and split areas. Set the QGIS **project** CRS to EPSG:3857 as well if
you want the map canvas to use it; a project's CRS is separate from a raster's CRS.

Intermediate balanced tiles and the mosaic are written beside the output and removed
after completion or cancellation. Allow ample free space for both the intermediates
and final mosaic, potentially several times the input size. Cancellation is cooperative
and can wait for the current GDAL operation. Existing output files are never overwritten.
The packaged GUI still requires QGIS on the destination computer; it does not bundle QGIS.

## Split maps visually

The **Split maps visually** tab splits one merged TIFF or a group of compatible
TIFFs into smaller geographic areas:

1. Click **Load TIFFs & preview** and select your merged file, or multiple TIFFs.
2. Adjust **Rows** and **Columns** (for example 2 × 2 makes four areas). The yellow
   grid updates immediately over the map preview. Labels are row,column, with north
   at the top. Nothing is selected initially. Checkerboard areas indicate masked/no-data coverage in the preview.
   Use the mouse wheel or **+ / −** buttons to zoom (25–800% of the preview image).
   Wheel zoom centres on the cursor. Drag with the left or middle mouse button to
   pan; a plain left click selects/deselects an area. **Fit map** recentres
   the whole map. The split grid and hover bounds stay aligned while zooming/panning.
   Zoom enlarges the preview image; it does not load additional source detail or
   change exported extents, resolution, selected areas or pixel values.
3. Click the areas you want to export. Selected areas have a yellow outline and
   check mark; click again to deselect. **Select all** and **Clear selection** handle
   the whole grid. Changing the rows/columns clears the selection. Hover to see exact
   pixel dimensions and bounds in EPSG:3857 metres.
4. Choose an output location. The app proposes a new subfolder.
5. Leave **Create whole-region overview** enabled and choose **50, 100, 250 or 500**
   EPSG:3857 metres per pixel (default **100**). Uncheck it to export detailed areas only.
6. Click **Export selected areas** (enabled once an area is selected). Files are named `area_row01_col01.tif`, etc.

The separate `region_overview_100m.tif` (or selected spacing) covers the **entire loaded
region**, including grid areas excluded from detailed export. It averages the rendered
pixel colours down to a coarser grid; nodata and alpha are respected. It does not
change the merged file or the detailed split pixels. The overview uses the same
EPSG:3857 CRS, with a QGIS RGB style and viewing overviews. Its bounds may extend
by less than one overview pixel at the east/south edges to cover the complete region.
An overview must have spacing at least as coarse as the detailed image.
Categorical rasters use mode resampling rather than averaging class IDs.

For matching colours, load your **colour-balanced merged TIFF** before exporting.
The overview does not apply a new colour stretch. In QGIS, put it below the detailed
areas; optional scale visibility can control which layers show as you zoom.
EPSG:3857 spacing is in projected map metres, not constant ground metres at every latitude.

The preview is reduced in size to keep the GUI responsive; exports use a full-detail
EPSG:3857 grid. Existing EPSG:3857 rasters are cut directly without reprojection.
Other CRSs are reprojected with nearest-neighbour sampling first, using a virtual
grid rather than a full intermediate TIFF. Data type, bands and scale metadata are kept.
Already rendered byte RGB maps are shown without a further stretch. Scaled imagery
is shown with the 0.005–0.40 reflectance stretch for preview only. Other non-byte
images use an approximate preview stretch. No colour processing is applied to exports.

Grid cuts follow exact EPSG:3857 pixel boundaries; all areas together cover that grid without
missing or duplicating pixels. Each is a compressed tiled GeoTIFF. The size estimate
is pixel data before compression, not an exact output-file size or a hard limit.
Areas containing only no-data are still exported if selected. A JSON manifest lists
the exported files, pixel windows and geographic bounds. Completed areas remain if
cancelled; an incomplete area is removed. Existing output folders are never overwritten.

Multiple inputs use a virtual mosaic, avoiding another large intermediate merged TIFF.
Inputs must match in CRS, pixel size, band layout, types, scale/offset and nodata.
They must be north-up. Slightly offset source grids use the virtual mosaic's nearest
pixel mapping; EPSG:3857 inputs are split without another reprojection. Later input files
take priority in overlaps; the virtual mosaic does not alpha-blend overlapping images.
For predictable overlapping imagery, merge it first using the processing tab.
The temporary preview is stored in the operating system's temporary folder and
removed when reloading or closing the application. Source folders may be read-only.

`tests/split_integration.py` verifies full pixel coverage, exported values and scale,
selected areas, virtual-mosaic previews, overview averaging and transparency,
unchanged full-detail areas, cancellation and existing-folder protection.

## Export compression

Both **Colour balance & merge** and **Split maps visually** include an
**Export compression** selector:

- **Lossless (DEFLATE)** preserves the exported pixel values exactly and remains
  the default. It supports source data types, classifications and extra bands.
- **JPEG (rendered RGB)** writes a tiled GeoTIFF with `COMPRESS=JPEG`,
  `JPEG_QUALITY=85` by default, `PHOTOMETRIC=YCBCR` and pixel interleaving.
  Change quality from 1–100 if desired. It keeps EPSG:3857 and full pixel dimensions
  but is lossy, so decoded pixel colours may change slightly.

The split tab applies the selected compression to both detailed areas and the
optional whole-region overview. The merge tab applies it to the final mosaic,
after colour rendering and reprojection; intermediates remain lossless.

JPEG output contains **three rendered 8-bit RGB bands**. Already rendered byte
maps are encoded without another colour stretch. Raw multi-band reflectance inputs
in the split tab are rendered as RGB bands 1/2/3 with the preview's fixed
0.005–0.40 stretch first. Near infrared and original scientific values are omitted.
Classified or single-band rasters require the lossless option.

For JPEG, transparency is stored as a **lossless internal binary mask**, instead
of compressing an alpha band with JPEG. Masked pixels stay transparent; partially
transparent pixels become valid/invalid mask pixels. No external `.msk` is needed.
Keep the supplied `.qml` next to the TIFF: it tells QGIS to use the internal mask's
virtual alpha band and avoids applying a second colour stretch. Viewing overviews
are retained for fast navigation.

For the best master-copy workflow, keep the merged TIFF lossless and select JPEG
85 for the final split maps and overview. Selecting JPEG in both tabs works, but
splitting a JPEG master recompresses its already lossy colours.

`tests/compression_integration.py` checks actual JPEG encoding, size reduction on a
synthetic map, quality, raw reflectance rendering, internal-mask transparency in
QGIS, merged/split outputs, and regional overviews.

## Data and output scope

The **land-cover classification** product is a categorical dataset, not natural-colour satellite
photography, terrain elevation, or a street map. It has 11 land-cover classes and
10 m nominal resolution. Downloads contain entire intersecting 3° × 3° source
tiles, including data outside the requested rectangle. Imagery instead uses 1° tiles.
Files retain their original
georeferencing and class values in the download tab. The processing tab creates
a separate display mosaic reprojected to EPSG:3857. No region clipping, MBTiles,
or ATAK package conversion is performed. View/style these files in GIS software;
ATAK compatibility depends on the target app's supported raster formats and styling.

The app supports these two WorldCover products. Other ESA products need their own
discovery/download adapter. The preview count depends on the selected product;
classification and imagery use different grids and are not interchangeable.

Official sources:

- https://esa-worldcover.org/en/data-access
- https://github.com/ESA-WorldCover/esa-worldcover-datasets
- https://registry.opendata.aws/esa-worldcover-vito/

Data license: CC BY 4.0. Preserve acknowledgement when sharing maps or derived data:

> © ESA WorldCover project [year] / Contains modified Copernicus Sentinel data ([year]) processed by ESA WorldCover consortium

## Checks

```powershell
python -m unittest discover -s tests -v
```

Tests cover coordinate/tile boundaries, cancellation, interrupted transfers and
existing-file protection using mocked HTTP responses (no large downloads).

`tests/mosaic_integration.py` runs separately under QGIS Python and verifies
synthetic mosaic dimensions, transparency, known colour-offset correction,
cancellation and output-file protection.
