import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


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
