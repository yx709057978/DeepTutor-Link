# 原题、PDF 与历史题流程

`CLIENT` 表示 `SKILL.md` 中定义的绝对命令。每条流程从新的 `CLIENT context` 开始，
先取得当前 topic revision、资料定位、待答题和租约状态。UUID 在本地生成；答案 JSON
通过 `--file -` 从 stdin 传入，页面图片保存到用户指定的临时目录。

## 接续原站待答题

### 读取题目

1. 当 `context` 返回 `pending.present=true` 时，运行：

   ```sh
   CLIENT pending-question
   CLIENT describe answer-pending-question
   ```

2. 查看题目提示、选项、`question_id`、revision、`state_fingerprint`、`stored_answer`、
   `continuation.supported` 和 `blocker`。`mutation_lease.status` 单独表示当前写入活动。
3. 把原题和当前状态展示给学习者。答案键、评分标准和私有证据留在服务端；
   `stored_answer` 存在时，评价这份已经提交的答案。

### 继续学习

普通叶子题和兼容旧格式的待答题可以沿用当前聊天继续。先读取目标资料，再等待学习者在当前聊天给出真实答案。

```sh
CLIENT answer-pending-question --file - <<'JSON'
{
  "request_id": "UUID",
  "expected_revision": 7,
  "question_id": "ORIGINAL_QUESTION_ID",
  "state_fingerprint": "FINGERPRINT_FROM_PENDING_QUESTION",
  "learner_answer": "The learner's exact actual answer",
  "result": "correct",
  "confidence": 0.9,
  "feedback": "Evaluate against the original objective and evidence.",
  "independent": true,
  "revise_answer": false,
  "source_ids": [],
  "evidence_quotes": []
}
JSON
```

目标型主题使用空引用；材料型主题按 `describe` 契约提交已选 source ID 和实际读到的原文引用。
当前聊天提供过答案提示时填写 `independent=false`。`confidence` 反映当前证据把握。
服务端会把答案、评价、证据和复习状态写回原题，并保留原 session 与答题契约。

提交前重新读取 `pending-question`，使用最新的 revision 和 fingerprint。返回 `saved=true` 后检查
`progress_recorded` 与 `final`，两者均为 true 时报告评价已计入进度；非最终评价继续保留待答状态。
成功后重新读取 `context`。

### 接续分支

- `answer_revision_allowed=true` 且上次评价为非最终结果：学习者补充真实答案时设置 `revise_answer=true`，原服务保留旧答案、旧评价和修订审计。
- `pending_group_unsupported`：保留题组原题，使用原生题组工具或回到网站完成题组流程。
- `pending_visual_review_required`：保留视觉题，按 PDF/视觉证据流程准备并检查原图。
- `pending_options_unavailable`：恢复原题选项后继续，当前聊天提供选项选择。
- `pending_question_binding_required`：为历史 PDF 题补齐已验证的题目/答案绑定，再提交评价。

网络中断重试沿用完全相同的 JSON 和 UUID，服务端返回原收据。评价内容发生变化时创建新的 UUID；
revision 或 fingerprint 过期时重新读取当前题目和已存答案。

## 恢复教学角色与协议

新聊天先完成 `SKILL.md` 的 Guided Start，再运行 `CLIENT resume --new-chat`，读取
`teaching_protocol.persona`、`mode`、`sections`、`engine_state` 和 `operations`。
角色选择由用户明确指定，选择后重新读取协议：

```sh
CLIENT persona-list
CLIENT persona-select NAME
CLIENT protocol --new-chat
CLIENT persona-clear
CLIENT protocol --new-chat
```

用户要求恢复网站会话时，先用 `CLIENT history-list` 展示有界会话元数据，让用户按标题选择；
再用 `CLIENT history-show --session SESSION_ID` 读取对应会话。需要恢复该会话的角色、模式或语言时：

```sh
CLIENT persona-restore --session SESSION_ID
CLIENT protocol --new-chat
```

历史会话用于恢复明确选择的偏好，当前学习进度以 DeepTutor 状态为准。遇到 `persona_missing` 或
Mastery prompt 资源诊断时，展示具体状态，协助用户重新选择可见角色或修复原安装；协议中的
`operations` 是后续桥接命令的执行映射。

