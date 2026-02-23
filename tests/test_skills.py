"""test skills."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


SKILLS_DIR = Path(__file__).parent.parent / "skills"

EXPECTED_SKILLS = [
    "lerim",
]


class TestSkills(unittest.TestCase):
    def test_only_supported_skill_directories_exist(self) -> None:
        actual = sorted(
            path.name
            for path in SKILLS_DIR.iterdir()
            if path.is_dir() and (path / "SKILL.md").exists()
        )
        self.assertEqual(actual, sorted(EXPECTED_SKILLS))

    def test_skill_files_exist(self) -> None:
        for skill_name in EXPECTED_SKILLS:
            path = SKILLS_DIR / skill_name / "SKILL.md"
            self.assertTrue(path.exists(), f"Missing skill: {path}")

    def test_skill_has_valid_frontmatter(self) -> None:
        for skill_name in EXPECTED_SKILLS:
            path = SKILLS_DIR / skill_name / "SKILL.md"
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), f"No frontmatter in {skill_name}")
            # Extract frontmatter
            end = text.index("---\n", 4)
            fm_text = text[4:end]
            fm = yaml.safe_load(fm_text)
            self.assertIsInstance(fm, dict, f"Invalid frontmatter in {skill_name}")
            self.assertIn("name", fm, f"Missing 'name' in {skill_name}")
            self.assertIn("description", fm, f"Missing 'description' in {skill_name}")

    def test_skill_has_body_content(self) -> None:
        for skill_name in EXPECTED_SKILLS:
            path = SKILLS_DIR / skill_name / "SKILL.md"
            text = path.read_text(encoding="utf-8")
            end = text.index("---\n", 4) + 4
            body = text[end:].strip()
            self.assertTrue(len(body) > 20, f"Skill {skill_name} has no body content")
