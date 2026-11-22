import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class NssClosureTests(unittest.TestCase):
    def test_dlopen_nss_modules_are_staged_and_initialize_without_host_fallback(self):
        source = Path(__file__).resolve().parents[2] / "scripts/stage-linux-libraries.py"
        nss = Path("/lib64/libnss3.so")
        if not nss.exists():
            self.skipTest("needs build-host NSS")
        modules = ("libsoftokn3.so", "libfreeblpriv3.so", "libfreebl3.so", "libnssdbm3.so")
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "libs"
            result = subprocess.run(
                [sys.executable, str(source), "--out", str(out),
                 *[arg for module in (*modules, "libnss3.so") for arg in ("--dlopen", str(nss.parent / module))], str(nss)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((out / "inputs.json").read_text())
            self.assertTrue(set(modules).issubset(entry["file"] for entry in manifest))
            dependencies = subprocess.check_output(["ldd", str(nss.parent / "libsoftokn3.so")], text=True)
            for line in dependencies.splitlines():
                if "libsqlite3.so.0 =>" in line:
                    self.assertIn("libsqlite3.so.0", [entry["file"] for entry in manifest])
            check = subprocess.run(
                [sys.executable, "-c", "import ctypes; nss=ctypes.CDLL('libnss3.so'); assert nss.NSS_NoDB_Init(None)==0; nss.PK11_GetInternalKeySlot.restype=ctypes.c_void_p; slot=nss.PK11_GetInternalKeySlot(); assert slot; nss.PK11_FreeSlot.argtypes=[ctypes.c_void_p]; nss.PK11_FreeSlot(slot); assert nss.NSS_Shutdown()==0"],
                env={**os.environ, "LD_LIBRARY_PATH": str(out)}, text=True, capture_output=True,
            )
            self.assertEqual(check.returncode, 0, check.stderr)


class FontCacheTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("fc-cache") and shutil.which("fc-match"), "needs build-host fontconfig")
    def test_fontconfig_cache_stays_out_of_the_workspace(self):
        source = Path(__file__).resolve().parents[2] / "scripts/stage-fonts.py"
        spec = importlib.util.spec_from_file_location("stage_fonts", source)
        fonts_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fonts_module)
        font = subprocess.check_output(["fc-match", "-f", "%{file}", "sans"], text=True)
        self.assertTrue(Path(font).is_file(), "build host needs a font for this integration check")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fonts = root / "runtime/fonts"
            fonts.mkdir(parents=True)
            shutil.copyfile(font, fonts / "test.ttf")
            config = fonts / "fonts.conf"
            config.write_text(fonts_module.CONF)
            workspace = root / "project/nested"
            workspace.mkdir(parents=True)
            home = root / "home"
            home.mkdir()
            env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_DATA_HOME": str(fonts.parent),
                   "XDG_CACHE_HOME": str(home / ".cache"), "FONTCONFIG_FILE": str(config)}
            subprocess.run(["fc-cache", "-f"], cwd=workspace, check=True, capture_output=True, env=env)
            matched = subprocess.check_output(["fc-match", "-f", "%{file}", "sans"], cwd=workspace, env=env, text=True)
            self.assertEqual(matched, str(fonts / "test.ttf"))
            self.assertEqual(list((root / "project").iterdir()), [workspace], "fontconfig wrote into the project")
            self.assertEqual(list(workspace.iterdir()), [])
            self.assertTrue(list((home / ".cache").rglob("*.cache-*")), "private font cache was not created")
