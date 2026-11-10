"""System Prompt 构建：把项目级 Skill 清单注入 LLM 上下文。"""
from __future__ import annotations

from .skills import Skill


def build_skills_system_prompt(skills: list[Skill]) -> str:
    """把扫描到的 Skill 拼成一段英文系统提示，告知 LLM 可用技能列表与按需加载方式。

    如果没有任何 Skill，返回空字符串（调用方应跳过注入）。
    """
    if not skills:
        return ""

    lines = ["You have access to the following skills:"]
    for s in skills:
        desc = s.description.strip().rstrip(".")
        lines.append(f"- '{s.name}': {desc}.")
    lines.append(
        "Use the `loadSkill` tool to load the full instructions of a skill when needed. "
        "You can also load additional files referenced by a skill."
    )
    return "\n".join(lines)
