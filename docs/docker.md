# Docker：一个服务，一个阅读空间

[English](docker.en.md)

默认镜像包含 **Claude Code、Codex CLI、Pi Agent、Chromium、BabelDOC、Git、Beads/Dolt、三种 TeX 引擎与 MCP**。现在只需要一个容器、一个端口：阅读、授权码设置、CLI 登录与更新、Web Terminal 都在 EasyRead 内完成，不再有网关/终端 sidecar、第二个端口或浏览器账号密码弹窗。

## 启动

需要 Docker Engine 24+、Compose v2，建议至少 4 GB 内存；完整研究镜像含 TeX，构建请预留至少 20 GB 磁盘（镜像层缓存会额外占用）。[研究空间指南](research-workspaces.md)说明可信私有部署边界、原生依赖、版本开关与真实编译验证。

```bash
git clone https://github.com/Jasaxion/easyread.git
cd easyread
cp .env.example .env
docker compose up -d --build
```

打开 **http://localhost:8765**。默认只发布本机端口，不要求用户名。第一次构建会下载 CLI、浏览器、PDF 依赖、布局模型、Beads/Dolt 和 TeX，并执行真实依赖/编译烟测；以后直接 `docker compose up -d`。

## 访问授权码

在 **设置 → 访问保护** 中开启、关闭或修改访问授权码：

- 开启后，访问者先看到 EasyRead 的独立授权页面，输入授权码即可进入；没有用户名或 HTTP Basic 弹窗。
- 授权码至少 6 字符，建议使用较长的随机字符串。服务端只存带盐哈希，不保存可回显的明文。
- 修改或关闭保护需要当前授权码。修改后其他浏览器会退出授权、所有终端会关闭；当前浏览器保持授权。
- 首次启动可用 `.env` 的 `EASYREAD_ACCESS_CODE` 预设授权码。之后以设置中保存的值为准，修改环境变量不会覆盖它。
- 授权会话保存在 HttpOnly/SameSite Cookie 中；服务重启后需重新授权。

默认授权码关闭，适合仅本机使用。**如果要开放到网络，建议在首次启动前配置授权码；关闭保护意味着所有能访问该地址的人都能读写文献、调用模型及使用容器终端。**

```dotenv
EASYREAD_BIND_IP=0.0.0.0
EASYREAD_PORT=8765
EASYREAD_ALLOWED_HOSTS=192.168.1.20,easyread.lan
EASYREAD_ACCESS_CODE=请换成自己的长随机授权码
```

填写服务器 IP/域名，不是客户端 IP。Host 白名单不能替代授权码。忘记授权码时，拥有服务器管理权限的人可以先停止容器、删除数据卷内 `/data/EasyRead/.access.json`，再设置新的 `EASYREAD_ACCESS_CODE` 并启动；不要删除整个文献库。

### 远程访问

不想开放网络时，在自己的电脑运行：

```bash
ssh -N -L 8765:127.0.0.1:8765 user@your-server
```

然后访问 localhost。公网或不可信网络请在外部使用 HTTPS 反向代理，保留 Host、Origin、Cookie、WebSocket Upgrade/Connection，关闭流式回答缓冲，并允许所需 PDF 上传大小（应用上限 200 MiB）。必须部署在域名根路径，`/terminal/` 也转发到同一个端口。此项目不再自动生成自签名证书；本机 HTTP 没有证书警告，但不提供传输加密。

## 设置内的 CLI 与终端

打开 **设置 → CLI 与终端**：

- Claude Code / Codex / Pi Agent 卡片显示安装版本。Pi 缺失时可直接通过 npm 安装。
- 点击“登录”“登录状态”或“更新”，结果直接显示在下方内嵌终端。
- 点击“打开终端”进入自由交互 shell；不需要打开其他网站或输入第二次密码。
- 登录链接在自己的浏览器打开，按 CLI 提示完成授权和验证码粘贴。Codex 使用设备码登录，不需要公开 OAuth 回调端口。
- “断开”、切换设置页或关闭设置都会停止当前终端及未完成命令。更新/登录完成后再关闭。

