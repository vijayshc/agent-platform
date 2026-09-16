from __future__ import annotations

import pytest
from platform_samples import SAMPLES_DIR

# The bundled example skill packages live with the samples; the framework's
# built-in skills directory is empty by default.
SKILLS_DIR = SAMPLES_DIR / "skills"
from src.agent_platform.plugins.skills.file_skills import SkillsProvider


def test_skills_provider_loads_seeded_skills():
    provider = SkillsProvider.from_paths([str(SKILLS_DIR)])
    instructions = provider.get_instructions()
    assert "Available Skills" in instructions
    tools = provider.get_tools()
    # The provider ships both a loader and a bundled-file reader.
    assert {t.name for t in tools} == {"load_skill", "read_skill_file"}


def test_load_skill_tool_returns_body():
    provider = SkillsProvider.from_paths([str(SKILLS_DIR)])
    tools = provider.get_tools()
    load_skill_fn = tools[0]
    if provider.skills:
        first_name = list(provider.skills.keys())[0]
        res = load_skill_fn.invoke({"name": first_name})
        assert len(res) > 0
