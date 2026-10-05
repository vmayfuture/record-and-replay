#!/usr/bin/env python3
"""Install the shared skill into an agent directory or export a portable ZIP."""

import argparse
import json
import os
from pathlib import Path
import sys
import zipfile


NAME = "record-and-replay"
FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "assets/example-workflow.json",
    "references/workflow-format.md",
    "references/agent-compatibility.md",
    "scripts/workflow.py",
)
DIRECTORIES = {
    "codex": ".agents",
    "claude-code": ".claude",
    "hermes": ".hermes",
    "codebuddy": ".codebuddy",
}
AGENTS = (*DIRECTORIES, "cherry-studio", "generic")


def payload(source):
    """Read only release files; exclude recordings, credentials and caches."""
    result = {}
    for relative in FILES:
        file = source / relative
        if file.is_symlink() or not file.is_file():
            raise ValueError(f"Required regular file is missing: {relative}")
        if source.is_symlink() or any(
            (source / parent).is_symlink() for parent in Path(relative).parents
        ):
            raise ValueError(f"Symlinked source paths are not supported: {relative}")
        data = file.read_bytes()
        if not data:
            raise ValueError(f"Required file is empty: {relative}")
        result[relative] = data
    if result["SKILL.md"].decode("utf-8").splitlines()[:2] != ["---", "name: record-and-replay"]:
        raise ValueError("Source is not the record-and-replay skill")
    return result


def skill_root(agent, project=None, skills_dir=None):
    if skills_dir is not None:
        return Path(skills_dir).expanduser().absolute()
    if agent == "generic":
        raise ValueError("generic requires --skills-dir")
    if project is not None:
        return Path(project).expanduser().absolute() / DIRECTORIES[agent] / "skills"
    if agent == "hermes" and os.environ.get("HERMES_HOME", "").strip():
        return Path(os.environ["HERMES_HOME"].strip()).expanduser().absolute() / "skills"
    return Path.home() / DIRECTORIES[agent] / "skills"


def install(destination, files, dry_run=False):
    if destination.is_symlink():
        raise ValueError("Destination is a symlink; choose a different skill directory")
    if destination.exists():
        unchanged = destination.is_dir() and all(
            (destination / name).is_file()
            and not (destination / name).is_symlink()
            and (destination / name).read_bytes() == content
            for name, content in files.items()
        )
        if not unchanged:
            raise ValueError("An existing skill differs; preserve it and choose another directory")
        return "already-installed"
    if dry_run:
        return "planned"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()  # Exclusive creation: never replace an existing skill.
    for name, content in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(content)
    return "installed"


def export_archive(destination, files, dry_run=False):
    if destination.is_symlink():
        raise ValueError("ZIP destination is a symlink")
    # Release files are text. A Windows CRLF checkout must match the bundled ZIP.
    entries = {f"{NAME}/{name}": content.replace(b"\r\n", b"\n") for name, content in files.items()}
    if destination.exists():
        try:
            with zipfile.ZipFile(destination) as archive:
                unchanged = sorted(archive.namelist()) == sorted(entries) and all(
                    archive.read(name) == content for name, content in entries.items()
                )
        except (OSError, zipfile.BadZipFile):
            unchanged = False
        if not unchanged:
            raise ValueError("The ZIP path already exists with different content; choose a new path")
        return "already-exported"
    if dry_run:
        return "planned-export"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return "exported-for-import"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent / NAME)
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--project", type=Path, help="Install within this project")
    location.add_argument("--skills-dir", type=Path, help="Override the parent skills directory")
    parser.add_argument("--zip", type=Path, help="Cherry Studio ZIP destination")
    parser.add_argument("--dry-run", action="store_true", help="Show the destination without writing")
    args = parser.parse_args(argv)
    try:
        if args.agent == "cherry-studio":
            if args.project is not None or args.skills_dir is not None:
                raise ValueError("Cherry Studio uses application import; specify --zip instead")
            destination = (args.zip or Path(f"{NAME}.zip")).expanduser().absolute()
            files = payload(args.source.expanduser().absolute())
            status = export_archive(destination, files, args.dry_run)
        else:
            if args.zip is not None:
                raise ValueError("--zip is for --agent cherry-studio")
            destination = skill_root(args.agent, args.project, args.skills_dir) / NAME
            files = payload(args.source.expanduser().absolute())
            if args.source.expanduser().absolute() == destination:
                raise ValueError("Source and destination must be different directories")
            status = install(destination, files, args.dry_run)
        result = {"agent": args.agent, "status": status, "destination": str(destination), "files": len(files)}
        if args.agent == "cherry-studio":
            result["next"] = "Import in Settings > Skills, enable globally, then enable for the Work agent."
        elif args.agent == "hermes" and args.project is not None:
            result["next"] = "Start a new Hermes session; decide whether to trust the project skills."
        else:
            result["next"] = "Load record-and-replay in the target agent; restart if discovery is stale."
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ValueError, OSError, UnicodeError, zipfile.BadZipFile) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
