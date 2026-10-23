"""Run with python app.py. Build with build.ps1."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from downloader import Cancelled, PRODUCTS, download, preview, parse_region
from mosaic_gui import MosaicTab
from split_gui import SplitTab
from hybrid_gui import HybridTab
from process_timing import ProcessDisplay
from gui_style import configure, section
from region_preview import RegionPreview


def human_size(value):
    return f'{value / 1024**2:,.1f} MiB'


def parse_bounds(text):
    return parse_region(text)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        configure(self)
        self.title('ESA Maps — Map workspace')
        self.geometry('1140x900')
        self.minsize(1100, 860)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.tiles = []
        self.selection = None
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True)
        frame = ttk.Frame(tabs, padding=18)
        tabs.add(frame, text='1  Download')
        self.mosaic = MosaicTab(tabs)
        tabs.add(self.mosaic, text='2  Colour & merge')
        self.splitter = SplitTab(tabs)
        tabs.add(self.splitter, text='3  Split maps')
        self.hybrid = HybridTab(tabs, lambda: self.values()[0])
        tabs.add(self.hybrid, text='4  OSM hybrid')
        ttk.Label(frame, text='ESA WorldCover region downloader', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Free public S3 • No account or AWS keys required • Imagery and land cover').pack(anchor='w', pady=(5, 14))
        region = ttk.Frame(frame)
        region.pack(fill='both', expand=True)
        region.columnconfigure(0, weight=1, uniform='region')
        region.columnconfigure(1, weight=1, uniform='region')
        inputs = ttk.LabelFrame(region, text='Region & dataset — WGS84', padding=10)
        inputs.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        self.region_preview = RegionPreview(region, self.select_region)
        self.region_preview.grid(row=0, column=1, sticky='nsew', padx=(8, 0))
        inputs.columnconfigure(1, weight=1)
        inputs.columnconfigure(3, weight=1)
        ttk.Label(inputs, text='Paste your region: west, south, east, north').grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 5))
        self.pasted_bounds = tk.StringVar(value='-61.528931,-52.484453,-57.615051,-50.934200')
        ttk.Entry(inputs, textvariable=self.pasted_bounds).grid(row=1, column=0, columnspan=3, sticky='ew', padx=5)
        self.apply_button = ttk.Button(inputs, text='Apply', command=self.apply_bounds)
        self.apply_button.grid(row=1, column=3, padx=5, pady=5)
        ttk.Label(inputs, text='Reversed edges are sorted automatically. Search or draw an area on the map to fill these coordinates.',
                  wraplength=470, style='Muted.TLabel').grid(row=2, column=0, columnspan=4, sticky='w', pady=5)
        self.coords = {}
        for i, (key, label, default) in enumerate([
            ('west', 'West', '-61.528931'), ('south', 'South', '-52.484453'),
            ('east', 'East', '-57.615051'), ('north', 'North', '-50.934200')]):
            row, col = divmod(i, 2)
            row += 3
            ttk.Label(inputs, text=label).grid(row=row, column=col * 2, sticky='w', padx=5, pady=5)
            var = tk.StringVar(value=default)
            self.coords[key] = var
            ttk.Entry(inputs, textvariable=var, width=15).grid(row=row, column=col * 2 + 1, padx=5)
        ttk.Label(inputs, text='Dataset year').grid(row=5, column=0, sticky='w', padx=5, pady=8)
        self.year = tk.StringVar(value='2021')
        ttk.Combobox(inputs, textvariable=self.year, values=['2021', '2020'], state='readonly', width=12).grid(row=5, column=1)
        ttk.Label(inputs, text='Product').grid(row=6, column=0, sticky='w', padx=5, pady=8)
        self.product = tk.StringVar(value=next(iter(PRODUCTS)))
        ttk.Combobox(inputs, textvariable=self.product, values=list(PRODUCTS), state='readonly', width=30).grid(row=6, column=1, columnspan=3, sticky='ew')
        ttk.Label(inputs, text='1. Choose a place, apply coordinates, or draw an area.\n'
                  '2. Preview region to check tiles and total size.\n'
                  '3. Download full ESA tiles to your chosen destination.\n\n'
                  'The street map is for orientation. Downloads use the ESA product selected above, in its native CRS.',
                  wraplength=470, style='Muted.TLabel').grid(row=7, column=0, columnspan=4, sticky='nw', pady=(14, 0))
        self.region_inputs = []
        for widget in inputs.winfo_children():
            if widget.winfo_class() in ('TEntry', 'TCombobox'):
                self.region_inputs.append((widget, str(widget.cget('state'))))
        output = section(frame, 'Download destination')
        self.folder = tk.StringVar(value=str(Path.home() / 'Downloads' / 'ESA-WorldCover'))
        ttk.Entry(output, textvariable=self.folder).pack(side='left', fill='x', expand=True)
        ttk.Button(output, text='Output folder…', command=self.browse).pack(side='left', padx=(8, 0))
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=8)
        self.preview_button = ttk.Button(actions, text='1. Preview region', command=self.start_preview)
        self.preview_button.pack(side='left')
        self.download_button = ttk.Button(actions, text='2. Download tiles', command=self.start_download, state='disabled', style='Primary.TButton')
        self.download_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel.set, state='disabled')
        self.cancel_button.pack(side='left')
        self.status = tk.StringVar(value='Your supplied region is loaded. Click “1. Preview region” to check available tiles.')
        self.process_display = ProcessDisplay(frame, self.status, wraplength=740)
        self.process_display.pack(fill='x')
        self.bar = ttk.Progressbar(frame, maximum=100)
        self.bar.pack(fill='x', pady=8)
        self.log = ScrolledText(frame, height=12, state='disabled', font=('Consolas', 9))
        self.log.pack(fill='both', expand=True)
        self.process_display.log = self.log
        self.region_preview.set_region(self.values()[0], PRODUCTS[self.product.get()], online=False)
        self.year.trace_add('write', self.dataset_changed)
        self.product.trace_add('write', self.dataset_changed)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder.set(folder)

    def apply_bounds(self):
        if self.busy:
            return
        try:
            bounds = parse_bounds(self.pasted_bounds.get())
        except ValueError as error:
            messagebox.showerror('Invalid coordinates', str(error))
            return
        self.select_region(bounds)

    def select_region(self, bounds):
        if self.busy:
            return
        for key, value in vars(bounds).items():
            self.coords[key].set(str(value))
        self.pasted_bounds.set(','.join(str(value) for value in vars(bounds).values()))
        self.tiles = []
        self.selection = None
        self.download_button.configure(state='disabled')
        self.status.set('Coordinates applied. Click “1. Preview region” to check available tiles.')
        self.region_preview.set_region(bounds, PRODUCTS[self.product.get()])

    def dataset_changed(self, *_):
        self.tiles = []
        self.selection = None
        self.download_button.configure(state='disabled')
        try:
            self.region_preview.set_region(self.values()[0], PRODUCTS[self.product.get()], online=self.region_preview.online)
        except ValueError:
            pass
        self.status.set('Dataset changed. Preview the region again to check available tiles.')

    def values(self):
        bounds = parse_bounds(','.join(self.coords[key].get().strip()
                                      for key in ('west', 'south', 'east', 'north')))
        for key, value in vars(bounds).items():
            self.coords[key].set(str(value))
        return bounds, int(self.year.get()), PRODUCTS[self.product.get()]

    def launch(self, work, operation='Download region'):
        self.busy = True
        self.process_display.start(operation)
        self.cancel.clear()
        self.preview_button.configure(state='disabled')
        self.download_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.apply_button.configure(state='disabled')
        self.region_preview.set_busy(True)
        for widget, _ in self.region_inputs:
            widget.configure(state='disabled')
        self.bar['value'] = 0
        def run():
            try:
                work()
            except Cancelled:
                self.events.put(('status', 'Cancelled. Completed tiles remain available; retry to skip them.'))
            except Exception as error:
                self.events.put(('error', str(error)))
            finally:
                self.events.put(('done', self.process_display.stamp()))
        threading.Thread(target=run, daemon=True).start()

    def start_preview(self):
        try:
            selection = self.values()
        except ValueError as error:
            messagebox.showerror('Invalid region', str(error))
            return
        self.region_preview.set_region(selection[0], selection[2])
        self.tiles = []
        self.selection = None
        def work():
            bounds, year, product = selection
            tiles = preview(bounds, year, self.cancel, lambda message: self.events.put(('status', message)), product)
            self.events.put(('preview', (selection, tiles)))
        self.launch(work, 'Preview region')

    def start_download(self):
        try:
            if self.values() != self.selection:
                raise ValueError('The region, year or product changed. Preview the region again.')
            if not self.folder.get().strip():
                raise ValueError('Choose an output folder.')
            folder = Path(self.folder.get()).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError) as error:
            messagebox.showerror('Cannot download', str(error))
            return
        tiles, selection = list(self.tiles), self.selection
        def work():
            total = sum(tile.size for tile in tiles)
            completed = 0
            for i, tile in enumerate(tiles, 1):
                self.events.put(('status', f'Downloading {i}/{len(tiles)}: {tile.name}'))
                result = download(tile, folder, self.cancel,
                                  lambda size: self.events.put(('progress', 100 * (completed + size) / total)))
                completed += tile.size
                self.events.put(('log', f'{result}: {tile.name}'))
            bounds, year, product = selection
            manifest = {'dataset': 'ESA WorldCover', 'product': product, 'year': year, 'bounds': vars(bounds),
                        'coverage': 'Full intersecting tiles, not clipped to bounds',
                        'attribution': f'© ESA WorldCover project {year} / Contains modified Copernicus Sentinel data ({year}) processed by ESA WorldCover consortium',
                        'license': 'https://creativecommons.org/licenses/by/4.0/',
                        'tiles': [vars(tile) for tile in tiles]}
            # A unique manifest preserves earlier region downloads in the same folder.
            import time
            path = folder / f'region-{time.time_ns()}.json'
            path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            self.events.put(('status', f'Complete: {len(tiles)} tiles available in {folder}'))
        self.launch(work, 'Download tiles')

    def poll(self):
        for _ in range(200):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                self.bar['value'] = value
            elif kind in ('status', 'error'):
                self.status.set(value)
                if kind == 'error':
                    messagebox.showerror('Download error', value)
            elif kind == 'preview':
                self.selection, self.tiles = value
                self.status.set(f'{len(self.tiles)} available tiles • {human_size(sum(t.size for t in self.tiles))} total' if self.tiles else 'No WorldCover tiles available in this region.')
                self.process_display.append('\n'.join(f'{t.name}  {human_size(t.size)}' for t in self.tiles))
                self.region_preview.set_tiles(self.tiles)
            elif kind == 'log':
                self.process_display.append(value)
            elif kind == 'done':
                self.process_display.finish(value)
                self.busy = False
                self.preview_button.configure(state='normal')
                self.download_button.configure(state='normal' if self.tiles else 'disabled')
                self.cancel_button.configure(state='disabled')
                self.apply_button.configure(state='normal')
                self.region_preview.set_busy(False)
                for widget, state in self.region_inputs:
                    widget.configure(state=state)
        self.process_display.refresh()
        self.after(100, self.poll)

    def close(self):
        if self.hybrid.busy:
            self.hybrid.cancel()
            return
        if self.splitter.busy:
            self.splitter.cancel()
            return
        if self.mosaic.busy:
            self.mosaic.cancel()
            return
        if self.busy:
            self.cancel.set()
            self.status.set('Cancelling… wait for the current network request to finish, then close.')
        else:
            self.splitter.cleanup()
            self.destroy()


if __name__ == '__main__':
    App().mainloop()
