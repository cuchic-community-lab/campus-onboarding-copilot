import base64
import re
import unittest
from pathlib import Path


WEB_INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"
LOGO = Path(__file__).resolve().parents[1] / "web" / "assets" / "hic-copilot-text-logo.svg"


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
            'id="chatMessages"',
            'id="libraryDrawer"',
        ):
            self.assertIn(contract, self.html)

    def test_mobile_library_drawer_has_direct_interruptible_gesture_primitives(self):
        for primitive in (
            "setPointerCapture",
            "pointerdown",
            "pointermove",
            "requestAnimationFrame",
            "rubberband",
            "project(velocity",
            "animateDrawer",
        ):
            self.assertIn(primitive, self.html)

    def test_chat_first_shell_replaces_the_floating_panel_and_filter_toolbar(self):
        for contract in (
            'class="app-shell sidebar-collapsed"',
            'class="sidebar compact"',
            'class="workspace"',
            'class="composer-wrap"',
            'id="openDrawer"',
            'id="collapseSidebar"',
            "嗨，我是HIC Copilot",
        ):
            self.assertIn(contract, self.html)
        for retired_control in (
            'id="newChat"',
            'id="chatLauncher"',
            'id="chatPanel"',
            'id="resourcesTab"',
            'id="questionsTab"',
            'id="tags"',
            'class="welcome-mark"',
            'class="sidebar-foot"',
        ):
            self.assertNotIn(retired_control, self.html)

    def test_hic_identity_collapsible_sidebar_and_rotating_welcome_contract(self):
        self.assertTrue(LOGO.exists())
        logo = LOGO.read_text(encoding="utf-8")
        self.assertIn("<svg", logo)
        self.assertIn("HIC Copilot", logo)
        for contract in (
            'class="brand-logo" src="data:image/svg+xml;base64,',
            'class="welcome-copy-line"',
            'function setDesktopSidebar',
            'function scheduleSidebarAutoExpand',
            "setTimeout(()=>{sidebarAutoTimer=null",
            "},5000)",
            "current.classList.add('copy-fade')",
            "},800)},10000)",
            "transition:opacity 800ms ease-in-out",
            "grid-template-rows:minmax(0,1fr) auto",
            "grid-template-rows:auto minmax(0,1fr) auto",
            ".chat-messages:has(.welcome){display:grid;place-items:center}",
            "基于往届师哥师姐搜集的材料回复，请注意核实",
        ):
            self.assertIn(contract, self.html)
        for copy in (
            "['嗨，我是HIC Copilot']",
            "['你好呀，欢迎加入','中传海南！']",
            "['关于学校的问题，','尽管问！']",
            "['准备好开始','大学村生活了吗？']",
            "['别紧张，','就当在问师哥师姐。']",
        ):
            self.assertIn(copy, self.html)
        embedded = re.search(
            r'class="brand-logo" src="data:image/svg\+xml;base64,([^"]+)"',
            self.html,
        )
        self.assertIsNotNone(embedded)
        self.assertEqual(base64.b64decode(embedded.group(1)), LOGO.read_bytes())

    def test_accessibility_preferences_have_explicit_fallbacks(self):
        for preference in (
            "prefers-reduced-motion:reduce",
            "prefers-reduced-transparency:reduce",
            "prefers-contrast:more",
            "aria-hidden",
            "aria-modal",
            "aria-expanded",
            ":focus-visible",
        ):
            self.assertIn(preference, self.html)

    def test_chat_progress_is_human_readable_and_sources_remain_available(self):
        self.assertIn("正在翻阅往届师兄师姐留下来的材料…", self.html)
        self.assertIn("正在整理回答中…", self.html)
        self.assertIn('class="assistant-progress"', self.html)
        self.assertNotIn('class="answer-note', self.html)
        self.assertIn('class="sources"', self.html)
        self.assertIn("参考来源（", self.html)

    def test_direct_file_open_redirects_to_the_loopback_service(self):
        self.assertIn("location.protocol==='file:'", self.html)
        self.assertIn("location.replace('http://127.0.0.1:8767/')", self.html)

    def test_empty_composer_can_send_and_advance_a_guided_question(self):
        self.assertIn("const guidedQuestions=", self.html)
        self.assertIn("const q=input.value.trim()||guidedQuestion()", self.html)
        self.assertIn("updateInputGuide(true)", self.html)
        self.assertIn("发送示例问题：", self.html)
        self.assertIn("基于往届师哥师姐搜集的材料回复，请注意核实", self.html)

    def test_library_search_includes_files_links_and_questions(self):
        self.assertIn("library.files.filter", self.html)
        self.assertIn("library.links.filter", self.html)
        self.assertIn("library.questions.filter", self.html)
        self.assertIn('class="library-item question-item"', self.html)


if __name__ == "__main__":
    unittest.main()
