# office —— OfficeCLI 工具与技能包（第三方）

本目录不是本仓库原创代码，随附的是第三方 **OfficeCLI** 发行包与其技能定义，用于处理 `.docx` / `.xlsx` / `.pptx`：

| 路径 | 说明 |
| --- | --- |
| `officecli-win-x64.exe` | OfficeCLI 官方命令行二进制（Windows x64），约 32MB |
| `OfficeCli操作说明.md` | OfficeCLI 官方使用说明 |
| `.agents/skills/notice/` | OfficeCLI 附带的技能定义（SKILL.md） |
| `.agents/skills/mini-encryption/` | OfficeCLI 附带的技能定义（SKILL.md） |

来源与许可：以上内容均来自 OfficeCLI 发行包，版权与许可归其原作者所有；本仓库仅作课程演示用途随附，未做修改。

注意：

- 二进制文件 `*.exe` 已被 `.gitignore` 忽略，不会提交到版本库；克隆仓库后需自行从 OfficeCLI 发行包放置到本目录。
- 技能通过 YAgent 的 Skill 机制加载；如需在 `YAgent` 中使用，请在项目根目录（包含 `.agents/skills` 的目录）启动，或把技能目录复制到启动目录下。
