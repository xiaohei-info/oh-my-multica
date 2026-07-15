# 解除 Block 最低条件 — 开发前评审收口清单

当架构评审对详细设计文档集给出 `overall_verdict=block` 时，不要继续在单个模块文档上零散修订。先确认是否满足以下 5 条最低条件，满足后方可从 `block` 降级为 `revise` 或 `pass with comments`。

## 最低条件清单

### 1. 唯一 RunTimelineEvent 目录已冻结
- 每个 `event_type` 的来源、语义、必填字段、是否对前端暴露已明确
- 前端只认统一 timeline 协议，不直接依赖运行时原始事件名
- Gateway 内部原始事件与北向产品事件边界已划定

### 2. 唯一 cursor 契约已冻结
- 对外只一种格式（推荐：`cursor_no: bigint` 数字递增游标）
- Gateway 内部映射规则已说明（若需 `{timestamp}-{sequence}` 作为内部 source cursor）
- 前端/API/存储三端对 cursor 语义理解一致

### 3. 唯一状态机已冻结
- 明确哪些是持久化主状态（`conversation.status`, `team_run.status` 等）
- 明确哪些是 UI/投影状态（`waiting_reply`, `streaming`, `reconnecting` 等）
- 持久化状态与投影状态不混用同一 enum

### 4. 关键北向 API 已冻结
至少冻结以下接口的完整契约：
- `POST /api/team/runs`
- `GET /api/team/runs/{run_id}/stream`
- `GET /api/team/runs/{run_id}/events`
- `POST /api/team/group-conversations/{id}/messages`

每个接口必须明确：
- 请求体 schema
- 响应体 schema
- 幂等键位置（body vs header，V1 只选一个）
- 错误码与错误体
- 权限要求
- 字段可空性

### 5. 唯一角色模型已冻结
- 企业内角色 + 平台角色双层模型已定稿
- 页面、接口、数据 schema 使用同一套角色名词
- 不再有并行角色枚举（如 `admin/manager/viewer` vs `enterprise_admin/member`）

---

## 执行顺序

当评审 verdict 为 `block` 时：

1. **先定位 drift 根因**：哪几份文档对同一契约有不同表述
2. **创建/更新共享口径定稿文档**：作为唯一裁决层
3. **grep 级验证**：检查所有子文档是否还有旧术语残留
4. **系统性替换**：统一术语后再提交下一轮评审
5. **确认 5 条最低条件满足**：在评审回复中逐条确认

---

## 常见 Block 根因分类

| 根因类型 | 典型表现 | 解法 |
|---------|---------|------|
| 事件协议 drift | Gateway 文档用 `event: run_created`，前端文档用 `event: timeline` | 创建统一事件目录，Gateway 改为内部原始事件示例，北向统一 timeline |
| cursor drift | 三份文档分别用字符串游标、数字游标、`evt_` 前缀游标 | 选一种对外格式，内部映射规则单独说明 |
| 状态机 drift | 持久化 enum 与 UI 状态词混用，如 `conversation.status = streaming` | 拆分持久化状态 vs 投影状态，投影状态不入库 enum |
| API drift | 幂等键 body/header 双轨并存，字段名/可空性不一致 | 冻结单一 API schema，幂等键位置固定 |
| 角色 drift | 数据模型用 `admin/manager/viewer`，前端用 `enterprise_admin/finance_admin` | 合并为企业内角色 + 平台角色双层模型 |

---

## Block 不是否定，是工程治理拦截

`block` verdict 通常意味着：
- 分层方向大体正确
- 但跨模块共享契约未收敛成单一口径
- 若此时开工，前后端/数据/运维会各自补解释，产生 duplicate truth

解除 block 的关键是**收敛共享契约**，而不是重做架构。
