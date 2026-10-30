"""Shared desktop styling and small, consistent layout helpers."""
from tkinter import ttk
from tkinter import font
import tkinter as tk


class ScrollablePage(ttk.Frame):
    """A notebook page whose content keeps its requested height."""
    def __init__(self, parent):
        super().__init__(parent)
        navigation = ttk.Frame(self, padding=(12, 4))
        navigation.pack(fill='x')
        ttk.Label(navigation, text='Scroll for settings, maps and progress', style='Muted.TLabel').pack(side='left')
        ttk.Button(navigation, text='Top ↑', command=lambda: self.viewport.yview_moveto(0)).pack(side='right')
        ttk.Button(navigation, text='Progress & log ↓', command=self.show_progress).pack(side='right', padx=6)
        self.viewport = tk.Canvas(self, background='#f4f6f9', highlightthickness=0, yscrollincrement=24)
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.viewport.yview)
        scrollbar.pack(side='right', fill='y')
        self.viewport.pack(side='left', fill='both', expand=True)
        self.viewport.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.viewport)
        self.window = self.viewport.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', self._layout)
        self.viewport.bind('<Configure>', self._resize)
        # Child widgets with their own wheel behaviour keep it (maps, logs,
        # lists and selectors). Only events belonging to this page are handled.
        for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.bind_all(event, self._wheel, add='+')
        self.bind_all('<FocusIn>', self._focus, add='+')

    def _contains(self, widget):
        while widget is not None:
            if widget == self.content:
                return True
            widget = getattr(widget, 'master', None)
        return False

    def _layout(self, _event=None):
        self.viewport.configure(scrollregion=self.viewport.bbox('all'))

    def _resize(self, event):
        self.viewport.itemconfigure(self.window, width=event.width)

    def _wheel(self, event):
        if not self._contains(event.widget) or event.widget.winfo_class() in (
                'Canvas', 'Text', 'Listbox', 'TCombobox', 'TSpinbox', 'Scrollbar', 'TScrollbar'):
            return
        delta = getattr(event, 'delta', 0)
        number = getattr(event, 'num', None)
        if number in (4, 5) or delta:
            if number in (4, 5):
                steps = -1 if number == 4 else 1
            else:
                steps = max(1, abs(delta) // 120) * (-1 if delta > 0 else 1)
            self.viewport.yview_scroll(steps * 3, 'units')
            return 'break'

    def _focus(self, event):
        if not self._contains(event.widget) or not self.winfo_viewable():
            return
        top = event.widget.winfo_rooty() - self.viewport.winfo_rooty()
        bottom = top + event.widget.winfo_height()
        height = self.viewport.winfo_height()
        content_height = max(1, self.content.winfo_height())
        if top < 0:
            self.viewport.yview_moveto((self.viewport.canvasy(0) + top - 12) / content_height)
        elif bottom > height:
            self.viewport.yview_moveto((self.viewport.canvasy(0) + bottom - height + 12) / content_height)

    def show_progress(self):
        self.update_idletasks()
        self.viewport.yview_moveto(1)


def configure(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont'):
        font.nametofont(name).configure(family='Segoe UI', size=10)
    style.configure('.', background='#f4f6f9', foreground='#202e40')
    style.configure('TFrame', background='#f4f6f9')
    style.configure('TLabel', background='#f4f6f9')
    style.configure('Title.TLabel', font=('Segoe UI', 18, 'bold'))
    style.configure('Muted.TLabel', foreground='#526278')
    style.configure('TButton', padding=(10, 4))
    style.configure('Primary.TButton', background='#2164ad', foreground='white')
    style.map('Primary.TButton', background=[('disabled', '#dce3ed'), ('active', '#174c87')],
              foreground=[('disabled', '#778497')])
    style.configure('TEntry', padding=4, fieldbackground='white')
    style.configure('TNotebook', borderwidth=0, tabmargins=(12, 8, 12, 0))
    style.configure('TNotebook.Tab', padding=(16, 9), background='#e5eaf1')
    style.map('TNotebook.Tab', background=[('selected', '#f4f6f9')],
              foreground=[('selected', '#155a9e')])
    style.configure('TLabelframe', bordercolor='#d4dde8', relief='solid')
    style.configure('TLabelframe.Label', font=('Segoe UI', 10, 'bold'), foreground='#314b6b')
    style.configure('Horizontal.TProgressbar', background='#2164ad', troughcolor='#e0e7f0')
    root.option_add('*Text.background', '#ffffff')
    root.option_add('*Text.foreground', '#26364b')
    root.option_add('*Text.font', ('Consolas', 9))
    root.option_add('*Text.relief', 'flat')
    root.option_add('*Text.highlightThickness', 1)
    root.option_add('*Text.highlightBackground', '#d4dde8')
    root.option_add('*Listbox.background', '#ffffff')
    root.option_add('*Listbox.selectBackground', '#2164ad')
    root.option_add('*Listbox.selectForeground', '#ffffff')
    root.option_add('*Listbox.relief', 'flat')


def section(parent, title):
    frame = ttk.LabelFrame(parent, text=title, padding=(10, 6))
    frame.pack(fill='x', pady=(4, 6))
    return frame
