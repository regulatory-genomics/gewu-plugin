import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EditorUiTests(unittest.TestCase):
    def test_editor_uses_manimble_three_pane_layout(self):
        html = (ROOT / "web/index.html").read_text()
        css = (ROOT / "web/editor.css").read_text()

        self.assertIn('class="editor-toolbar"', html)
        self.assertIn('class="slide-sidebar"', html)
        self.assertIn('class="canvas-workspace"', html)
        self.assertIn('class="inspector-panel"', html)
        self.assertIn('class="ruler ruler-horizontal"', html)
        self.assertIn('class="ruler ruler-vertical"', html)
        self.assertRegex(css, r"color-scheme:\s*light")
        self.assertRegex(css, r"--accent:\s*#(?:ed7d31|f07b32)")
        self.assertRegex(
            css,
            re.compile(r"grid-template-columns:\s*220px\s+minmax\(0,\s*1fr\)\s+320px"),
        )

    def test_toolbar_keeps_all_actions_reachable_in_a_narrow_dock(self):
        css = (ROOT / "web/editor.css").read_text()

        toolbar = re.search(r"\.editor-toolbar\s*\{(?P<body>[^}]+)\}", css)
        self.assertIsNotNone(toolbar)
        self.assertRegex(toolbar.group("body"), r"flex-wrap:\s*wrap")
        self.assertNotIn(".arrange-actions { display: none; }", css)
        self.assertNotRegex(css, r"\.slide-actions button[^}]+display:\s*none")
        self.assertNotRegex(css, r"\.insert-actions button[^}]+display:\s*none")


if __name__ == "__main__":
    unittest.main()
