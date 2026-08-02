import unittest
from pathlib import Path


WEB_INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"
PROJECT_AGENTS = Path(__file__).resolve().parents[1] / "AGENTS.md"


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
            'id="expandSidebar"',
            'id="collapseSidebar"',
            '<div class="sidebar-title">资料库</div>',
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

    def test_logoless_collapsible_sidebar_and_rotating_welcome_contract(self):
        for contract in (
            'class="icon-btn sidebar-expand"',
            'aria-label="展开资料库"',
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
            "['嗨！我是','HIC Copilot。']",
            "['欢迎加入','中传海南！']",
            "['关于学校的问题，','尽管问！']",
            "['准备好开始','大敦村生活了吗？']",
            "['别紧张，','就当在问师哥师姐。']",
        ):
            self.assertIn(copy, self.html)
        for removed in (
            "brand-logo",
            "brand-home",
            "data:image/svg+xml",
            "radial-gradient",
            "可以直接问我，也可以从左侧资料库查文件",
        ):
            self.assertNotIn(removed, self.html)

    def test_responsive_welcome_suggestion_groups_are_qa_backed(self):
        for contract in (
            "const suggestionSeeds=",
            "library.questions.find",
            "seed.terms.every",
            'data-question="${esc(item.label)}"',
            "ask(b.dataset.question)",
            "renderWelcomeSuggestions(true)",
            "function suggestionGroupSize(){return mobileDrawer.matches?2:3}",
            "Array.from({length:size}",
            "suggestionGroupIndex+=1",
            "},260)},5000)",
            "宿舍几人间？",
            "快递怎么填？",
            "大墩村有什么？",
        ):
            self.assertIn(contract, self.html)
        self.assertNotIn(".suggestions{display:none}", self.html)
        self.assertNotIn("overflow-x:auto", self.html)
        self.assertNotIn("question:source.question", self.html)

    def test_suggestion_click_sends_visible_copy_and_enters_conversation(self):
        for contract in (
            "async function ask(explicitQuestion='')",
            "const q=explicitQuestion.trim()||input.value.trim()||guidedQuestion()",
            "messages.classList.add('conversation-active')",
            "messages.classList.remove('conversation-active')",
            "$('#welcomeState')?.remove()",
            ".chat-messages.conversation-active{padding-top:8px}",
            ".suggestion{flex:0 1 auto;width:auto",
            "overflow-wrap:anywhere",
        ):
            self.assertIn(contract, self.html)

    def test_desktop_and_mobile_composer_and_header_contracts(self):
        for contract in (
            ".compose-box:focus-within{border-color:rgba(60,60,67,.16);box-shadow:0 12px 36px rgba(0,0,0,.08)}",
            "textarea::-webkit-scrollbar{display:none}",
            "textarea:focus-visible{outline:0;box-shadow:none!important}",
            ".top-title{display:none}",
            ".compose-note{display:block",
            "justify-content:space-between",
        ):
            self.assertIn(contract, self.html)
        self.assertNotIn("box-shadow:var(--focus),0 12px 36px", self.html)

    def test_mobile_and_desktop_quality_gate_is_durable(self):
        rules = PROJECT_AGENTS.read_text(encoding="utf-8")
        for contract in (
            "Every user-interface change",
            "both desktop and phone layouts",
            "Mobile is a blocking product surface",
            "390 by 844 CSS pixels",
            "no horizontal page overflow",
            "Do not report a UI change complete until both surfaces pass",
        ):
            self.assertIn(contract, rules)

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
        for copy in (
            "正在查找往届师哥师姐的材料…",
            "让我看看有没有一些官方材料…",
            "稍等哈，看下热心师姐留下的经验…",
            "好像有个师哥之前提到过，我找下…",
            "找到啦！让我整理一下答案…",
            "回答梳理中…",
            "绝妙秘籍制作中…",
            "找到有用的了，让我想想怎么跟你讲…",
            "亲亲别急，马上就好…",
            "还有一会儿就好，等我下…",
            "在打最后几个字了，稍等～",
        ):
            self.assertIn(copy, self.html)
        self.assertIn("await delay(4000)", self.html)
        self.assertIn("setTimeout(reassure,8000)", self.html)
        self.assertIn('class="assistant-progress"', self.html)
        self.assertNotIn('class="answer-note', self.html)
        self.assertIn('class="sources"', self.html)
        self.assertIn("参考来源（", self.html)

    def test_unanswered_question_has_responsive_opt_in_human_handoff(self):
        for contract in (
            "/api/handoff",
            'class="handoff-card"',
            'class="handoff-email"',
            'type="email"',
            'autocomplete="email"',
            "仅用于回复本次问题，保留 30 天",
            "不会发送给大模型或加入知识库",
            "submitHandoff",
            "trace_id:form.dataset.traceId",
        ):
            self.assertIn(contract, self.html)

    def test_direct_file_open_redirects_to_the_loopback_service(self):
        self.assertIn("location.protocol==='file:'", self.html)
        self.assertIn("location.replace('http://127.0.0.1:8767/')", self.html)

    def test_empty_composer_can_send_and_advance_a_guided_question(self):
        self.assertIn("const guidedQuestions=", self.html)
        self.assertIn("explicitQuestion.trim()||input.value.trim()||guidedQuestion()", self.html)
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
