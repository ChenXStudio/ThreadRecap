# ThreadRecap

[English](README.md)

ThreadRecap 是一个本地 Codex 插件。当任务在当前轮次完成后连续空闲五分钟，
它会自动在原任务底部生成一份结构化阶段总结。

只有 Codex 确认当前轮次已经完成后，空闲计时才会开始。Codex 正在回复或调用
工具的时间不算空闲；新的用户消息会使旧计时失效。不同任务之间的状态彼此
隔离，ThreadRecap 自己生成的总结回合也不会再次触发总结。

## 功能

- 当前 Codex 轮次完成后才开始五分钟计时
- 收到新的用户消息后重新计时
- 将总结写回原任务
- 不同任务独立保存计时与总结游标
- 防止总结递归触发
- 使用 SQLite 状态在重启后恢复
- 无需额外的 OpenAI API Key
- 支持 Windows、macOS 和 Linux

## 环境要求

- 支持插件 Hook 和 app-server 的 Codex
- Python 3.10 或更高版本，可通过 `py -3`、`python3` 或 `python` 调用

## 安装

先把 GitHub 仓库添加为 Codex 插件市场，再安装 ThreadRecap：

```console
codex plugin marketplace add ChenXStudio/ThreadRecap
codex plugin add thread-recap@thread-recap
```

安装后新建一个 Codex 任务。当 Codex 提示审核插件 Hook 时，信任并启用
ThreadRecap。也可以打开 `/hooks`，在 Hook 列表里完成审核。

如果从本地克隆安装，仓库可以放在任意目录：

```console
git clone https://github.com/ChenXStudio/ThreadRecap.git
cd ThreadRecap
codex plugin marketplace add .
codex plugin add thread-recap@thread-recap
```

## 使用方法

日常使用不需要执行任何命令：

1. 正常使用 Codex 任务。
2. 等待当前 Codex 轮次完成。
3. 连续五分钟不发送新消息。
4. ThreadRecap 再次确认任务仍然空闲，然后在原任务底部追加总结。

五分钟是触发门槛，实际生成总结还可能需要额外时间。

也可以使用 `$thread-recap` 手动请求总结。

## 总结结构

每次总结包含六个部分：

1. 阶段目标
2. 已完成
3. 关键决定
4. 当前状态
5. 待办与下一步
6. 风险或阻塞

第一次总结覆盖当前可用的任务历史。后续总结重点覆盖上次总结后的普通回合，
同时刷新整体状态。工具日志不会被逐行复制进总结。

## 隐私与存储

ThreadRecap 使用现有的 Codex 登录和本地 Codex app-server，不要求额外的 API Key，
也不会把会话发送给第三方总结服务。

协调状态保存在 `PLUGIN_DATA/state.db`，诊断信息写入
`PLUGIN_DATA/worker.log`。SQLite 数据库不会复制 transcript 正文。插件文件通过
`PLUGIN_ROOT` 定位，因此不依赖任何写死的本机安装路径。

## 常见问题

- **Hook 被跳过：** 打开 `/hooks`，审核并信任 ThreadRecap Hook。
- **插件仍然像旧版本：** 重新安装插件，然后新建任务测试。
- **五分钟后没有总结：** 确认 Codex 轮次已经完成，等待至少五分钟，并检查
  `worker.log` 中的脱敏错误码。
- **找不到 Python：** 安装 Python 3.10 或更高版本，并确保支持的 Python 命令
  位于 `PATH`。
- **找不到 Codex：** 确保 `codex` 可执行文件位于 `PATH`。高级运行环境可以设置
  `THREAD_RECAP_CODEX` 指向明确的可执行文件，无需修改仓库代码。

## 卸载

```console
codex plugin remove thread-recap@thread-recap
codex plugin marketplace remove thread-recap
```

## 当前限制

- 空闲时间固定为 300 秒。
- 暂无设置界面。
- 同任务写回会在生成总结前产生一条可见的内部用户消息。