也可以在内嵌终端输入：

```bash
easyread-cli status
easyread-cli login claude
easyread-cli login codex
easyread-cli auth-status claude
easyread-cli auth-status codex
easyread-cli install pi
easyread-cli login pi             # Pi 内使用 /login、/model
easyread-cli auth-status pi       # 检查原生默认服务商；也可加 --provider anthropic
easyread-cli update all
```

Pi 安装/更新使用 `npm install --global --ignore-scripts @earendil-works/pi-coding-agent@latest`（Node.js 22.19+）。首次构建默认 `PI_AGENT_VERSION=latest`，可在 `.env` 固定版本；Docker 缓存不会自动追踪 npm latest 变化，需要新版本时在终端更新。旧 CLI HOME 不会被重建镜像覆盖，升级后没有 Pi 时点击“安装”。Pi 登录状态只检查选定服务商且不刷新凭据、不输出密钥；没有默认服务商时给出配置说明。Pi 完整模式没有内置沙箱或逐工具审批，请只在可信私有环境使用。

安装成功不代表已经登录；账号、地区、额度和模型能力仍由服务商决定。不会复制宿主机登录，也不会自动使用付费模型。CLI 更新请在模型/PDF 任务空闲时进行。

终端底层仍复用 ttyd，但它由 EasyRead 按需启动，仅监听容器的 `127.0.0.1`，通过主站的授权和同源检查访问。它是个人管理员 shell，不是多用户隔离沙箱；不要共享文献库写权限给不可信的人。

## 可选全局代理

**设置 → 网络与代理** 可开启/关闭并分别配置 `http_proxy`、`https_proxy` 和 `no_proxy`。例如：

```text
http_proxy  = http://172.16.1.116:7890
https_proxy = http://172.16.1.116:7890
```

上面的 IP 仅为当前测试环境示例，请换成你自己的代理。HTTPS 目标通过 HTTP CONNECT 代理访问，因此两个字段都可以填 `http://`。当前不支持 TLS 到代理本身的 `https://` URL 或 SOCKS。

设置持久化到数据卷，新 API/CLI/网页导入请求生效，已有终端需重新打开。关闭代理会清除应用新请求使用的代理变量；未设置时继承运行环境。内部连接始终绕过代理，不修改宿主系统或其他容器。

“测试代理连接”只验证到 Claude 下载服务和 OpenAI 的网络可达性，不代表模型账户可用。代理凭证在接口中隐藏；动态网页 Chromium 暂不支持带账号密码的代理 URL。

构建和首次启动前，也可在 `.env` 配置：

```dotenv
EASYREAD_BUILD_PROXY=http://172.16.1.116:7890
EASYREAD_HTTP_PROXY=http://172.16.1.116:7890
EASYREAD_HTTPS_PROXY=http://172.16.1.116:7890
```

Python 依赖下载慢时，可使用国内镜像（完整版和轻量版均支持）：

```dotenv
EASYREAD_PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
EASYREAD_BUILD_NO_PROXY=localhost,127.0.0.1,.aliyun.com,.tsinghua.edu.cn
```

也可将索引换成 `https://pypi.tuna.tsinghua.edu.cn/simple`。国内源直连，海外 CLI 下载仍走构建代理；这些配置只影响镜像构建，不更改模型 API 地址，也不关闭 TLS 校验。

构建代理不替代 Docker daemon 拉基础镜像时的代理配置。宿主机代理/Ollama 可使用 `host.docker.internal`，容器的 `127.0.0.1` 不是宿主机。

## 研究工作区与 Obsidian

完整版和轻量版都包含 PaperRead 迁入的研究工作区、阅读路径、可编辑导图、批注、跨论文分析及 Markdown 导出。访问 `/research`，或从文献库点击「研究探索」；它们沿用同一访问授权码。AI 功能仍需在设置里选择已登录的 CLI 或可用 API。

