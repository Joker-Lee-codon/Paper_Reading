# Paper Reading

个人论文精读与组会准备资料库，整理中文阅读笔记、方法与实验分析，并通过统一总览连接各次阅读项目。采用 Markdown 优先的内容工作流，支持本机 Zotero 阅读语境、可编辑阅读页面和跨项目记录。

## 阅读入口

[打开公开阅读总览](public/dashboard.html)。GitHub 文件预览不执行 HTML；下载或 clone 仓库后，在浏览器打开该文件即可阅读。公开副本保留分析内容，公式以 LaTeX 源码展示。

完整本地文献库使用父目录的 `dashboard.html`。双击 `Start Reading.command` 启动本机服务并打开总览；从总览进入论文页，或从任意论文页返回全部论文。

## 更新与保存

使用 `paper-reading read` 完成阅读后，统一总览自动更新。也可点击总览的“更新”检查新论文及已登记页面的变化；没有变化时显示“暂无可更新内容”。

通过本机服务打开的新版阅读页自动把显式编辑保存到项目数据文件；多页面编辑冲突会提示导出后重载。直接打开文件时使用 Sync Folder 或 Export JSON 保存。内容更新时间与实际阅读完成状态分别记录。

## GitHub 备份

通过 `paper-reading notes backup` 或下方的 `backup` 命令生成公开阅读副本、提交并推送；需要本机 Git 已完成 GitHub 身份验证。本仓库备份公开分析、静态总览和维护工具；原始 Zotero 笔记、浏览器编辑缓存、本机路径、论文原 PDF、源码下载包及临时运行环境不在公开备份范围内。

`public/` 是可独立阅读的公开副本；各 `PaperReading*/` 项目和 `library/` 数据保存在完整本地文献库中，不上传到公开仓库。仅 clone 此仓库能够恢复公开阅读内容，无法恢复未上传的私人资料。维护工具也随仓库保存。

## 工具

```bash
python3 tools/library_manager.py --root . update
python3 tools/library_manager.py --root . serve --open
python3 tools/library_manager.py --root . backup
```

首次在另一台电脑建立完整文献库时安装配套的 `paper-reading` / `paper-notes` skill，配置 `library/config.json` 中的 `github_remote`，并将项目恢复到父目录下；公开副本可以直接打开，无需配置 Zotero 或运行服务。

本文献库用于学习与研究。论文及引用内容的权利归原作者；分析笔记中的结论应结合原文核对。
