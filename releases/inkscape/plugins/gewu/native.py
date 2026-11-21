"""Bounded, headless Inkscape calls over validated private snapshots."""
import csv
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from PIL import Image


class Failure(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class Engine:
    def __init__(self, executable=None, timeout=30):
        sibling = Path(sys.executable).parent / 'inkscape'
        self.executable = str(executable or (sibling if sibling.is_file() else shutil.which('inkscape') or ''))
        self.timeout = timeout
        self.version = self.run(['--version']).strip()
        if not self.version.startswith('Inkscape '):
            raise Failure('DEPENDENCY_MISSING', 'Configured executable is not Inkscape')

    def run(self, args, control=None):
        if control and control.cancelled.is_set():
            raise Failure('CANCELLED', 'Call cancelled before native execution')
        env = {k: v for k, v in os.environ.items() if k in ('HOME','PATH','LANG','TMPDIR','FONTCONFIG_FILE','FONTCONFIG_PATH')}
        env['LC_ALL'] = 'C'
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                child = subprocess.Popen([self.executable, *map(str,args)], stdin=subprocess.DEVNULL,
                                         stdout=out, stderr=err, env=env, start_new_session=True)
            except OSError as e:
                raise Failure('DEPENDENCY_MISSING', f'Cannot start Inkscape: {e.strerror}') from e
            deadline = time.monotonic() + self.timeout
            try:
                while child.poll() is None:
                    if control and control.cancelled.is_set():
                        raise Failure('CANCELLED', 'Native operation cancelled')
                    if time.monotonic() > deadline:
                        raise Failure('NATIVE_TIMEOUT', 'Inkscape did not settle before the timeout')
                    if out.tell() > 32 * 1024 * 1024 or err.tell() > 2 * 1024 * 1024:
                        raise Failure('NATIVE_FAILED', 'Inkscape exceeded its output budget')
                    time.sleep(.02)
            finally:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
            if child.returncode:
                err.seek(max(0,err.tell()-1200))
                raise Failure('NATIVE_FAILED', f'Inkscape exited {child.returncode}: {err.read(1200).decode(errors="replace")}')
            out.seek(0)
            raw = out.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:
                raise Failure('NATIVE_FAILED', 'Inkscape query exceeded its output budget')
            return raw.decode('utf-8', errors='replace')

    def query(self, source, control=None):
        rows = csv.reader(self.run(['--app-id-tag=gewu', '--query-all', source], control).splitlines())
        result = {}
        for row in rows:
            if len(row) == 5:
                try:
                    values = tuple(float(n) * 25.4 / 96 for n in row[1:])
                except ValueError:
                    continue
                if all(map(math.isfinite,values)):
                    result[row[0]] = dict(zip(('x','y','width','height'),values))
        return result

    def export(self, source, output, fmt, dpi=96, text='preserve', page=None, control=None):
        argv = ['--app-id-tag=gewu', f'--export-type={fmt}', f'--export-filename={output}']
        if fmt == 'png':
            argv.append(f'--export-dpi={dpi}')
        if fmt == 'pdf' and text == 'paths':
            argv.append('--export-text-to-path')
        if page is not None:
            argv.append(f'--export-page={page}')
        self.run([*argv,source],control)
        try:
            with output.open('rb') as stream:
                magic = stream.read(8)
            if not (magic.startswith(b'%PDF-') if fmt == 'pdf' else magic == b'\x89PNG\r\n\x1a\n'):
                raise ValueError('wrong file signature')
            if fmt == 'png':
                with Image.open(output) as image:
                    image.verify()
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            raise Failure('NATIVE_FAILED', 'Inkscape did not create a valid export') from e
