# AI 安装与配置指南

当用户要求安装、连接、切换实例、升级或排查配置时，按本指南执行。
先读当前脚本的 `--help`，复用已有配置，只询问无法发现且会影响结果的信息。
一次普通的“继续学习”使用 `resume`，不需要重新安装或初始化。

## 用户入口

当前已验证的入口是：用户下载仓库、用 Codex 打开项目后，说：

> 帮我配置连接一下我的 deeptutor。

根目录 `AGENTS.md` 将这类请求路由到本指南。负责配置的 AI 执行适用步骤，
不把本指南或整套命令交还用户手动操作。用户无需先安装 Skill 或知道内部
ID。配置完成后，在同一项目新聊天说“开始学习”，按 `SKILL.md` 的 Guided
Start 引导选择；全局安装后也可在其他合适的学习聊天中使用。

本项目尚未发布。使用已经取得并审查的本地源码或 wheel，不假设 PyPI
已有可安装版本，也不编造下载地址。仓库中的本文件和安装后的本文件是
同一份发行资源。

## 先确定已有环境

1. 检查本项目、用户提供的路径、已有启动信息和当前监听端口。可用
   `command -v python3.11 python3.12 python3.13 uv docker` 确认工具，实际
   运行候选解释器查看版本，不能假定 `python3` 就满足 3.11+。发现 Docker
   时用 `docker ps` 与筛选后的 `docker inspect` 查看容器名、镜像、端口、
   Compose 路径和数据挂载。不要输出整个容器环境变量或凭据。源码部署则
   核实进程的解释器和实际导入包位置。根据已发现路径读取部署说明，不为
   找地址遍历整个 Home、密钥目录或浏览器配置。
2. 检查客户端是否已有 Skill，以及 `$HOME/.config/deeptutor-link/` 下
   是否已有配置。只读取连接字段，不把 Cookie、token 或密码放进输出。
   已有连接先运行 `doctor`；绑定了主题则再运行 `resume --new-chat`。正常时直接
   沿用，跳过安装和 `initialize`。
3. 从已有配置或启动信息确定 API origin 和原网站 origin。找不到时请
   用户提供；浏览器地址不一定是 API 地址。origin 只含协议、主机和
   端口，不含路径、账号密码、query 或 fragment。
4. 本版服务端支持 macOS/Linux、本地文件系统、Python 3.11+，以及
   DeepTutor v1.7.1 / `f7aed0744c36c5afef16a4261c31ebee1208cac2`
   的已审查源码指纹。源码或可写 wheel 使用下方原解释器安装路径；Docker
   使用持久派生镜像路径，不在运行中容器热补丁。Windows 服务端和
   NFS/SMB 共享写入不在支持范围。版本名相同不代表指纹匹配。
5. 没有配置时，先结合端口、进程或 Compose 找到候选 origin，以只读
   `/api/auth/status`、`/health/ready` 和 `/api/link/v1/identity` 确认。原生
   服务健康而扩展接口 404 表示可能尚未安装扩展，不能反复调用 discover
   或把用户带进登录循环。多个实例时让用户按名称或地址选择。确实无法
   发现时才询问地址或远程机器访问方式。
6. AI 助手端安装整个脚本型 Skill，并使用已验证的 Python 3.11+。Skill
   可安装在与 DeepTutor 服务端不同的电脑上。

远程连接使用 HTTPS，或用户已有的 SSH 隧道加 localhost HTTP。不要关闭
TLS 验证或把未认证服务开放到公网。若只有远程网站访问权，没有服务端
终端权限，可先安装本机 Skill，并明确列出还需管理员执行的服务端步骤。

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

`pip show` 找不到扩展包表示尚未安装。确认 `DTC_HOST` 对应原进程实际导入
的宿主；它可指向源码根目录或 `site-packages/deeptutor`，应使用解析后的
真实路径。不要补丁一个参考仓库却让服务继续运行另一份代码。

在用户授权的维护范围内，先按原启动方式停止服务，再安装扩展。安装器
不负责停止或重启进程，不重新创建宿主环境，不安装另一份 DeepTutor。

```sh
"$DTC_HOST_PYTHON" -m pip install "${DTC_REPO}[server]"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-check --host "$DTC_HOST"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-install --host "$DTC_HOST" --dry-run
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-install --host "$DTC_HOST"
```

