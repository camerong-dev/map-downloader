"""Tkinter controls for QGIS/GDAL colour balancing and mosaicking."""
import json
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from mosaic_runtime import find_qgis, run_qgis_worker, worker_script, request_cancel
from downloader import Cancelled
from export_gui import CompressionControls
from process_timing import ProcessDisplay
from gui_style import section


class MosaicTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=18)
        self.busy = False
        self.files = []
        self.events = queue.Queue()
        self.cancel_path = None
        ttk.Label(self, text='Colour balance & merge TIFFs', style='Title.TLabel').pack(anchor='w')
        ttk.Label(self, text='Output: EPSG:3857 • Full-detail 8-bit RGB GeoTIFF with transparency and viewing overviews.').pack(anchor='w', pady=8)
        runtime = ttk.Frame(self)
        runtime.pack(fill='x', pady=5)
        ttk.Label(runtime, text='QGIS folder').pack(side='left', padx=(0, 8))
        self.qgis = tk.StringVar(value=find_qgis())
        ttk.Entry(runtime, textvariable=self.qgis).pack(side='left', fill='x', expand=True)
        ttk.Button(runtime, text='Browse…', command=self.browse_qgis).pack(side='left', padx=5)
        ttk.Label(self, text='Input TIFFs — first file is the reference when histogram matching is selected').pack(anchor='w', pady=(12, 4))
        self.listbox = tk.Listbox(self, height=6, selectmode='extended')
        self.listbox.pack(fill='both', expand=True)
        controls = ttk.Frame(self)
        controls.pack(fill='x', pady=8)
        self.edit_buttons = []
        for title, command in [('Add TIFFs…', self.add_files), ('Add folder…', self.add_folder),
                               ('Remove selected', self.remove), ('Use selected as reference', self.reference)]:
            button = ttk.Button(controls, text=title, command=command)
            button.pack(side='left', padx=(0, 6))
            self.edit_buttons.append(button)
        options = ttk.LabelFrame(self, text='Colour settings', padding=10)
        options.pack(fill='x')
        self.mode = tk.StringVar(value='fixed')
        ttk.Radiobutton(options, text='Your QGIS colours — fixed RGB stretch (default)',
                        variable=self.mode, value='fixed').pack(anchor='w')
        fixedrow = ttk.Frame(options)
        fixedrow.pack(fill='x', pady=4)
        self.minimum = tk.StringVar(value='0.005')
        self.maximum = tk.StringVar(value='0.40')
        for label, variable in [('Minimum', self.minimum), ('Maximum', self.maximum)]:
            ttk.Label(fixedrow, text=label).pack(side='left', padx=(0, 6))
            ttk.Entry(fixedrow, textvariable=variable, width=10).pack(side='left', padx=(0, 15))
        ttk.Radiobutton(options, text='Shared colour stretch — one consistent 2–98% range for all tiles',
                        variable=self.mode, value='shared').pack(anchor='w')
        ttk.Radiobutton(options, text='Match colours to reference tile — stronger correction; may alter real landscape colours',
                        variable=self.mode, value='match').pack(anchor='w')
        bandrow = ttk.Frame(options)
        bandrow.pack(fill='x', pady=5)
        ttk.Label(bandrow, text='RGB band numbers (WorldCover RGBNIR: 1,2,3)').pack(side='left')
        self.bands = tk.StringVar(value='1,2,3')
        ttk.Entry(bandrow, textvariable=self.bands, width=10).pack(side='left', padx=8)
        ttk.Label(options, text='RGB imagery only. All inputs must have the same CRS and pixel size. Originals are kept.').pack(anchor='w')
        outrow = section(self, 'Merged output')
        self.output = tk.StringVar()
        pathrow = ttk.Frame(outrow)
        pathrow.pack(fill='x')
        ttk.Entry(pathrow, textvariable=self.output).pack(side='left', fill='x', expand=True)
        ttk.Button(pathrow, text='Save merged TIFF…', command=self.choose_output).pack(side='left', padx=5)
        self.encoding = CompressionControls(outrow)
        self.encoding.pack(fill='x', pady=(0, 8))
        actions = ttk.Frame(self)
        actions.pack(fill='x')
        self.run_button = ttk.Button(actions, text='Balance colours & merge', command=self.start, style='Primary.TButton')
        self.run_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.status = tk.StringVar(value='Add imagery TIFFs and choose a new output file.')
        self.process_display = ProcessDisplay(self, self.status, wraplength=780)
        self.process_display.pack(fill='x', pady=8)
        self.progress = ttk.Progressbar(self, maximum=100)
        self.progress.pack(fill='x')
        self.log = ScrolledText(self, height=12, state='disabled')
        self.log.pack(fill='both', expand=True, pady=8)
        self.process_display.log = self.log
        self.after(100, self.poll)

    def browse_qgis(self):
        path = filedialog.askdirectory(title='Select QGIS installation folder')
        if path:
            self.qgis.set(path)

    def refresh(self):
        self.listbox.delete(0, 'end')
        for i, path in enumerate(self.files):
            self.listbox.insert('end', f'{"Reference: " if i == 0 else ""}{path}')

    def add(self, paths):
        if self.busy:
            return
        for path in paths:
            path = str(Path(path).resolve())
            if path not in self.files:
                self.files.append(path)
        self.refresh()

    def add_files(self):
        self.add(filedialog.askopenfilenames(title='Select imagery TIFFs', filetypes=[('GeoTIFF', '*.tif *.tiff')]))

    def add_folder(self):
        path = filedialog.askdirectory(title='Select folder containing TIFFs')
        if path:
            self.add(sorted(p for p in Path(path).iterdir() if p.suffix.lower() in ('.tif', '.tiff')))

    def remove(self):
        for index in reversed(self.listbox.curselection()):
            del self.files[index]
        self.refresh()

    def reference(self):
        selected = self.listbox.curselection()
        if selected:
            self.files.insert(0, self.files.pop(selected[0]))
            self.refresh()

    def choose_output(self):
        path = filedialog.asksaveasfilename(title='Save merged GeoTIFF', defaultextension='.tif', filetypes=[('GeoTIFF', '*.tif')])
        if path:
            self.output.set(path)

    def cancel(self):
        if self.cancel_path:
            request_cancel(self.cancel_path)
            self.status.set('Cancelling safely… temporary processing files will be removed.')

    def start(self):
        if self.busy:
            return
        workspace = None
        try:
            if not self.files:
                raise ValueError('Add at least one imagery TIFF.')
            if not self.output.get().strip():
                raise ValueError('Choose an output file.')
            output = Path(self.output.get()).resolve()
            if output.exists():
                raise ValueError('Choose a new output filename; existing files are kept.')
            bands = [int(value.strip()) for value in self.bands.get().split(',')]
            if len(bands) != 3 or min(bands) < 1:
                raise ValueError('Enter three positive band numbers, for example 1,2,3.')
            minimum, maximum = 0.005, 0.40
            if self.mode.get() == 'fixed':
                import math
                minimum, maximum = float(self.minimum.get()), float(self.maximum.get())
                if not math.isfinite(minimum) or not math.isfinite(maximum) or minimum >= maximum:
                    raise ValueError('Enter a finite minimum smaller than the maximum.')
            root = self.qgis.get()
            # Validate before starting the background task.
            from mosaic_runtime import qgis_command
            qgis_command(root, '')
            encoding = self.encoding.options()
            output.parent.mkdir(parents=True, exist_ok=True)
            workspace = tempfile.TemporaryDirectory(prefix='esa-job-', dir=output.parent)
            self.cancel_path = Path(workspace.name) / 'cancel'
            job = {'inputs': list(self.files), 'output': str(output), 'bands': bands,
                   'mode': self.mode.get(), 'minimum': minimum, 'maximum': maximum,
                   'cancel': str(self.cancel_path)}
            job.update(encoding)
            job_path = Path(workspace.name) / 'job.json'
            job_path.write_text(json.dumps(job), encoding='utf-8')
        except (ValueError, OSError) as error:
            if workspace is not None:
                workspace.cleanup()
            self.cancel_path = None
            messagebox.showerror('Cannot process TIFFs', str(error))
            return
        self.busy = True
        self.process_display.start('Colour balance & merge')
        self.encoding.set_busy(True)
        self.progress['value'] = 0
        self.status.set('Starting QGIS raster engine…')
        self.run_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        for button in self.edit_buttons:
            button.configure(state='disabled')
        script = worker_script('mosaic_worker.py')
        def work():
            try:
                run_qgis_worker(root, script, job_path,
                                on_event=lambda event: self.events.put(('message', event)))
            except Cancelled:
                self.events.put(('message', {'message': 'Cancelled. Original TIFFs are unchanged.'}))
            except Exception as error:
                self.events.put(('error', str(error)))
            finally:
                try:
                    workspace.cleanup()
                finally:
                    self.events.put(('done', self.process_display.stamp()))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        for _ in range(100):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'done':
                self.process_display.finish(data)
                self.busy = False
                self.encoding.set_busy(False)
                self.cancel_path = None
                self.run_button.configure(state='normal')
                self.cancel_button.configure(state='disabled')
                for button in self.edit_buttons:
                    button.configure(state='normal')
            else:
                message = data if kind == 'error' else data.get('message', '')
                self.status.set(message)
                if kind == 'message' and data.get('progress') is not None:
                    self.progress['value'] = data['progress']
                if kind == 'error':
                    messagebox.showerror('Raster processing failed', message)
        self.process_display.refresh()
        self.after(100, self.poll)
