"""Contract checks for graph explorer frontend source."""

from __future__ import annotations

import unittest
from pathlib import Path


class TestGraphExplorerFrontend(unittest.TestCase):
    """Ensure graph explorer filter UI keeps checkbox multi-select behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        src_path = (
            Path(__file__).parent.parent
            / "dashboard"
            / "frontend"
            / "graph-explorer"
            / "src"
            / "main.ts"
        )
        inspector_path = (
            Path(__file__).parent.parent
            / "dashboard"
            / "frontend"
            / "graph-explorer"
            / "src"
            / "render"
            / "inspector.ts"
        )
        graph_path = (
            Path(__file__).parent.parent
            / "dashboard"
            / "frontend"
            / "graph-explorer"
            / "src"
            / "render"
            / "graph.ts"
        )
        cls.source = src_path.read_text(encoding="utf-8")
        cls.inspector_source = inspector_path.read_text(encoding="utf-8")
        cls.graph_source = graph_path.read_text(encoding="utf-8")

    def test_filter_markup_uses_checklists(self) -> None:
        for filter_name in ("types", "states", "projects", "tags"):
            self.assertIn(
                f'data-filter-name="{filter_name}" data-el="{filter_name}"',
                self.source,
                f"{filter_name} should render as a checklist container",
            )
        self.assertNotIn(
            '<select data-el="types" multiple></select>',
            self.source,
            "Legacy multi-select element should be removed",
        )
        self.assertIn(
            'input.type = "checkbox";',
            self.source,
            "Filter options should render as checkboxes",
        )

    def test_query_payload_reads_checked_values(self) -> None:
        self.assertIn(
            "type: checkedValues(this.elements.typeChecklist)",
            self.source,
            "Type filters should read from checked checkboxes",
        )
        self.assertIn(
            "state: checkedValues(this.elements.stateChecklist)",
            self.source,
            "State filters should read from checked checkboxes",
        )
        self.assertIn(
            "projects: checkedValues(this.elements.projectChecklist)",
            self.source,
            "Project filters should read from checked checkboxes",
        )
        self.assertIn(
            "tags: checkedValues(this.elements.tagChecklist)",
            self.source,
            "Tag filters should read from checked checkboxes",
        )

    def test_default_max_nodes_is_200(self) -> None:
        self.assertIn(
            "const DEFAULT_MAX_NODES = 200;",
            self.source,
            "Graph explorer should default max nodes to 200",
        )

    def test_inspector_hides_body_preview_in_properties(self) -> None:
        self.assertIn(
            'return key !== "body_preview";',
            self.inspector_source,
            "Inspector properties should hide body_preview because content is shown separately",
        )
        self.assertIn(
            "<h4>Other Properties</h4>",
            self.inspector_source,
            "Inspector properties section should be labeled as Other Properties",
        )

    def test_inspector_uses_double_click_instead_of_expand_buttons(self) -> None:
        self.assertIn(
            "Double-click a node to expand one hop.",
            self.inspector_source,
            "Inspector should communicate double-click expansion behavior",
        )
        self.assertNotIn(
            "Expand 1 hop",
            self.inspector_source,
            "Inspector should not expose one-hop expansion button",
        )
        self.assertNotIn(
            "Expand 2 hops",
            self.inspector_source,
            "Inspector should not expose two-hop expansion button",
        )

    def test_graph_selection_applies_focus_and_double_click_expands(self) -> None:
        self.assertIn(
            'this.cy.on("dbltap", "node"',
            self.graph_source,
            "Graph renderer should bind double-click handler on nodes",
        )
        self.assertIn(
            "this.onNodeDoubleClick?.(nodeId);",
            self.graph_source,
            "Double-click handler should delegate expansion through callback",
        )
        self.assertIn(
            'selector: "node.muted"',
            self.graph_source,
            "Graph renderer should provide muted node style for focus mode",
        )
        self.assertIn(
            "private applySelectionFocus(): void {",
            self.graph_source,
            "Graph renderer should compute focus styling from selection",
        )


if __name__ == "__main__":
    unittest.main()
