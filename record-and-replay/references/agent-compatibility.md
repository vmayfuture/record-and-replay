# Agent 适配说明

同一份 `SKILL.md`、JSON 工作流和 Python helper 可在不同 Agent 中使用。适配的是技能加载、资源路径和可用工具；实际执行仍由宿主完成。宿主必须能读取配套文件；使用 helper 时需能执行 Python 3.10+。

## 安装与调用

以下目录均指完整 `record-and-replay` 文件夹的父目录。`~` 表示当前用户主目录；项目目录应是对应 Agent 的工作项目。

| Agent | 用户技能目录 | 项目技能目录 | 调用方式 |
| --- | --- | --- | --- |
| Codex | `~/.agents/skills/` | `.agents/skills/` | `$record-and-replay`，或 `/skills` 中选择 |
| Claude Code | `~/.claude/skills/` | `.claude/skills/` | `/record-and-replay`，或按任务自动选择 |
| Hermes Agent | `~/.hermes/skills/` | `.hermes/skills/` | `/record-and-replay`，或自然语言要求加载 |
| Cherry Studio | 由应用管理 | 由应用管理 | 在 Work 中为目标 Agent 启用后，用自然语言调用 |
| CodeBuddy Code / CLI | `~/.codebuddy/skills/` | `.codebuddy/skills/` | `/record-and-replay`，或按任务自动选择 |
| 其他 Agent | 以其技能设置为准 | 以其技能设置为准 | 原生加载 `SKILL.md`，或明确要求读取入口及配套资源 |

核对来源：[Codex](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)、[Claude Code](https://code.claude.com/docs/en/skills#choose-where-skills-load)、[Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)、[CodeBuddy](https://www.codebuddy.ai/docs/cli/skills)。其他 CodeBuddy 产品形态应核对当前版本的技能设置，不把 CLI 的适配结果当作所有 IDE 版本的实测。

仓库根目录的 `install.py` 提供离线复制安装和 ZIP 导出，不联网、不更改 Agent 的权限或 MCP 配置。选择一个 Agent 安装；不要把同名技能同时装入多个加载目录。

## Codex

```text
python install.py --agent codex
python install.py --agent codex --project <project-dir>
```

当前默认使用官方文档中的 `.agents/skills`。若已有环境采用其他已配置的技能目录，用 `--skills-dir <skills-root>` 指定。`agents/openai.yaml` 提供 Codex 界面信息；其他 Agent 无需依赖该文件。加载后执行 `$record-and-replay 记录这次文件处理操作`。

## Claude Code

```text
python install.py --agent claude-code
```

使用 `/record-and-replay 记录这次文件处理操作`。脚本运行使用当前环境的文件与终端工具，浏览器操作使用已有浏览器工具或 MCP。通用入口不设置 Claude 专用的 `allowed-tools`、hooks 或 `context: fork`，保留宿主的权限和工具配置。

## Hermes Agent

```text
python install.py --agent hermes
```

安装器尊重非空 `HERMES_HOME`，未设置时使用 `~/.hermes`。如果使用命名 profile，传入该 profile 的 `skills` 目录；项目安装使用 `.hermes/skills`，并按 Hermes 的项目技能机制由用户决定是否信任该项目。安装器不自动修改信任设置。

在新会话通过 `/record-and-replay` 调用；Agent 也可用其原生 `skill_view` 读取入口和配套参考文件。helper 应在 Hermes 实际执行终端所在环境运行，不能假定聊天客户端与远端终端共享同一文件系统。参考 [Hermes 使用指南](https://hermes-agent.nousresearch.com/docs/guides/work-with-skills)。

## Cherry Studio

使用具有 Skills 功能的版本，进入「设置 → Skills」。可从本地文件夹导入完整 `record-and-replay` 目录，或从 ZIP 导入；也可在 GitHub 来源粘贴仓库内 `record-and-replay/SKILL.md` 的链接。

```text
python install.py --agent cherry-studio --zip ./record-and-replay.zip
```

该命令只导出包，并未完成应用内安装。导入后启用技能的全局开关，再进入「Work → Agent 菜单 → 编辑 → Skills」为具体 Agent 启用。从下一条消息开始使用。文件处理需设置可访问的工作目录；运行 helper 需该 Agent 的执行工具可以调用 Python。普通聊天助手仅接收提示词时无法完成本包的日志管理和实际回放。[Cherry Studio 官方安装与绑定说明](https://cherryai.com/docs/en/advanced-basic/extensions/skills/)

## CodeBuddy

```text
python install.py --agent codebuddy
```

在 CodeBuddy Code / CLI 中通过 `/skills` 核对已加载技能，再用 `/record-and-replay`。操作映射到该会话真实可用的文件、终端、浏览器或 MCP 工具。入口不固定工具名称，也不修改权限白名单。[CodeBuddy 官方技能说明](https://www.codebuddy.ai/docs/cli/skills)

## 其他 Agent 与能力检查

```text
python install.py --agent generic --skills-dir <skills-root>
```

若宿主没有技能目录但能读取文件并操作工具，可要求它读取完整入口和相关参考文件后执行任务；这属于手动加载，不能称为原生技能安装。只粘贴说明文字不会提供 Python、浏览器、桌面控制或后台录制能力。

开始执行前核对这三件事：入口及配套文件已加载；记录目录与证据在执行环境中可访问；当前工具能够完成所需动作及后置条件核验。缺少实际操作工具时生成计划并说明缺少的能力。

## 跨 Agent 恢复

例如 Codex 录制文件合并后，可把记录目录交给 Hermes。Hermes 先验证工作流与参数，再加载日志、核对输入和输出；`started` 或 `outcome_unknown` 必须核查实际结果，不能因为更换了 Agent 就重试。换了主机路径、账号或执行定义时建立新日志。

日志保存任务状态，不保存宿主的进程、浏览器会话或登录凭据。读取格式兼容不等于两套工具有相同权限和执行能力。

## 验证口径

区分官方格式支持、安装器文件布局测试、原生技能识别测试和完整任务回放。安装器测试只证明文件正确到位，不能代替某个 Agent 的实际任务验证。当前实测及未验证部分见仓库首页。
