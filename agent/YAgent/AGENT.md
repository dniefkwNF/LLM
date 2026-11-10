# YAgent 项目指引（AGENT.md）

本文件会在启动时被读取并注入系统消息，用于约束 Agent 在本项目中的行为。

## 工作边界

- 所有文件 / 目录工具只能操作 `YAGENT_FS_ROOT`（默认是启动目录）内的路径，越界会返回 `[路径越界]`。
- 禁止删除或移动工作根目录本身；`rmtree` 只用于确认可丢弃的临时目录。
- 修改代码前先读文件，不要凭猜测编辑。

## 工具使用

- `getDate` / `isLeapYear`：日期与闰年判断。
- `getCwd` / `listDir` / `getFileType` / `readFile`：只读探查，优先使用。
- `createFile` / `editFile` / `mkdir` / `rename`：写入类操作，执行前说明目标路径。
- `deleteFile` / `rmtree`：破坏性操作，仅在用户明确要求时使用。
- `searchweb` / `fetchpage` / `fetchWeb`：需要最新信息或读取网页正文时使用。
- `createSkill` / `loadSkill`：需要复用流程时创建技能；技能正文按需加载，不要一次性读入全部技能。
- MCP 工具以 `[MCP:server]` 前缀区分来源，调用失败时把错误原样告知用户。

## 回复风格

- 中文回答，先给结论，再给必要依据。
- 涉及文件位置时给出相对路径与行号。
- 工具报错时不要隐藏，说明原因并给出下一步建议。
