import unittest
from pathlib import Path


WEB_INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


class FrontendContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = WEB_INDEX.read_text(encoding="utf-8")

    def test_fused_library_and_chat_contract_is_preserved(self):
        for contract in (
            "/api/library",
            "/api/health",
            "/api/chat",
            "/api/session/reset",
            'id="libraryContent"',
            'id="chatPanel"',
        ):
            self.assertIn(contract, self.html)

    def test_mobile_sheet_has_direct_interruptible_gesture_primitives(self):
        for primitive in (
            "setPointerCapture",
            "pointerdown",
            "pointermove",
            "requestAnimationFrame",
            "rubberband",
            "project(velocity",
        ):
            self.assertIn(primitive, self.html)

    def test_accessibility_preferences_have_explicit_fallbacks(self):
        for preference in (
            "prefers-reduced-motion:reduce",
            "prefers-reduced-transparency:reduce",
            "prefers-contrast:more",
            "aria-hidden",
            "aria-modal",
            ":focus-visible",
        ):
            self.assertIn(preference, self.html)


if __name__ == "__main__":
    unittest.main()
