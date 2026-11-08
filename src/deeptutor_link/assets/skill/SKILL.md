---
name: deeptutor-link
description: Connect an AI assistant to an existing DeepTutor, configure or update the integration, and guide role/topic/history selection with original materials and progress. Use for DeepTutor setup, 配置连接/更新/升级, and 开始学习/继续学习 in a DeepTutor context.
---

# DeepTutor-Link

DeepTutor-Link 把现有 DeepTutor 主题接入当前聊天。DeepTutor 保存资料、目标、掌握度、
复习记录和授权范围；当前助手负责讲解、提问、评价和学习引导。

## 命令入口

1. 从本文件所在目录解析 `scripts/deeptutor_link.py`，定义：

   ```sh
   CLIENT="python3 /absolute/path/to/this/skill/scripts/deeptutor_link.py"
   ```

   客户端要求 Python 3.11+，脚本、references 和配置保持同一份安装版本。
2. 优先使用已安装的 `deeptutor-link` Skill 及其脚本。可检查：
   `$CODEX_HOME/skills/deeptutor-link`、`$HOME/.codex/skills/deeptutor-link`、
   `$HOME/.agents/skills/deeptutor-link`。
3. 新命令先读取离线契约：

   ```sh
   CLIENT describe
   CLIENT describe resume
   CLIENT describe context
   CLIENT describe operation search_vector_knowledge
   CLIENT describe tool call_mastery_tutor
   ```

   契约提供参数、范围、默认值、调用方式、返回值、成功条件、绑定范围、重试规则和模型依赖。
   旧版客户端先运行 `CLIENT --help`；没有 `describe` 时，按该版本已有命令工作，并把更新任务
   交给 [references/setup.md](references/setup.md)。

## 请求路由

| 用户请求 | 执行入口 |
| --- | --- |
| 配置、安装、连接、升级、恢复、卸载 | 读取并执行 [references/setup.md](references/setup.md) 对应章节 |
| 开始学习、继续学习、新聊天接续 | 按下方 Guided Start 执行 |
| 新建或修改路线 | 读取 [references/routes.md](references/routes.md) |
| PDF 资料、视觉证据、历史题修复、原站待答题 | 读取 [references/workflows.md](references/workflows.md) |
| 原生 Mastery 工具或显式 DeepTutor 对话 | 读取 [references/native-tools.md](references/native-tools.md) |

先根据请求选择一条入口，再执行该入口的检查和写入。所有 API 调用沿用原账号、工作区和
已选主题；服务端返回的内部 ID 用于后续调用，名称用于展示和选择。

## 配置与更新

用户说“帮我配置连接一下我的 deeptutor”时：

1. 读取 [references/setup.md](references/setup.md)，检查本地仓库、DeepTutor 启动方式、
   宿主 Python、已安装 Skill、连接配置和服务端健康状态。
2. 读取身份和认证状态。认证关闭时直接继续；认证开启时，让用户在原服务的安全入口完成
   登录，再读取身份。
3. 发现工作区、主题、角色、资料和可选模型能力。按名称展示选项，只询问无法从本地或服务端
   确定的选择。
4. 用 `initialize`、`bind`、`persona-select` 等客户端命令保存已验证的选择，再运行
   `doctor` 和 `resume --new-chat` 验证连接。
5. 分别报告服务端健康、连接范围、Skill/服务端发行内容是否一致。配置成功以身份、工作区、
   健康接口和实际主题读取为依据。

用户说“更新 DeepTutor-Link”时：

1. 读取更新章节，记录服务端包路径、Skill 路径、版本、完整文件清单、连接范围、主题 revision
   和可回退资源。
2. 取得并核对目标源码或 wheel，比较服务端包和 Skill 的实际内容。
3. 按宿主部署方式执行可逆更新，保留原配置、资料、索引、账号和学习库。
4. 分别运行新客户端的 `--version`、`describe`、`doctor`、`capabilities`，再检查服务端
   `identity`、健康接口和已绑定主题。
5. 两端内容和验证结果符合目标后报告更新完成；只更新仓库或一端时，报告当前状态和剩余步骤。

## 开始学习

用户说“开始学习”或“继续学习”时，按以下顺序恢复上下文：

1. 读取私有连接配置。配置缺失时转到配置流程；配置存在时运行 `doctor`，检查身份、工作区、
   服务端健康和扩展协议。
2. 运行：

   ```sh
   CLIENT workspaces
   CLIENT topics
   CLIENT persona-list
   ```

   按 `next_offset` 分页，把工作区、主题和角色的名称、目标和当前状态展示给用户。
3. 将有效绑定列为“继续上次学习”；存在多个候选时请用户选择。主题选择用
   `CLIENT bind --topic ID`，角色选择用 `CLIENT persona-select NAME`。
4. 用户明确要求恢复网站会话时，先运行 `CLIENT history-list`，让用户按标题选择，再运行
   `CLIENT history-show --session ID`。用户要求恢复该会话的角色、模式或语言时，再运行
   `CLIENT persona-restore --session ID`。历史用于恢复偏好，当前进度以 DeepTutor 状态为准。
5. 运行 `CLIENT resume --new-chat`，读取 `teaching_protocol`、角色、模式、语言、当前目标、
   下一步、待答题和可用操作。需要指定回复语言时运行 `CLIENT protocol --language CODE`。
6. 向用户确认当前主题、角色、学习目标和接续位置，然后进入日常教学。

## 日常教学

每次新聊天和每次状态写入前运行 `CLIENT context`，读取当前目标、revision、偏好、资料定位、
待答题和租约状态。

