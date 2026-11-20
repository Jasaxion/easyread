# EasyRead

让你更舒服地读论文。导入 PDF，第一段译好就能开始读。遇到难句，展开原文或对照 PDF，在段落旁划线、记笔记、问 AI。也可以直接读英文原文。

[下载桌面版](https://github.com/Edwardxlai/easyread/releases/latest) · [在线试读](https://edwardxlai.github.io/easyread/demo/) · [项目主页](https://edwardxlai.github.io/easyread/) · [English](README.en.md)

![译文与 PDF 原页对照](docs/images/pages.jpg)

## 安装

从上方下载 Windows 安装程序（`.exe`）、Apple 芯片 Mac 安装包（`.dmg`）或 Linux 应用（`.AppImage`）。首次打开遇到系统提示时，见[分平台安装说明](docs/guide.md#安装)。

<a id="run-from-source"></a>

也可以用 Python 3.10+ 从源码启动。Windows 双击 `start.cmd`，macOS 或 Linux 运行 `./start.sh`。

## Docker 浏览器版

```bash
git clone https://github.com/Jasaxion/easyread.git
cd easyread
docker compose up -d --build
```

一个容器、一个端口：访问 <http://localhost:8765>。包含 Claude Code、Codex CLI、Chromium、BabelDOC，以及研究空间所需的 Git、Beads/Dolt、三种 TeX 引擎与 MCP；在“设置 → CLI 与终端”直接登录、更新 CLI 或打开内嵌终端。

独立[研究空间与 LaTeX 工作台](docs/research-workspaces.md)支持项目文件、文献引用和原生 Agent 多会话，仅供可信私有部署。Lite/桌面/源码的原生依赖安装与检测、第三方版本开关见该指南；桌面安装包不内置 TeX 发行版。

不再使用账号密码弹窗；可在“设置 → 访问保护”开启、关闭或修改访问授权码，开启后显示独立授权页面。默认关闭且仅发布本机端口，网络部署请先设置授权码并按需配置 HTTPS。

数据和 CLI 登录持久化；“设置 → 网络与代理”支持全局 `http_proxy` / `https_proxy`。详见 [完整 Docker 部署说明](docs/docker.md)。

## 读第一篇论文

1. 把 PDF 拖进文献库，输入 arXiv 编号，或粘贴博客链接。选择目标语言，也可以直接读原文。
2. 要翻译或提问时，在设置中选择已登录的受支持 CLI，或配置模型 API。文献库存放在本机，翻译和提问会把相关内容交给所选模型处理。
3. 在侧栏并排看 PDF、写笔记。想找以前读过的内容，在文献库点「全库问 AI」，从回答中的引文回到原段落。

研究工作区、阅读路径、可编辑 AI 思维导图、手写与贴纸批注、深度/跨论文分析、多标签分屏和 Obsidian 导出已整合到现有阅读流程。入口与模型/数据说明见[研究功能指南](docs/research.md)。

[完整使用指南](docs/guide.md) · [研究功能](docs/research.md) · [贡献指南](CONTRIBUTING.md) · [版本记录](CHANGELOG.md) · [贡献者](docs/guide.md#贡献者) · [MIT 许可及演示论文署名](docs/guide.md#许可)
