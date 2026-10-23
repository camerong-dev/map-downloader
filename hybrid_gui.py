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

from downloader import Bounds, Cancelled
from mosaic_runtime import find_qgis, qgis_command, start_worker
from osm_download import ATTRIBUTION, LICENSE, parse_region
from geofabrik import DEFAULT_SOURCE, obtain, validate_source
from hybrid_style import DEFAULT_ROADS, ROAD_GROUPS, ROAD_LABELS
from export_gui import CompressionControls


class HybridTab(ttk.Frame):
    def __init__(self, parent, bounds_provider):
        super().__init__(parent, padding=18)
        self.bounds_provider = bounds_provider
        self.busy = False
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.cancel_path = None
        ttk.Label(self, text='OSM hybrid overlay', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(self, text='By default, OSM covers the selected satellite TIFF, including split TIFFs.',
                  wraplength=760).pack(anchor='w', pady=8)
        ttk.Label(self, text='Offline roads and place names • White labels with dark halos • Scale-aware QGIS project',
                  wraplength=760).pack(anchor='w', pady=4)
        self.qgis = tk.StringVar(value=find_qgis())
        self.osm_source = tk.StringVar(value=DEFAULT_SOURCE)
        self.refresh_extract = tk.BooleanVar(value=False)
        self.imagery = tk.StringVar()
        self.output = tk.StringVar()
        self.resolution = tk.StringVar(value='10')
        self.controls = []
        self.coordinates = tk.StringVar()
        self.row('West, south, east, north', self.coordinates)
        ttk.Label(self, text='Four comma-separated numbers. Reversed west/east or south/north edges are sorted automatically.',
                  wraplength=760).pack(anchor='w')
        self.row('QGIS folder', self.qgis, lambda: self.choose(self.qgis, 'directory'))
        self.row('Geofabrik URL / local PBF', self.osm_source, lambda: self.choose(self.osm_source, 'osm'))
        refresh = ttk.Checkbutton(self, text='Refresh cached Geofabrik download (otherwise reuse offline)', variable=self.refresh_extract)
        refresh.pack(anchor='w')
        self.controls.append(refresh)
        ttk.Label(self, text='Default: West Sussex. Choose a larger extract if your TIFF extends beyond that county.', wraplength=760).pack(anchor='w')
        self.row('Satellite TIFF (optional)', self.imagery, lambda: self.choose(self.imagery, 'file'))
        self.use_tiff_bounds = tk.BooleanVar(value=True)
        extent_toggle = ttk.Checkbutton(self, text='Use full satellite TIFF bounds (overrides coordinates above)', variable=self.use_tiff_bounds)
        extent_toggle.pack(anchor='w')
        self.controls.append(extent_toggle)
        ttk.Label(self, text='Select a rendered RGB TIFF from Colour balance & merge for baked satellite + labels.\n'
                  'Leave blank for an overlay only. Originals are kept.', wraplength=760).pack(anchor='w', pady=5)
        self.row('New output folder', self.output, lambda: self.choose(self.output, 'output'))
        self.row('Export metres/pixel', self.resolution)
        self.render_rasters = tk.BooleanVar(value=True)
        raster_toggle = ttk.Checkbutton(self, text='Also export fixed-resolution GeoTIFFs', variable=self.render_rasters)
        raster_toggle.pack(anchor='w')
        self.controls.append(raster_toggle)
        self.encoding = CompressionControls(self, allow_source=True)
        self.encoding.pack(fill='x', pady=4)
        options = ttk.LabelFrame(self, text='Roads and names to show', padding=6)
        options.pack(fill='x', pady=5)
        self.road_groups = {}
        for index, (key, title) in enumerate(ROAD_GROUPS.items()):
            variable = tk.BooleanVar(value=key in DEFAULT_ROADS)
            self.road_groups[key] = variable
            button = ttk.Checkbutton(options, text=title, variable=variable)
            button.grid(row=index // 3, column=index % 3, sticky='w', padx=5)
            self.controls.append(button)
        ttk.Label(options, text='Road labels').grid(row=2, column=0, sticky='w', padx=5)
        self.road_labels = tk.StringVar(value=ROAD_LABELS[0])
        selector = ttk.Combobox(options, textvariable=self.road_labels, values=ROAD_LABELS, state='readonly', width=20)
        selector.grid(row=2, column=1, sticky='w', padx=5)
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
        ttk.Label(self, text='Open hybrid.qgz in QGIS for sharp roads and labels that adapt as you zoom.\n'
                  'GeoTIFFs have fixed labels: 10 m/pixel is the default; try 5 for smaller areas.\n'
                  'Large GeoTIFFs render in blocks at the requested resolution; allow extra time and disk space.', wraplength=760).pack(anchor='w', pady=10)
        ttk.Label(self, text='Geofabrik files are downloaded once and processed locally; no Overpass queries.\n'
                  '© OpenStreetMap contributors • Data from Geofabrik • ODbL', wraplength=760).pack(anchor='w', pady=6)
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=8)
        self.run_button = ttk.Button(actions, text='Create hybrid from Geofabrik', command=self.start)
        self.run_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.status = tk.StringVar(value='Choose a new output folder. Satellite imagery is optional.')
        ttk.Label(self, textvariable=self.status, wraplength=760).pack(anchor='w', pady=8)
        self.log = ScrolledText(self, height=12, state='disabled')
        self.log.pack(fill='both', expand=True)
        self.after(100, self.poll)

    def row(self, title, variable, command=None):
        frame = ttk.Frame(self)
        frame.pack(fill='x', pady=5)
        ttk.Label(frame, text=title, width=23).pack(side='left')
        entry = ttk.Entry(frame, textvariable=variable)
        entry.pack(side='left', fill='x', expand=True)
        self.controls.append(entry)
        if command:
            button = ttk.Button(frame, text='Browse…', command=command)
            button.pack(side='left', padx=5)
            self.controls.append(button)

    def choose(self, variable, kind):
        if kind == 'osm':
            path = filedialog.askopenfilename(title='Select downloaded Geofabrik PBF', filetypes=[('OSM extract', '*.pbf *.osm')])
        elif kind == 'file':
            path = filedialog.askopenfilename(filetypes=[('GeoTIFF', '*.tif *.tiff')])
        elif kind == 'output':
            # Save dialog permits a folder name which does not yet exist.
            path = filedialog.asksaveasfilename(title='Name a NEW output folder', filetypes=[('Folder name', '*')])
        else:
            path = filedialog.askdirectory()
        if path:
            variable.set(path)

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
            source = validate_source(self.osm_source.get().strip())
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
                    osm_path, source_info = obtain(source, cache_dir, self.cancel_event, report, refresh=refresh_extract)
                    report('Coverage is limited to the selected regional extract. Use a larger extract for maps crossing its boundary.')
                    extraction = {'source': str(osm_path), 'cache_dir': str(cache_dir), 'folder': str(folder),
                                  'bounds': vars(selected_bounds), 'cancel': str(self.cancel_path)}
                    job_path = Path(temporary) / 'job.json'
                    job_path.write_text(json.dumps(extraction), encoding='utf-8')
                    import_script = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'geofabrik_worker.py'
                    self.run_worker(root, import_script, job_path, report)
                    counts = json.loads((folder / 'extract-info.json').read_text(encoding='utf-8'))['counts']
                    from datetime import datetime, timezone
                    manifest = {'bounds': vars(selected_bounds), 'bounds_source': 'satellite TIFF' if use_tiff_bounds else 'coordinates', 'source': source_info, 'created_utc': datetime.now(timezone.utc).isoformat(),
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
                self.events.put(('done', ''))
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
                self.busy = False
                self.run_button.configure(state='normal')
                self.cancel_button.configure(state='disabled')
                self.label_selector.configure(state='readonly')
                self.encoding.set_busy(False)
                for control in self.controls:
                    control.configure(state='normal')
            else:
                self.status.set(message)
                self.log.configure(state='normal')
                self.log.insert('end', message + '\n')
                self.log.see('end')
                self.log.configure(state='disabled')
                if kind == 'error':
                    messagebox.showerror('Hybrid map failed', message)
        self.after(100, self.poll)
