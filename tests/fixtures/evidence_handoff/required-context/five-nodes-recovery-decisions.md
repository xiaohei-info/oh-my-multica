# 五节点收敛恢复：范围与合同边界决策（权威）

日期：2026-08-16
状态：用户授权的恢复推进中，由协调方依据各节点最新 review report 作出的合同边界决策。
适用节点：workspace-sdk、agent-run-job、contracts-credential、release-preview、contracts-audit-usage。

本文件是这五个节点 amendment 提案的权威输入。凡与本决策冲突的 reviewer 扩展要求，
在提案中应以本决策为准收敛范围，而不是继续扩大实现义务。

## 1. workspace-sdk

**问题**：生产 Kubernetes provider 声明了它无法用标准 Kubernetes API 证明的能力
（NetworkPolicy/隔离"有效性"依赖非标准 status 字段），源物化证明是回调/标记式，
Missing-PVC 恢复信任未认证的调用方句柄，描述符相对清理在 Linux 上返回 EINVAL。

**决策**：
1. **收缩生产合同至可证明语义**：v1 生产 provider 只承诺标准 Kubernetes API 可证明的
   事实——资源分配（Pod/PVC 规格与容量）、生命周期状态机（含配对资源 CAS 与崩溃恢复）、
   描述符相对文件系统包含。
2. **隔离/网络有效性声明降级**：不在生产合同中声明"有效强制"。隔离与 NetworkPolicy 的
   生效观察降级为 best-effort observational 状态字段，明确标注未经生产路径证明；
   能力广告（capabilities）不得包含无法证明的强制语义。
3. **源物化**：保留 exact-source 要求但允许以"物化后从实际工作区计算身份并与请求的
   digest/commit 绑定"的方式实现；in-cluster 构造器必须装配真实物化器（不得留空）。
4. **恢复路径**：Missing-PVC 恢复必须基于 provider 自有持久化记录或认证描述符，
   不得信任未签名调用方句柄。
5. 若上述收缩后单节点仍过大，提案可将"隔离/网络观察"拆为后续补偿节点。

## 2. agent-run-job

**问题**：交付的"生产 vendor"是 fixture（AGENTRUN_FIXTURE_MODE 等环境变量可选行为、
伪造全部原生命令/文件/MCP/usage 事件）；机械 gate 无法检测。

**决策**：
1. **真实 vendor 定义**：生产 vendor 是 stream-json 协议适配器——通过部署期配置的外部
   运行时命令（可执行路径+参数）启动真实子进程，逐行映射其 stdout stream-json 事件流。
   不要求捆绑特定第三方运行时二进制。
2. **release 源禁止 fixture**：fixture 模式、marker/PID 钩子、blockForever、合成事件生成
   必须移出 cmd/agentrun-job/vendor 发布源；测试 fixture 只允许存在于 testdata 或
   非发布包。
3. **gate 机械检测**：release-boundary 检查增加对发布源中 fixture 钩子标识的扫描
   （环境变量名/标记常量），命中即 gate 失败。
4. 协议 fail-closed：terminal 后拒绝任何后续行；acceptance kind 与请求模式不匹配时
   必须清理子进程；普通事件携带外来 session id 必须拒绝。

## 3. contracts-credential

**问题**：rotation/finalization 的 CAS-审计-事务边界语义长期不收敛（9 个 blocker 全 deeper，
历史从未收敛）。

**决策**（收缩完成语言到可实现边界）：
1. **committed 结果必须绑定记录证明**：CredentialRotationOutcome 的 committed/replayed
   必须携带并验证 finalization 记录身份与 Binding CAS revision；无记录证明的结果拒绝。
2. **回执防重用**：重激活回执必须绑定当前 Binding revision 摘要，过期/跨 revision 回执拒绝。
3. **状态再水化不变量**：rotation 状态 API 不得在 finalization 证据存在前返回 Committed；
   再水化路径与转换不变量共用同一校验。
4. **审计证据边界收缩**：broker 审计证据要求限定为"规范化审计尝试+结果记录的存在性与
   摘要绑定"，不要求跨系统事务原子性证明（该语义超出当前平台边界）。
5. **lease panic 语义**：物化消费 panic 必须回滚或标记 lease 未完成，不得留下
   pending+已消费状态。

## 4. release-preview

**问题**：preview decision 幂等时间戳漂移、证据主体绑定缺口、回归证据缺口。

**决策**：
1. **幂等身份去时钟化**：preview decision 的幂等身份由输入内容 digest 派生，
   不依赖自由时钟时间戳；时间戳仅作记录字段不参与身份/幂等判定。
2. **证据主体绑定**：preview 证据必须与 preview 输入 digest 精确相等（不是包含关系）。
3. **回归证据**：定义为"同一 preview 输入集合的期望/实际输出差异记录"，
   由 preview 生成器在产出时自动记录，不需外部回归系统。

## 5. contracts-audit-usage

**问题**：审计 detail 锚点自签名、wire 边界未脱敏、CAS 存在性验证、analytics 零值语义。

**决策**：
1. **锚点权威化**：audit detail 锚点必须由权威已验证 AuditEvent 派生（store 提供的事件
   绑定控制 digest），构造函数内部派生/比对，禁止调用方自签名锚点。
2. **wire 脱敏矩阵**：export wire 层按字段级脱敏矩阵输出；未脱敏字段不得出现在 wire DTO。
3. **CAS 完整性**：target CAS 验证必须包含已提交 revision，不得仅验证存在性。
4. **零值语义 fail-closed**：analytics 零观察必须显式表达（省略指标或显式 unknown+原因），
   0 不得作为已知零接受。
5. **omission 绑定**：嵌套 omission 的 Field 必须等于外层字段 Name，拒绝重复/错配条目。

## 执行要求

- 五个节点均以 minimal_rerun 恢复 authoring，Worker 以上述决策 + 各自最新 review report
  的 blocker 清单为返工依据。
- 不得引入本决策之外的新合同义务；reviewer 后续审查以本决策文件为范围权威。
- workspace-sdk 若按第 1.5 条拆分，补偿节点必须在本提案中显式定义。