先检查现有依赖，避免强制升级原宿主依赖。原环境由 uv 管理且没有 pip 时，
沿用现有 uv，以 `uv pip install --python "$DTC_HOST_PYTHON" "${DTC_REPO}[server]"`
代替 pip 安装；其他 CLI 命令仍使用同一个宿主解释器。若使用已构建 wheel，则以实际
wheel 路径替换 pip 的本地源码参数，并保证原环境已有服务端依赖。
`--dry-run` 仅预览宿主补丁；前面的 pip 安装仍会安装包。

`server-check` 校验关键源码指纹，返回宿主版本、基线提交和补丁状态。
安装成功应返回 `patched=true`；检查失败就停止，不改版本号、指纹清单
或宿主源码来强行通过。安装器保存补丁收据及标准库独立恢复脚本，不
修改学习库、资料、索引、账号或模型配置。重跑相同版本安装是安全的。

按原方式启动 DeepTutor，确认原网站仍可打开，并检查
`GET /api/link/v1/identity` 和后面的认证/连接流程。只装好本机 Skill
不等于远程服务端已装好。Docker 部署使用下方持久派生镜像路径。只有远程
网站访问权时，明确说明还需服务端权限，并给出已准备好的具体操作。

## Docker 持久安装

先从容器的 Compose 标签、镜像和挂载确认原部署。记录原镜像的精确引用、
Compose 文件、服务名、端口、环境文件及数据卷；不输出环境变量值，不删除卷。
固定官方基线是：

```text
ghcr.io/hkuds/deeptutor:1.7.1@sha256:4982ca6bf43b98431366b43d1fec674a63656db3fbe49ba18c674bb6f19196dd
```

已有镜像必须匹配受支持的 commit 和源码指纹。自定义镜像中的未知改动需要
先核实，不能丢弃原依赖或用别的镜像覆盖。下面使用默认固定基线；构建工具
检查本地镜像标签，在派生层中安装 wheel 并执行同一 `server-check` 和
`server-install`。构建机解释器要满足 Python 3.11+ 且具有 pip：

```sh
DTC_BUILD_PYTHON='/absolute/path/to/verified/python3.11-or-newer'
DTC_IMAGE='deeptutor-link-local:1.7.1-ext-0.2.0'
"$DTC_BUILD_PYTHON" "$DTC_REPO/scripts/build_docker_extension.py" --tag "$DTC_IMAGE"
```

本地没有默认基线镜像时，按原部署的联网策略取得上述精确引用后再构建。
`--base` 可指定含 digest 的已审查基线，工具仍校验版本和 commit；不使用
`latest`。构建只生成 wheel 和派生镜像，不切换运行容器。

把 `docker/compose.link-derived.override.yaml` 复制到原部署目录中，改为
原服务名，并将 image 设为本次实际构建标签。也可保留模板中的
`DTC_LINK_IMAGE` 变量，但要在该部署的持久配置中保存，不能只在当前 AI
进程临时 export。以下变量替换为发现的真实绝对路径和服务名：

```sh
DTC_COMPOSE='/absolute/path/to/original/docker-compose.yml'
DTC_OVERRIDE='/absolute/path/to/original/compose.link.override.yaml'
DTC_SERVICE='deeptutor'
docker compose -f "$DTC_COMPOSE" -f "$DTC_OVERRIDE" config --services
docker compose -f "$DTC_COMPOSE" -f "$DTC_OVERRIDE" config --images
docker compose -f "$DTC_COMPOSE" -f "$DTC_OVERRIDE" up -d --no-build --pull never "$DTC_SERVICE"
```

使用原部署的 project name、工作目录和 env-file。执行切换前核对合并配置的
挂载、端口、环境来源和 service 未改变，仅更新镜像；不要将含秘密的完整
`compose config` 输出到聊天。已有 `build` 配置由 `--no-build` 避免触发。
配置请求包含此维护操作时直接完成；仍在运行的学习任务需先妥善结束。
启动后先验证原健康接口和网站，再验证扩展身份接口及原主题只读状态。

