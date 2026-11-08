# DeepTutor-Link

在你常用的 AI 助手里，沿用 DeepTutor 的资料、角色与学习进度继续学习。
连接原实例、账号、内容工作区、Mastery 主题/目标 ID、索引和复习历史。
当前 AI 助手负责教学对话和测评，
原 DeepTutor 网站继续使用。无须 Private Git、迁移聊天、重新上传或重建索引。

客户端是 Python 标准库脚本，不依赖特定助手 SDK。当前已验证 Codex 的配置与
学习流程。其他客户端需要支持读取 Skill、执行本地脚本；PDF 视觉证据流程还需要
查看渲染图片。其他客户端的技能发现和安装目录需分别适配，尚未完成使用验证。

扩展 0.2.1 仅支持已审查的 **DeepTutor v1.7.1 /
f7aed0744c36c5afef16a4261c31ebee1208cac2**。源码已公开于 GitHub 仓库
`yx709057978/DeepTutor-Link`，尚未发布到 PyPI；按下方本地源码方式安装。

## 开始使用

把仓库下载到本地，用 Codex 打开这个项目，在聊天里说：

> 帮我配置连接一下我的 deeptutor。

AI 助手会检查本地已有的 DeepTutor、运行方式、Python、Skill 和连接配置，
处理适用的安装与连接步骤，只向你询问缺少的信息和需要选择的项目。
关闭认证的实例不需要登录；原资料、索引和学习进度继续使用。

配置完成后，在同一项目新开一个聊天，说：

> 开始学习。

AI 助手会引导你选择可用角色、学习主题，以及从原进度继续还是读取你选定的
历史会话。已有选择会作为接续选项展示。选好后就在这个聊天里讲解、提问、
评价，并将有效评估保存回原 DeepTutor。没有自定义角色也可使用默认导师。
你不需要手填内部 ID 或逐条执行命令。以后说“继续学习”即可接着学。

仓库的 [AGENTS.md](AGENTS.md) 提供未安装 Skill 时的入口；安装后的 Skill
也使用同一套流程。[AI 安装与配置指南](src/deeptutor_link/assets/skill/references/setup.md)
供执行配置的 AI 查阅，包含环境检查、两端安装、验证与恢复步骤。以下为
支持范围和手动操作参考。

## 更新

用 AI 助手打开本仓库，说：

> 帮我更新 DeepTutor-Link，保留现有连接、角色选择和学习进度，检查服务端与 Skill 是否匹配。

AI 会先识别现有安装和目标版本，备份到私有目录，再更新需要变化的部分，
最后验证原连接和主题。连接正常不代表已更新；只拉取仓库代码，也不会
自动更新已经安装的服务端包或 Skill。更新不自动升级 DeepTutor 本身。

通过 Git 获取更新时，先确认当前分支、远端和本地修改，再快进更新：

```sh
git status --short
git remote -v
git fetch origin
git pull --ff-only
```

有未提交改动、分叉或冲突时先保留并处理，不使用强制重置。下载 ZIP 的
用户将新版本解压到另一个目录，不覆盖旧目录，供 AI 核对和回退。
取得源码后，再让 AI 执行上面的更新请求。

