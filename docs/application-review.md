# Application review — 30 October 2026

This pass reviewed all five tab workflows and their supporting modules: download
and coordinate handling, place search and preview caching, colour processing,
grid/polygon extraction, hybrid and standalone OSM rendering, QGIS process
launching, timing/logging, build resources and verification tooling. It makes
focused reliability and maintainability changes while preserving the workflows
and existing Light/Dark map styles.

## Changes made

| Area | Finding | Result |
| --- | --- | --- |
| Raster exports | The intermittent Windows access violation was reproduced inside GDAL `Translate` when copying from a live JPEG TIFF writer. | JPEG input is flushed and read through a separate read-only decoder for lossless export. The shared transparency mask is copied explicitly in bounded blocks. A regression also covers a cropped lossless output after closing the original JPEG writer. |
| Worker lifecycle | Three GUI implementations duplicated output parsing, exit handling and process cleanup; native crashes lost their exit context. | `mosaic_runtime.run_qgis_worker` now normalizes structured/native diagnostics, retains the last error context, distinguishes cancellation, closes pipes and reaps processes. Native fault tracing is enabled. TIFF bounds inspection and worker script locations are shared. |
| Validation | OSM/hybrid validation could create an output folder before rejecting invalid compression settings. Mosaic preparation could leave a temporary workspace after validation failure. | Settings are validated first. The OSM output folder is created after bounds and render-size validation. Failed mosaic preparation cleans its workspace. Start methods guard against duplicate launches. |
| Cancellation | Closing the application stopped at the first busy tab. A finishing worker could remove its workspace just before a cancellation request touched its flag. | Closing signals every active tab and the downloader; the window remains open until the user closes it after work stops. Shared cancellation signalling tolerates a workspace that has already been cleaned up. |
| Concurrent downloads | Hybrid and standalone OSM jobs could simultaneously write the same Geofabrik partial download. | A cancellable, per-source lock coordinates downloads between tabs in this application instance. Tests verify one download is published and waiting jobs can be cancelled. |
| Export planning | Resolution did not communicate the cost of a large detailed TIFF. Dimension calculations were duplicated. | `map_plan.py` shares dimensions, block counts and uncompressed-size estimates between GUI and renderer. Coordinate-based estimates update as inputs change; TIFF-derived estimates update when bounds are inspected at export start. Invalid, overflowing and out-of-range dimensions produce clear errors. Vector-only projects are not subjected to TIFF dimension limits. |
| Long-running UI | Process text could grow indefinitely during a long session. | Logs retain the latest 10,000 lines. Existing timestamps, timing summaries and independent log scrolling are preserved. |
| Frozen builds | Worker resource lists were duplicated, increasing the risk of missing a new helper module. Concurrent launches could interleave changes to Windows' process-wide DLL directory. | Build resources are declared once in `mosaic_runtime.WORKER_RESOURCES`, including `map_plan.py`. Worker launches serialize the temporary DLL-directory change. |
| Verification | Native process exit codes were not included in the verification summary output. | Failed suites print the numeric/hex exit code. Each run also writes machine-readable `results.json` alongside its logs and text summary. |

## Validation

- The original compression suite was run repeatedly in separate QGIS processes.
  Before the fix, fault tracing reproduced access violation `0xC0000005` in
  `gdal.Translate`. After separating JPEG reader/writer state and explicitly
  copying the mask, ten consecutive runs passed.
- Added a live-JPEG cropped lossless regression that checks decoded pixel values
  and the internal transparency mask after closing the source writer.
- Added unit coverage for worker diagnostics, cancellation/native-exit handling,
  process cleanup after callback failure, shared TIFF inspection, export planning,
  and concurrent cache access.
- Expanded desktop checks for validation without output-folder side effects,
  export estimates, cancellation of all active tabs, and bounded log retention.
- Run `python check.py --gui` for the final unit, eight QGIS integration suites
  and desktop smoke checks. Per-run reports are in `.verification/checks/`.

Validation uses Windows with the installed QGIS runtime. Linux launch behaviour
has unit coverage, but this pass does not constitute a native Linux GUI test or
a rebuilt/frozen executable release.

## Larger follow-ups

1. **Separate map-job orchestration from Tk.** The worker boundary is now shared,
   but `HybridTab.start` still coordinates downloads, extraction, manifests and
   rendering. A GUI-independent job service with immutable request/result objects
   would make retries and future workflow changes easier to test.
2. **Add higher-detail split previews.** The current split map is a thumbnail;
   zooming enlarges it. On-demand raster windows would make precise border tracing
   more useful without loading the full raster into the GUI.
3. **Make TIFF inspection asynchronous.** Export-time inspection is already in a
   background job, while explicit “Use TIFF area” and “Use hybrid area” inspection
   can briefly block the UI. Move these button actions to a cancellable metadata
   task and add a pre-export TIFF-size check.
4. **Persist useful session settings.** Save QGIS location, source lists, recent
   areas and output preferences, while keeping the explicit export-area choice
   that prevents accidental use of an unrelated region.
5. **Manage cache lifetime and multiple application instances.** Preview entries
   have expiry information but no total disk quota/cleanup policy. Download
   coordination added here covers tabs in one process; separate application
   instances still need cross-process cache locking.
6. **Improve ESA network recovery.** Geofabrik already supports verified resume
   and bounded retries. ESA tile downloads remain serial and discard incomplete
   transfers; verified resume and bounded retry handling would improve recovery
   on unreliable connections.
7. **Split cartography and raster rendering modules.** `hybrid_worker.py` has clear
   helper functions but still combines feature indexing, cartography, coastline
   construction, project setup and TIFF rendering. Extract these responsibilities
   while retaining the pixel, label-order, transparency and project-reload tests.
8. **Add durable/exportable process history.** The bounded GUI history prevents
   unbounded memory use; a persistent run log and “Save log” action would retain
   older diagnostic history for long production exports.

These follow-ups are deliberately recorded separately from completed changes;
they are not claims that those features have already been implemented.
