"""CLI entrypoint for scaffold generation."""

from __future__ import annotations

import argparse

from src.scaffold.generator import ScaffoldOptions, VerticalAppScaffoldGenerator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a new vertical app scaffold for Friday.")
    parser.add_argument("app_id", help="Machine id, e.g. contract_review")
    parser.add_argument("--name", required=True, help="Display name shown to users")
    parser.add_argument("--trigger", required=True, help="Skill trigger keywords, separated by |")
    parser.add_argument("--description", default="", help="Skill and app description")
    parser.add_argument("--route", default="", help="Route path, defaults to /<app-id>")
    parser.add_argument("--page", default="", help="Static page file name, defaults to <app-id>.html")
    parser.add_argument("--icon", default="🧩", help="Skill icon shown in manifests")
    parser.add_argument("--project-id", default="default", help="Manifest grouping id")
    parser.add_argument("--project-name", default="", help="Optional project display name")
    parser.add_argument("--project-description", default="", help="Optional project description")
    parser.add_argument("--artifact-kind", default="", help="Artifact kind written into the skill manifest")
    parser.add_argument("--visibility", default="public", help="Manifest visibility, e.g. public or private")
    parser.add_argument(
        "--create-project-config",
        action="store_true",
        help="Also create config/projects/<project-id>.json for app-level metadata",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing scaffold files")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = ScaffoldOptions(
        app_id=args.app_id,
        app_name=args.name,
        trigger=args.trigger,
        description=args.description,
        route=args.route,
        page=args.page,
        icon=args.icon,
        project_id=args.project_id,
        project_name=args.project_name,
        project_description=args.project_description,
        artifact_kind=args.artifact_kind,
        visibility=args.visibility,
        create_project_config=args.create_project_config,
        overwrite=args.overwrite,
    )
    generator = VerticalAppScaffoldGenerator()
    written = generator.generate(options)

    print(f"Created vertical app scaffold: {options.module_id}")
    for _, target in written.items():
        print(f" - {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
