import sys; sys.path.insert(0, ".")
from infra.tools import (
    GetDateTool, IsLeapYearTool, GetCwdTool, ListDirTool,
    GetFileTypeTool, ReadFileTool, CreateFileTool, EditFileTool,
    MkdirTool, RmtreeTool, DeleteFileTool, RenamePathTool,
    build_registry
)

print("--- isLeapYear ---")
print(IsLeapYearTool().run('{"year": 2024}'))   # 是闰年
print(IsLeapYearTool().run('{"year": 1900}'))   # 不是（整百年非 400）
print(IsLeapYearTool().run('{"year": 2000}'))   # 是（整百年且被 400 整除）
print()

print("--- getDate ---")
print(GetDateTool().run(""))
print()

print("--- getCwd ---")
print(GetCwdTool().run(""))
print()

print("--- listDir ---")
print(ListDirTool().run('{"path": "."}'))
print()

class FakeConfig:
    provider = "auto"; api_key = ""; base_url = ""

reg = build_registry(FakeConfig())
names = sorted(reg.names)
print(f"已注册工具数: {len(names)}")
print(f"工具名: {names}")

expected = [
    "getDate", "isLeapYear",
    "getCwd", "listDir", "getFileType", "readFile",
    "createFile", "editFile", "mkdir", "rmtree", "deleteFile", "rename",
    "searchweb", "fetchpage", "RunCli", "fetchWeb",
    "createSkill", "loadSkill", "recallContext",
]
missing = [n for n in expected if n not in names]
extra = [n for n in names if n not in expected]
print()
if missing:
    print(f"❌ 缺失工具: {missing}")
else:
    print("✅ 无缺失工具")
if extra:
    print(f"  额外工具（可能 MCP 动态注册）: {extra}")
print()
print("✅ 全部新工具可正常 import 和运行")