把后续启动方式记录到原部署说明：使用原 Compose 加这份 override；不要
留下只有本次终端能运行的临时步骤。卸载时按原启动方式仅使用原 Compose
恢复记录的原镜像，使用 `--no-build --pull never`，保留全部原数据卷。原
Compose 本身已被修改过镜像时，先恢复保存的原引用。不要执行 `down -v`。
客户端 Skill 的安装与连接按下文继续。

## 安装 AI 助手 Skill

Codex 默认安装到 `$HOME/.agents/skills/`，供新聊天使用；用户明确要求仅当前
项目使用时，改放该项目的 `.agents/skills/`。不要同时保留同名新旧副本。
其他 AI 助手使用其实际支持的 Skill 目录，不能假定会读取 Codex 的路径。
先确认它能加载 Skill 并执行 Python 脚本；PDF 视觉流程另需查看本地图片。
目前没有为其他客户端提供已验证的安装适配，不把脚本可运行报告成完整支持。
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

客户端没有扩展包时，直接从已审查源码复制整个 Skill。先检查目标；仅在
`$DTC_SKILL_PARENT/deeptutor-link` 尚不存在时执行复制，避免嵌套或覆盖：

```sh
mkdir -p "$DTC_SKILL_PARENT"
cp -R "$DTC_REPO/src/deeptutor_link/assets/skill" "$DTC_SKILL_PARENT/deeptutor-link"
```

复制应包含 `SKILL.md`、`scripts/deeptutor_link.py` 和本
`references/setup.md`。目标已存在时先按“升级与恢复”处理。
客户端不需要为了复制 Skill 而安装 Python 包。随后检查独立运行：

```sh
DTC_SKILL="$DTC_SKILL_PARENT/deeptutor-link"
DTC_CLIENT_PYTHON='/absolute/path/to/verified/python3.11-or-newer'
"$DTC_CLIENT_PYTHON" -I -S "$DTC_SKILL/scripts/deeptutor_link.py" --help
```

本文后续 `CLIENT` 表示上述 Python 加脚本绝对路径，执行时替换为实际命令。
配置聊天可以直接读取本地 `SKILL.md` 继续，不要求用户为刷新技能列表中途
换聊天。安装结束后告知用户在同一项目新开聊天说“开始学习”；根目录入口
和已安装 Skill 都能引导。不要修改用户选定的 AI 助手 provider 或模型。

## 认证与选择主题

1. 先用已有凭据执行 `discover`。需要判断认证状态时，只读请求宿主
   `GET /api/auth/status`，检查 `enabled` 和 `authenticated`。
   `enabled=false` 时跳过登录，使用接口返回的本地身份，不询问账号密码。
2. `enabled=true` 且已有受支持的 Cookie/token 可用时，复用它们。否则
   执行下面的 `login`，由用户在自己的交互终端隐藏输入原密码。若 AI
   终端不能让用户安全输入，交给用户执行这一步后继续。不要让密码进入
   聊天、参数、stdin 管道或日志；不要读取浏览器 Cookie 数据库。
3. `discover` 只读，不写连接配置。展示返回的账号、工作区和主题名称，
   复用用户已明确选择的项目；有多个候选才询问。内部 ID 从返回值取得。
4. 用 `initialize` 验证并保存连接。首次配置省略 `--workspace` 接受宿主
   当前解析的工作区；同实例重配应沿用已验证选择。默认通用工作区可显式
   用 `--workspace ''`。其他工作区使用
   `workspaces` 返回的实际 ID，不能用名称猜 ID。

```sh
CLIENT login --url API_ORIGIN --username ORIGINAL_USERNAME
CLIENT discover --url API_ORIGIN --web-url WEB_ORIGIN
CLIENT initialize --url API_ORIGIN --web-url WEB_ORIGIN --workspace WORKSPACE_ID
CLIENT topics
CLIENT bind --topic TOPIC_ID
```

`login` 仅在认证开启且需要登录时执行。`API_ORIGIN` 和 `WEB_ORIGIN`
替换成已确认地址；`TOPIC_ID` 使用所选工作区内 `topics` 的返回值。
`topics` 可用 `--offset` / `--limit` 分页，不能把一页当完整清单。
若已在默认工作区发现目标，可给 `initialize` 增加 `--topic TOPIC_ID`，
省去独立绑定。原网站与 API 同 origin 时可省略 `--web-url`。

