"""Download OSM for the current region and launch the offline hybrid renderer."""
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from downloader import Bounds, Cancelled, parse_region
from mosaic_runtime import find_qgis, qgis_command, start_worker
from geofabrik import ATTRIBUTION, LICENSE, DEFAULT_SOURCE, obtain, validate_sources
from hybrid_style import DEFAULT_ROADS, ROAD_GROUPS, ROAD_LABELS
from export_gui import CompressionControls
from process_timing import ProcessDisplay
from gui_style import section


class HybridTab(ttk.Frame):
    def __init__(self, parent, bounds_provider):
        super().__init__(parent, padding=14)
        self.bounds_provider = bounds_provider
        self.busy = False
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.cancel_path = None
        ttk.Label(self, text='OSM hybrid overlay', style='Title.TLabel').pack(anchor='w')
        ttk.Label(self, text='By default, OSM covers the selected satellite TIFF, including split TIFFs.',
                  wraplength=760).pack(anchor='w', pady=4)
        self.qgis = tk.StringVar(value=find_qgis())
        self.refresh_extract = tk.BooleanVar(value=False)
        self.imagery = tk.StringVar()
        self.output = tk.StringVar()
        self.resolution = tk.StringVar(value='10')
        self.controls = []
        self.coordinates = tk.StringVar()
        settings = ttk.Frame(self)
        settings.pack(fill='x', pady=(4, 8))
        settings.columnconfigure(0, weight=1, uniform='settings')
        settings.columnconfigure(1, weight=1, uniform='settings')
        left = ttk.Frame(settings)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        right = ttk.Frame(settings)
        right.grid(row=0, column=1, sticky='nsew', padx=(8, 0))
        coverage = section(left, 'Map coverage')
        self.row('West, south, east, north', self.coordinates, parent=coverage)
        ttk.Label(coverage, text='Four comma-separated numbers. Reversed west/east or south/north edges are sorted automatically.',
                  wraplength=440).pack(anchor='w')
        self.row('QGIS folder', self.qgis, lambda: self.choose(self.qgis, 'directory'), parent=left)
        source_frame = ttk.LabelFrame(left, text='OSM sources — one URL or PBF path per line', padding=5)
        source_frame.pack(fill='x', pady=4)
        self.source_text = ScrolledText(source_frame, height=5, width=28, wrap='none', font=('Consolas', 9))
        self.source_text.pack(fill='x', expand=True)
        self.source_text.insert('1.0', DEFAULT_SOURCE)
        source_button = ttk.Button(source_frame, text='Add PBF files…', command=self.add_source_files)
        source_button.pack(anchor='w', pady=(6, 0))
        self.controls.extend([self.source_text, source_button])
        refresh = ttk.Checkbutton(left, text='Refresh cached downloads (otherwise reuse offline)', variable=self.refresh_extract)
        refresh.pack(anchor='w')
        self.controls.append(refresh)
        ttk.Label(left, text='Add all countries or regions needed to cover the map. Cached sources can be reused offline.', wraplength=440, style='Muted.TLabel').pack(anchor='w')
        self.row('Satellite TIFF (optional)', self.imagery, lambda: self.choose(self.imagery, 'file'), parent=coverage)
        self.use_tiff_bounds = tk.BooleanVar(value=True)
        extent_toggle = ttk.Checkbutton(coverage, text='Use full TIFF bounds instead of coordinates', variable=self.use_tiff_bounds)
        extent_toggle.pack(anchor='w')
        self.controls.append(extent_toggle)
        export = section(right, 'Output')
        self.row('New output folder', self.output, lambda: self.choose(self.output, 'output'), parent=export)
        self.row('Export metres/pixel', self.resolution, parent=export)
        self.render_rasters = tk.BooleanVar(value=True)
        raster_toggle = ttk.Checkbutton(export, text='Also export fixed-resolution GeoTIFFs', variable=self.render_rasters)
        raster_toggle.pack(anchor='w')
        self.controls.append(raster_toggle)
        self.encoding = CompressionControls(export, allow_source=True)
        self.encoding.pack(fill='x', pady=4)
        options = ttk.LabelFrame(right, text='Roads and names to show', padding=4)
        options.pack(fill='x', pady=5)
        self.road_groups = {}
        road_titles = {'a_road': 'A roads / major', 'b_road': 'B roads / secondary',
                       'local': 'Minor / residential roads'}
        for index, (key, title) in enumerate(ROAD_GROUPS.items()):
            variable = tk.BooleanVar(value=key in DEFAULT_ROADS)
            self.road_groups[key] = variable
            button = ttk.Checkbutton(options, text=road_titles.get(key, title), variable=variable)
            button.grid(row=index // 3, column=index % 3, sticky='w', padx=5)
            self.controls.append(button)
        ttk.Label(options, text='Road labels').grid(row=2, column=0, sticky='w', padx=5)
        self.road_labels = tk.StringVar(value=ROAD_LABELS[0])
        selector = ttk.Combobox(options, textvariable=self.road_labels, values=ROAD_LABELS, state='readonly', width=20)
        selector.grid(row=2, column=1, columnspan=2, sticky='w', padx=5)
        self.label_selector = selector
        self.city_names = tk.BooleanVar(value=True)
        self.village_names = tk.BooleanVar(value=False)
        self.small_place_names = tk.BooleanVar(value=False)
        for column, (title, variable) in enumerate([('City / town names', self.city_names),
                                                   ('Village names', self.village_names),
                                                   ('Hamlets / suburbs', self.small_place_names)]):
            button = ttk.Checkbutton(options, text=title, variable=variable)
            button.grid(row=3, column=column, sticky='w', padx=5)
            self.controls.append(button)
        ttk.Label(self, text='Outputs: QGIS project, transparent road overlay and optional satellite hybrid TIFF.',
                  style='Muted.TLabel').pack(anchor='w', pady=3)
        ttk.Label(self, text='© OpenStreetMap contributors • Geofabrik • ODbL • Download once, process locally', wraplength=760).pack(anchor='w', pady=4)
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=4)
        self.run_button = ttk.Button(actions, text='Create hybrid map', command=self.start, style='Primary.TButton')
        self.run_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.status = tk.StringVar(value='Choose a new output folder. Satellite imagery is optional.')
        self.process_display = ProcessDisplay(self, self.status)
        self.process_display.pack(fill='x', pady=4)
        self.log = ScrolledText(self, height=12, state='disabled')
        self.log.pack(fill='both', expand=True)
        self.process_display.log = self.log
        self.after(100, self.poll)

    def row(self, title, variable, command=None, parent=None):
        frame = ttk.Frame(parent if parent is not None else self)
        frame.pack(fill='x', pady=3)
        ttk.Label(frame, text=title, width=20).pack(side='left')
        entry = ttk.Entry(frame, textvariable=variable)
        entry.pack(side='left', fill='x', expand=True)
        self.controls.append(entry)
        if command:
            button = ttk.Button(frame, text='Browse…', command=command)
            button.pack(side='left', padx=5)
            self.controls.append(button)

    def choose(self, variable, kind):
        if kind == 'file':
            path = filedialog.askopenfilename(filetypes=[('GeoTIFF', '*.tif *.tiff')])
        elif kind == 'output':
            # Save dialog permits a folder name which does not yet exist.
            path = filedialog.asksaveasfilename(title='Name a NEW output folder', filetypes=[('Folder name', '*')])
        else:
            path = filedialog.askdirectory()
        if path:
            variable.set(path)

    def source_values(self):
        return validate_sources(self.source_text.get('1.0', 'end'))

    def add_source_files(self, paths=None):
        if self.busy:
            return
        if paths is None:
            paths = filedialog.askopenfilenames(title='Add OSM datasets', filetypes=[('OSM extracts', '*.pbf *.osm')])
        lines = self.source_text.get('1.0', 'end').strip().splitlines()
        for path in paths:
            value = str(Path(path).resolve())
            if value not in lines:
                lines.append(value)
        self.source_text.delete('1.0', 'end')
        self.source_text.insert('1.0', '\n'.join(lines))

    def cancel(self):
        self.cancel_event.set()
        if self.cancel_path:
            self.cancel_path.touch()
        self.status.set('Cancelling… waiting for the current network or QGIS operation.')

    def start(self):
        try:
            import math
            imagery = self.imagery.get().strip()
            use_tiff_bounds = bool(imagery and self.use_tiff_bounds.get())
            bounds = None if use_tiff_bounds else self.region_bounds()
            if bounds:
                self.validate_bounds(bounds)
            resolution = float(self.resolution.get())
            if not math.isfinite(resolution) or resolution <= 0:
                raise ValueError('Enter a positive finite metres/pixel value.')
            root = self.qgis.get().strip()
            sources = self.source_values()
            refresh_extract = self.refresh_extract.get()
            qgis_command(root, '')
            if not self.output.get().strip():
                raise ValueError('Choose a new output folder.')
            folder = Path(self.output.get()).expanduser().resolve()
            if folder.exists():
                raise ValueError('Output folder already exists. Choose a new folder to preserve existing files.')
            if imagery:
                imagery = str(Path(imagery).expanduser().resolve())
                if not Path(imagery).is_file():
                    raise ValueError('Satellite TIFF does not exist.')
            folder.mkdir(parents=True)
            render_rasters = self.render_rasters.get()
            encoding = self.encoding.options()
            display = {'road_groups': [key for key, variable in self.road_groups.items() if variable.get()],
                       'road_labels': self.road_labels.get(),
                       'place_types': (['city', 'town'] if self.city_names.get() else [])
                                      + (['village'] if self.village_names.get() else [])
                                      + (['hamlet', 'suburb'] if self.small_place_names.get() else [])}
        except (ValueError, OSError) as error:
            messagebox.showerror('Cannot create hybrid', str(error))
            return
        self.busy = True
        self.process_display.start('Geofabrik hybrid export')
        self.status.set('Reading full satellite TIFF coverage…' if use_tiff_bounds else f'Using bounds: {bounds.west}, {bounds.south}, {bounds.east}, {bounds.north}')
        self.cancel_event.clear()
        self.run_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.label_selector.configure(state='disabled')
        self.encoding.set_busy(True)
        for control in self.controls:
            control.configure(state='disabled')
        def report(message):
            self.events.put(('status', message))
        def work():
            try:
                selected_bounds = bounds
                script = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'hybrid_worker.py'
                if use_tiff_bounds:
                    inspector = start_worker(root, script, '--inspect', imagery)
                    output, _ = inspector.communicate()
                    if inspector.returncode:
                        raise RuntimeError(f'Cannot read satellite TIFF bounds: {output.strip()}')
                    for line in output.splitlines():
                        try:
                            info = json.loads(line)
                        except ValueError:
                            continue
                        if 'bounds' in info:
                            selected_bounds = Bounds(**info['bounds'])
                    if selected_bounds is None:
                        raise RuntimeError('QGIS did not return satellite TIFF bounds.')
                    self.validate_bounds(selected_bounds)
                report(f'Local OSM extraction bounds: {selected_bounds.west}, {selected_bounds.south}, {selected_bounds.east}, {selected_bounds.north}')
                if self.cancel_event.is_set():
                    raise Cancelled()
                with tempfile.TemporaryDirectory(prefix='esa-hybrid-') as temporary:
                    self.cancel_path = Path(temporary) / 'cancel'
                    cache_dir = folder.parent / '.geofabrik-cache'
                    osm_paths, source_infos = [], []
                    for index, source in enumerate(sources, 1):
                        report(f'Preparing Geofabrik source {index}/{len(sources)}: {source}')
                        osm_path, source_info = obtain(source, cache_dir, self.cancel_event, report, refresh=refresh_extract)
                        osm_paths.append(str(osm_path))
                        source_infos.append(source_info)
                    report('Combining selected datasets; overlapping OSM features are included once. Coverage is limited to the selected extracts.')
                    extraction = {'sources': osm_paths, 'cache_dir': str(cache_dir), 'folder': str(folder),
                                  'bounds': vars(selected_bounds), 'cancel': str(self.cancel_path)}
                    job_path = Path(temporary) / 'job.json'
                    job_path.write_text(json.dumps(extraction), encoding='utf-8')
                    import_script = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'geofabrik_worker.py'
                    self.run_worker(root, import_script, job_path, report)
                    extract_info = json.loads((folder / 'extract-info.json').read_text(encoding='utf-8'))
                    counts = extract_info['counts']
                    from datetime import datetime, timezone
                    manifest = {'bounds': vars(selected_bounds), 'bounds_source': 'satellite TIFF' if use_tiff_bounds else 'coordinates',
                                'source': source_infos[0] if len(source_infos) == 1 else {'kind': 'Multiple OSM extracts', 'sources': source_infos},
                                'sources': source_infos, 'duplicates_removed': extract_info['duplicates_removed'], 'created_utc': datetime.now(timezone.utc).isoformat(),
                                'attribution': ATTRIBUTION, 'license': LICENSE, 'resolution_m': resolution,
                                'imagery': imagery, 'roads': counts['roads'], 'places': counts['places'],
                                'coverage': 'Selected roads and settlement nodes; full intersecting ways may extend outside bounds. Rasters cover requested bounds, rounded out to the pixel grid.'}
                    manifest['display'] = display
                    manifest['export_compression'] = encoding
                    (folder / 'osm-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
                    (folder / 'ATTRIBUTION.txt').write_text(
                        f'{ATTRIBUTION}\nhttps://www.openstreetmap.org/copyright\nOSM data: {LICENSE}\n'
                        'Regional OSM extract provided by Geofabrik: https://download.geofabrik.de/\n'
                        'Keep this credit when sharing. Satellite imagery retains its source attribution; for ESA WorldCover, '
                        'preserve the year-specific credit from the imagery download manifest.\n', encoding='utf-8')
                    report(f"Ready: {manifest['roads']} roads and {manifest['places']} places.")
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    job = {'folder': str(folder), 'bounds': vars(selected_bounds), 'resolution': resolution, 'render_rasters': render_rasters,
                           'imagery': imagery, 'cancel': str(self.cancel_path)}
                    job.update(display)
                    job.update(encoding)
                    job_path = Path(temporary) / 'job.json'
                    job_path.write_text(json.dumps(job), encoding='utf-8')
                    script = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'hybrid_worker.py'
                    self.run_worker(root, script, job_path, report)
            except Cancelled:
                report('Cancelled. Completed outputs are kept; choose a new folder for another run.')
            except Exception as error:
                self.events.put(('error', str(error)))
            finally:
                self.cancel_path = None
                self.events.put(('done', self.process_display.stamp()))
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def validate_bounds(bounds):
        bounds.validate()
        if bounds.south < -85 or bounds.north > 85:
            raise ValueError('Hybrid maps support latitudes from -85 to 85 degrees.')

    @staticmethod
    def run_worker(root, script, job_path, report):
        process = start_worker(root, script, job_path)
        for line in process.stdout:
            try:
                report(json.loads(line).get('message', line.strip()))
            except ValueError:
                report(line.strip())
        code = process.wait()
        if code == 2:
            raise Cancelled()
        if code:
            raise RuntimeError('Processing failed. See the log above. Completed regional downloads and imports are kept.')

    def region_bounds(self):
        text = self.coordinates.get().strip()
        if not text:
            return self.bounds_provider()
        bounds = parse_region(text)
        self.coordinates.set(','.join(str(value) for value in vars(bounds).values()))
        return bounds

    def poll(self):
        for _ in range(100):
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'done':
                self.process_display.finish(message)
                self.busy = False
                self.run_button.configure(state='normal')
                self.cancel_button.configure(state='disabled')
                self.label_selector.configure(state='readonly')
                self.encoding.set_busy(False)
                for control in self.controls:
                    control.configure(state='normal')
            else:
                self.status.set(message)
                if kind == 'error':
                    messagebox.showerror('Hybrid map failed', message)
        self.process_display.refresh()
        self.after(100, self.poll)
