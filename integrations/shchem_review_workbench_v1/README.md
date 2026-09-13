# 主题大题复核不可变账本 v1/v2

这是供 Gateway 调用的独立 candidate-only 核心。它只读写 Gateway 配置的
`state_root`，不发现、不打开、也不修改中央题库。

## 五类不可变事件

- `task`：只允许服务器内部主体 `_system_catalog` 建立整主题任务；无浏览器创建接口。
- `claim`：一个任务同时只能有一个领取者，领取者由已认证服务器会话派生。
- `release`：只有当前领取者可以释放。
- `change_set`：v1 一次原子提交标签、父链、拆题边界、依赖边四类候选数组；
  v2 可同时提交第五类 `source_binding_candidates`。
- `decision`：决定只允许 `accept_candidate_overlay`、`reject`、
  `request_changes`、`blocked`。

每个事件目录先以 `O_EXCL` 写入规范请求和自哈希事件，最后写自哈希
`commit.json`。读取会重算文件清单、内容哈希、自哈希、事件链、任务级
sequence 与 `TRREV-<sequence>-<event-head-sha256>`。所有操作在跨进程文件锁内
执行；Windows 使用 `msvcrt.locking`，其他系统使用 `flock`。

## 安全边界

- body 使用 exact-key 合同；actor、assignee、reviewer、authority、路径、URL、
  时间戳和任何权限字段均不可由请求夹带。
- actor 与 UTC 时间由方法的可信服务器参数/时钟派生。
- 幂等身份是 `principal + task + operation + idempotency_key`；相同正文重放原
  结果，即使其 CAS revision 已旧；同 key 不同正文返回稳定 409。
- 每次新事件须匹配当前任务 revision。revision 同时含单调 sequence 和不可变
  事件头 SHA，claim→release→claim 不会回绕为旧 revision。
- `accept_candidate_overlay` 也始终固定
  `central_master_mutated=false`、`human_reviewed=false`；没有 apply/promote/
  teaching/generation/publication 方法。
- `dependency_replacements` 使用 `before_dependencies` / `after_dependencies`，
  每条边固定为 `{atomic_part_id, relationship_kind}`；关系词表只有
  `uses_prior_answer`、`uses_prior_calculated_value`、
  `uses_prior_identified_substance`、`uses_prior_structure`、
  `uses_prior_experimental_conclusion`。
- 来源绑定候选只能由 `_system_catalog` 随任务冻结，字段固定为
  `{candidate_id,candidate_sha256,source_id,source_version_id,binding_state,accept_allowed,evidence_binding_ids}`；
  `candidate_sha256` 必须覆盖其余全部冻结字段。复核者只能
  原样引用这三个身份字段，并从 `accept_binding_candidate`、
  `reject_binding_candidate`、`request_source_evidence`、
  `block_identity_binding` 中选择动作，填写中文理由和冻结证据 ID；未知候选、陈旧
  哈希或版本、重复候选和超出候选证据的引用整批失败。
- 只有 `binding_state=exact_content_set_candidate` 且 `accept_allowed=true` 的候选
  可以执行 `accept_binding_candidate`；`blocked_missing_source`、
  `blocked_ambiguous`、`blocked_hash_mismatch` 只能拒绝、请求补证或记录身份阻断。
- v1 事件字节、事件 ID、change-set ID 和 decision ID 按 v1 schema 原样重放；
  含来源候选的任务及后续事件写入明确的
  `shchem_theme_review_ledger_v2`。旧事件不迁移、不补字段、不改写。
- 状态根拒绝中央库、live/private、符号链接和 Windows reparse point。

主要 API：

```python
store = AppendOnlyThemeReviewStore(server_configured_state_root)
store.create_task(body, principal_id="_system_catalog")
store.claim_task(task_id, body, principal_id=authenticated_principal)
store.release_task(task_id, body, principal_id=authenticated_principal)
store.submit_change_set(task_id, body, principal_id=authenticated_principal)
store.create_decision(task_id, body, principal_id=authenticated_principal)
store.get_task(task_id)
store.list_tasks()
store.list_events(task_id)
store.get_event(task_id, event_id)
store.list_change_sets(task_id)
store.get_change_set(task_id, change_set_id)
store.list_decisions(task_id)
store.get_decision(task_id, decision_id)
store.preview_candidate_overlay(task_id, change_set_id)
```

异常均有稳定的 `code` 和 `status_code`，由 Gateway 翻译为中文 HTTP 错误。
