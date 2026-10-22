"""Run with python app.py. Build with build.ps1."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from downloader import Bounds, Cancelled, PRODUCTS, download, preview
from mosaic_gui import MosaicTab
from split_gui import SplitTab


def human_size(value):
    return f'{value / 1024**2:,.1f} MiB'


def parse_bounds(text):
    parts = text.strip().split(',')
    if len(parts) != 4:
        raise ValueError('Paste four comma-separated numbers: west, south, east, north.')
    try:
        bounds = Bounds(*(float(part.strip()) for part in parts))
    except ValueError:
        raise ValueError('Use decimal numbers separated by commas, for example -61.528931,-52.484453,-57.615051,-50.934200.')
    bounds.validate()
    return bounds


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('ESA Maps — Download & Merge')
        self.geometry('850x860')
        self.minsize(820, 820)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.tiles = []
        self.selection = None
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True)
        frame = ttk.Frame(tabs, padding=18)
        tabs.add(frame, text='Download region')
        self.mosaic = MosaicTab(tabs)
        tabs.add(self.mosaic, text='Colour balance & merge')
        self.splitter = SplitTab(tabs)
        tabs.add(self.splitter, text='Split maps visually')
        ttk.Label(frame, text='ESA WorldCover region downloader', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Free public S3 • No account or AWS keys required • Imagery and land cover').pack(anchor='w', pady=(5, 14))
        inputs = ttk.LabelFrame(frame, text='Region — decimal degrees (WGS84)', padding=12)
        inputs.pack(fill='x')
        ttk.Label(inputs, text='Paste your region: west, south, east, north').grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 5))
        self.pasted_bounds = tk.StringVar(value='-61.528931,-52.484453,-57.615051,-50.934200')
        ttk.Entry(inputs, textvariable=self.pasted_bounds).grid(row=1, column=0, columnspan=3, sticky='ew', padx=5)
        self.apply_button = ttk.Button(inputs, text='Use these coordinates', command=self.apply_bounds)
        self.apply_button.grid(row=1, column=3, padx=5, pady=5)
        ttk.Label(inputs, text='Click “Use these coordinates”, or edit the individual edges below.').grid(row=2, column=0, columnspan=4, sticky='w', pady=5)
        self.coords = {}
        for i, (key, label, default) in enumerate([
            ('west', 'West (left) longitude', '-61.528931'), ('south', 'South (bottom) latitude', '-52.484453'),
            ('east', 'East (right) longitude', '-57.615051'), ('north', 'North (top) latitude', '-50.934200')]):
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
        ttk.Combobox(inputs, textvariable=self.product, values=list(PRODUCTS), state='readonly', width=43).grid(row=6, column=1, columnspan=3, sticky='w')
        ttk.Label(frame, text='Original ESA tiles download in their native CRS. Merge and split outputs use EPSG:3857.').pack(anchor='w', pady=10)
        output = ttk.Frame(frame)
        output.pack(fill='x')
        self.folder = tk.StringVar(value=str(Path.home() / 'Downloads' / 'ESA-WorldCover'))
        ttk.Entry(output, textvariable=self.folder).pack(side='left', fill='x', expand=True)
        ttk.Button(output, text='Output folder…', command=self.browse).pack(side='left', padx=(8, 0))
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=12)
        self.preview_button = ttk.Button(actions, text='1. Preview region', command=self.start_preview)
        self.preview_button.pack(side='left')
        self.download_button = ttk.Button(actions, text='2. Download tiles', command=self.start_download, state='disabled')
        self.download_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel.set, state='disabled')
        self.cancel_button.pack(side='left')
        self.status = tk.StringVar(value='Your supplied region is loaded. Click “1. Preview region” to check available tiles.')
        ttk.Label(frame, textvariable=self.status, wraplength=740).pack(anchor='w')
        self.bar = ttk.Progressbar(frame, maximum=100)
        self.bar.pack(fill='x', pady=8)
        self.log = ScrolledText(frame, height=12, state='disabled', font=('Consolas', 9))
        self.log.pack(fill='both', expand=True)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder.set(folder)

    def apply_bounds(self):
        try:
            bounds = parse_bounds(self.pasted_bounds.get())
        except ValueError as error:
            messagebox.showerror('Invalid coordinates', str(error))
            return
        for key, value in vars(bounds).items():
            self.coords[key].set(str(value))
        self.tiles = []
        self.selection = None
        self.download_button.configure(state='disabled')
        self.status.set('Coordinates applied. Click “1. Preview region” to check available tiles.')

    def values(self):
        try:
            bounds = Bounds(**{key: float(var.get().strip()) for key, var in self.coords.items()})
        except ValueError:
            raise ValueError('Enter decimal coordinates, for example -0.6 or 51.2.')
        bounds.validate()
        return bounds, int(self.year.get()), PRODUCTS[self.product.get()]

    def launch(self, work):
        self.busy = True
        self.cancel.clear()
        self.preview_button.configure(state='disabled')
        self.download_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.apply_button.configure(state='disabled')
        self.bar['value'] = 0
        def run():
            try:
                work()
            except Cancelled:
                self.events.put(('status', 'Cancelled. Completed tiles remain available; retry to skip them.'))
            except Exception as error:
                self.events.put(('error', str(error)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=run, daemon=True).start()

    def start_preview(self):
        try:
            selection = self.values()
        except ValueError as error:
            messagebox.showerror('Invalid region', str(error))
            return
        self.tiles = []
        self.selection = None
        def work():
            bounds, year, product = selection
            tiles = preview(bounds, year, self.cancel, lambda message: self.events.put(('status', message)), product)
            self.events.put(('preview', (selection, tiles)))
        self.launch(work)

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
        self.launch(work)

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
                self.log.configure(state='normal')
                self.log.delete('1.0', 'end')
                self.log.insert('end', '\n'.join(f'{t.name}  {human_size(t.size)}' for t in self.tiles) + '\n')
                self.log.configure(state='disabled')
            elif kind == 'log':
                self.log.configure(state='normal')
                self.log.insert('end', value + '\n')
                self.log.see('end')
                self.log.configure(state='disabled')
            elif kind == 'done':
                self.busy = False
                self.preview_button.configure(state='normal')
                self.download_button.configure(state='normal' if self.tiles else 'disabled')
                self.cancel_button.configure(state='disabled')
                self.apply_button.configure(state='normal')
        self.after(100, self.poll)

    def close(self):
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
