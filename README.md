# Record and Replay

把一次实际操作保存成可替换参数的工作流，下次按当前环境重新执行、核验结果，并在中断后查明状态再继续。

本文是使用说明，单独保存这份 Markdown 不会安装可运行的 Skill。完整包的入口是 `record-and-replay/SKILL.md`，安装时需要保留整个 `record-and-replay` 目录及其配套文件。

## 可以用来做什么

- 重复处理文件：换一批输入、换一个输出目录，按同一流程整理或生成文件。
- 重复操作业务系统：换月份、查询词或筛选条件，重新执行经过记录的操作。
- 保存 AI 已完成的办事流程，供后续重复使用。
- 从中断处恢复：先核验上次操作是否完成，减少重复写入或提交。

浏览器和桌面场景需要当前环境提供对应操作工具。Skill 描述如何记录、执行和验证；实际可执行范围取决于这些工具和当前任务授权。

## 快速开始

将完整的 `record-and-replay` 文件夹放入当前 Codex 环境的 Skill 目录，例如 `$CODEX_HOME/skills/record-and-replay`。在能够加载该 Skill 的聊天里使用：

```text
$record-and-replay 记录这次操作：把这两份文本按顺序合并到一个新文件，保留原文件。
```

记录一次实际执行后，再替换参数回放：

```text
$record-and-replay 回放这个 workflow.json，把输入换成另外两份文本，输出到一个新文件。
```

中断之后恢复：

```text
$record-and-replay 从这个 runs/run.json 恢复，先检查上次输出文件是否已生成且内容正确。
```

只生成计划：

```text
$record-and-replay 根据我给的步骤生成草稿，只输出回放计划，不执行操作。
```

只根据文字描述生成的流程会标为草稿；记录了实际动作和前后证据的流程才标为已观察。实际回放并核验结果之后，才能标为验证通过。

## 录制、回放与恢复

### 录制

先确定任务和完成条件，记录实际动作的目的、目标、输入和执行前后的证据。月份、文件路径、查询词等可变内容提取为参数，例如 `{{period}}`。

支持记录 agent 实际执行的动作，或导入真正 recorder 捕获的操作轨迹。人工键鼠演示需要可用的动作捕获工具；本包没有后台键鼠监听器。前后截图可以证明状态，无法还原中间的全部动作。

### 回放

回放时检查应用、账号和起始条件，根据当前页面或窗口重新定位目标。历史坐标、临时元素编号、窗口 ID 不会被当作永久定位依据。

每步操作先记入运行日志，执行后检查完成条件。点击“导出”成功，仍需确认导出的文件存在并符合预期。

### 恢复

如果日志只记录了 `started`，就不能假定动作没有完成。先检查实际文件、目标系统或动作回执：

- 确认完成且结果符合要求：补写核验结果，继续后续步骤。
- 确认未完成：按该步骤的重试规则继续。
- 无法确定：保留断点和核验缺口，暂不重复该动作。

换参数、换账号或修改执行步骤后，创建新的运行日志。

## 完整包组成

```text
record-and-replay/
  SKILL.md
  agents/
    openai.yaml
  assets/
    example-workflow.json
  references/
    workflow-format.md
  scripts/
    workflow.py
```

| 文件 | 用途 |
| --- | --- |
| `SKILL.md` | agent 使用的完整工作规则 |
| `agents/openai.yaml` | Skill 的显示名称和默认提示 |
| `assets/example-workflow.json` | 报表导出流程模板；尚未实际执行 |
| `references/workflow-format.md` | 工作流、参数、日志和恢复格式 |
| `scripts/workflow.py` | 使用 Python 3.10+ 标准库的辅助脚本 |

一次记录建议保存在独立目录：

```text
my-recording/
  workflow.json
  evidence/
  runs/
  parameters.local.json
```

`workflow.json` 保存流程定义，`evidence/` 保存经检查的证据，`runs/` 保存各次运行日志。包含秘密的参数文件、登录状态和未经处理的截图应留在本地，不能随工作流公开。

## Python helper 做什么

helper 提供结构校验、参数化计划和运行日志管理：

```text
python "<skill-dir>/scripts/workflow.py" validate workflow.json
python "<skill-dir>/scripts/workflow.py" plan workflow.json --params parameters.local.json
python "<skill-dir>/scripts/workflow.py" journal-init workflow.json --params parameters.local.json --out runs/run-01.json
```

将 `<skill-dir>` 替换为完整包安装后的目录，在选定的记录目录运行命令。更多日志命令见包内的 `references/workflow-format.md`。

helper 不执行工作流中的工具调用或命令，也不注入键鼠。实际文件处理、浏览器或桌面动作由 agent 调用当前环境已有工具完成；`plan` 成功只代表生成了计划。

## 实际验证范围

当前实现已完成以下验证：

1. **16 项 helper 行为测试**：覆盖参数类型、秘密脱敏、禁止临时定位目标、录制与验证证据要求、日志匹配、未知结果恢复、不覆盖已有日志，以及不执行输入中的命令文本等。
2. **真实本地文件录制与回放**：先合并两份 UTF-8 文本，再换为另一组输入和新输出路径执行。验证原文件未变，输出包含预期中文和 emoji。
3. **未知结果恢复**：模拟文件已经写好、日志停在 `started` 的情况。恢复时核验文件后确认已完成，没有重复执行写入。

第二次回放的实际输出：

```text
Replay second set
中文与 emoji：🐼
```

上述实测针对本地文件流程和 helper。浏览器、桌面点击及报表导出模板尚未在真实网站或桌面应用上验证，不能据此声称支持所有页面、软件版本或业务系统。

## 使用边界

一次录制不能证明未演示的分支、循环或软件版本也能正确运行。界面变化后需要重新定位和核验；业务步骤变化时应保存新流程版本。

工作流不会扩大当前授权。重复发送、购买、删除或对外提交等动作，仍按本次用户指令的范围执行。口令、令牌和验证码使用秘密参数或当前环境的凭据方式，不能写入公开工作流和日志。

先选一个步骤少、容易检查结果的任务，完成一次真实记录与回放，再扩大使用范围。
