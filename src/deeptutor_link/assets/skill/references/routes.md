# 路线创建与修订

`CLIENT` 表示 `SKILL.md` 中定义的绝对命令。开始设计前读取：

```sh
CLIENT describe route-create
CLIENT describe route-preview
CLIENT describe route-commit
CLIENT doctor
CLIENT route
CLIENT context
```

根据学习目标、基础、时间和用户选定的资料设计路线。路线结构由当前助手生成，DeepTutor 负责保存、绑定和维护学习状态。先检查 `capabilities.route_create` 与 `capabilities.route_edit`，再根据返回能力选择创建或修订入口。向用户展示模块、目标、知识点和资料范围；用户明确要求执行已审阅方案后保存。

## 创建路线

1. 从用户目标整理路线名称、目标、模块、知识点、先修关系和学习偏好。
2. 为本次创建生成 UUID `creation_id`。新模块和目标省略 `id`，需要在提交前互相引用的目标使用 `client_ref`。
3. 将 JSON 通过 `CLIENT route-create --file -` 提交。服务端回执中的 `topic_id` 是新路线的持久 ID。

```json
{
  "creation_id": "12345678-1234-4123-8123-123456789abc",
  "name": "A learner-approved route name",
  "goal": "The learner's intended outcome",
  "modules": [{
    "name": "Foundations",
    "objective": "Explain and apply the foundations",
    "knowledge_points": [
      {"client_ref": "first", "name": "First concept", "type": "concept"},
      {"name": "Apply the concept", "type": "procedure", "prerequisite_ids": ["first"]}
    ]
  }],
  "profile": {"time_budget": "30 minutes a day", "preferences": "Concrete examples"}
}
```

可用字段包括 `description`；`profile` 可填写 `prior_knowledge`、`target_level`、`time_budget`、`preferences` 和 `teaching_strategy`（`probe_first` 或 `teach_first`）。目标类型支持 `memory`、`concept`、`procedure`、`design`。最多 20 个模块，通常每个模块安排 7 个目标；默认通过阈值为 0.7。

资料绑定使用用户从原工作区选定的知识库或文件引用：`sources` 最多 16 项，每项包含 `client_ref`、`kind`（`knowledge_base` 或 `file`）、`source_id` 和 `label`。文件引用还提供 `kb_name`，并在目标的 `topic_source_ids` 中使用对应别名。服务端会检查工作区访问范围和文件包含关系，并在创建时保存目标绑定。

例如，一个文件资料来源及其目标引用如下：

```json
{
  "sources": [{
    "client_ref": "chapter-one",
    "kind": "file",
    "source_id": "chapter-one.pdf",
    "label": "第一章",
    "kb_name": "原知识库引用"
  }],
  "modules": [{
    "name": "第一章",
    "knowledge_points": [{
      "name": "掌握核心概念",
      "type": "concept",
      "topic_source_ids": ["chapter-one"]
    }]
  }]
}
```

创建回执中的 `saved=true` 与 `topic_id` 表示路线已经保存。随后执行：

```sh
CLIENT bind --topic RETURNED_TOPIC_ID
CLIENT context
```

使用完全相同的 JSON 和 `creation_id` 重试中断请求；新的路线内容生成新的 `creation_id`。创建只保存路线，学习进度从后续真实教学回合开始记录。

## 修订路线

1. 读取 `CLIENT route` 获取完整模块树和当前 revision，再读取 `CLIENT context` 获取学习状态。
2. 基于返回对象保留未变化的模块与目标 ID。每个对象通过持久 ID 识别，顺序只负责展示和排序。
3. 准备完整的 `modules` 数组，新增项省略 `id`，保留项带回原 `id`。省略的模块或目标表示删除；省略关系、模块目标和阈值表示保留现值，显式空数组表示清空关系。
4. 用新的 UUID `request_id`、当前 `expected_revision` 和完整 `modules` 调用 `CLIENT route-preview --file -`。

```json
{"request_id":"A_UUID","expected_revision":7,"modules":[...]}
```

5. 阅读 `changes` 和 `identity_map`，向用户说明新增、删除、移动和替换。预览会保存一小时的草稿，路线、revision、掌握度和复习记录仍保持原状态。
6. 用户确认预览后，使用新的 UUID 和预览返回的真实 `preview_id` 提交。调用参数为整数：

```sh
CLIENT route-commit --file - <<'JSON'
{"request_id":"A_NEW_UUID","preview_id":123}
JSON
```

7. 重新读取 `CLIENT route` 与 `CLIENT context`，报告保存后的 revision 和原网站链接。

预览和提交的请求分别保留自己的 UUID。某次请求在网络中断后，使用同一 UUID 和完全相同的 JSON 重试；内容发生变化时先刷新 route/context，创建新的预览。草稿有效期一小时。

有学习记录的目标继续保留原 ID、掌握度、作答、证据、复习、学习者声明、解释、阶段失败记录和视觉门槛。调整名称或类型会生成新的目标 ID；已有学习记录的目标通过新增目标承载新内容。

路线修改前读取当前网页回合、待答题和租约状态。活动回合结束后刷新状态再预览；待答题沿用原题接续流程。服务端保留现有主题名称、目标、profile 和主题级资料集合，路线修订只处理模块和目标结构。