## 准备并提交 PDF 证据

1. 列出目标关联的原始 PDF：

   ```sh
   CLIENT tool list_objective_pdfs --file - <<'JSON'
   {"objective_id": "OBJECTIVE_ID"}
   JSON
   ```

2. 用当前 revision 和选定页码创建准备包：

   ```sh
   CLIENT tool prepare_pdf_evidence --file - <<'JSON'
   {
     "request_id": "UUID",
     "expected_revision": 7,
     "objective_id": "OBJECTIVE_ID",
     "pages": [{"source_id": "SOURCE_ID", "filename": "lesson.pdf", "page_index": 0}]
   }
   JSON
   ```

3. 对返回的每个页面执行 `CLIENT pdf-image --preparation PREPARATION_ID --page 0 --output /tmp/page-1.png`，
   打开图片后记录实际看到的文字和视觉关系。准备包只保存原页摘要和 digest，页面观察来自本地打开的图像。
4. 将原文引用、视觉观察、证据覆盖范围和当前 revision 提交：

   ```sh
   CLIENT tool commit_pdf_evidence --file - <<'JSON'
   {
     "request_id": "UUID",
     "preparation_id": "PREPARATION_ID",
     "expected_revision": 7,
     "objective_id": "OBJECTIVE_ID",
     "purpose": "assessment",
     "coverage_summary": "State which required facets these inspected pages cover.",
     "coverage_complete": true,
     "confirm": true,
     "sources": [{
       "page_number": 0,
       "quote": "Exact text from the rendered original page.",
       "visual_observation": "Specific relationship seen in the opened image.",
       "supports": "Explain how the page supports the objective or question/key."
     }]
   }
   JSON
   ```

5. 读取保存回执中的 source ID，再用普通 `CLIENT assess --file -` 完成评价。assessment 的
   `evidence_quotes[].page_number` 使用一基页码；`question_citations[].source_index` 使用已提交
   packet 中的零基 source 索引。需要视觉证据的目标至少提供一条带匹配 `visual_observation` 的题目引用。

`prepare.pages[].page_index` 和 `commit.sources[].page_number` 都是零基页槽位。提交前服务端会重新核对
   原页 digest、资料范围、准备包引用和 topic revision；页面图片和实际观察完成后再提交即可。

## 检查并修复历史题

使用宿主返回的确切 interaction ID，或当前助手评价回执中的 `codex-ASSESSMENT_ID`。先读取题目和历史评价：

```sh
CLIENT tool inspect_question_repair --file - <<'JSON'
{"question_id": "QUESTION_ID"}
JSON
```

根据原题、学习者答案、历史评价和已验证资料准备修复预览。更正题目答案键或评价时使用 `action=correct`；
作废无效评价时使用 `action=void`。

```sh
CLIENT tool preview_question_repair --file - <<'JSON'
{
  "request_id": "UUID",
  "expected_revision": 9,
  "question_id": "QUESTION_ID",
  "action": "correct",
  "reason": "Why the stored key or verdict is wrong.",
  "expected_answer": "Corrected answer key or rubric.",
  "result": "correct",
  "confidence": 0.95,
  "feedback": "Corrected rationale.",
  "citations": [{
    "source_index": 0,
    "quote": "Exact supporting passage.",
    "visual_observation": "Matching inspected-page observation when visual.",
    "support_statement": "How this evidence supports the corrected key."
  }]
}
JSON

CLIENT tool commit_question_repair --file - <<'JSON'
{
  "request_id": "NEW_UUID",
  "preview_id": "PREVIEW_ID",
  "confirm": true
}
JSON
```

`action=void` 使用具体的作废原因；`expected_answer`、`result`、`confidence`、`feedback` 和 `citations`
按 `describe` 契约提交对应字段。预览保存一小时，提交只接受这份已审阅预览。中断重试沿用相同的
`request_id` 和 `preview_id`，完成后再次读取历史题或 `context`，确认评价、掌握度和复习状态。