语义检索首次使用会下载固定版本的多语向量模型（约 250 MB），缓存在 `/data/EasyRead/.research-os/`，随应用数据卷保留。下载失败可配置代理后重试；模型准备过程中先提供关键词检索。

Obsidian Vault 必须是容器内的**可写**目录。不需要与宿主机直接共享时，可以填写 `/data/EasyRead/vault`，导出会保存在现有数据卷中。要连接宿主机的 Vault，可创建 `docker-compose.override.yml`，挂入同一个服务：

```yaml
services:
  easyread:
    volumes:
      - ./vault:/vault
```

确保宿主机 `./vault` 存在且容器 UID/GID 1000 有读写权限，再运行 `docker compose up -d`。在研究设置或首次导出时填写 `/vault`，不是客户端电脑上的路径；没有系统文件夹窗口时，页面会让你手动输入。不要使用论文导入示例中的 `:ro` 只读挂载作为 Vault。

导出会保留用户在托管区块之外的内容；思维导图同时保留 PNG 预览和可编辑 Mermaid 源码。完整说明见[研究功能指南](research.md)。

## 仅更新应用代码（复用已有完整运行环境）

本机已经有可用的 `easyread:local` 完整镜像，且 Python 依赖未改变时，可避免重复下载 CLI、浏览器及 PDF 组件：

```bash
docker build -f docker/Dockerfile.app --build-arg BASE_IMAGE=easyread:local \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple -t easyread:next .
docker tag easyread:next easyread:local
docker compose up -d --no-build
```

此路径会重新安装应用并运行 `pip check`。首次安装、依赖或 CLI 运行环境变更时，仍使用根目录 Dockerfile 的完整构建。

## 数据和更新

| 命名卷（带 Compose 项目前缀） | 内容 |
| --- | --- |
| `easyread-data` | 论文、笔记、阅读进度、API/代理设置、授权码哈希 |
| `easyread-cli-home` | CLI 安装和更新、登录凭证、配置、PDF 模型缓存 |

容器内分别是 `/data/EasyRead` 和 `/home/easyread`，统一非 root 用户 UID/GID 1000。重建容器不会覆盖已有 CLI HOME；在终端升级过的 CLI 优先保留，更新应用镜像不自动覆盖它们。

```bash
git pull
docker compose up -d --build --remove-orphans
docker compose ps
docker compose logs -f --tail=100
```

从旧三服务版本升级，`--remove-orphans` 会移除旧 gateway/terminal 容器。旧 `easyread-auth` 卷不再使用，旧账号密码也不再生效；文献和 CLI HOME 继续复用。

普通停止用 `docker compose down`；**不要用 `down -v` 做普通更新**，否则删除数据。不要改变项目名或同时让多个 EasyRead 实例写同一个库。备份两份数据时停止服务，并把凭证当作敏感数据保管。

## 功能边界

- Chromium 保留沙箱，Compose 使用 Moby 默认策略加用户 namespace 的 clone/setns/unshare；没有 privileged、SYS_ADMIN、Docker socket 或宿主 HOME。宿主机需允许用户 namespace。
- BabelDOC 独立环境已包含布局模型，首次生成 PDF 时可能继续下载公共字体。仍需配置可用翻译模型。
- 文件夹导入/Zotero/链接 PDF 使用容器内路径，例如挂载 `./papers:/papers:ro` 后填 `/papers`。
- 生图权限取决于 Codex/图片 API；不会自动调用单独计费的图片 API。
- 不包含 Electron 系统菜单或客户端文件夹对话框。浏览器支持 [PWA 与离线正文/笔记](guide.md#安装为-pwa-与离线阅读)，手机访问需在根路径部署可信 HTTPS；代理必须保留 `/sw.js`、`/manifest.webmanifest`、`/pwa/` 和 `/web/` 路径，不要长期缓存 `sw.js`。
- 可选轻量 API 版：`docker compose -f docker-compose.lite.yml up -d --build`，没有内嵌终端。不要和完整版同时占用相同端口/数据卷。
