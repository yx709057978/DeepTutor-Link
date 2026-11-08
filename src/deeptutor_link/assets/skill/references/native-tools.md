# 原生工具与 DeepTutor 对话

用户明确选择原生 Mastery 工具或 DeepTutor 对话时，读取本节。日常讲解、提问、评价和写回沿用 `SKILL.md` 的教学流程；选择原生路径后，按服务端返回的工具契约执行。

`CLIENT describe tool OPERATION` 是字段、回执、成功条件、绑定范围、重试、待答和模型依赖的准确信息来源。先用 `CLIENT tool get_mastery_tutor_tools` 查看已注册工具及其宿主模型要求，再决定当前操作。两项查询都是只读检查。

## 执行原生 Mastery 工具

1. 用 `CLIENT describe tool call_mastery_tutor` 读取调用格式，并从工具目录取得目标 `tool`、`path_id` 和 `arguments` schema。
2. 为同一学习回合建立稳定的 `client_session_id` 和 `turn_id`，为每次工具操作生成新的 UUID `request_id`，设置 `confirm: true`。
3. 把当前账号、工作区和主题范围交给扩展处理。扩展会沿用原服务的 host hooks、path lease 和 receipt 语义。
4. 读取回执：后续原生工具请求继续使用返回的 `client_session_id`；`host_session_id` 只标识底层 DeepTutor 会话；`replayed=true` 表示服务端返回了同一请求的原收据。

`record_turn` 的调用方式是：省略 `arguments`，在 `assistant_message` 中提交已经完成的 tutor 文本，并为该操作生成新的 `request_id`。`direct_dt_conversation` 属于显式能力回合，原生工具请求使用工具自身的参数结构。

提交评分或作答前，先核对答案来源：使用当前用户消息、`arguments.answer` 或 DeepTutor 已经写入的答复。答案为空时回到提问步骤，取得真实作答后再提交。工具声明需要宿主模型时，按返回的模型诊断处理；工具的宿主模型能力与 Codex 的日常教学评价是两条独立路径。

网页待答题和活动租约通过原服务状态处理。遇到活动写入，读取对应状态并等待回合结束；回合完成后刷新契约和主题状态，再继续原生调用。

只读桥接工具单独执行：

```sh
CLIENT tool read_mastery_status --file - <<'JSON'
{}
JSON

CLIENT tool read_mastery_paths --file - <<'JSON'
{}
JSON
```

## 显式 DeepTutor 对话

用户要求“让 DeepTutor 回答”或明确选择 DeepTutor 原生对话时，启动一个 capability turn：

```sh
CLIENT tool send_message_to_deeptutor --file - <<'JSON'
{
  "capability": "chat",
  "message": "The learner's explicit request",
  "request_id": "A_FRESH_UUID",
  "direct_dt_conversation": true,
  "confirm": true
}
JSON
```

这条路径由 DeepTutor capability 决定模型调用；符合其运行条件时会调用已配置的宿主模型。消息与回复保存在 DeepTutor 会话中，和当前助手聊天分别保留上下文。

若回执为 `waiting_for_user`，使用返回的 session ID、turn ID 和 sequence cursor，提交学习者的实际回复：

```sh
CLIENT tool reply_to_deeptutor_turn --file - <<'JSON'
{
  "message": "The learner's exact reply",
  "request_id": "A_NEW_UUID",
  "session_id": "SESSION_ID_FROM_RESPONSE",
  "turn_id": "TURN_ID_FROM_RESPONSE",
  "after_seq": 1,
  "confirm": true
}
JSON
```

回复操作沿用服务端返回的会话和序列信息，并将响应里的 `last_seq` 作为下一次 `after_seq`。每次新操作生成新的 `request_id`。原生对话由用户选择后启动；日常教学仍由当前助手根据角色、资料和学习状态完成。
