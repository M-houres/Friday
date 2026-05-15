from pathlib import Path

import pytest

from src.scaffold.generator import ScaffoldOptions, VerticalAppScaffoldGenerator


class TestVerticalAppScaffold:
    def test_generate_vertical_app_files(self, tmp_path):
        template_root = Path(__file__).resolve().parent.parent / "templates" / "vertical_app"
        generator = VerticalAppScaffoldGenerator(repo_root=tmp_path, template_root=template_root)

        written = generator.generate(
            ScaffoldOptions(
                app_id="contract-review",
                app_name="合同审查助手",
                trigger="合同|法务|审查",
                description="面向合同审查场景的垂直应用骨架",
                project_id="legal_suite",
                create_project_config=True,
                artifact_kind="markdown",
            )
        )

        assert "skill.py.template" in written
        skill_file = tmp_path / "skills" / "contract_review_skill.py"
        page_file = tmp_path / "static" / "contract-review.html"
        manifest_file = tmp_path / "config" / "skills" / "contract_review.json"
        project_file = tmp_path / "config" / "projects" / "legal_suite.json"

        assert skill_file.exists()
        assert "class ContractReviewSkill" in skill_file.read_text(encoding="utf-8")
        assert "合同审查助手" in skill_file.read_text(encoding="utf-8")

        assert page_file.exists()
        assert "/contract-review" in page_file.read_text(encoding="utf-8")

        assert manifest_file.exists()
        manifest_text = manifest_file.read_text(encoding="utf-8")
        assert "\"project_id\": \"legal_suite\"" in manifest_text
        assert "\"artifact_kind\": \"markdown\"" in manifest_text

        assert project_file.exists()
        project_text = project_file.read_text(encoding="utf-8")
        assert "合同审查助手" in project_text
        assert "\"pages\"" in project_text

    def test_refuse_overwrite_without_flag(self, tmp_path):
        template_root = Path(__file__).resolve().parent.parent / "templates" / "vertical_app"
        generator = VerticalAppScaffoldGenerator(repo_root=tmp_path, template_root=template_root)
        options = ScaffoldOptions(
            app_id="briefing_app",
            app_name="行业简报助手",
            trigger="简报|行业",
        )

        generator.generate(options)

        with pytest.raises(FileExistsError):
            generator.generate(options)
