from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from langchain_core.tools import tool

from src.agent_platform.paths import SKILLS_DIR, user_skills_dir

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class SkillInfo:
    def __init__(self, name: str, description: str, body: str, path: Path) -> None:
        self.name = name
        self.description = description
        self.body = body
        self.path = path
        # ``path`` is already the package directory that holds SKILL.md.
        self.root = path

    def artifact_listing(self, limit: int = 120) -> str:
        """Flat listing of the package so the model can open sibling files."""
        from src.agent_platform.catalog.skill_artifacts import list_files_in_root

        entries = list_files_in_root(self.root, limit=limit + 1)
        if not entries:
            return ""
        lines = []
        for item in entries[:limit]:
            marker = " (binary)" if item.get("binary") else ""
            lines.append(f"- {item['path']} — {item.get('size') or 0} B{marker}")
        if len(entries) > limit:
            lines.append(f"- … {len(entries) - limit} more files")
        return "\n".join(lines)

    def reference_block(self) -> str:
        listing = self.artifact_listing()
        if not listing:
            return ""
        return (
            "\n\n---\n\n"
            f"### Bundled files in this skill\n\n{listing}\n\n"
            "Read one with `read_skill_file` using the relative path shown above. "
            f"To run a bundled script, prefix it with the alias `@skills/{self.name}/` "
            "(for example `python @skills/"
            + self.name
            + "/scripts/build_deck.py outline.json deck.pptx`). "
            "Read the referenced documents before you start."
        )


class SkillsProvider:
    def __init__(self, skills: list[SkillInfo]) -> None:
        self.skills = {s.name: s for s in skills}

    def restrict(self, predicate) -> "SkillsProvider":
        """Drop every skill whose name does not satisfy ``predicate``."""
        self.skills = {name: skill for name, skill in self.skills.items() if predicate(name)}
        return self

    @classmethod
    def from_paths(cls, paths: list[str | Path], **_kwargs: Any) -> SkillsProvider:
        skills: list[SkillInfo] = []
        for p in paths:
            path = Path(p)
            if not path.exists():
                continue
            if (path / "SKILL.md").exists():
                s = cls._parse_skill(path / "SKILL.md")
                if s:
                    skills.append(s)
            elif path.is_dir():
                for sub in path.iterdir():
                    if sub.is_dir() and (sub / "SKILL.md").exists():
                        s = cls._parse_skill(sub / "SKILL.md")
                        if s:
                            skills.append(s)
        return cls(skills)

    @classmethod
    def _parse_skill(cls, file_path: Path) -> SkillInfo | None:
        try:
            content = file_path.read_text(encoding="utf-8")
            match = _FRONTMATTER.match(content)
            name = file_path.parent.name
            description = ""
            body = content
            if match:
                fm, body = match.groups()
                for line in fm.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip().lower()
                        v = v.strip().strip("'\"")
                        if k == "name":
                            name = v
                        elif k == "description":
                            description = v
            return SkillInfo(name=name, description=description, body=body, path=file_path.parent)
        except Exception:
            return None

    def get_instructions(self) -> str:
        """Declarative catalogue only.

        The skills are *announced*, never choreographed: no tool names, no
        ordering, no "call load_skill first". The `load_skill` /
        `read_skill_file` tools describe themselves, and the model decides on
        its own whether a skill applies to the task at hand.
        """
        if not self.skills:
            return ""
        lines = ["\n\n## Available Skills:"]
        for s in self.skills.values():
            lines.append(f"- **{s.name}**: {s.description}")
        return "\n".join(lines)

    def get_tools(self) -> list[Any]:
        skills_map = self.skills

        @tool
        def load_skill(name: str) -> str:
            """Load a skill's instructions plus the list of files bundled with it."""
            skill = skills_map.get(name)
            if not skill:
                return f"Error: Skill '{name}' not found."
            return skill.body + skill.reference_block()

        @tool
        def read_skill_file(name: str, path: str) -> str:
            """Read one bundled file from a skill package (path relative to the skill root)."""
            from src.agent_platform.catalog.skill_artifacts import ArtifactError, read_file_in_root

            skill = skills_map.get(name)
            if not skill:
                return f"Error: Skill '{name}' not found."
            try:
                row = read_file_in_root(skill.root, path)
            except ArtifactError as exc:
                return f"Error: {exc}"
            if row.get("binary"):
                return f"'{path}' is a binary file ({row.get('size')} B); open it with your shell tool instead."
            return row.get("content") or ""

        return [load_skill, read_skill_file]


class FileSkillsPlugin:
    type_id = "file_skills"
    kind = "skill"
    label = "File Skills"
    icon = "book"
    schema = {
        "type": "object",
        "properties": {
            "skill_ids": {"type": "array", "items": {"type": "string"}},
            "paths": {"type": "array", "items": {"type": "string"}},
        },
    }

    def compile(self, spec: dict[str, Any], ctx: Any) -> SkillsProvider:
        """Load only the skills the *running user* may access.

        The run context carries the authenticated user id; every resolved
        package (and every child of a directory root such as the seeded library)
        is filtered through the same resource-access decision the admin API
        uses, so an agent can never load another tenant's skill.
        """
        from src.agent_platform.catalog.skill_packages import visible_skill_names

        user_id = getattr(ctx, "user_id", None)
        if user_id is None:
            # No identity -> no accessible skills; do not even touch the store.
            return SkillsProvider([])
        paths = list(spec.get("paths") or [])
        skill_ids = list(spec.get("skill_ids") or spec.get("maf_skill_ids") or [])
        if skill_ids:
            for sid in skill_ids:
                candidate = Path(sid)
                if candidate.is_dir() and (candidate / "SKILL.md").exists():
                    paths.append(str(candidate))
                    continue
                user = user_skills_dir() / sid
                if user.is_dir() and (user / "SKILL.md").exists():
                    paths.append(str(user))
                    continue
                seeded = SKILLS_DIR / sid
                if seeded.is_dir():
                    paths.append(str(seeded))
                    continue
                from src.agent_platform.catalog.skills_store import MafSkillStore

                row = MafSkillStore.get_by_name(sid)
                if row and row.get("path"):
                    paths.append(row["path"])
        if not paths:
            paths = [str(SKILLS_DIR)]
        provider = SkillsProvider.from_paths(paths)
        allowed = visible_skill_names(user_id)
        return provider.restrict(lambda name: allowed is None or name in allowed)