切换工作区先保存原连接备份，再用 `initialize` 选择工作区并列出其主题。
同工作区切换主题仅用 `bind`。已有正常配置不要反复 `initialize`。同一
实例、账号和工作区重配时，省略的 topic、persona、模式和语言应保留；保存前
校验原 topic 仍可访问。web origin 在同实例下沿用，token 环境变量名在相同
连接账号下沿用。换实例、账号或工作区后重新选择学习绑定。确实要清除同范围
的主题绑定可用 `initialize --clear-topic`，不要靠猜一个 ID 来消除连接错误。

## 配置与凭据

默认文件位于 `$HOME/.config/deeptutor-link/`，与源码及 Skill 分开存放。
用 `initialize` / `bind` 写入经过验证的连接，不要求用户手填内部 ID。

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
同一私有配置目录中，要求权限 600 且不是符号链接。换账号、工作区、主题、角色
模式或语言后旧缓存自动失效；`resume --new-chat` 表示新聊天首读，不能用磁盘缓存
冒充本聊天已经读过。

配置、凭据和教学缓存 JSON 均要求非符号链接、权限 `600`，写入脚本会设置权限。
自定义路径使用全局参数，必须放在子命令之前，后续每次调用都带上：

```sh
CLIENT --config /absolute/private/connection.json --credentials /absolute/private/credentials.json doctor
```

不要把凭据放进 Skill、源码仓库或示例配置。使用用户已有的安全环境注入
token，不在命令行写 token 值。当前默认路径固定在 `Path.home()` 下，
不会自动采用 `XDG_CONFIG_HOME`。

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
HTTP 成功或帮助信息能显示，不能单独证明配置完成。

如需角色，先列出并让用户明确选择；不要从最新网页会话猜测：

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

`persona-restore` 只读取该已授权会话的 persona/mode/language 偏好，不改变原
网页 session，也不读取或导入聊天正文。选择删除的角色时返回 `persona_missing`；
Mastery prompt 包缺失或不兼容返回 `mastery_prompts_unavailable` /
`mastery_prompts_incompatible`。这些情况如实报告并让用户修复或重新选择，
不静默换成别的角色或提示包。

若主题有资料，用 `context.sources[].locator` 或授权文件列表取得真实
KB/文件名，再做一次有界只读检查；不猜本地路径。例如：

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

把占位值替换为已授权真实引用。有 PDF/视觉限制时按当前 capabilities 和
Skill 的证据流程诊断，普通文件片段读取不代表视觉证据已验证。无资料的
目标型主题可跳过资料检查并说明原因。安装验收不提交虚构作答、测评或
学习进度。向量检索仅在用户需要且已有索引/查询 embedding 可用时额外
验证，不为了完成安装重建索引。

DeepTutor 对话模型和 embedding 均为按能力可选配置。AI 助手负责日常
教学与评价；没有宿主模型时按当前证据能力继续，不要求补齐 API key。
宿主原生模型工具仍受各自依赖约束，不能承诺所有工具都自动回退到 AI 助手。

完成后报告：服务端与 Skill 版本、安装位置、选定工作区/主题名称、
实际验证的能力、未验证或受限的功能。不要报告密码、Cookie 或 token。
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
| 403 或账号/工作区不一致 | 核实实际身份和 workspace；重新发现并显式选择，不绕过权限或 private-login-host 策略 |
| 找不到主题 | 核实工作区并分页 `topics`，不要新建同名路线代替旧进度 |
| unsupported/drifted source | 停止打补丁，报告具体文件；核实支持版本与用户改动，不强行改指纹 |
| 配置权限错误 | 仅对确认的非符号链接配置/凭据文件设置 `chmod 600`，不递归改整个 Home |
| DeepTutor connection failed | 检查服务、origin、HTTPS 证书、已有隧道和网络；客户端不跟随重定向、不使用系统 HTTP 代理 |
| readiness 未通过 | 检查宿主原服务与日志，解决后重试；不要忽略健康检查或伪报已完成 |
| `persona_missing` | 用 `persona-list` 重新选择，或 `persona-clear` 回到 Mastery baseline；不猜替代角色 |
| `mastery_prompts_unavailable` / `mastery_prompts_incompatible` | 修复原 DeepTutor 安装/资源，不要用第二套硬编码教学库替代 |
| 网站 persona 恢复失败 | 确认确切 session ID、账号和工作区；不要改网页偏好，也不要改用“最新聊天” |
| 没有 embedding 或兼容索引 | 使用授权资料直读/字面搜索；单独说明向量检索不可用，不全局阻断无关能力 |
| active/pending/protected lease | 等待或在原网站完成已有工作，刷新 context；不要取消网页 turn 或删除锁来通过安装验收 |