### 有资料目标

1. 用 `CLIENT retrieve` 检索目标选定资料；返回真实引用且 `chat_model_used=false` 时采用结果。
2. 向量检索未配置或未验证时，用 context 返回的 locator 执行有界文件读取和字面搜索；
   引用实际读取到的文本和来源定位。
3. 基于已验证材料讲解一个小节，提出一个符合当前目标的问题。
4. 等待学习者在当前聊天给出真实答案，再按目标、题目和来源证据评价。
5. 刷新 `context` 取得最新 revision，用 `CLIENT assess --file -` 提交评价。
6. 重新读取 `context`。只有返回 `saved=true`、`final=true` 且 `progress_recorded=true` 时，
   才报告评价已计入学习进度。

### 目标型主题

没有资料绑定的目标可以直接讲解、提问和评价。`source_ids` 与 `evidence_quotes` 使用空数组；
其余步骤与有资料目标相同。

### 评价字段

评价请求包含学习者真实答案、实际题目、最新 `expected_revision`、新 UUID、`result`、诚实的
`confidence` 和具体反馈。材料型目标附上已读取的 source ID 与原文引用；目标型主题使用空引用。
`CLIENT describe assess` 是字段和范围的唯一契约。低置信度评价保持待澄清状态，取得补充证据
或答案后再继续。

## 接续原站待答题

`context` 返回 `pending.present=true` 时，先运行：

```sh
CLIENT pending-question
CLIENT describe answer-pending-question
```

1. 分别查看 `pending` 和 `mutation_lease`：租约表示实际写入状态，持久待答题表示原题仍在学习流程中。
2. 向学习者展示返回的原题、选项和当前状态。服务端不返回答案键、评分标准或私有证据；
   `stored_answer` 存在时，评价这份已经提交的原答案。
3. 检查 `continuation.supported`。支持的普通叶子题读取当前资料、等待真实答案，然后提交
   `expected_revision`、`question_id`、`state_fingerprint` 和评价字段：

   ```sh
   CLIENT answer-pending-question --file -
   ```

4. 返回 `saved=true` 后，按 `progress_recorded` 和 `final` 判断学习状态，再读取 `context`
   确认原题、学习证据和复习计划已更新。
5. 返回 `answer_revision_allowed=true` 且上次评价为非最终结果时，学习者可以补充真实答案，
   请求中设置 `revise_answer=true`。宿主保存原答案、旧评价和修订记录。
6. `pending_group_unsupported`、`pending_visual_review_required`、
   `pending_question_binding_required` 表示原题需要对应的原生或 PDF 证据流程。读取
   [references/workflows.md](references/workflows.md)，按具体状态继续处理。

## 模型与资料能力

运行 `CLIENT capabilities`，按返回能力选择路径：

| DeepTutor 对话模型 | embedding | 当前聊天能力 |
| --- | --- | --- |
| 未配置 | 未配置 | 直接读取资料、字面搜索、讲解、提问、Codex 评价和状态写回 |
| 未配置 | 已配置 | 向量检索成功时优先采用向量结果，其余能力相同 |
| 已配置 | 未配置 | 直接读取资料、字面搜索，并按能力契约使用无需 embedding 的原生工具 |
| 已配置 | 已配置 | 按能力契约启用 DeepTutor 原生工具；向量检索成功时优先使用 |

Codex 日常教学和支持的外部评价走当前聊天。原生 Mastery 工具的模型依赖以 `describe tool`
和 `capabilities` 返回为准；用户明确选择原生路径时，读取
[references/native-tools.md](references/native-tools.md) 并按其确认流程调用。

资料读取先检查 `context.sources` 的 `direct_read_available` 和 locator，再用工作区授权的
文件名读取。PDF、视觉题和来源证据沿用原题验证状态；需要图片观察时，准备页面、打开渲染图，
再提交包含页码和观察内容的证据。向量检索以实际返回匹配来源且 `chat_model_used=false`
作为成功依据。

## 角色、提示词与运行时技能

`persona-list`、`persona-show`、`persona-select`、`persona-clear` 和 `persona-restore`
管理当前聊天使用的角色选择。`protocol` 返回 DeepTutor Mastery prompt pack 的已验证章节、
语言和指纹。将这些内容作为教学策略、语气、模式和复习要求，再通过 `operations` 选择客户端命令。

运行时 Skill 流程：

1. 用 `CLIENT skills` 获取当前账号和工作区可见的 Skill 名称及桥接信息。
2. 用 `CLIENT skill-read NAME --path SKILL.md` 分页读取需要的正文和引用资源。
3. 将 Skill 步骤映射到 `CLIENT describe` 和 operation catalog 中 `available=true` 的操作。
   脚本步骤使用服务端明确提供的对应能力。
4. 在当前主题、账号、工作区和权限范围内调用映射操作，并将返回文本作为参考资料。

## 完成标准

一次学习接续完成时，向用户说明当前账号、工作区、主题、角色和目标，使用的来源及实际读取位置，
学习者的真实答案和评价结果，DeepTutor 的保存收据、最新 revision、学习证据和复习状态，以及
当前聊天接下来的学习步骤。

遇到 revision 冲突、权限变化、服务端健康失败或资料证据失效时，刷新对应状态，按返回原因选择
恢复、重新读取、补充证据或请用户选择。状态写入通过原服务的事务、租约、权限和去重收据完成，
DeepTutor 网站与当前聊天保持同一份学习状态。

安装、升级、路线、PDF、历史题修复和原生工具的详细步骤见上方 references。
