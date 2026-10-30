"""Visual grid selection for TIFF splitting, with a small map preview."""
import json
import math
from pathlib import Path
import queue
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from mosaic_runtime import find_qgis, qgis_command, start_worker
from export_gui import CompressionControls
from process_timing import ProcessDisplay


class SplitTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=14)
        self.busy = False
        self.inputs = []
        self.info = None
        self.photo = None
        self.display_photo = None
        self.zoom_levels = [(1, 4), (1, 2), (1, 1), (2, 1), (3, 1), (4, 1), (6, 1), (8, 1)]
        self.zoom_index = 2
        self.fit_following = True
        self.pan_x = self.pan_y = 0
        self._display_key = None
        self._drag_origin = None
        self.selected = set()
        self.polygon = []
        self.polygon_closed = False
        self.selection_mode = tk.StringVar(value='Grid')
        self.events = queue.Queue()
        self.workspace = None
        self.cancel_path = None
        ttk.Label(self, text='Split maps visually', style='Title.TLabel').pack(anchor='w')
        ttk.Label(self, text='Select grid cells or draw a polygon to trace the area you want to export.').pack(anchor='w', pady=5)
        runtime = ttk.Frame(self)
        runtime.pack(fill='x', pady=4)
        ttk.Label(runtime, text='QGIS folder').pack(side='left', padx=(0, 8))
        self.qgis = tk.StringVar(value=find_qgis())
        ttk.Entry(runtime, textvariable=self.qgis).pack(side='left', fill='x', expand=True)
        ttk.Button(runtime, text='Browse…', command=self.browse_qgis).pack(side='left', padx=5)
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=5)
        self.load_button = ttk.Button(actions, text='Load TIFFs & preview…', command=self.load)
        self.load_button.pack(side='left')
        self.source_label = tk.StringVar(value='No map loaded')
        ttk.Label(actions, textvariable=self.source_label).pack(side='left', padx=10)
        grid = ttk.Frame(self)
        grid.pack(fill='x', pady=5)
        self.rows = tk.StringVar(value='2')
        self.columns = tk.StringVar(value='2')
        self.grid_controls = []
        for label, variable in [('Rows', self.rows), ('Columns', self.columns)]:
            ttk.Label(grid, text=label).pack(side='left', padx=(0, 5))
            spin = ttk.Spinbox(grid, from_=1, to=30, textvariable=variable, width=5)
            spin.pack(side='left', padx=(0, 12))
            self.grid_controls.append(spin)
            variable.trace_add('write', self.grid_changed)
        ttk.Button(grid, text='Select all', command=self.select_all).pack(side='left')
        ttk.Button(grid, text='Clear selection', command=self.clear_selection).pack(side='left', padx=5)
        drawing = grid
        for mode in ('Grid', 'Draw polygon'):
            control = ttk.Radiobutton(drawing, text=mode, value=mode, variable=self.selection_mode, command=self.draw)
            control.pack(side='left', padx=(0, 10))
            self.grid_controls.append(control)
        for label, command in [('Finish outline', self.finish_polygon), ('Undo point', self.undo_point)]:
            control = ttk.Button(drawing, text=label, command=command)
            control.pack(side='left', padx=4)
            self.grid_controls.append(control)
        view = ttk.Frame(self)
        view.pack(fill='x', pady=2)
        ttk.Button(view, text='−', width=3, command=lambda: self.change_zoom(-1)).pack(side='left')
        ttk.Button(view, text='+', width=3, command=lambda: self.change_zoom(1)).pack(side='left', padx=5)
        ttk.Button(view, text='Fit map', command=self.fit_map).pack(side='left')
        self.zoom_label = tk.StringVar(value='Preview 100%')
        ttk.Label(view, textvariable=self.zoom_label).pack(side='left', padx=10)
        ttk.Label(view, text='Wheel: zoom • Drag: pan • Polygon: click points, then Finish outline').pack(side='left')
        self.canvas = tk.Canvas(self, background='#23313b', height=440, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, pady=8)
        self.canvas.bind('<Configure>', self.canvas_resized)
        self.canvas.bind('<ButtonPress-1>', self.pan_start)
        self.canvas.bind('<B1-Motion>', self.pan_move)
        self.canvas.bind('<ButtonRelease-1>', self.pan_end)
        self.canvas.bind('<ButtonPress-2>', self.pan_start)
        self.canvas.bind('<B2-Motion>', self.pan_move)
        self.canvas.bind('<ButtonRelease-2>', self.pan_end)
        self.canvas.bind('<MouseWheel>', self.wheel_zoom)
        self.canvas.bind('<Button-4>', self.wheel_zoom)
        self.canvas.bind('<Button-5>', self.wheel_zoom)
        self.canvas.bind('<Key-plus>', lambda _: self.change_zoom(1))
        self.canvas.bind('<Key-equal>', lambda _: self.change_zoom(1))
        self.canvas.bind('<Key-minus>', lambda _: self.change_zoom(-1))
        self.canvas.bind('<Motion>', self.hover)
        self.canvas.bind('<Return>', lambda _: self.finish_polygon())
        self.canvas.bind('<BackSpace>', lambda _: self.undo_point())
        self.summary = tk.StringVar(value='The map and split lines will appear here.')
        ttk.Label(self, textvariable=self.summary, wraplength=780).pack(anchor='w')
        self.details = tk.StringVar(value='Grid areas follow the raster pixels; north is at the top.')
        ttk.Label(self, textvariable=self.details, wraplength=780).pack(anchor='w', pady=4)
        out = ttk.Frame(self)
        out.pack(fill='x', pady=7)
        self.output = tk.StringVar()
        ttk.Entry(out, textvariable=self.output).pack(side='left', fill='x', expand=True)
        ttk.Button(out, text='Choose output location…', command=self.choose_output).pack(side='left', padx=5)
        ttk.Label(self, text='Exports at source detail in EPSG:3857. Other CRSs are reprojected; preview colours do not alter exports.').pack(anchor='w')
        overview = ttk.Frame(self)
        overview.pack(fill='x', pady=6)
        self.create_overview = tk.BooleanVar(value=True)
        self.overview_resolution = tk.StringVar(value='100')
        self.overview_check = ttk.Checkbutton(overview, text='Create whole-region overview', variable=self.create_overview,
                                              command=self.overview_changed)
        self.overview_check.pack(side='left')
        self.overview_combo = ttk.Combobox(overview, textvariable=self.overview_resolution,
                                          values=['50', '100', '250', '500'], width=7, state='readonly')
        self.overview_combo.pack(side='left', padx=8)
        ttk.Label(overview, text='m/pixel (ESA Data is 10 m/px) • EPSG:3857 • Includes the entire loaded region').pack(side='left')
        self.encoding = CompressionControls(self)
        self.encoding.pack(fill='x', pady=5)
        bottom = ttk.Frame(self)
        bottom.pack(fill='x', pady=8)
        self.export_button = ttk.Button(bottom, text='Export selected areas', command=self.export, state='disabled', style='Primary.TButton')
        self.export_button.pack(side='left')
        self.combine_grid = tk.BooleanVar(value=True)
        combine = ttk.Checkbutton(bottom, text='Export grid as one TIFF', variable=self.combine_grid)
        combine.pack(side='left', padx=8)
        self.grid_controls.append(combine)
        self.cancel_button = ttk.Button(bottom, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.progress = ttk.Progressbar(bottom, maximum=100)
        self.progress.pack(side='left', fill='x', expand=True)
        self.status = tk.StringVar(value='Ready')
        self.process_display = ProcessDisplay(self, self.status, wraplength=780)
        self.process_display.pack(fill='x')
        self.log = ScrolledText(self, height=12, state='disabled')
        self.log.pack(fill='x', pady=(4, 0))
        self.process_display.log = self.log
        self.after(100, self.poll)

    def browse_qgis(self):
        root = filedialog.askdirectory(title='QGIS installation folder')
        if root:
            self.qgis.set(root)

    def prepare_display(self):
        if self.photo is None:
            return
        numerator, denominator = self.zoom_levels[self.zoom_index]
        key = str(self.photo), numerator, denominator
        if key != self._display_key:
            self.display_photo = (self.photo if numerator == denominator == 1 else
                                  self.photo.zoom(numerator) if denominator == 1 else
                                  self.photo.subsample(denominator))
            self._display_key = key
        self.zoom_label.set(f'Preview {100 * numerator / denominator:g}%')

    def fit_map(self):
        self.fit_following = True
        self.pan_x = self.pan_y = 0
        if self.photo:
            scale = min(self.canvas.winfo_width() / self.photo.width(), self.canvas.winfo_height() / self.photo.height())
            self.zoom_index = max((i for i, (n, d) in enumerate(self.zoom_levels) if n / d <= min(1, scale)), default=0)
        self.draw()

    def canvas_resized(self, _event):
        if self.fit_following:
            self.fit_map()
        else:
            self.draw()

    def change_zoom(self, direction, x=None, y=None):
        if not self.photo or not self.info:
            return
        new_index = max(0, min(len(self.zoom_levels) - 1, self.zoom_index + direction))
        if new_index == self.zoom_index:
            return
        self.draw()
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        x, y = cw / 2 if x is None else x, ch / 2 if y is None else y
        left, top, width, height = self.image_box
        u, v = (x - left) / width, (y - top) / height
        self.zoom_index = new_index
        self.fit_following = False
        self.prepare_display()
        self.pan_x = x - cw / 2 + (0.5 - u) * self.display_photo.width()
        self.pan_y = y - ch / 2 + (0.5 - v) * self.display_photo.height()
        self.draw()

    def wheel_zoom(self, event):
        number = getattr(event, 'num', None)
        delta = getattr(event, 'delta', 0)
        if number in (4, 5) or delta:
            self.change_zoom(1 if number == 4 or delta > 0 else -1, event.x, event.y)
        return 'break'

    def pan_start(self, event):
        self.canvas.focus_set()
        self._drag_origin = (event.x, event.y, self.pan_x, self.pan_y, getattr(event, 'num', 1))
        self._dragged = False

    def pan_move(self, event):
        if self._drag_origin is None or not self.photo:
            return
        x, y, px, py, _ = self._drag_origin
        dx, dy = event.x - x, event.y - y
        if self._dragged or math.hypot(dx, dy) > 5:
            self._dragged = True
            self.fit_following = False
            self.pan_x, self.pan_y = px + dx, py + dy
            self.canvas.configure(cursor='fleur')
            self.draw()

    def pan_end(self, event):
        origin = self._drag_origin
        self._drag_origin = None
        self.canvas.configure(cursor='')
        if origin is not None and not self._dragged and origin[4] == 1:
            self.click(event)

    def shape(self):
        rows, columns = int(self.rows.get()), int(self.columns.get())
        if not 1 <= rows <= 30 or not 1 <= columns <= 30:
            raise ValueError('Rows and columns must be between 1 and 30.')
        if self.info and (rows > self.info['height'] or columns > self.info['width']):
            raise ValueError('The grid has more areas than available pixels.')
        return rows, columns

    def grid_changed(self, *_):
        self.selected.clear()
        self.draw()

    def select_all(self):
        if not self.busy:
            try:
                rows, columns = self.shape()
                self.selected = set(range(rows * columns))
            except ValueError:
                return
            self.draw()

    def clear_selection(self):
        if not self.busy:
            self.selected.clear()
            self.polygon.clear()
            self.polygon_closed = False
            self.draw()

    def load(self):
        paths = filedialog.askopenfilenames(title='Select merged TIFF or compatible TIFF group', filetypes=[('GeoTIFF', '*.tif *.tiff')])
        if not paths:
            return
        self.inputs = list(paths)
        self.info = None
        self.photo = None
        self.display_photo = None
        self._display_key = None
        self.fit_following = True
        self.pan_x = self.pan_y = 0
        self.selected.clear()
        self.polygon.clear()
        self.polygon_closed = False
        self.draw()
        if self.workspace:
            self.workspace.cleanup()
        # The preview is retained for this tab's lifetime, then removed on close.
        self.workspace = tempfile.TemporaryDirectory(prefix='esa-split-preview-')
        self.source_label.set(f'{len(paths)} TIFF file(s)')
        self.start({'action': 'preview', 'preview': str(Path(self.workspace.name) / 'preview.png')})

    def choose_output(self):
        folder = filedialog.askdirectory(title='Choose parent folder for a new split-map subfolder')
        if folder:
            import time
            self.output.set(str(Path(folder) / time.strftime('split-maps-%Y%m%d-%H%M%S')))

    def overview_changed(self):
        self.overview_combo.configure(state='readonly' if self.create_overview.get() and not self.busy else 'disabled')

    def export(self):
        try:
            drawing = self.selection_mode.get() == 'Draw polygon'
            rows, columns = (1, 1) if drawing else self.shape()
            if not self.info:
                raise ValueError('Load TIFFs and wait for the map preview first.')
            if not self.output.get().strip():
                raise ValueError('Choose an output location.')
            selected = sorted(self.selected)
            if drawing:
                if not self.polygon_closed:
                    raise ValueError('Draw at least three points and choose Finish outline first.')
            elif not selected:
                raise ValueError('Select at least one area by clicking it on the map.')
            overview_resolution = float(self.overview_resolution.get())
            if self.create_overview.get():
                gt = self.info['transform']
                if not math.isfinite(overview_resolution) or overview_resolution < max(abs(gt[1]), abs(gt[5])):
                    raise ValueError('Choose an overview spacing larger than the detailed pixel spacing.')
            self.start({'action': 'export', 'rows': rows, 'columns': columns,
                        'selected': selected, 'output': self.output.get(),
                        'combine_grid': self.combine_grid.get(),
                        'polygon': self.polygon if drawing else None,
                        'create_overview': self.create_overview.get(), 'overview_resolution': overview_resolution,
                        **self.encoding.options()})
        except ValueError as error:
            messagebox.showerror('Cannot split map', str(error))

    def cancel(self):
        if self.cancel_path:
            self.cancel_path.touch()
            self.status.set('Cancelling… completed areas will be kept.')

    def start(self, job):
        try:
            qgis_command(self.qgis.get(), '')
            if not self.workspace:
                raise ValueError('Load TIFFs first.')
            self.cancel_path = Path(self.workspace.name) / 'cancel'
            self.cancel_path.unlink(missing_ok=True)
            job.update(inputs=self.inputs, cancel=str(self.cancel_path), workspace=self.workspace.name)
            job_path = Path(self.workspace.name) / 'job.json'
            job_path.write_text(json.dumps(job), encoding='utf-8')
        except (ValueError, OSError) as error:
            messagebox.showerror('Cannot start splitting', str(error))
            return
        root = self.qgis.get()
        self.busy = True
        self.process_display.start('Map preview' if job['action'] == 'preview' else 'Split map export')
        self.encoding.set_busy(True)
        self.load_button.configure(state='disabled')
        self.export_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress['value'] = 0
        self.status.set('Creating map preview…' if job['action'] == 'preview' else 'Exporting selected areas…')
        for control in self.grid_controls:
            control.configure(state='disabled')
        self.overview_check.configure(state='disabled')
        self.overview_combo.configure(state='disabled')
        script = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'split_worker.py'
        def work():
            try:
                process = start_worker(root, script, job_path)
                for line in process.stdout:
                    try:
                        self.events.put(json.loads(line))
                    except ValueError:
                        self.events.put({'kind': 'status', 'message': line.strip()})
                code = process.wait()
                if code and code != 2:
                    self.events.put({'kind': 'failed'})
            except Exception as error:
                self.events.put({'kind': 'error', 'message': str(error)})
            finally:
                self.events.put({'kind': 'done', 'finished': self.process_display.stamp()})
        threading.Thread(target=work, daemon=True).start()

    def draw(self):
        self.canvas.delete('all')
        self.export_button.configure(state='disabled')
        if not self.info or not self.photo:
            self.canvas.create_text(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                                    text='Load TIFFs to see your map and split grid', fill='white')
            return
        try:
            rows, columns = (1, 1) if self.selection_mode.get() == 'Draw polygon' else self.shape()
        except ValueError as error:
            self.summary.set(str(error))
            return
        self.prepare_display()
        iw, ih = self.display_photo.width(), self.display_photo.height()
        left = (self.canvas.winfo_width() - iw) / 2 + self.pan_x
        top = (self.canvas.winfo_height() - ih) / 2 + self.pan_y
        self.image_box = left, top, iw, ih
        self.canvas.create_image(left, top, image=self.display_photo, anchor='nw')
        width, height = self.info['width'], self.info['height']
        if self.selection_mode.get() == 'Draw polygon':
            coords = [(left + x / width * iw, top + y / height * ih) for x, y in self.polygon]
            flat = [v for point in coords for v in point]
            if len(coords) >= 2:
                if self.polygon_closed:
                    self.canvas.create_polygon(*flat, outline='#ffe34d', fill='#ffe34d', stipple='gray12', width=3)
                else:
                    self.canvas.create_line(*flat, fill='#ffe34d', width=3)
            for x, y in coords:
                self.canvas.create_oval(x-3, y-3, x+3, y+3, fill='#ffe34d', outline='black')
            self.summary.set(f'{len(coords)} outline points • ' + ('Ready to export' if self.polygon_closed else 'Click along the border, then Finish outline. Undo point to correct it.'))
            self.details.set('Outside the outline is masked out. Drag to pan; zoom to trace borders more closely. Clear selection to start again.')
            self.export_button.configure(state='normal' if self.polygon_closed and not self.busy else 'disabled')
            return
        for r in range(rows):
            for c in range(columns):
                i = r * columns + c
                x1, x2 = left + (c * width // columns) / width * iw, left + ((c + 1) * width // columns) / width * iw
                y1, y2 = top + (r * height // rows) / height * ih, top + ((r + 1) * height // rows) / height * ih
                selected = i in self.selected
                self.canvas.create_rectangle(x1, y1, x2, y2, outline='#ffe34d' if selected else '#b9bdc2', width=3 if selected else 1,
                                             fill='' if selected else '#222222', stipple='' if selected else 'gray50')
                if x2 - x1 >= 45 and y2 - y1 >= 22:
                    label = f'{r + 1},{c + 1}' + (' ✓' if selected else '')
                    self.canvas.create_text((x1 + x2) / 2 + 1, (y1 + y2) / 2 + 1, text=label, fill='black', font=('Segoe UI', 10, 'bold'))
                    self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=label, fill='white', font=('Segoe UI', 10, 'bold'))
        estimated = math.ceil(width / columns) * math.ceil(height / rows) * self.info['bytes_per_pixel'] / 1024**2
        self.summary.set(f'{width:,} × {height:,} pixels • {len(self.selected)} of {rows * columns} areas selected • Up to ~{estimated:,.1f} MiB per area before compression')
        self.export_button.configure(state='normal' if self.selected and not self.busy else 'disabled')

    def cell_at(self, event):
        if not self.info or not self.photo:
            return None
        try:
            rows, columns = self.shape()
        except ValueError:
            return None
        left, top, iw, ih = self.image_box
        if not left <= event.x < left + iw or not top <= event.y < top + ih:
            return None
        px = (event.x - left) / iw * self.info['width']
        py = (event.y - top) / ih * self.info['height']
        c = min(columns - 1, next((i for i in range(columns) if px < (i + 1) * self.info['width'] // columns), columns - 1))
        r = min(rows - 1, next((i for i in range(rows) if py < (i + 1) * self.info['height'] // rows), rows - 1))
        return r, c, rows, columns

    def finish_polygon(self):
        if self.busy or self.selection_mode.get() != 'Draw polygon':
            return
        if len(self.polygon) < 3:
            self.summary.set('Add at least three points before finishing the outline.')
            return
        self.polygon_closed = True
        self.draw()

    def undo_point(self):
        if not self.busy and self.selection_mode.get() == 'Draw polygon':
            self.polygon_closed = False
            if self.polygon:
                self.polygon.pop()
            self.draw()

    def click(self, event):
        if self.selection_mode.get() == 'Draw polygon':
            if self.busy or not self.info or not self.photo or self.polygon_closed:
                return
            left, top, iw, ih = self.image_box
            if left <= event.x <= left + iw and top <= event.y <= top + ih:
                self.polygon.append(((event.x-left)/iw*self.info['width'], (event.y-top)/ih*self.info['height']))
                self.draw()
            return
        cell = self.cell_at(event)
        if cell and not self.busy:
            r, c, _, columns = cell
            i = r * columns + c
            self.selected.symmetric_difference_update({i})
            self.draw()

    def hover(self, event):
        if self.selection_mode.get() == 'Draw polygon':
            return
        cell = self.cell_at(event)
        if cell:
            r, c, rows, columns = cell
            w, h = self.info['width'], self.info['height']
            x, y, x2, y2 = c * w // columns, r * h // rows, (c + 1) * w // columns, (r + 1) * h // rows
            gt = self.info['transform']
            self.details.set(f'Area {r + 1},{c + 1} • {x2 - x:,} × {y2 - y:,} pixels • EPSG:3857 bounds (metres): {gt[0] + x * gt[1]:.2f}, {gt[3] + y2 * gt[5]:.2f}, {gt[0] + x2 * gt[1]:.2f}, {gt[3] + y * gt[5]:.2f}')

    def poll(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event['kind']
            if kind == 'preview':
                self.info = event['info']
                self.photo = tk.PhotoImage(file=str(Path(self.workspace.name) / 'preview.png'))
                self.status.set('Preview ready. Click the areas you want to export, or choose Select all. Yellow outlines show your selection.')
                self.fit_map()
            elif kind == 'done':
                self.process_display.finish(event.get('finished'))
                self.busy = False
                self.encoding.set_busy(False)
                self.load_button.configure(state='normal')
                self.draw()
                self.cancel_button.configure(state='disabled')
                for control in self.grid_controls:
                    control.configure(state='normal')
                self.overview_check.configure(state='normal')
                self.overview_changed()
            elif kind == 'failed':
                messagebox.showerror('Split failed', self.status.get())
            else:
                self.status.set(event.get('message', ''))
                if event.get('progress') is not None:
                    self.progress['value'] = event['progress']
        self.process_display.refresh()
        self.after(100, self.poll)

    def cleanup(self):
        if self.workspace:
            self.workspace.cleanup()
