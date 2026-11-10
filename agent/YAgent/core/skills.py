"""项目级 Skill 发现与注册。

Skill 是位于 ``.agents/skills/<skill_name>/SKILL.md`` 的 Markdown 文件，
文件头部以 YAML Frontmatter 声明元数据。典型格式::

    ---
    version: "1.0.0"
    name: 撰写通知
    description: "撰写公司内部通知"
    rules:
      - 标题必须带上部门名称
    ---

    （正文省略……）

发现时同时扫描项目根及其一级子目录下的 ``.agents/skills``，因此
``<root>/.agents/skills`` 与 ``<root>/office/.agents/skills`` 里的 Skill
都会被加载。初始化阶段仅加载 frontmatter 中的 ``name`` 与 ``description``，
正文通过 ``Skill.read_content()`` 按需延迟读取，避免启动时解析全部文件。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# ---------- 数据类 ----------

@dataclass(frozen=True)
class Skill:
    """已注册的项目 Skill 元数据。

    ``content`` 不随实例加载，需调用 :meth:`read_content` 才读磁盘。
    """

    name: str
    description: str
    dir: Path          # 子目录路径，如 <root>/.agents/skills/notice
    skill_file: Path   # 完整 SKILL.md 路径

    def read_content(self) -> str:
        """读取 SKILL.md 的完整正文（含 frontmatter）。"""
        return self.skill_file.read_text(encoding="utf-8")


# ---------- Frontmatter 解析 ----------

_FRONTMATTER_DELIM = "---"


def _split_frontmatter(text: str) -> tuple[str, str]:
    """将文件拆成 ``(frontmatter_body, rest_of_file)``。

    支持两种格式：

    1. **标准 YAML Frontmatter**（以 ``---`` 开头并结束）::

           ---
           name: xxx
           ---
           正文...

    2. **裸 YAML 头部**（无前导 ``---``，连续 ``key: value`` 行后紧跟空行再是正文）::

           name: xxx
           description: yyyy

           正文...

    两种格式都会被识别。都匹配不上时返回 ``("", text)``。
    """
    stripped = text.lstrip("\ufeff")  # 去 BOM

    # 格式 1：标准 Frontmatter
    if stripped.startswith(_FRONTMATTER_DELIM) and stripped[
        len(_FRONTMATTER_DELIM) : len(_FRONTMATTER_DELIM) + 1
    ] in ("\n", "\r"):
        end = stripped.find("\n" + _FRONTMATTER_DELIM, len(_FRONTMATTER_DELIM))
        # 也兼容 \r\n 结尾
        if end == -1:
            end = stripped.find("\r\n" + _FRONTMATTER_DELIM, len(_FRONTMATTER_DELIM))
        if end != -1:
            # 取到 --- 前
            fm = stripped[len(_FRONTMATTER_DELIM):end].strip()
            after_delim = stripped[end + len(_FRONTMATTER_DELIM):]
            # 跳过结束分隔符那行的 --- 本身
            nl_pos = after_delim.find("\n")
            if nl_pos != -1:
                rest = after_delim[nl_pos + 1:].lstrip("\n\r")
            else:
                rest = after_delim
            return fm, rest

    # 格式 2：裸 YAML 头部，遇到第一个空行段之后就是正文
    lines = stripped.splitlines()
    fm_lines: list[str] = []
    in_body = False
    for i, line in enumerate(lines):
        # 空行之前且已经有过内容？可能是分隔
        if not line.strip():
            if fm_lines:
                # 空行作为 frontmatter / 正文分界
                rest_idx = i + 1
                rest = "\n".join(lines[rest_idx:]).lstrip("\n\r")
                return "\n".join(fm_lines), rest
            continue
        fm_lines.append(line)

    # 没遇到空行分隔：把全部都当 frontmatter（没有正文）
    if fm_lines:
        return "\n".join(fm_lines), ""
    return "", stripped


def _parse_simple_yaml(text: str) -> dict[str, str]:
    """极简 YAML 解析器，只处理 SKILL.md frontmatter 中常见的顶层 ``key: value`` 行。

    支持：
      - ``name: 撰写通知``
      - ``description: "带空格的值"``
      - ``description: '单引号也可'``

    不处理嵌套结构（list/mapping），因为初始化阶段只关心 name / description。
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # 跳过顶层列表项 "- xxx"
        if line.startswith("- "):
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()
        # 去掉包裹引号
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


# ---------- 发现 ----------

def _candidate_skill_dirs(root: Path) -> list[Path]:
    """列出所有待扫描的 ``.agents/skills`` 目录。

    顺序（优先级由高到低）：

    1. ``<root>/.agents/skills``（项目根，最高优先级）
    2. ``<root>/<一级子目录>/.agents/skills``（按子目录名排序）

    隐藏目录（以 ``.`` 开头，如 ``.git`` / ``.agents`` 自身）不参与扫描。
    """
    candidates = [root / ".agents" / "skills"]
    try:
        subdirs = sorted(
            p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    except OSError:
        subdirs = []
    candidates.extend(sub / ".agents" / "skills" for sub in subdirs)
    return candidates


def discover_skills(root: Path) -> list[Skill]:
    """扫描项目根及其一级子目录下的 ``.agents/skills/``，返回所有合法 Skill。

    扫描范围：
      - ``<root>/.agents/skills/*/SKILL.md``
      - ``<root>/<一级子目录>/.agents/skills/*/SKILL.md``

    规则：
      - 目录不存在或为空时静默跳过，不抛错。
      - 缺少 SKILL.md 或 frontmatter 里没有 ``name`` / ``description`` 的子目录会被
        **跳过并打印一条警告**，不影响其他 Skill 的加载。
      - 同名 Skill 出现多次时，**保留优先扫描到的那一个**（根目录优先），
        后出现的打印一条冲突警告。
    """
    loaded: list[Skill] = []
    seen_names: dict[str, Path] = {}

    for skills_dir in _candidate_skill_dirs(root):
        if not skills_dir.is_dir():
            continue

        for subdir in sorted(skills_dir.iterdir()):
            if not subdir.is_dir():
                continue
            skill_file = subdir / "SKILL.md"
            if not skill_file.is_file():
                print(f"[skills] 跳过 {subdir}：缺少 SKILL.md")
                continue

            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError as exc:
                print(f"[skills] 跳过 {subdir}：读取失败 {exc}")
                continue

            fm_body, _ = _split_frontmatter(text)
            if not fm_body:
                print(f"[skills] 跳过 {subdir}：SKILL.md 缺少 YAML Frontmatter")
                continue

            meta = _parse_simple_yaml(fm_body)
            name = meta.get("name", "").strip()
            description = meta.get("description", "").strip()

            if not name:
                print(f"[skills] 跳过 {subdir}：frontmatter 缺少 name")
                continue
            if not description:
                print(f"[skills] 跳过 {subdir}：frontmatter 缺少 description")
                continue

            if name in seen_names:
                print(
                    f"[skills] 跳过 {subdir}：Skill 名称 '{name}' 与 "
                    f"{seen_names[name]} 重复，保留先扫描到的那个"
                )
                continue

            seen_names[name] = subdir
            loaded.append(
                Skill(name=name, description=description, dir=subdir, skill_file=skill_file)
            )

    return loaded
