# 工作流、参数与运行日志

此文件用于编写、校验和回放 JSON 工作流。helper 使用 Python 3.10+ 标准库。实际操作由 agent 和当前环境已有工具执行。

## 工作流字段

必填字段为 `version`、`name`、`goal`、`context`、`parameters`、`recording`、`steps`、`verification`。

| 字段 | 约定 |
| --- | --- |
| `version` | 整数 `1` |
| `name` | 小写字母、数字、连字符组成的流程名 |
| `goal` | 实际要达到的任务结果 |
| `context` | 至少包含 `application`；补充域名、账号身份和起始条件等非秘密上下文 |
| `parameters` | 参数名到定义的映射，定义含 `type`、`required`、`secret`，可选 `default` |
| `recording.source` | `agent-executed` / `recorder-import` / `user-description` |
| `recording.status` | `draft` / `observed` |
| `steps` | 有序、非空的步骤列表，ID 唯一 |
| `verification.status` | `not-run` / `passed` / `failed` |
| `verification.run_evidence` | `passed` 时必须提供非空字符串，引用实际试跑证据；不是列表 |

`user-description` 来源只能是 `draft`。`observed` 必须为每一步提供实际录制的 `recorded_evidence.before` 和 `recorded_evidence.after`；引用文件实际存在与内容可信仍须由 agent 核验，格式校验器无法证明发生过操作。

回放验证成功后，`verification` 例如 `{"status": "passed", "run_evidence": "evidence/replay-result.json"}`。多项证据整理到该结果文件，在这里引用一个文件。尚未试跑则保持 `{"status": "not-run"}`。

每一步包含：

```json
{
  "id": "fill-period",
  "intent": "设置月份",
  "operation": "fill",
  "target": {
    "description": "报表筛选区的月份输入框",
    "role": "textbox",
    "name": "月份",
    "within": "报表筛选区"
  },
  "value": "{{period}}",
  "precondition": "月份输入框唯一且可编辑。",
  "postcondition": "实际值等于 {{period}}。",
  "effect": "local_write",
  "retry": "safe"
}
```

`operation` 可选 `open`、`click`、`fill`、`select`、`key`、`scroll`、`drag`、`wait`、`tool`、`manual`。这些值描述动作类别，不能被解释成可任意执行的代码。`value` 按需填写；`tool` 步骤可用结构化值描述参数，但调用前必须核对当前工具及其参数格式。

`target` 保存语义定位。不要保存永久使用的坐标、element index、临时 DOM ref 或窗口 ID。拖拽保存起点和终点的语义描述；点击的坐标只能根据当前观察临时计算。

`effect` 可选 `none`、`local_write`、`external_write`、`irreversible`。重试规则可选 `safe`、`check-first`、`never-on-unknown`。填写默认值必须针对动作的实际语义：追加、提交和下载不应因为是“本地动作”就自动标为 `safe`。

## 参数解析

参数类型为 `string`、`integer`、`number`、`boolean`。缺少必需参数且没有默认值时，计划生成失败。未声明参数和不符合类型的值会被拒绝。

```json
{
  "period": {"type": "string", "required": true, "secret": false},
  "limit": {"type": "integer", "required": false, "secret": false, "default": 20},
  "access_token": {"type": "string", "required": true, "secret": true}
}
```

整个值为 `{{limit}}` 时保留参数类型；嵌入普通文字中时转成文字。输入参数里的 `{{...}}` 是输入数据，不继续执行二次插值。模板不支持表达式或代码求值。

秘密参数禁止字面量默认值。计划显示 `<secret:access_token>`；日志与指纹不包含秘密值或秘密值的 hash。实际操作需要秘密时使用当前环境支持的凭据通道，或交给用户完成登录。helper 的脱敏只保护明确标记为秘密的参数，不能自动判断所有个人信息。

命令示例的相对路径均从选定记录目录运行，`<skill-dir>` 替换为安装后的 Skill 目录。用结构化参数传给工具；向 shell 传路径或文字时使用目标 shell 的正确转义。

```text
python "<skill-dir>/scripts/workflow.py" validate workflow.json
python "<skill-dir>/scripts/workflow.py" plan workflow.json --params parameters.local.json
python "<skill-dir>/scripts/workflow.py" journal-init workflow.json --params parameters.local.json --out runs/run-01.json
```

非秘密参数文件示例：

```json
{"period": "2026-10", "output_file": "./exports/report-2026-10.xlsx"}
```

## 日志与恢复

工作流是操作定义；日志是某一次运行的事实。每次运行创建独立日志，已有文件不会被覆盖。

执行指纹绑定 `version`、`name`、`goal`、`context`、`parameters`、`steps`；排除 `recording`、`verification` 和步骤中的 `recorded_evidence` 元信息。因此补写证据不会改变执行定义，但动作、语义目标、前后条件、参数或环境变化会使旧日志不再匹配。

另外绑定本次非秘密参数。账号变化须反映在非秘密 `context` 中；秘密值脱敏后的指纹不能证明账号相同，agent 仍需核验实际登录身份。

动作发出前持久化 `started`，检查后置条件成功后写 `verified`，证据使用不含秘密的事实描述或记录目录内的证据路径：

```text
python "<skill-dir>/scripts/workflow.py" journal-step runs/run-01.json --step fill-period --status started
python "<skill-dir>/scripts/workflow.py" journal-step runs/run-01.json --step fill-period --status verified --evidence "月份输入框实际值为2026-10"
python "<skill-dir>/scripts/workflow.py" plan workflow.json --params parameters.local.json --journal runs/run-01.json
```

`plan` 的恢复决策是执行前提示，不是执行结果：

- `recheck`：以前验证过，复核当前结果和依赖后再决定是否保留。
- `reconcile`：只有 `started` 或 `outcome_unknown`，存在未知结果，计划标记阻断。
- `execute`：待执行或先前明确失败；仍要满足前置条件、重试规则及授权。

超时或中断时：

```text
python "<skill-dir>/scripts/workflow.py" journal-step runs/run-01.json --step export-report --status outcome_unknown
```

查询实际文件或目标系统，得到足够证据后再处理未知结果：

```text
python "<skill-dir>/scripts/workflow.py" journal-reconcile runs/run-01.json --step export-report --outcome applied --evidence "已确认本次下载文件存在且内容为2026-10"
```

`applied` 转为 `verified`，前提是 agent 真正核验了后置条件。`not-applied` 转为 `failed`，允许根据重试规则开始新的尝试。两者都要求明确证据并保留历史。未知结果不可直接改为 `started` 或 `verified`。

`--evidence` 的文字由调用者提供，helper 无法识别后来手工粘贴进去的任意秘密。调用前须脱敏，只传不含秘密的事实摘要、回执引用或证据路径。

只有能确定未发生副作用的失败才记为 `failed`。当一个操作改变了界面却没有达到目标时，先检查是否已部分执行；结果不清楚时仍是未知结果。

## 验证范围

helper 校验文件结构、参数引用、类型、脱敏和日志状态转换，不检查真实 UI、网站权限、账号或文件业务内容，不授予外部操作权限。实际回放通过必须来自工具观察与最终产物验证。

共享工作流时，只打包已检查的流程与必要脱敏证据；排除秘密参数文件、登录状态、原始录制和未处理的截图。
