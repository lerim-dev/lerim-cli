"""Tests to verify index.html changes for agentic widget removal and FTS support."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX_HTML = (Path(__file__).parent.parent / "dashboard" / "index.html").read_text(encoding="utf-8")


class TestIndexHtmlAgenticWidgetRemoval(unittest.TestCase):
    """Tests that verify agentic chat widget elements have been removed from index.html."""
    html_content = INDEX_HTML

    def test_no_agentic_chat_widget_element(self) -> None:
        """Verify there is NO element with id='agenticChatWidget'."""
        pattern = r'id\s*=\s*["\']agenticChatWidget["\']'
        match = re.search(pattern, self.html_content, re.IGNORECASE)
        self.assertIsNone(
            match,
            "Found element with id='agenticChatWidget' which should have been removed"
        )

    def test_no_agentic_chat_toggle_element(self) -> None:
        """Verify there is NO element with id='agenticChatToggle'."""
        pattern = r'id\s*=\s*["\']agenticChatToggle["\']'
        match = re.search(pattern, self.html_content, re.IGNORECASE)
        self.assertIsNone(
            match,
            "Found element with id='agenticChatToggle' which should have been removed"
        )

    def test_no_agentic_chat_panel_element(self) -> None:
        """Verify there is NO element with id='agenticChatPanel'."""
        pattern = r'id\s*=\s*["\']agenticChatPanel["\']'
        match = re.search(pattern, self.html_content, re.IGNORECASE)
        self.assertIsNone(
            match,
            "Found element with id='agenticChatPanel' which should have been removed"
        )

    def test_no_agentic_scope_variable(self) -> None:
        """Verify the JavaScript does NOT contain 'agenticScope' variable declaration."""
        # Look for variable declarations like: let agenticScope, var agenticScope, const agenticScope
        pattern = r'\b(let|var|const)\s+agenticScope\b'
        match = re.search(pattern, self.html_content)
        self.assertIsNone(
            match,
            "Found 'agenticScope' variable declaration which should have been removed"
        )

    def test_no_send_agentic_query_function(self) -> None:
        """Verify the JavaScript does NOT contain 'sendAgenticQuery' function."""
        # Look for function declarations
        pattern = r'\bfunction\s+sendAgenticQuery\s*\('
        match = re.search(pattern, self.html_content)
        self.assertIsNone(
            match,
            "Found 'sendAgenticQuery' function which should have been removed"
        )
        # Also check for arrow function or method assignment
        pattern2 = r'\bsendAgenticQuery\s*[=:]\s*(async\s+)?\('
        match2 = re.search(pattern2, self.html_content)
        self.assertIsNone(
            match2,
            "Found 'sendAgenticQuery' assignment which should have been removed"
        )

    def test_no_open_agentic_panel_function(self) -> None:
        """Verify the JavaScript does NOT contain 'openAgenticPanel' function."""
        # Look for function declarations
        pattern = r'\bfunction\s+openAgenticPanel\s*\('
        match = re.search(pattern, self.html_content)
        self.assertIsNone(
            match,
            "Found 'openAgenticPanel' function which should have been removed"
        )
        # Also check for arrow function or method assignment
        pattern2 = r'\bopenAgenticPanel\s*[=:]\s*(async\s+)?\('
        match2 = re.search(pattern2, self.html_content)
        self.assertIsNone(
            match2,
            "Found 'openAgenticPanel' assignment which should have been removed"
        )


class TestIndexHtmlFtsSearchSupport(unittest.TestCase):
    """Tests that verify FTS (Full-Text Search) support in index.html."""
    html_content = INDEX_HTML

    def test_search_input_placeholder_mentions_full_text(self) -> None:
        """Verify the search input placeholder mentions 'Full-text search'."""
        # Look for placeholder attribute containing "full-text" or "Full-text"
        pattern = r'placeholder\s*=\s*["\'][^"\']*[Ff]ull-text[^"\']*search[^"\']*["\']'
        match = re.search(pattern, self.html_content, re.IGNORECASE)
        self.assertIsNotNone(
            match,
            "Search input placeholder should mention 'Full-text search'"
        )

    def test_load_runs_uses_fts_mode(self) -> None:
        """Verify loadRuns function uses mode='fts' when there's a search query."""
        # Look for the pattern that sets mode to 'fts'
        pattern = r"mode.*['\"]fts['\"]"
        match = re.search(pattern, self.html_content)
        self.assertIsNotNone(
            match,
            "loadRuns function should use mode='fts' for search queries"
        )

    def test_handles_fts_results_with_snippets(self) -> None:
        """Verify the code handles FTS results that include snippets."""
        # Check for handling of FTS mode results
        self.assertIn(
            "mode === 'fts'",
            self.html_content,
            "Code should check for FTS mode in results"
        )
        # Check for snippet handling
        self.assertIn(
            "snippet",
            self.html_content,
            "Code should handle snippet field from FTS results"
        )


