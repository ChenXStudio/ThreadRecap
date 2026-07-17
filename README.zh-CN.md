# ThreadRecap

[English](README.md)

ThreadRecap 是一个本地 Codex 插件。当当前回合完成并连续空闲五分钟后，它会自动在原任务中生成一份结构化阶段总结。

只有 Codex 确认当前回合完成后才开始空闲计时。Codex 正在回复或调用工具的时间不计入空闲时间；新的用户消息会让旧计时失效。不同任务的状态彼此隔离，ThreadRecap 自己生成的总结回合也不会再次触发总结。

## 功能

- 当前 Codex 回合完成后开始五分钟计时
- 收到新的用户消息后重新计时
- 将总结写回原任务
- 不同任务独立保存计时与总结游标
- 防止总结递归触发
- 使用 SQLite 保存状态，可在重启后恢复
- 无需额外的 OpenAI API Key
- 支持 Windows、macOS 和 Linux

## 环境要求

- 支持插件 Hook 和 app-server 的 Codex
- Python 3.10 或更高版本，可通过 `py -3`、`python3` 或 `python` 调用

## 安装

先将 GitHub 仓库添加为 Codex 插件市场，再安装 ThreadRecap：

```console
codex plugin marketplace add ChenXStudio/ThreadRecap
codex plugin add thread-recap@thread-recap
```

安装后新建一个 Codex 任务。当 Codex 提示审核插件 Hook 时，选择信任并启用 ThreadRecap。也可以在 CLI 中打开 `/hooks` 完成审核。

从本地克隆安装时，仓库可以位于任意目录：

```console
git clone https://github.com/ChenXStudio/ThreadRecap.git
cd ThreadRecap
codex plugin marketplace add .
codex plugin add thread-recap@thread-recap
```

## 使用方法

日常使用不需要额外命令：

1. 正常使用 Codex 任务。
2. 等待当前回合完成。
3. 连续五分钟不发送新消息。
4. ThreadRecap 再次确认任务仍然空闲，然后生成总结。

五分钟是触发阈值，实际生成总结还可能需要额外时间。也可以使用 `$thread-recap` 手动请求总结。

## 伴生看板

仓库的 `apps/dashboard` 中包含一个可选的 Tauri 伴生应用。它不依赖 Codex 当前页面刷新，可以实时显示：

- 会话处于工作、冷却、生成总结或总结完成状态
- 五分钟冷却倒计时
- 格式化后的完整总结
- 新总结完成时的桌面通知
- 系统托盘入口
- 一键恢复对应 Codex 会话

看板会在运行时读取 `CODEX_HOME`，未设置时使用标准的 `~/.codex`，不会写死本机路径。

从源码运行：

```console
cd apps/dashboard
npm install
npm run tauri dev
```

构建原生安装包：

```console
npm run tauri build
```

开发看板需要 Node.js、Rust 以及 Tauri 对应平台的构建环境。仅安装 Codex 插件时仍只需要 Python 和 Codex。

## 总结结构

每次总结包含六个部分：

1. 阶段目标
2. 已完成
3. 关键决定
4. 当前状态
5. 待办与下一步
6. 风险或阻塞

第一次总结覆盖当前可用的任务历史；后续总结重点覆盖上次总结之后的普通回合，同时刷新整体状态。工具日志不会被逐行复制到总结中。

## 隐私与存储

ThreadRecap 使用现有的 Codex 登录和本地 Codex app-server，不需要额外 API Key，也不会将会话发送到第三方总结服务。

协调状态保存在 `PLUGIN_DATA/state.db`。worker 输出写入 `PLUGIN_DATA/worker.log`，Hook 诊断信息写入 `PLUGIN_DATA/hook.log`。SQLite 数据库不会复制 transcript 正文。插件通过 `PLUGIN_ROOT` 定位文件，因此不依赖固定安装路径。

## 常见问题

- **Hook 被跳过：** 打开 `/hooks`，审核并信任 ThreadRecap Hook。
- **插件仍然像旧版本：** 重新安装插件，然后新建任务测试。
- **五分钟后没有总结：** 确认 Codex 回合已经完成，等待至少五分钟，并检查 `worker.log` 中的脱敏错误码。
- **找不到 Python：** 安装 Python 3.10 或更高版本，并确保受支持的 Python 命令位于 `PATH`。
- **找不到 Codex：** 确保 `codex` 位于 `PATH`。高级环境可通过 `THREAD_RECAP_CODEX` 指定可执行文件路径。

## 卸载

```console
codex plugin remove thread-recap@thread-recap
codex plugin marketplace remove thread-recap
```

## 当前限制

- 空闲时间固定为 300 秒。
- 第一版看板尚未提供可配置设置。
- 写回同一任务时，会在总结前产生一条可见的内部用户消息。
- Codex 当前页面不会实时刷新由另一个 app-server 进程写入的回合；需要立即查看和接收通知时，请使用伴生看板。