## 升级与恢复

从旧名称 DeepTutor-Codex 迁移时，先检查旧全局/项目 Skill 和私有目录
`$HOME/.config/deeptutor-codex/`。不自动覆盖已有新版配置，也不同时启用同名
功能的新旧 Skill。原服务停止后，用旧环境的 `deeptutor-codex server-uninstall`
或旧 `.deeptutor-codex-recover.py` 卸载旧补丁，再按本指南安装新版。
Docker 使用新版派生镜像与原挂载，无需在运行容器里卸包或改源码。

旧连接与凭据可在用户要求沿用时复制到新的私有目录，保留 `600` 权限；先验证
origin 和实际身份，再检查账号、工作区及主题。旧 `token_env` 的显式值仍有效，
全新连接默认用 `DT_LINK_TOKEN`。`persona_source=codex_explicit` 可改为
`assistant_explicit`；不复制教学协议缓存，首次用 `resume --new-chat` 刷新。
旧配置与 Skill 的备份留在 skills 和公共仓库之外。新版安装器不会自动卸载旧
补丁；遇到旧源码指纹就停止，不能改指纹强行通过。

升级前记录当前版本、解释器、宿主真实路径和 Skill 位置。连接/凭据备份
放在用户私有目录，保留权限 `600`；完整 Skill 备份放到 skills 目录外。
不要备份到公共仓库，不修改学习库或迁移资料。

停止原服务，在同一解释器更新扩展，再运行 `server-check`、
`server-install --dry-run`、`server-install`，按原方式启动并重复只读验证。
若不支持已有补丁或恢复脚本，停止并报告，不清理未知标记来绕过检查。
Skill 安装器拒绝覆盖内容不同的发行文件；先将旧目录完整移至私有备份
目录，再安装新 Skill。连接配置独立保留，无须重建索引或重新初始化。

卸载先停止原服务，卸补丁后才卸 Python 包：

```sh
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-uninstall --host "$DTC_HOST" --dry-run
"$DTC_HOST_PYTHON" -m deeptutor_link.cli server-uninstall --host "$DTC_HOST"
"$DTC_HOST_PYTHON" -m deeptutor_link.cli skill-uninstall --target "$DTC_SKILL_PARENT"
"$DTC_HOST_PYTHON" -m pip uninstall deeptutor-link
```

原环境由 uv 管理且没有 pip 时，最后一步改用
`uv pip uninstall --python "$DTC_HOST_PYTHON" deeptutor-link`。

服务器与客户端分开时，在各自机器处理；客户端只有复制的 Skill 时，
确认安装目录和本地修改后只移走该 Skill。卸载不会自动删除连接、Cookie
或学习状态。需要清除本地登录时，在脚本移走前运行
`CLIENT logout --url API_ORIGIN`；它只删除本地该 origin 的 Cookie，
不撤销环境变量里的 token，也不注销原网站会话。

扩展包缺失、无法启动时，保持原服务停止，使用安装时保存的独立恢复
脚本。以下 `DTC_HOST_PACKAGE` 是实际包含 `api/`、`learning/` 的
`deeptutor` 包目录，可从之前 `server-check` 的 `package` 字段取得：

```sh
"$DTC_HOST_PYTHON" -I -S "$DTC_HOST_PACKAGE/.deeptutor-link-recover.py" --host "$DTC_HOST_PACKAGE" --dry-run
"$DTC_HOST_PYTHON" -I -S "$DTC_HOST_PACKAGE/.deeptutor-link-recover.py" --host "$DTC_HOST_PACKAGE"
```

恢复拒绝未知源码改动、符号链接和不匹配收据；保留文件并报告冲突。
不要删除或替换固定 `.flock` / 学习库 `.lock` 文件，文件存在不代表有
进程占锁。安装中断可重跑已知安装/卸载流程；学习写入租约的诊断和
恢复另见 `SKILL.md`，不用于绕过网页正在进行的学习。
