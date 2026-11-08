# AI 安装与配置指南

本指南覆盖安装、连接、切换实例、升级和配置排查。先识别当前客户端版本，
用 `--help` 查看命令，并在客户端支持时用 `describe` 读取参数和成功条件。
接着发现已有配置和宿主环境，沿用有效连接，只询问本地与服务端都无法确定的选择。
普通的“继续学习”读取 `SKILL.md` 并运行 `resume`；明确更新请求进入
[升级与恢复](#升级与恢复)，完成后再执行验证流程。

## 用户入口

当前已验证的入口是：用户下载仓库、用 Codex 打开项目后，说：

> 帮我配置连接一下我的 deeptutor。

根目录 `AGENTS.md` 将这类请求路由到本指南。负责配置的 AI 读取环境、执行适用步骤，
并在需要用户选择或安全输入时给出下一步。用户无需先安装 Skill 或知道内部
ID。配置完成后，在同一项目新聊天说“开始学习”，按 `SKILL.md` 的 Guided
Start 引导选择；全局安装后也可在其他合适的学习聊天中使用。

源码已公开于 GitHub 仓库 `yx709057978/DeepTutor-Link`，尚未发布到 PyPI。
从已取得并审查的本地源码或 wheel 安装。
仓库中的本文件和安装后的本文件是同一份发行资源。
若仓库开发副本和已安装副本都存在，日常操作使用安装副本自身的脚本和发行资源，
保持版本一致。明确更新时先识别当前副本，再使用选定目标发行资源中的本指南
执行“升级与恢复”；更新完成后切换到新安装副本继续操作。

## 先确定已有环境

1. 从本项目、用户提供的路径、已有启动信息和监听端口发现 DeepTutor。
   用 `command -v python3.11 python3.12 python3.13 uv docker` 查看可用工具，
   再运行候选解释器确认版本达到 3.11+。Docker 部署使用 `docker ps` 和筛选后的
   `docker inspect` 读取容器名、镜像、端口、Compose 路径与数据挂载；日志和输出
   只保留完成诊断所需的字段。源码部署核对服务进程的解释器和实际导入包位置。
   发现范围限定在项目、用户提供的路径、已有启动信息和监听服务；按该路径读取部署说明与配置。
2. 检查客户端是否已有 Skill，以及 `$HOME/.config/deeptutor-link/` 下
   是否已有配置。输出连接字段和状态，Cookie、token 与密码留在本机。
   已有连接运行 `doctor`；绑定主题后再运行 `resume --new-chat`。检查正常时沿用
   当前连接，将安装和 `initialize` 留给缺少连接或范围变更的场景。明确更新请求
   比较目标发行资源与已安装服务端、Skill，并按“升级与恢复”处理差异。
   根据 `--help` 判断旧脚本是否支持 `describe`；旧版用自身列出的只读命令完成检查。
   在本仓库配置时比较目标 Skill 与已安装文件，只记录不同或缺失的文件名。
   分别报告连接状态和发行内容是否更新；用户已指定更新时直接继续对应步骤。
3. 从已有配置或启动信息确定 API origin 和原网站 origin。找不到时请
   用户提供；浏览器地址不一定是 API 地址。origin 只含协议、主机和
   端口，不含路径、账号密码、query 或 fragment。
4. 本版服务端运行于 macOS/Linux、本地文件系统、Python 3.11+，以及
   DeepTutor v1.7.1 / `f7aed0744c36c5afef16a4261c31ebee1208cac2`
   的已审查源码指纹。源码或可写 wheel 使用下方原解释器安装路径；Docker
   使用持久派生镜像路径；Windows 服务端和 NFS/SMB 共享写入需要单独适配。
   通过源码指纹确认版本内容，版本名只作为初步识别。
5. 没有配置时，结合端口、进程或 Compose 找到候选 origin，以只读
   `/api/auth/status`、`/health/ready` 和 `/api/link/v1/identity` 确认宿主与扩展状态。
   原生健康接口正常而扩展身份接口返回 404 时，先检查扩展安装位置和代理路由。
   多个实例按名称或地址让用户选择；本地信息无法确定地址时，再询问 API 地址或远程访问方式。
6. AI 助手端安装整个脚本型 Skill，并使用已验证的 Python 3.11+。Skill
   可安装在与 DeepTutor 服务端不同的电脑上。

远程连接使用 HTTPS，或用户已有的 SSH 隧道加 localhost HTTP，并保持 TLS
证书验证开启。公网服务采用 DeepTutor 已配置的认证。只有远程网站访问权时，
可先安装本机 Skill，再列出需由服务端管理员执行的扩展安装步骤。

## 安装服务端扩展

以下 shell 变量是说明中的占位值。执行前替换为已确认的实际路径；
`DTC_HOST_PYTHON` 必须是原 DeepTutor 服务使用的解释器。

```sh
DTC_REPO='/absolute/path/to/deeptutor-link'
DTC_HOST='/absolute/path/to/DeepTutor'
DTC_HOST_PYTHON='/absolute/path/to/original/venv/bin/python'

"$DTC_HOST_PYTHON" -c 'import importlib.util, sys; s = importlib.util.find_spec("deeptutor"); print(sys.executable); print(s.origin if s else "deeptutor not importable")'
"$DTC_HOST_PYTHON" -m pip show deeptutor-link
```

`pip show` 找不到扩展包表示尚未安装。将 `DTC_HOST` 指向原进程实际导入的宿主，
它可以是源码根目录或 `site-packages/deeptutor`；使用解析后的真实路径与运行进程核对。

在用户授权的维护范围内，先按原启动方式停止服务，再安装扩展。服务停止、
宿主环境管理和 DeepTutor 版本管理沿用原部署流程；安装器只处理扩展文件。

```sh
"$DTC_HOST_PYTHON" -m pip install "${DTC_REPO}[server]"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-check --host "$DTC_HOST"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-install --host "$DTC_HOST" --dry-run
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-install --host "$DTC_HOST"
```

安装前检查现有依赖，维持原宿主依赖版本。原环境由 uv 管理且没有 pip 时，
沿用现有 uv，以 `uv pip install --python "$DTC_HOST_PYTHON" "${DTC_REPO}[server]"`
代替 pip 安装；其他 CLI 命令仍使用同一个宿主解释器。若使用已构建 wheel，则以实际
wheel 路径替换 pip 的本地源码参数，并保证原环境已有服务端依赖。
`--dry-run` 仅预览宿主补丁；前面的 pip 安装仍会安装包。

`server-check` 校验关键源码指纹，返回宿主版本、基线提交和补丁状态。
安装完成以 `patched=true` 为成功判据。指纹不匹配时保留现场，核对支持版本和
本地改动后选择兼容的发行资源。安装器保存补丁收据及标准库独立恢复脚本，
只修改扩展安装所需文件；学习库、资料、索引、账号和模型配置保持原样。
相同版本的安装可以安全重跑。

按原方式启动 DeepTutor，确认原网站可打开，再检查
`GET /api/link/v1/identity` 和认证/连接流程。本机 Skill 与服务端扩展分别验收；
远程部署还需在服务端完成本节安装。Docker 部署使用下方持久派生镜像路径。
缺少服务端权限时，提供已准备好的管理员操作步骤。

## Docker 持久安装

从容器的 Compose 标签、镜像和挂载确认原部署。记录原镜像的精确引用、
Compose 文件、服务名、端口、环境文件及数据卷；检查结果只保留诊断所需的变量名与挂载信息，
秘密值不进入输出，数据卷保持不变。
固定官方基线是：

```text
ghcr.io/hkuds/deeptutor:1.7.1@sha256:4982ca6bf43b98431366b43d1fec674a63656db3fbe49ba18c674bb6f19196dd
```

选择与受支持 commit 和源码指纹匹配的镜像。自定义镜像先核实其改动和依赖，
再确定兼容基线。下面使用默认固定基线；构建工具
检查本地镜像标签，在派生层中安装 wheel 并执行同一 `server-check` 和
`server-install`。构建机解释器要满足 Python 3.11+，支持 `venv` 和
`ensurepip`。脚本自动建立临时构建环境，用其中的 pip 按 `pyproject.toml`
   安装构建依赖，结束后删除临时环境；系统 Python 的 setuptools 保持原版本。
构建依赖仍需按本机既有 pip 源或缓存策略取得。

```sh
DTC_BUILD_PYTHON='/absolute/path/to/verified/python3.11-or-newer'
DTC_IMAGE='deeptutor-link-local:1.7.1-ext-0.2.1'
"$DTC_BUILD_PYTHON" "$DTC_REPO/scripts/build_docker_extension.py" --tag "$DTC_IMAGE"
```

本地没有默认基线镜像时，按原部署的联网策略取得上述精确引用后再构建。
`--base` 可指定含 digest 的已审查基线，工具仍校验版本和 commit；基础镜像
始终使用已审查的具体版本或 digest。构建只生成 wheel 和派生镜像，不切换运行容器。

把 `docker/compose.link-derived.override.yaml` 复制到原部署目录，改为
原服务名，并将 image 设为本次实际构建标签。模板中的 `DTC_LINK_IMAGE`
也可继续使用，值应写入该部署的持久配置。以下变量替换为发现的真实绝对路径和服务名：

```sh
DTC_COMPOSE='/absolute/path/to/original/docker-compose.yml'
DTC_OVERRIDE='/absolute/path/to/original/compose.link.override.yaml'
DTC_SERVICE='deeptutor'
docker compose -f "$DTC_COMPOSE" -f "$DTC_OVERRIDE" config --services
docker compose -f "$DTC_COMPOSE" -f "$DTC_OVERRIDE" config --images
docker compose -f "$DTC_COMPOSE" -f "$DTC_OVERRIDE" up -d --no-build --pull never "$DTC_SERVICE"
```

使用原部署的 project name、工作目录和 env-file。切换前检查合并配置的挂载、
端口、环境来源和 service，只把镜像变更带入部署；展示配置时筛除秘密值。
`--no-build` 保留原 build 设置而不触发构建。先按“待答题与维护判断”确认活动回合，
保留持久待答题，再执行切换。
启动后先验证原健康接口和网站，再验证扩展身份接口及原主题只读状态。

把后续启动方式记录到原部署说明：使用原 Compose 加这份 override，确保维护后
仍有持久可复用的启动命令。卸载时按原启动方式仅使用原 Compose
恢复记录的原镜像，使用 `--no-build --pull never`，保留全部原数据卷。原
Compose 本身已修改镜像时，先恢复保存的原引用。恢复时保留全部数据卷。
客户端 Skill 的安装与连接按下文继续。

## 安装 AI 助手 Skill

Codex 默认安装到 `$HOME/.agents/skills/`，供新聊天使用；仅当前项目使用时，
安装到项目的 `.agents/skills/`。目标目录保留一个与目标发行资源一致的 Skill 副本。
其他 AI 助手使用其实际支持的 Skill 目录，并先验证 Skill 加载、Python 脚本执行和本地图片查看能力。
当前已有 Codex 验证流程；其他客户端需要按其 Skill 机制完成适配验证后再报告支持状态。
在客户端终端先设置目标目录；若与服务端分开，`DTC_REPO` 要指向客户端
已取得的源码目录，两台机器的 shell 变量不会自动同步。

```sh
DTC_SKILL_PARENT="$HOME/.agents/skills"
```

如果同一台机器已安装扩展包，可使用原解释器和已有安全安装器：

```sh
"$DTC_HOST_PYTHON" -m deeptutor_link.cli skill-install --target "$DTC_SKILL_PARENT" --dry-run
"$DTC_HOST_PYTHON" -m deeptutor_link.cli skill-install --target "$DTC_SKILL_PARENT"
```

客户端没有扩展包时，直接从已审查源码复制整个 Skill。先检查
`$DTC_SKILL_PARENT/deeptutor-link`；目标不存在时复制完整发行目录：

```sh
mkdir -p "$DTC_SKILL_PARENT"
cp -R "$DTC_REPO/src/deeptutor_link/assets/skill" "$DTC_SKILL_PARENT/deeptutor-link"
```

复制应包含 `SKILL.md`、`scripts/deeptutor_link.py` 和本
`references/setup.md`，并保留发行目录中的全部 `references/*.md`。目标已存在时
先按“升级与恢复”处理。
复制 Skill 的客户端可以直接运行其标准库脚本，随后检查独立运行：

```sh
DTC_SKILL="$DTC_SKILL_PARENT/deeptutor-link"
DTC_CLIENT_PYTHON='/absolute/path/to/verified/python3.11-or-newer'
"$DTC_CLIENT_PYTHON" -I -S "$DTC_SKILL/scripts/deeptutor_link.py" --help
"$DTC_CLIENT_PYTHON" -I -S "$DTC_SKILL/scripts/deeptutor_link.py" describe
```

本文后续 `CLIENT` 表示上述 Python 加脚本绝对路径，执行时替换为实际命令。
配置聊天可直接读取本地 `SKILL.md` 继续，无需中途切换聊天。安装结束后，
告知用户在同一项目新开聊天说“开始学习”；根目录入口和已安装 Skill 都可引导。
连接流程沿用用户当前选定的 AI 助手 provider 和模型。

## 认证与选择主题

1. 先用已有凭据执行 `discover`。需要判断认证状态时，只读请求宿主
   `GET /api/auth/status`，检查 `enabled` 和 `authenticated`。
   `enabled=false` 时跳过登录，使用接口返回的本地身份，不询问账号密码。
2. `enabled=true` 时复用可用的受支持 Cookie/token；缺少凭据时调用 `login`，
   由用户在自己的交互终端隐藏输入原密码。若 AI 终端不支持安全输入，
   让用户在本机完成这一步后继续。密码仅通过登录界面的隐藏输入提交；
   聊天、命令参数、stdin 管道和日志均不承载密码；浏览器 Cookie 由用户通过 DeepTutor 正常登录管理。
3. `discover` 读取账号、工作区和主题名称。复用用户已明确选择的项目；
   有多个候选时请用户选择，并从接口返回值取得内部 ID。
4. 用 `initialize` 验证并保存连接。首次配置省略 `--workspace` 接受宿主
   当前解析的工作区；同实例重配应沿用已验证选择。默认通用工作区可显式
   用 `--workspace ''`。其他工作区使用
   `workspaces` 返回的实际 ID。

```sh
CLIENT login --url API_ORIGIN --username ORIGINAL_USERNAME
CLIENT discover --url API_ORIGIN --web-url WEB_ORIGIN
CLIENT initialize --url API_ORIGIN --web-url WEB_ORIGIN --workspace WORKSPACE_ID
CLIENT topics
CLIENT bind --topic TOPIC_ID
```

`login` 仅在认证开启且需要登录时执行。`API_ORIGIN` 和 `WEB_ORIGIN`
替换成已确认地址；`TOPIC_ID` 使用所选工作区内 `topics` 的返回值。
`topics` 可用 `--offset` / `--limit` 分页，每页 1–50 项，offset 从 0 开始；
按 `next_offset` 逐页读取完整清单。`context` 每页最多 80 项，
历史命令每页最多 50 项，`skill-read` 每页最多 24000 字符，`retrieve` 每次最多
返回 8 条。`CLIENT describe COMMAND` 不联网，可查类型、范围、默认值、最小调用、
返回结构、成功判据、绑定范围和重试规则。参数错误时先按契约修正参数；凭据状态变化时
再重新登录或运行 `doctor`。
若已在默认工作区发现目标，可给 `initialize` 增加 `--topic TOPIC_ID`，
省去独立绑定。原网站与 API 同 origin 时可省略 `--web-url`。

切换工作区先保存原连接备份，再用 `initialize` 选择工作区并列出其主题。
同工作区切换主题使用 `bind`。已有正常配置继续沿用；同一
实例、账号和工作区重配时，省略的 topic、persona、模式和语言应保留；保存前
校验原 topic 仍可访问。web origin 在同实例下沿用，token 环境变量名在相同
连接账号下沿用。换实例、账号或工作区后重新选择学习绑定。确实要清除同范围
的主题绑定可用 `initialize --clear-topic`；连接异常先运行 `doctor`，再按返回身份和范围修复。

## 配置与凭据

默认文件位于 `$HOME/.config/deeptutor-link/`，与源码及 Skill 分开存放。
用 `initialize` / `bind` 写入经过验证的连接；工作区和主题 ID 采用 `discover`、`workspaces` 与 `topics` 返回的值。

| `connection.json` 字段 | 含义 |
| --- | --- |
| `url` | API origin，必需 |
| `web_url` | 原网站 origin；初始化未指定时使用 API origin |
| `account_id` | 接口验证的原账号 ID；关闭认证也使用接口返回值 |
| `workspace_id` | 原内容工作区 ID；空字符串代表通用工作区 |
| `topic_id` | 当前主题 ID，可未绑定；`resume` 需要它 |
| `token_env` | Bearer token 的环境变量名，默认 `DT_LINK_TOKEN`，不是 token 本身 |
| `persona` | AI 助手显式选择的原账号 persona；空/缺省表示 Mastery tutor baseline |
| `persona_source` | `assistant_explicit` 或 `website_session:<id>`，只记录显式来源 |
| `teaching_mode` | AI 助手本地教学模式：`outline` / `study` / `review`，不改网页 session |
| `teaching_language` | 请求的回复语言；提示词资源可按宿主规则回退到另一语言 |

`credentials.json` 以 API origin 为键保存 `login` 取得的 `dt_token`
Cookie，不保存密码。客户端优先使用 `token_env` 指定环境变量中的 token，
没有时才使用该 origin 的已存 Cookie；浏览器登录不会自动复制到这里。
诊断账号不符时先检查正在使用哪种凭据，不打印其内容。

`teaching-protocol.json` 缓存已读到的 persona/Mastery section 和指纹。它也在
同一私有配置目录中，权限为 600 且使用普通文件。换账号、工作区、主题、角色
模式或语言后旧缓存自动失效；`resume --new-chat` 读取当前聊天对应的新协议，
磁盘缓存只作为客户端缓存使用。

配置、凭据和教学缓存 JSON 均要求非符号链接、权限 `600`，写入脚本会设置权限。
自定义路径使用全局参数，必须放在子命令之前，后续每次调用都带上：

```sh
CLIENT --config /absolute/private/connection.json --credentials /absolute/private/credentials.json doctor
```

连接配置只保存 token 的环境变量名；用户提供的 token 通过现有安全环境注入。
凭据文件留在权限受限的私有配置目录，与 Skill 和源码仓库分开。当前默认路径固定在
`Path.home()` 下，不自动采用 `XDG_CONFIG_HOME`。

## 验证与开始使用

```sh
CLIENT doctor
CLIENT capabilities
```

检查协议/版本、账号、工作区和 host readiness。尚未选择主题时，连接可先
验收完成，把角色和主题选择留到新聊天的“开始学习”。已选择主题时再运行：

```sh
CLIENT resume --new-chat
CLIENT context --limit 1
```

确认主题存在，`resume` 返回原目标 ID、进度、原网站链接和
`teaching_protocol`。确认 `teaching_protocol.sections` 有 general、runtime_policy、
loop.system、当前 mode 和 playbook；`operations` 只按 `available=true` 的映射执行。
配置成功以实际身份、主题读取和对应 readiness 字段为依据；HTTP 成功或帮助信息只表示命令可达。

连接验收与学习验收分别报告。身份、健康检查、主题列表和 `capabilities`
确认连接及声明能力；资料检索路径以对选定资料实际调用 `retrieve` 成功为准。
测评写回以用户真实作答、评估提交成功和原状态重读为准；配置阶段只做只读验收，
学习记录从真实教学回合开始产生。

如需角色，先列出可用角色，让用户按名称选择：

```sh
CLIENT persona-list
CLIENT persona-select RETURNED_PERSONA_NAME
CLIENT resume --new-chat
```

用户选择了列表中的具体网页会话，并要求恢复其角色、模式或语言时，使用
列表返回的确切 ID 执行；无需让用户自己查 ID：

```sh
CLIENT persona-restore --session EXACT_SESSION_ID
CLIENT resume --new-chat
```

`persona-restore` 读取该已授权会话的 persona/mode/language 偏好，并保持原
网页 session 与聊天正文独立。选择已删除角色时返回 `persona_missing`；
   Mastery prompt 包缺失或不兼容返回 `mastery_prompts_unavailable` /
   `mastery_prompts_incompatible`。展示诊断结果，协助用户修复原资源或从可用角色中重新选择，
   再读取协议。

若主题有资料，用 `context.sources[].locator` 或授权文件列表取得 KB/文件名，
再做一次有界只读检查。例如：

```sh
CLIENT tool read_knowledge_file --file -
```

向该命令 stdin 提交 JSON：

```json
{
  "kb_name": "RETURNED_KB_REF",
  "filename": "RETURNED_RELATIVE_FILENAME",
  "offset": 0,
  "max_chars": 1200
}
```

把占位值替换为已授权真实引用。有 PDF/视觉要求时按当前 capabilities 和
Skill 的证据流程逐页验证；普通文件片段读取只用于文本内容。无资料的
目标型主题可跳过资料检查并说明原因。安装验收保持学习记录不变，只做连接和只读资料检查；
向量检索仅在用户需要且已有索引/查询 embedding 可用时额外验证。索引重建作为单独的维护操作处理。

DeepTutor 对话模型和 embedding 均按能力可选。AI 助手负责日常教学与评价；
未配置宿主模型时，按可用资料证据继续。原生模型工具分别按自身依赖运行，
需要宿主模型的能力在对应诊断中标明。

完成后报告：服务端与 Skill 版本、安装位置、选定工作区/主题名称、
实际验证的能力和仍待处理的功能。凭据值始终留在用户本地。
分开说明“连接是否可用”和“已安装副本是否与目标发行资源一致”；
`doctor` 的 `client_version` 和 `extension_version` 分别标识客户端和服务端，
`client_server_versions_match=false` 表示两端版本不同。0.2.1 客户端可以
诊断旧 0.2.0 服务；旧服务没有待答接续接口时，先更新两端，再重新读取原题并继续。
接口返回 404 时，待答题仍按未完成状态保留。`pending_question_continuation_available` 是该接口
的服务端声明，实际能力仍以原题读取和提交结果为准。
服务端发行内容以实际文件核验结果为准；相同的 `0.2.0` 版本号也逐项核对文件内容。
告诉用户在同一项目的新聊天说“开始学习”。按 `SKILL.md` 的 Guided Start
展示角色、主题和接续选项，使用 API 返回的真实 ID 保存选择，再运行
`resume --new-chat`。明确说“继续上次学习”时可沿用有效选择；用户不需要
复述配置、记住命令或在聊天中提供主题 ID。历史会话按标题列出供选择，
只读取用户选定会话的有界内容，不把历史状态覆盖到当前进度。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| `/api/link/v1/identity` 返回 404 | 核对 API origin、反向代理是否转发该路径、扩展是否装进实际运行环境，以及原服务是否重启 |
| 401 或登录过期 | 先查 `/api/auth/status`；认证开启时更新原凭据，关闭时跳过登录 |
| 403 或账号/工作区不一致 | 核实接口返回身份、工作区和资源范围，再通过 `discover` 重新选择；沿用 private-login-host 策略 |
| 找不到主题 | 核实当前工作区并分页读取 `topics`，按原主题 ID 继续其既有进度 |
| unsupported/drifted source | 记录具体文件，核对受支持版本与本地改动，再选择兼容发行资源 |
| 配置权限错误 | 确认配置/凭据文件路径与类型，再对该文件设置 `chmod 600`；其他用户目录保持原权限 |
| DeepTutor connection failed | 依次检查宿主服务、origin、HTTPS 证书、SSH 隧道和网络；按客户端的显式 origin 与代理策略发起请求 |
| readiness 未通过 | 检查宿主健康状态和日志，修复原因后重跑 readiness 与 `doctor` |
| `persona_missing` | 用 `persona-list` 选择当前可用角色，或由用户选择 `persona-clear` 回到 Mastery baseline |
| `mastery_prompts_unavailable` / `mastery_prompts_incompatible` | 核实原 DeepTutor 安装与 prompt 资源版本，修复原资源后重新运行 `resume --new-chat` |
| 网站 persona 恢复失败 | 用用户选定会话的精确 session ID、账号和工作区重新检查网页偏好 |
| 没有 embedding 或兼容索引 | 使用授权资料直读/字面搜索，并单独报告向量检索状态；其他可用能力照常运行 |
| 持久待答题，写入租约为空闲 | 保留原题，继续连接检查或有序维护；学习写入按对应命令的接续契约进行 |
| 活动写入或 protected/unknown lease | 查询对应回合和运行状态，等待活动写入结束后再维护；锁文件由原服务管理，待答题沿学习流程保留 |

## 升级与恢复

### 更新入口与范围

用户可以直接说“帮我更新 DeepTutor-Link，保留现有连接和学习进度”。
把它作为维护任务执行，先确定本次目标：用户指定的版本/commit，或已核实远端后取得的仓库更新。
把源码获取、服务端安装、Skill 安装和最终验收分别核实；本扩展更新保持 DeepTutor、模型、provider 和索引版本原样；
目标发行资源不支持现有宿主时，保持现状并报告具体不兼容项。

本项目尚未发布到 PyPI，使用已取得并审查的源码或 wheel。目标资源已经选定并取得时，
核实其身份和完整性后直接按该版本执行。需要取得远端更新时，先核对远端、分支和
`git status --short`；工作区干净且允许快进时执行 `git fetch origin` 和
`git pull --ff-only`。存在本地修改或分叉时先保留现场，再选定隔离的新目录。ZIP 更新解压到新目录，原目录继续作为回退点。

仓库、服务端和客户端可能位于不同机器。分别记录安装位置、执行权限和维护负责人；
沿用前文核实过的路径变量，并将客户端更新与服务端待办分开报告。

### 更新前记录与备份

1. 记录宿主实际解释器/包路径、扩展包版本、目标源码 commit 或 wheel
   SHA-256、安装 Skill 的位置和完整文件清单。相同的 0.2.0 版本号可能
   包含不同源码提交，必须比较实际发行文件；`server-check` 返回的补丁
   标记和收据版本用于识别补丁状态；扩展包版本以实际包元数据为准。
2. 用现有客户端只读运行 `doctor`；已绑定主题时运行 `context --limit 1`，
   记录账号、工作区、主题、角色和 revision。保留原 API/web origin、
   `token_env` 以及自定义 `--config` / `--credentials` / `--cache` 路径；
   凭据值留在本机。按下节区分持久待答与正在执行的回合；待答原样保留，
   活动回合按其运行状态有序结束后再维护。
3. 在仓库和所有 Skill 发现目录之外建立权限 `700` 的私有备份目录，
   保存原连接/凭据/缓存文件（如存在，权限 `600`）、完整旧 Skill、
   安装收据、独立恢复脚本，以及原启动方式。备份目录放在仓库和 Skill 发现路径之外，
   让回退文件独立保存，旧 Skill 也不会参与发现。
4. 在变更前准备可重新安装的旧版 wheel 或精确旧源码及其构建条件，
   Docker 则保留原镜像精确引用和 Compose override。版本号与 Git commit 用于识别版本，
   可离线回退的资源由备份目录实际保存。完成备份后再切换现有安装。

先比较 Skill 和服务端包的实际文件，再确定更新范围。Skill 内容一致时沿用；
只更新 Skill 时保持服务端运行。服务端包变化按下节维护；两端都更新时先完成
服务端验收，再安装 Skill。

### 待答题与维护判断

`pending.present=true`、`next.action=answer_pending` 表示原题保存在学习流程中。
旧版 `pending.action=finish_in_web` 同样表示网页学习步骤。维护判断分别读取
`pending.present` 和 `mutation_lease.status`；Python 中用
`context.get("pending", {}).get("present") is True` 判断是否存在待答题。

先核实真正活动的回合/写入与租约。`extension_active` 表示扩展正在写入；
`protected_lease` 关联网页或其他客户端，按对应回合确认。未知状态通过原服务的运行状态核实。
已绑定主题的 `clear` 只表示该主题当前没有活动写入；服务级维护还需使用原服务提供的
整体运行状态和有序停止方式。

持久待答与活动写入分开处理：活动回合结束并具备有序停服条件后，保留题目、
已提交答案和进度进行维护。重启后核对待答仍在，并记录本次维护是否改变 revision；
并发产生的合法变化单独说明。连接验收沿用待答原状。活动写入通过具体句柄/状态跟进，
持久待答由学习流程接续，不作为一个等待运行结束的任务轮询。

### 服务端源码或 wheel 更新

维护期间按原方式停止 DeepTutor。先使用旧版扩展和原解释器检查、卸载
已知旧补丁，确认 `patched=false` 后才替换 Python 包：

```sh
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-check --host "$DTC_HOST"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-uninstall --host "$DTC_HOST" --dry-run
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-uninstall --host "$DTC_HOST"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-check --host "$DTC_HOST"
```

旧版卸载保留 `.deeptutor-link-recover.py`。将恢复脚本保存到私有备份目录，
由新版安装器为本次发行资源生成匹配脚本。固定 `.flock` 文件由锁流程使用，
更新期间保持原样。旧包缺失时使用已备份的独立恢复脚本。源码或收据冲突时，
先记录冲突并核实补丁身份，再选择恢复或更新路径。

随后在同一宿主环境安装选定的新源码：

```sh
"$DTC_HOST_PYTHON" -m pip install --upgrade --upgrade-strategy only-if-needed "${DTC_REPO}[server]"
"$DTC_HOST_PYTHON" -m pip install --force-reinstall --no-deps "$DTC_REPO"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-check --host "$DTC_HOST"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-install --host "$DTC_HOST" --dry-run
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-install --host "$DTC_HOST"
```

第一步补齐需要的服务端依赖；第二步只重装本包，保证相同版本号但内容
不同的发行资源也被替换。wheel 安装用实际目标 wheel 路径替换两步中的
源码参数，并先核实服务端依赖。uv
环境使用 `uv pip install --python "$DTC_HOST_PYTHON" --reinstall-package deeptutor-link "${DTC_REPO}[server]"`，
确保相同版本号下的源码变化也重新安装；只刷新本包，不批量刷新宿主依赖。
`--force-reinstall` 必须和 `--no-deps` 一起用于本包。确认新包实际导入位置、
`server-check` 的基线与完整补丁状态，再按原方式启动原服务。

Docker 更新按“Docker 持久安装”构建新的持久派生镜像，使用新的本地
镜像标签并记录 digest；复用原挂载、端口、环境来源和 Compose project。
切换前核对合并配置，并保留可回退的旧镜像/override。更新通过构建和切换派生镜像完成，
原运行容器和数据卷沿用既有管理方式。

### 更新已安装 Skill

`skill-install` 会比较目标完整 Skill 目录和已安装目录。发现本地自定义文件时，
先备份并单独保留这些修改，再安装目标发行资源；混合内容按自定义副本说明。

完成私有备份后，将旧 `deeptutor-link` 目录移出 Skill 发现目录，再按
“安装 AI 助手 Skill”复制完整新目录。检查目标层级及 `SKILL.md`、脚本和全部参考文档，
确保它们来自同一发行资源。服务端安装器使用本次选定发行资源；客户端没有 Python 包时，
直接复制目标版资源即可。

私有连接目录保持原样，现有登录、连接范围、主题绑定和角色选择继续沿用。
凭据过期时通过原账号重新登录；账号或范围不符时修复对应连接。新聊天运行
`resume --new-chat` 读取当前协议，磁盘缓存只用于客户端运行。

### 更新验收与回退

使用新安装目录的脚本运行 `--help`、离线 `describe`、`doctor` 和
`capabilities`；已绑定主题时再运行 `resume --new-chat`、
`context --limit 1`。检查原网站和原健康接口仍正常，核对身份接口的
`extension_version`、服务端实际包版本与目标资源，以及 Skill 完整文件
是否一致。核对账号/工作区/主题绑定和角色选择；并发期间 revision
可以增加，以当前学习库为准并将备份用于文件回退。配置和只读验收保持学习记录不变。

报告目标 commit/wheel、两端安装位置、已完成和待完成的更新、验证结果
及私有备份位置。全部通过后新开聊天说“继续学习”；分别说明仓库获取、服务端更新和 Skill 更新状态。

失败时保持维护状态：用当前包或本次安装保存的恢复脚本卸载新补丁，
确认宿主恢复后备份本次恢复脚本，再用已保留的旧源码/wheel 安装原包和原补丁；完整恢复旧 Skill。
Docker 切回原镜像和原 override，并复用原数据挂载。学习库保持当前状态；
连接配置按需从对应私有备份恢复。补丁身份或宿主指纹需要进一步核实时，记录冲突并完成核实后再启动服务。

### 旧名称迁移

从旧名称 DeepTutor-Codex 迁移时，检查旧全局/项目 Skill 和私有目录
`$HOME/.config/deeptutor-codex/`，再确定迁移配置范围。保留已有新版配置，
Skill 发现目录使用选定版本。原服务停止后，用旧环境的 `deeptutor-codex server-uninstall`
或旧 `.deeptutor-codex-recover.py` 卸载旧补丁，再按本指南安装新版。
Docker 使用新版派生镜像与原挂载，无需在运行容器里卸包或改源码。

旧连接与凭据在用户选择沿用时复制到新的私有目录并保留 `600` 权限；用 API origin
和实际身份核对账号、工作区及主题。旧 `token_env` 的显式值仍有效，全新连接默认用
`DT_LINK_TOKEN`。迁移时把 `persona_source=codex_explicit` 映射为 `assistant_explicit`；
运行 `resume --new-chat` 重新读取教学协议。旧配置与 Skill 的备份放在 skills 和公共仓库之外。
先通过旧版扩展或恢复脚本卸载旧补丁，再使用新版安装器并核对源码指纹。

### 卸载与独立恢复

卸载按“停止原服务 → 卸载宿主补丁 → 移除 Skill → 卸载 Python 包”的顺序执行：

```sh
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-uninstall --host "$DTC_HOST" --dry-run
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-uninstall --host "$DTC_HOST"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli skill-uninstall --target "$DTC_SKILL_PARENT"
"$DTC_HOST_PYTHON" -m pip uninstall deeptutor-link
```

原环境由 uv 管理且没有 pip 时，最后一步改用
`uv pip uninstall --python "$DTC_HOST_PYTHON" deeptutor-link`。

服务器与客户端分开时，在各自机器处理；客户端只有复制的 Skill 时，
确认安装目录和本地修改后移走该 Skill。卸载会保留连接、Cookie 和学习状态。
需要清除本地登录时，在脚本移走前运行
`CLIENT logout --url API_ORIGIN`；它只删除本地该 origin 的 Cookie，
不撤销环境变量里的 token，也不注销原网站会话。

扩展包缺失、无法启动时，保持原服务停止，使用安装时保存的独立恢复
脚本。以下 `DTC_HOST_PACKAGE` 是实际包含 `api/`、`learning/` 的
`deeptutor` 包目录，可从之前 `server-check` 的 `package` 字段取得：

```sh
"$DTC_HOST_PYTHON" -I -S "$DTC_HOST_PACKAGE/.deeptutor-link-recover.py" --host "$DTC_HOST_PACKAGE" --dry-run
"$DTC_HOST_PYTHON" -I -S "$DTC_HOST_PACKAGE/.deeptutor-link-recover.py" --host "$DTC_HOST_PACKAGE"
```

恢复脚本会先用源码指纹、符号链接检查和收据确认目标，并保留冲突文件供核对。
固定 `.flock` / 学习库 `.lock` 文件由锁流程管理；活动状态通过租约和运行状态判断。
安装中断时按已确认的安装/卸载步骤重试；学习写入租约按 `SKILL.md` 的接续流程处理。