class TestIndexHtmlCssForSessionViewer(unittest.TestCase):
    """Tests that CSS for chat widget styles still exists for session viewer pages."""
    html_content = INDEX_HTML

    def test_agentic_widget_css_exists(self) -> None:
        """Verify .agentic-widget CSS class still exists (for session pages)."""
        pattern = r'\.agentic-widget\s*\{'
        match = re.search(pattern, self.html_content)
        self.assertIsNotNone(
            match,
            ".agentic-widget CSS class should still exist for session viewer pages"
        )

    def test_agentic_panel_css_exists(self) -> None:
        """Verify .agentic-panel CSS class still exists (for session pages)."""
        pattern = r'\.agentic-panel\s*\{'
        match = re.search(pattern, self.html_content)
        self.assertIsNotNone(
            match,
            ".agentic-panel CSS class should still exist for session viewer pages"
        )

    def test_agentic_toggle_css_exists(self) -> None:
        """Verify .agentic-toggle CSS class still exists (for session pages)."""
        pattern = r'\.agentic-toggle\s*\{'
        match = re.search(pattern, self.html_content)
        self.assertIsNotNone(
            match,
            ".agentic-toggle CSS class should still exist for session viewer pages"
        )

    def test_agentic_message_css_exists(self) -> None:
        """Verify .agentic-message CSS class still exists (for session pages)."""
        pattern = r'\.agentic-message\s*\{'
        match = re.search(pattern, self.html_content)
        self.assertIsNotNone(
            match,
            ".agentic-message CSS class should still exist for session viewer pages"
        )

    def test_agentic_header_css_exists(self) -> None:
        """Verify .agentic-header CSS class still exists (for session pages)."""
        pattern = r'\.agentic-header\s*\{'
        match = re.search(pattern, self.html_content)
        self.assertIsNotNone(
            match,
            ".agentic-header CSS class should still exist for session viewer pages"
        )


class TestIndexHtmlSetAgenticScopeExists(unittest.TestCase):
    """Tests that verify setAgenticScope function behavior."""
    html_content = INDEX_HTML

    def test_set_agentic_scope_function_called(self) -> None:
        """Verify setAgenticScope is called (for managing session state)."""
        # The function is called to set/clear session context
        self.assertIn(
            "setAgenticScope(",
            self.html_content,
            "setAgenticScope should be called to manage session context"
        )


class TestIndexHtmlMemoryGraphView(unittest.TestCase):
    """Tests for the rebuilt memory graph explorer integration."""
    html_content = INDEX_HTML

    def test_memory_graph_navigation_is_tab_based(self) -> None:
        self.assertIn(
            "Graph Explorer",
            self.html_content,
            "Memories page should expose Graph Explorer tab navigation",
        )
        self.assertIn(
            '@click="setMemoryView(\'graph\')"',
            self.html_content,
            "Graph view tab should switch memory view to graph",
        )
        self.assertNotIn(
            "Open Graph Explorer",
            self.html_content,
            "Redundant open button should be removed when tab navigation exists",
        )

    def test_graph_explorer_root_and_assets_exist(self) -> None:
        self.assertIn(
            'id="memory-graph-explorer-root"',
            self.html_content,
            "Graph explorer mount container should exist",
        )
        self.assertIn(
            '/assets/graph-explorer/graph-explorer.js',
            self.html_content,
            "Graph explorer bundle should be included",
        )
        self.assertIn(
            '/assets/graph-explorer/graph-explorer.css',
            self.html_content,
            "Graph explorer stylesheet should be included",
        )

    def test_memory_graph_explorer_bridge_methods_exist(self) -> None:
        self.assertIn(
            "setMemoryView(view)",
            self.html_content,
            "Dashboard should include setMemoryView(view)",
        )
        self.assertIn(
            "mountMemoryExplorer()",
            self.html_content,
            "Dashboard should include mountMemoryExplorer()",
        )
        self.assertIn(
            "refreshMemoryExplorer()",
            self.html_content,
            "Dashboard should include refreshMemoryExplorer()",
        )

    def test_graph_explorer_does_not_autorun_query_on_mount(self) -> None:
        self.assertNotIn(
            "memoryExplorerHasRunQuery",
            self.html_content,
            "Dashboard should no longer track auto graph run state",
        )
        self.assertNotIn(
            "await this.memoryExplorerApp.runQuery();",
            self.html_content,
            "Graph query should not auto-run when opening graph tab",
        )

    def test_memories_view_tabs_exist(self) -> None:
        self.assertIn(
            "Library &amp; Editor",
            self.html_content,
            "Memories workspace should provide a library/editor tab",
        )
        self.assertIn(
            "Graph Explorer",
            self.html_content,
            "Memories workspace should provide a graph explorer tab",
        )
        self.assertNotIn(
            "Back To Editor",
            self.html_content,
            "Graph explorer should not show a redundant back button when view tabs already exist",
        )

    def test_old_modal_entrypoint_removed(self) -> None:
        self.assertNotIn(
            'class="memory-graph-modal"',
            self.html_content,
            "Old modal entrypoint should be removed from memory page",
        )


if __name__ == "__main__":
    unittest.main()
