---
name: thread-recap
description: Generate a structured checkpoint recap for an existing Codex task, including automatic ThreadRecap cooldown turns.
---

# ThreadRecap

Summarize the task state so someone returning later can continue without rereading
the entire history. Use the full available task on the first recap. On later
recaps, focus on ordinary turns after the previous ThreadRecap summary while
refreshing the overall current state.

如果上次摘要后没有新增普通用户内容，不生成新的摘要。

不要逐行复制工具日志。Do not copy tool logs line by line. Preserve only outcomes that matter for
continuing the work. Never invent completion; label uncertain information as
`不确定`. If there is no new ordinary user content after the previous recap,
do not produce another recap.

Use exactly these sections:

1. `阶段目标`
2. `已完成`
3. `关键决定`
4. `当前状态`
5. `待办与下一步`
6. `风险或阻塞`

End with the machine-readable marker supplied by the invoking prompt, in this
form:

`[thread-recap:summary:v1 covered-through=<turn-id>]`
