"""Run unit tests and every QGIS integration suite: python check.py [--gui]."""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import time

from mosaic_runtime import find_qgis, start_worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qgis', default=find_qgis(), help='QGIS installation prefix; detected automatically')
    parser.add_argument('--gui', action='store_true', help='Also test desktop tabs (requires a graphical desktop)')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    reports = root / '.verification' / 'checks' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    reports.mkdir(parents=True)
    results = []

    def run(name, script=None, command=None):
        print(f'Running {name}…', flush=True)
        started = time.monotonic()
        log = reports / f'{name}.log'
        try:
            with log.open('w', encoding='utf-8') as stream:
                if command:
                    process = subprocess.Popen(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT)
                else:
                    process = start_worker(args.qgis, root / 'tests' / script)
                    for line in process.stdout:
                        stream.write(line)
                code = process.wait()
        except Exception as error:
            log.write_text(str(error), encoding='utf-8')
            code = 1
        seconds = round(time.monotonic() - started, 2)
        results.append((name, code, seconds))
        print(f'{"PASS" if code == 0 else "FAIL"} {name} ({seconds:g}s)', flush=True)
        if code:
            print(log.read_text(encoding='utf-8', errors='replace')[-6000:], flush=True)

    run('unit-tests', command=[sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    for script in sorted((root / 'tests').glob('*_integration.py')):
        run(script.stem, script=script.name)
    if args.gui:
        run('gui-smoke', command=[sys.executable, str(root / 'tests' / 'gui_smoke.py')])
    summary = '\n'.join(f'{"PASS" if code == 0 else "FAIL"} {name} ({seconds:g}s)' for name, code, seconds in results)
    (reports / 'summary.txt').write_text(summary + '\n', encoding='utf-8')
    print(f'Reports: {reports}', flush=True)
    return int(any(code for _, code, _ in results))


if __name__ == '__main__':
    raise SystemExit(main())
