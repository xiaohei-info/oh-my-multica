# Tool Recipes Reference

This reference preserves the tool-adaptation guidance and the concrete execution workflow for rendering core functional flow diagrams.

## 工具适配与输出规范（Tool Agnostic Output）

本 Skill 为**工具无关的抽象规范**。可根据场景和交付物要求，自行选择以下工具之一实现：

- **Excalidraw**
  - 最佳场景：架构脑暴、白板评审、快速迭代
  - 关键适配技巧：利用 Library 保存元语模板；手绘风格降低“过早精细化”压力；布局可适度自由
- **Draw.io (diagrams.net)**
  - 最佳场景：正式文档、Wiki 嵌入、矢量导出、需要保留可编辑源文件
  - 关键适配技巧：使用 Container 实现嵌套边界；全局样式统一颜色；严格对齐网格；优先保存 `.drawio` 并导出 PNG/SVG/PDF
- **PlantUML**
  - 最佳场景：复杂逻辑、需要精确控制的场景
  - 关键适配技巧：利用 `rectangle`, `database`, `cloud` 等关键字；通过 `skinparam` 统一配色
- **SVG / HTML**
  - 最佳场景：官网、动态交互、程序化生成
  - 关键适配技巧：CSS 变量定义调色板；`<g>` 标签分组；响应式缩放
- **Structurizr / C4-PlantUML**
  - 最佳场景：与 C4 Model 结合的企业架构
  - 关键适配技巧：明确标注本图为 C4 中的动态图（Dynamic Diagram），而非静态容器图

> **建议**：先用 Excalidraw 快速对齐思路（10 分钟），确认后再用 Draw.io 固化（30 分钟）。

## Agent 执行工作流（Execution Workflow）

当接到“绘制某系统核心功能流程图”任务时，按以下步骤执行：

### Step 1: 需求解析（Clarify）
- 确认要回答的核心问题：是业务流程？服务交互？还是数据管道？
- 确认抽象层级：L1（业务）/ L2（服务）/ L3（组件）？
- 确认架构范式：线性流？事件驱动？分层？Saga？闭环控制？
- 确认 detailed-design 评审最关心的分支是什么

### Step 2: 布局选型（Select Pattern）
根据布局 cookbook 选择一种布局模式（A-F）。

### Step 3: 节点抽象（Abstract Nodes）
- 列出所有候选节点，用 diagram grammar 中的节点元语映射为抽象角色名
- 检查：是否去除了所有技术产品名？是否在统一抽象层级？
- 控制数量：单图核心节点 3-9 个，超过则拆分子域

### Step 4: 连接与标注（Connect & Label）
- 用 diagram grammar 中的线型表达交互模式（同步 / 异步 / 双向 / 补偿）
- 每条线必须标注：动词（同步）或事件名（异步）
- 标记主路径（Happy Path）和异常路径（若有）
- 为关键分支标出条件

### Step 5: 容器与边界（Contain）
- 用容器框（大矩形）对节点进行逻辑分组
- 嵌套不超过 2 层（Domain → Subdomain → Node）
- 容器标题置于顶部或左上角

### Step 6: 视觉优化（Visualize）
- 应用 color / typography 规则：推荐“仅连线着色，节点中性”
- 统一字体层级
- 添加阶段编号（若步骤 > 5）
- 如需强调 role/stage，优先用容器或泳道，不要乱加装饰

### Step 7: 自检与交付（Review）
逐条检查反模式，并确认：
- [ ] 无技术产品硬编码
- [ ] 无混用抽象层级
- [ ] 无裸线
- [ ] 有明确起点和终点
- [ ] 彩色不超过 3 种
- [ ] 能在更换技术栈后依然成立
- [ ] 关键分支不重不漏
- [ ] pending / rollback / compensation / cancel 在需要时已显式表达
- [ ] terminal ownership 清楚

### Step 8: 工具渲染（Render）
根据用户指定或场景默认，选择 Excalidraw / Draw.io / SVG 等工具输出最终图。