完整步骤见 [升级与恢复](src/deeptutor_link/assets/skill/references/setup.md#升级与恢复)：
包括服务端源码/wheel、Docker 派生镜像、独立 Skill、验收和失败回退。
服务端与 Skill 应来自同一份发行资源；当前版本号为 0.2.1，源码更新还应
记录 Git commit，不能只凭相同版本号判断内容一致。尚未发布到 PyPI，
不要用 `pip install -U deeptutor-link` 代替本仓库的更新流程。

更新成功后新开聊天说“继续学习”。原私有配置继续使用，无须重新选择
有效的绑定、重新上传资料或重建索引。
原网站留有一道待答题时，题目和进度原样保留，不要求先回网页答完才能
更新。维护需确认实际活动写入，而不是把“等待你回答”当作后台仍在运行。

## 支持范围

| 能力 | 当前行为 |
| --- | --- |
| Mastery 主题/路线 | 读取原状态；AI 助手设计新路线，修改既有路线时先预览再提交 |
| 资料检索 | 选定 KB/file，复用 LlamaIndex 索引与原 embedding，禁用 query rewrite |
| 其他 provider | 明确拒绝；LightRAG/GraphRAG/PageIndex 等未承诺纯检索 |
| 测评写回 | AI 助手的真实答题评估写入原 LearningService、证据与复习状态 |
| 不确定评估 | 沿用原 Verdict 规则，confidence < 0.8 返回 ungraded，不写掌握、证据、复习或 interaction |
| 测评修复 | AI 助手预览/提交端点支持定量与定性历史记录；定量沿用原修复，AI 助手定性用精确审计修复 |
| PDF/视觉 | AI 助手可选中原 PDF 页、本地打开原始页渲染图、提交来源校验 packet；视觉目标还必须引用已检查页面 observation |
| 学习偏好 | 新建路线可带入 intake；原路线 intake 和 teaching preferences 读取后沿用 |
| Reading | 原进度位置/百分比，只读；不写 Reading quiz/笔记/词汇状态 |
| 日记 | mission 与最近 8 条 insight，只读；不导出 last_session |
| 教学 persona/协议 | 新聊天先读取显式 persona 与原 Prompt Manager 中的 Mastery 白名单 section；返回 section 身份、语言、指纹和 AI 助手操作映射，不返回完整 assembled system prompt |
| Native Mastery | `mastery_status` / `mastery_paths` 有界读取；按 `tool` + `arguments` 明确执行单个宿主 Mastery 工具，沿用宿主 session、路径租约和状态钩子 |
| 直接 DeepTutor 对话 | 只有用户明确要求并确认才启动一次性 `chat` turn；消息和回复保存在 DeepTutor session，AI 助手日常对话不自动复制 |
| Notebook | 列表、分页读取、创建/编辑/删除；写学习笔记时只提交用户明确提供的标题、摘要和笔记正文 |
| Practice | 读取题目时过滤答案键和解析；用户明确答题并确认后，通过原复习服务写入版本化评分与间隔复习状态 |
| 历史聊天/答卷 | 不自动导入或镜像；显式请求时只读单个 DeepTutor session 的有界摘要字段，题目列表不返回答案键 |
| Course/syllabus | 宿主没有对应 API；六个兼容目录操作均不可用，需使用 Mastery topic/route |

DeepTutor v1.7.1 没有 Course/syllabus API。以下兼容目录项保留为明确不可用，
不会发送请求或伪造成功：`list_courses`、`get_course_state`、
`list_course_resource_candidates`、`replace_course_syllabus`、
`set_course_unit_covered`、`attach_course_resource`。需要课程结构时使用已支持的
Mastery topic/route，而不是把它报告成 Course API。

## 安装

需要 Python 3.11+。服务端仅支持 macOS/Linux、本机文件系统上的 POSIX `flock`，
支持源码/可写 wheel，以及下述固定基线的 Docker 派生镜像路径。Windows 服务端在检查/安装前拒绝；
AI 助手 Skill 客户端仍只依赖 Python 标准库，可在其他系统连接支持的服务端。
不要把服务端学习库或安装目录放在 NFS/SMB 等网络文件系统，不支持跨机器共享写入。
服务端和 AI 助手端是两处安装；两端可在同一台电脑或通过 HTTPS 连接。
自动化测试使用临时状态和显式配置路径。

服务端：在 **运行原 DeepTutor 的 Python 环境**安装本项目，然后指定宿主源码目录，
或该环境 `site-packages/deeptutor` 目录。先停服务再操作；安装器不自动重启。

```sh
python -m pip install '/path/to/deeptutor-link[server]'
deeptutor-link server-check --host /path/to/DeepTutor
deeptutor-link server-install --host /path/to/DeepTutor --dry-run
deeptutor-link server-install --host /path/to/DeepTutor
```

安装只修改两个宿主文件，并保存不含用户数据的安装收据：

- `deeptutor/api/main.py`：main guard 前增加扩展路由挂载。
- `deeptutor/learning/service.py`：在同步修复事务及语义改判入口增加
  内联检查，仅拒绝本版无法正确撤销的 AI 助手定性记录。检查仅引用宿主原有模块，
  不依赖扩展包，避免卸载中断且扩展包缺失时破坏原生题目修复。

31 份关键宿主源码必须与基线指纹一致，版本相同但源码漂移也拒绝。
补丁块带明确标记，卸载恢复两个文件的原始字节；两份补丁未完整安装时拒绝挂载。
安装还在 `deeptutor/` 内保存 `.deeptutor-link-recover.py` 独立恢复脚本，
在首次写入补丁前落盘；它只依赖标准库，包含已审查基线和精确补丁，可在扩展包
缺失时卸载。不会编辑 KB、模型、账号配置或学习库。按原方式启动 DeepTutor。
如果宿主环境找不到扩展包，启动会失败，应使用下述卸载恢复入口。

AI 助手端通过 DeepTutor-Link Skill 连接 DeepTutor，继续学习或设计、修改学习路线，
并保留原有资料、目标、掌握度和复习记录。Skill 需要 Python 3.11+。将整个
`src/deeptutor_link/assets/skill` 目录（含 `scripts/`）复制到
`$HOME/.agents/skills/deeptutor-link` 或项目 `.agents/skills/`。

```sh
mkdir -p /path/to/.agents/skills
cp -R /path/to/deeptutor-link/src/deeptutor_link/assets/skill \
  /path/to/.agents/skills/deeptutor-link
python3 /实际安装路径/deeptutor-link/scripts/deeptutor_link.py --help
python3 /实际安装路径/deeptutor-link/scripts/deeptutor_link.py describe
```

默认 Skill 路径 `$HOME/.agents/skills/deeptutor-link`。项目级可用
`skill-install --target /path/to/project/.agents/skills`，不会修改 AI 助手 provider/模型。
如果本机已为服务端安装本包，也可运行 `deeptutor-link skill-install` 自动复制整个
Skill。后续无须 CLI 在 PATH 中。下面 `CLIENT` 表示上面脚本的完整 Python 命令；
执行时需展开为实际路径，Skill 自身也按其安装位置定位脚本。若开发仓库副本和
已安装副本都存在，学习流程优先使用安装副本及其配套脚本和参考文档。

## 连接原实例

完整配置流程见 [AI 安装与配置指南](src/deeptutor_link/assets/skill/references/setup.md)。
先检查原实例 `GET /api/auth/status`；`enabled=false` 时跳过登录，不需要账号密码。
认证开启且没有可用凭据时才使用原登录。密码通过隐藏输入提示读取；仅保存原登录 Cookie，文件权限 600。
也可由安全环境注入 `DT_LINK_TOKEN` bearer token，不在配置中保存 token。
关闭认证的单用户实例会解析为原 local-admin；不要把这种实例暴露到公网。

```sh
CLIENT discover --url http://localhost:8001 --web-url http://localhost:3782
CLIENT initialize --url http://localhost:8001 --web-url http://localhost:3782 \
  --workspace '' --topic ORIGINAL_TOPIC_ID
CLIENT workspaces
```

认证开启且需要登录时，在 `discover` 前运行
`CLIENT login --url http://localhost:8001 --username existing-user`，
由用户在自己的终端输入密码。以上端口仅为示例，须使用原实例实际地址。

`discover` 不保存配置，返回已认证账号、当前默认工作区、可选工作区、主题及运行时 Skill。
确认所选 ID 后运行 `initialize`；首次省略 `--workspace` 时使用 DeepTutor 当前解析的工作区，
同实例、同账号重配时保留已验证的工作区。默认 workspace 可显式写 `--workspace ''`。
同范围重配保留原主题、角色、模式和语言；换范围重新选择。也可先完成连接，等“开始学习”时选主题。
之后用 `bind --topic ID` 切换主题。每个请求同时带原认证和
`X-DeepTutor-Workspace`，读写要求 `X-DeepTutor-Account` 与真实账号一致。
仅同一个 URL 不代表同一份数据。远程仅允许 HTTPS，可用 SSH 转发到 localhost。
Cookie 模式（包括 PocketBase）由原宿主处理；登录受宿主 private-login-host 策略约束，
失败时检查原网站与服务端策略，不伪造 frontend-host 绕过限制。

```sh
CLIENT topics
CLIENT bind --topic ORIGINAL_TOPIC_ID
CLIENT doctor
CLIENT context
CLIENT records
CLIENT retrieve --objective ORIGINAL_OBJECTIVE_ID \
  --source ORIGINAL_SOURCE_ID --query '需要查证的问题'
```

`doctor` 校验协议、原账号/工作区、主题存在性和 host readiness，
并在已绑定主题上返回 `mutation_lease` 诊断（不会自动删除租约）。`capabilities` 分别报告 DeepTutor 聊天模型和 embedding 是否配置；配置状态不等于向量检索健康，兼容索引和查询 embedding 仍以一次 `retrieve` 成功为准。无 embedding 时仍可用授权资料的有界文件读取和 literal search；不能满足来源或宿主证据门槛时停止该项测评。上下文与检索字段
有白名单和上限；分页字段 `next_offset` 可继续请求。原网站链接由客户端统一拼接
为 `--web-url` 指定 origin 的绝对 URL。`retrieve --top-k` 每次最多返回 8 条；
`CLIENT describe` 离线给出命令参数和调用契约。

三种可诊断配置：无 DeepTutor 聊天模型、无 embedding 时，AI 助手自己的教学、
资料直读、literal search 与满足证据门槛的评估仍可用；仅配置 embedding 时，
向量路径仍须由一次 `retrieve` 验证现存索引兼容性；两者都配置时，除上述能力外，
宿主模型依赖工具也只在其自身能力契约允许时可用。`context.sources[].locator`
只返回授权验证过的 KB 引用与相对文件名，不返回绝对路径；`retrieval_status`
表示向量索引/查询 embedding 尚未验证，不代表已可检索。

授权文件列表最多完整枚举 1000 项；超过此界限会返回 422，需用文件名或后缀
缩小结果，避免在截断清单上报告不完整的总数。隐藏文件不会列出；请求
`include_hidden=true` 明确返回 422。

在 AI 助手中说“使用 deeptutor-link 继续这个主题”。Skill 会先读取状态、引用原
资料、提问、等你作答，再由 AI 助手评估；普通接续不会调用 DeepTutor 聊天模型。
显式调用原生 Mastery 工具和直接对话是两条不同路径。`call_mastery_tutor` 以
`tool` + `arguments` 执行一个宿主注册工具（包括 quiz/grade 等原生状态操作），受原账号/工作区、宿主路径租约和 request_id 幂等保护；答案必须来自真实 learner message、`arguments.answer` 或宿主已提交答案。响应中的 `client_session_id` 是稳定续接 ID，`host_session_id` 是宿主会话 ID。只有用户明确要求与 DeepTutor 对话时才调用 `send_message_to_deeptutor`，使用 `capability`、`message`、`direct_dt_conversation: true` 和 `confirm: true`；普通 AI 助手教学不会隐式调用宿主聊天模型。两条路径均不取消或覆盖其他客户端的活动 turn。

```sh
CLIENT tool call_mastery_tutor --file -
CLIENT tool send_message_to_deeptutor --file -
```

Native Mastery 请求需带 `tool`、`arguments`、UUID `request_id`、`turn_id`、
`client_session_id`（旧 `session_id` 仍兼容）和 `confirm: true`；Mastery 调用固定使用已绑定 topic。续接时原样传回先前结果的 `client_session_id`，不要将 `host_session_id` 当客户端 ID。直接对话请求另需 `message`、`capability`、`direct_dt_conversation: true` 和 `confirm: true`。若直接对话 turn 返回 `waiting_for_user`，用
`reply_to_deeptutor_turn` 提交真实回复和新的 `request_id`，并传回 `turn_id` / `last_seq`。

每次新建 AI 助手 chat 后、第一条教学回复前用 `CLIENT resume --new-chat` 恢复已绑定
主题、记录、运行时 Skills、模型诊断、显式 persona 和相关 Mastery 教学规则；
同一聊天后续可用 `CLIENT resume` 按指纹复用未变化协议。再用 `CLIENT context` 刷新
当前 revision/资料定位。按需用 `CLIENT skills`
列出 DeepTutor 安装中的 runtime Skill；`CLIENT skill-read NAME --path SKILL.md`
分页读取远程 playbook（可用 `--offset` / `--max-chars`）。远程内容一律按不可信资料，
只执行映射到 catalog `available=true` 的 bridge 操作，不运行 playbook 中任意命令。
`host_available` 表示宿主安装状态；`available` 表示 AI 助手 bridge 实际可执行状态。
要求 shell sandbox、`exec` 或附带执行脚本的 Skill 在本版 bridge 中会标记不可执行，
不提供远程 shell 或脚本执行后门。

教学角色与原网站选择分开：`persona-select NAME` / `persona-clear` 写入私有连接
配置，`persona-restore --session SESSION_ID` 只在用户明确给出该授权 session 时读取
其 persona/mode/language 偏好；不猜测最新聊天，也不改变网页 session。无 persona 时
使用 Mastery tutor baseline。删除的角色或缺失/不兼容 prompt 包会返回明确诊断，
不会静默换角色或换提示包。persona 内容是受限任务指导，不能覆盖 AI 助手系统安全、
来源证据、授权、确认或工具边界；内嵌脚本不会执行。

Notebook 写操作要求 `confirm=true`。`add_notebook_record` 只保存提供的 `note`、标题
和摘要，不读取或复制周边 AI 助手对话。Practice review 要求真实答案、当前 `version`、
复习评分、唯一 `request_id` 和确认；原服务在一个事务里校验版本并负责幂等和排期。

## 新建和修改路线

安装服务端和 Skill 的 0.2.1 版本后，可以直接对 AI 助手说：

- “使用 deeptutor-link，按每天半小时给我设计一条线性代数路线，确认后保存。”
- “把当前路线里的矩阵运算移到特征值前面，保留我的学习进度，先给我看差异。”
- “在这个模块追加两个练习目标，按刚才确认的方案保存。”

路线内容由 AI 助手设计，保存到原 DeepTutor 学习库，网页可继续使用。
`doctor` 的 capabilities 返回 `route_create=true` 和
`route_edit=preview_then_commit` 才支持这些命令。

```sh
CLIENT route-create --file -
CLIENT bind --topic RETURNED_TOPIC_ID
CLIENT route
CLIENT route-preview --file -
CLIENT route-commit --file -
CLIENT context
```

三个写命令从 stdin 接受 JSON，也支持 `--file /absolute/path.json`；完整示例和
字段见打包 Skill 的“Create or Revise a Route”。新建请求带 UUID `creation_id`、
`name`、`goal` 和 `modules`，可选 `description`、`profile`、`sources`。
支持最多 20 个模块，通常每模块最多 7 个目标；已有模块可以保留超过 7 个的
原目标数量。目标类型支持 memory/concept/procedure/design，可设置先修关系、
资料关联、模块目的和通过门槛。

新建时可引用当前账号有权读取的已有 KB/file，文件必须属于所选 KB；不会上传、
重建索引或调用 DeepTutor 的路线生成模型。资料关联不等于已验证检索或 embedding。
未选资料时创建目标型路线。新建完成不自动切换当前绑定，使用返回的 topic ID 绑定。

修改前先用 `route` 读取完整结构和 revision，再提交完整 modules 数组。
未变化的目标保留原 ID，新增目标留空 ID，可调整模块/目标顺序、移动目标、
设置先修关系和资料关联。省略的已有模块/目标视为删除。名称或类型改变的目标
获得新 ID，不继承旧目标的掌握记录。有学习历史的目标禁止删除、替换或改变
资料关联，可保留旧目标并追加新目标。原视觉要求和阶段失败记录也会保留。
本次修改命令不更改主题名称、总目标、已有 profile 或主题级资料集合。

`route-preview` 带 UUID `request_id`、`expected_revision` 和 `modules`，返回精确
课程差异、ID 保留/新增/删除清单以及整数 `preview_id`。预览只在原事件表保存
草稿，不改变路线、revision、掌握或复习状态，不导出包含答卷的原始进度差异。
检查差异后，以另一个 UUID `request_id` 和 `preview_id` 调用 `route-commit`。
提交只能应用该草稿；草稿一小时后过期，进度变化时须重新预览。

三个写命令均按原库中的摘要或提交收据去重，网络重试必须复用原始 JSON/UUID。
提交后以 `saved=true`、revision 和再次读取的路线确认结果。网页活跃回合或未完成
问题会阻止修改，归档主题须先恢复；不会取消原网页回合。

从旧名称 DeepTutor-Codex 迁移：先停止原服务，用旧版安装器或随安装保存的
恢复脚本卸掉旧补丁，再安装 DeepTutor-Link；不要将新旧补丁叠加。Docker 部署
则使用新版派生镜像替换旧扩展镜像，保留原数据挂载。将旧 Skill 备份至 skills
目录外，再安装 `deeptutor-link`。旧私有连接配置可在校验账号、工作区和主题后
迁入 `.config/deeptutor-link/`；原凭据环境变量名若已显式记录可继续沿用。
不迁移原始聊天、重建资料索引或更换学习库。详见 AI 配置指南的迁移步骤。

新接口使用 `/api/link/v1`，挂载时保留 `/api/codex/v1` 别名。旧记录中的
`codex_*` 来源字段、表名、interaction/session ID 和学习写入锁仍沿用原格式，
用于识别已有记录及网络重试，不表示使用了 Codex 模型。项目包名、CLI、Skill
和新配置目录统一为 `deeptutor-link`。

## 测评与并发

`CLIENT assess --file -` 从 stdin 接受 JSON；完整字段见打包的
`src/deeptutor_link/assets/skill/SKILL.md`。调用者是已登录用户控制的 AI 助手，
服务端记录来源 `codex_external_assessment`，并不声称验证模型供应商身份。
只有用户实际作答后才可提交，不以“我会了”替代测评。原定量/定性门槛和间隔
复习均由原 LearningService/scheduler 执行，既有证据与 learner override 保留。
原宿主 `Verdict.receipt()` 要求置信度至少 0.8 才能成为 final。低于门槛返回
`result=ungraded`、`final=false`、`needs_clarification=true`、`saved=false`，
版本、mastery、evidence、review 和 interaction 均不改变，不能报告为已记分。
HTTP 200 表示请求已处理；只有 `saved=true` 才表示已保存评估。

`assessment_id` UUID + 完整请求摘要存于原 mastery_interactions；同一个请求重试
只写一次，相同 ID 不同内容返回冲突。`expected_revision` 在同一个原子事务中比较。
主动尝试原 path lease，使用宿主已认可的 `__path_api__` 管理操作身份，
每次请求的 turn ID 独立；证据与 interaction 的来源仍为 AI 助手。
不调用取消网页回合的 `_exclusive_path_mutation`。
每次写入从取得 SQLite 租约前到事务和租约释放后，都持有独占 OS 文件锁。
锁按原 `store.db_path.resolve()` 与 topic ID 隔离，同一进程的不同线程也互斥。
新租约使用 `codex-lock-v1:锁标识:请求UUID`；进程强杀后 OS 释放锁，下一次评估
取得同一锁后，只能清理标识匹配的新租约。提交后崩溃的相同请求仍按原记录去重。
不以 PID、年龄或超时推断死亡，不清理网站/管理员租约，也不自动清理没有锁协议的
旧 `codex-...` 租约。
活跃网页写入仍受原租约保护；过期版本需刷新状态后重新判断。原站留下的
待答题可通过 `CLIENT pending-question` 读取，再由当前助手评价真实作答，
使用 `CLIENT answer-pending-question --file -` 写回原题和复习状态，无需
DeepTutor 对话模型。原题的选项、已提交答案和来源证据保留；题组、视觉题
及缺失、失效或未绑定原题的证据返回具体限制，不清题绕过。非最终评分后，
学习者可提交真实补充答案，由宿主留存原答案和评价的修订记录。持久待答本身不阻止连接或
有序维护，安装/卸载不删除新增学习证据。

PDF 目标可使用原宿主已验证 packet，或走 AI 助手的 prepare -> `pdf-image` ->
实际打开图片 -> commit 流程。视觉声明必须来自实际打开的原始页渲染图；
视觉必需目标还要在题目/答案层提供与页 observation 匹配的
`visual_observation` citation，纯文本 citation 会被拒绝。普通检索命中不能替代
该 packet 或视觉门槛。

`mastery_repair_question` 等原生宿主修复入口继续拒绝 AI 助手定性记录，返回
“No record was changed”，因为它们不能完整重放这类记录的掌握、解释和复习历史。
授权修复必须走 AI 助手的 question-repairs `previews` -> `commits` 两步端点：
定量记录沿用原宿主修复逻辑；AI 助手定性记录在原子事务中做精确修复、来源校验、
掌握/解释/复习重算和审计。`identity.capabilities` 公开
`qualitative_assessment_repair=true` 和 `qualitative_repair_behavior=exact_preview_confirmed_commit_source_validated`。
新的独立定性评估仍可更新当前掌握，但不撤销旧证据，也不是历史修复的替代。

## 卸载与恢复

评估请求中断后，先用绑定的账号/工作区/主题检查和恢复：

```sh
CLIENT doctor
CLIENT context
CLIENT recover
```

`recover` 调用带原认证和作用域检查的 `POST /api/link/v1/topics/{id}/recover`。
`orphan_recoverable` 表示诊断时可取得 OS 锁且新租约标识匹配；`recover` 会重新取锁
并验证，成功返回 `status=clear,recovered=true`。`extension_active` 表示活跃写入，
`protected_lease` 表示网站、管理员、旧版或标识不匹配的租约，均不删除。
无需专门恢复时，可重试原始评估 JSON/UUID，服务端也会安全清理本版遗留租约。
旧版遗留租约须由管理员停止该实例所有写入进程后，核实原库作用域与确切 turn ID，
再通过原 LearningStore 的 `release_path_lease(path_id, turn_id=...)` 处理；
不能在运行中按前缀或超时批量删除。

安装/卸载中断可重跑下列命令，OS 自动释放被杀进程的锁：

```sh
deeptutor-link server-uninstall --host /path/to/DeepTutor --dry-run
deeptutor-link server-uninstall --host /path/to/DeepTutor
deeptutor-link skill-uninstall
CLIENT logout --url http://localhost:8001
```

先停原服务、卸载挂载补丁，再从该 Python 环境卸载扩展包。入口按已知原始字节
恢复；如宿主改动则拒绝自动覆盖。多文件写入中断可重跑安装或卸载，收据会记录
两文件原始及补丁后的摘要；安装器不会覆盖未识别的改动。
若扩展包已经缺失，先停止原服务，用安装时已保存的独立脚本恢复：

```sh
python3 -I -S /path/to/DeepTutor/deeptutor/.deeptutor-link-recover.py \
  --host /path/to/DeepTutor --dry-run
python3 -I -S /path/to/DeepTutor/deeptutor/.deeptutor-link-recover.py \
  --host /path/to/DeepTutor
```

`--host` 也接受实际 `site-packages/deeptutor` 目录。脚本先验证全部 31 份基线、
精确补丁状态和收据，再按 main、service 顺序逐文件原子恢复；两文件间中断可
重跑同一脚本。未知改动、符号链接或不匹配的收据会拒绝，不直接覆盖。
只剩 service 补丁时，即使扩展包缺失，原生修复仍正常；AI 助手定性修复继续明确拒绝。
独立恢复脚本和固定锁文件在卸载后保留，以便重复恢复，不含用户资料。

安装固定锁为 `deeptutor/.deeptutor-link-install.flock`，评估固定锁位于原
`mastery.sqlite3` 同目录，名为 `.deeptutor-codex-<作用域哈希>.lock`。
它们即使无人持锁也不能删除、替换或清空，否则会丢失互斥与恢复身份。
若发现旧目录锁 `.deeptutor-link-install.lock`，本版明确拒绝，不猜测其是否过期；
先停止所有旧安装进程，确认目录为空后以 `rmdir` 删除该旧目录，再重跑。
这一步仅用于旧版目录锁，不能删除新的 `.flock` 或学习库内 `.lock` 文件。
卸载也移除定性 repair 保护，已有 AI 助手定性证据仍保留；在原宿主获得完整支持前，
不要对这些旧记录调用原 repair，必要时保留扩展保护或恢复该保护后再使用网站。
Skill 卸载只删除仍匹配的发行文件，保留已修改
文件与用户自定义文件；安装遇到已修改的发行文件会拒绝覆盖。
安装两次/卸载两次安全。配置与 Cookie 默认在
`$HOME/.config/deeptutor-link/`，可用 `--config`/`--credentials` 覆盖。

Docker 部署使用 `scripts/build_docker_extension.py` 和
`docker/Dockerfile.link` 构建持久派生镜像，再将原 Compose 的服务指向该镜像。
原端口、数据卷、环境和模型设置沿用；回退时换回原镜像。构建工具不部署或重建
容器，具体发现、构建、审查、切换与回退步骤见
[AI 安装与配置指南](src/deeptutor_link/assets/skill/references/setup.md)。
本次核对了固定官方镜像的版本、解释器和依赖入口；未执行派生镜像构建或真实实例安装。

## 开发验证

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[test]'
uv pip install --python .venv/bin/python -r tests/requirements-host.txt
uv pip install --python .venv/bin/python llama-index-core openai tiktoken
.venv/bin/python scripts/prepare_test_host.py --target /tmp/deeptutor-link-test-host
DEEPTUTOR_SOURCE=/tmp/deeptutor-link-test-host .venv/bin/pytest
.venv/bin/python -m build --wheel
.venv/bin/ruff check src tests scripts
```

`prepare_test_host.py` 获取上述固定 commit 并校验受支持指纹；已有副本可用
`--source /path/to/DeepTutor` 只校验，不访问网络。测试必须显式指定
`DEEPTUTOR_SOURCE`，不依赖父目录或其他应用的 vendor。验证源码指纹可用
`python scripts/generate_contract.py /path/to/DeepTutor/deeptutor src/deeptutor_link/assets/host-contract.json --check`；升级支持基线需要重新审查，不能改指纹绕过检查。

真实宿主 service 测试还需要其轻量依赖，见 `tests/requirements-host.txt`。
其中 `markdown-it-py` 用于导入原网页会话模块，运行租约交错测试时必需。
持久化检索集成测试另外需要 `llama-index-core`、`openai` 和 `tiktoken`；使用本地
合成 embedding 和现存合成索引，不消耗真实 API，也不表示真实 embedding 服务已验证。
测试仅将 DEEPTUTOR_HOME 指向临时目录，Skill/配置用显式临时路径。
恢复测试使用真实 SQLite 和子进程 SIGKILL，覆盖事务提交前后、安装/卸载/独立恢复
的多文件中断，以及活跃线程/进程保护、跨账号作用域和缺包修复。
测试不等于真实用户安装成功；
真实生产启动、真实 embedding 凭据、远程鉴权及浏览器操作均需单独验收。
