"""Locate and launch QGIS's Python on Windows and native Linux installations."""
import os
from pathlib import Path
import subprocess
import sys
import shutil
import json
import threading

from downloader import Bounds, Cancelled

_launch_lock = threading.Lock()

WORKER_RESOURCES = (
    'mosaic_worker.py', 'split_worker.py', 'hybrid_worker.py', 'hybrid_style.py',
    'geofabrik_worker.py', 'raster_output.py', 'map_plan.py',
)


def worker_script(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / name


def request_cancel(path):
    """A worker may finish and remove its workspace before the GUI polls done."""
    if path is not None:
        try:
            Path(path).touch()
        except FileNotFoundError:
            return False
        return True
    return False


class WorkerFailed(RuntimeError):
    def __init__(self, code, detail=''):
        self.code = code
        native = f'0x{code & 0xffffffff:08X}' if code < 0 or code > 255 else str(code)
        message = f'Processing stopped (exit {native}).'
        if detail:
            message += ' ' + detail[:500]
        super().__init__(message + ' See the process log for details.')


def worker_event(line):
    """Normalize structured worker output and native GDAL/Qt diagnostics."""
    line = line.strip()
    try:
        event = json.loads(line)
    except ValueError:
        event = None
    if not isinstance(event, dict):
        return {'kind': 'status', 'message': line}
    return event


def run_qgis_worker(root, script, *args, on_event):
    """Drain output, retain error context and always reap the worker process."""
    process = start_worker(root, script, *args)
    last_message = ''
    try:
        for line in process.stdout:
            if not line.strip():
                continue
            event = worker_event(line)
            if event.get('message'):
                last_message = str(event['message'])
            on_event(event)
        code = process.wait()
    except BaseException:
        if process.poll() is None:
            process.terminate()
        process.wait()
        raise
    finally:
        process.stdout.close()
    if code == 2:
        raise Cancelled('Cancelled. Completed downloads and outputs were kept.')
    if code:
        raise WorkerFailed(code, last_message)


def inspect_tiff_bounds(root, path):
    bounds = []
    run_qgis_worker(root, worker_script('hybrid_worker.py'), '--inspect', path,
                    on_event=lambda event: bounds.append(event['bounds']) if 'bounds' in event else None)
    if not bounds:
        raise ValueError('QGIS did not return TIFF bounds.')
    result = Bounds(**bounds[-1])
    result.validate()
    return result


def find_qgis():
    if sys.platform.startswith('linux'):
        roots = []
        if os.environ.get('QGIS_PREFIX_PATH'):
            roots.append(Path(os.environ['QGIS_PREFIX_PATH']))
        binary = shutil.which('qgis')
        if binary:
            roots.append(Path(binary).resolve().parent.parent)
        roots += [Path('/usr'), Path('/usr/local')]
        return next((str(p) for p in roots if (p / 'share/qgis').is_dir() and (p / 'bin/python3').is_file()), '')
    roots = list(Path(os.environ.get('ProgramFiles', 'C:/Program Files')).glob('QGIS*'))
    roots += [Path('C:/OSGeo4W'), Path('C:/OSGeo4W64')]
    return next((str(p) for p in sorted(roots, reverse=True) if (p / 'bin/python3.exe').is_file()), '')


def qgis_command(root, script, *args, platform=None):
    platform = platform or sys.platform
    root = Path(root).resolve()
    if platform.startswith('linux'):
        python = Path(os.environ.get('ESA_QGIS_PYTHON', str(root / 'bin/python3')))
        if not python.is_file() or not (root / 'share/qgis').is_dir():
            raise ValueError('Select the native QGIS prefix (usually /usr), with bin/python3 and share/qgis. Install QGIS, Python bindings, GDAL and NumPy through your Linux package manager.')
        env = dict(os.environ)
        for name in ('PYTHONHOME', 'PYTHONEXECUTABLE', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
            env.pop(name, None)
        modules = root / 'share/qgis/python'
        env.update(QGIS_PREFIX_PATH=str(root), PYTHONPATH=str(modules) if modules.is_dir() else '',
               PYTHONUTF8='1', PYTHONUNBUFFERED='1', PYTHONFAULTHANDLER='1', QT_QPA_PLATFORM='offscreen')
        if getattr(sys, 'frozen', False):
            # Restore the host library path before launching system QGIS/Python.
            original = env.get('LD_LIBRARY_PATH_ORIG')
            if original is None:
                env.pop('LD_LIBRARY_PATH', None)
            else:
                env['LD_LIBRARY_PATH'] = original
            bundled = str(getattr(sys, '_MEIPASS', ''))
            if bundled:
                env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep) if not p.startswith(bundled))
        return [str(python), str(script), *map(str, args)], env
    if platform != 'win32':
        raise ValueError('This application supports Windows and Linux with a native QGIS installation.')
    python = root / 'bin/python3.exe'
    homes = sorted((root / 'apps').glob('Python3*'))
    homes = [p for p in homes if (p / 'Lib').is_dir()]
    if not python.is_file() or not homes:
        raise ValueError('Choose the QGIS installation folder containing bin/python3.exe and apps/Python3*.')
    env = dict(os.environ)
    env.update(PYTHONHOME=str(homes[-1]), PYTHONPATH='', PYTHONUTF8='1', PYTHONUNBUFFERED='1', PYTHONFAULTHANDLER='1',
               GDAL_DATA=str(root / 'apps/gdal/share/gdal'),
               GDAL_DRIVER_PATH=str(root / 'apps/gdal/lib/gdalplugins'),
               PROJ_DATA=str(root / 'share/proj'),
               PATH=str(root / 'bin') + os.pathsep + os.environ.get('SystemRoot', 'C:/Windows') + '/System32')
    prefixes = [p for p in (root / 'apps/qgis', root / 'apps/qgis-ltr') if (p / 'python').is_dir()]
    if prefixes:
        prefix = prefixes[0]
        qt = root / 'apps/qt6' if (root / 'apps/qt6').exists() else root / 'apps/Qt5'
        env.update(QGIS_PREFIX_PATH=str(prefix), PYTHONPATH=str(prefix / 'python'),
                   QT_QPA_PLATFORM='offscreen', QT_PLUGIN_PATH=str(qt / 'plugins'),
                   PATH=os.pathsep.join([str(prefix / 'bin'), str(qt / 'bin'), env['PATH']]))
    env.pop('PROJ_LIB', None)
    return [str(python), str(script), *map(str, args)], env


def start_worker(root, script, *args):
    command, env = qgis_command(root, script, *args)
    # A frozen GUI must not pass its bundled DLL search directory to QGIS Python.
    frozen = sys.platform == 'win32' and getattr(sys, 'frozen', False)
    # SetDllDirectory is process-wide: concurrent tabs must not interleave
    # clearing/restoring the frozen application's DLL search directory.
    with _launch_lock:
        if frozen:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace',
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        finally:
            if frozen:
                ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
