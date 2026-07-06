# omac 端到端冒烟测试计划(从需求出发,全链路验证)

> 目的:用一个**最小、可预期**的真实需求,验证 omac **从「一句话需求」到「真实交付」的完整命脉**在 Multica + codex-ubuntu 上跑得通 —— 计划制定 → 人机确认 → 验收文档 → 人机确认 → DAG 拆解 → 派发执行 → 开 PR → 回收 → 总控验收 → 退出 0。
> 读者是**执行该测试的 agent / 操作员**——照本文从上到下执行即可,无需额外背景。

## 0. 这次验证什么

一条**完整命脉**,从需求端到端逐环打通:

```
控制机 omac plan create --goal "<需求>" --name smoke      # ★入口 = 需求
   → planner(codex)被派「制定计划」issue → 产出计划 → 【人机门:你确认】
   → planner 被派「制定验收文档」issue → 产出验收文档 → 【人机门:你确认】
   → orchestrator(codex)被派「拆解」issue → 产出 manifest(过 lint 机器门)
   → 产出 .omac/smoke.yaml(+ .acceptance.yaml,带 meta.source_issues 溯源)
控制机 omac dag run .omac/smoke.yaml                       # 确定性 loop 派发
   → develop issue([DAG:] 前缀)assign 给 codex → 改代码 + 开 PR + work submit
   → omac 回收(证据门:pr_url)→ 节点 done → DAG 收敛
   → 总控验收外层循环:acceptor(codex)端到端走查验收文档 → acceptance-results
   → 全部 pass → dag run 退出 0
```

**这次验证的完整面**(与旧「半链路」版最大区别):需求驱动的 planner/orchestrator 产出、**human-in-the-loop 人机确认门**、验收文档、provenance 源头引用、总控验收。

**这次仍不验证**(留作后续加深):plan 侧 agent reviewer 评审(用 `--no-review` 跳过,人机门替代把关)、CI 门、自动 merge、多节点并行、契约证据门(单节点无 contract)。

---

## 1. 角色与固定参数(已核实)

| 项 | 值 |
|---|---|
| 控制机(跑 `plan create` / `dag run`) | **本机** |
| 全部 omac 角色(planner/orchestrator/worker/acceptor) | **codex-ubuntu**(id `8dc91607-6825-41c6-b0de-45c001afc58d`)—— 角色可自由指定,一个 agent 演全部,最省配置 |
| plan 侧 reviewer | **无**(`--no-review`,人机门替代把关) |
| Multica workspace | `410ade5e-8ae0-4402-b975-813dea2ff3e1`(guantik-aiteam) |
| 测试 GitHub repo | `xiaohei-info/omac-smoke-test`(private,gh 已认证为 xiaohei-info) |
| Multica project | **走新建流程**(A4 产出 id,关联上面的 repo,自动注入 omac 编排横幅) |
| Multica daemon | 本机常驻(`multica daemon start --foreground`) |

> **为何全用 codex-ubuntu**:本次改动放开了「reviewer 必须 ≠ producer」的限制,角色可自由指定。用同一个已知可跑 omac 的 agent 演 planner/orchestrator/worker/acceptor,把「需要多个 agent 都能跑 omac」这个变量消掉,先把命脉焊死。想验证跨 agent 转派,见 §附。

**认证红线**:token 一律留在各自 CLI(`gh` / `multica` 自管认证),**绝不写进 `.omac/config.yaml` 或任何 `.env`**。config 里只有 engine/workspace/project/roles,无任何密钥。

---

## 2. 前提准备 · Part A —— 控制机一次性预置

> 需要 `gh` + `multica` owner 认证。若执行 agent 不具备这些认证,这一部分由控制机操作员预先跑完,执行 agent 直接从 Part B 开始。每步都给**命令 + 预期 + 校验**。

### A1. 安装 omac 到「daemon 子进程可见」的 PATH ★硬阻塞

daemon 唤醒 codex 后,codex 子进程要能直接调 `omac`(**每个阶段都要**:plan/decompose/develop/acceptance 都靠 codex 跑 omac)。

```bash
python3 -m pip install --user pipx 2>/dev/null; python3 -m pipx ensurepath
cd <oh-my-agent-cluster 仓库路径>
pipx install .        # 或 pipx reinstall omac 更新
```

- **预期**:`omac --version` 有输出。
- **校验(关键)**:omac 必须在 **daemon 环境**里也可见,不只当前 shell。
  ```bash
  which omac            # 期望在 PATH 上(pipx 默认 ~/.local/bin)
  ```
  若 daemon 的 PATH 不含 `~/.local/bin`:
  ```bash
  sudo ln -sf "$HOME/.local/bin/omac" /usr/local/bin/omac   # 软链到公共 PATH
  ```

### A2. 建测试 GitHub repo

```bash
gh repo create xiaohei-info/omac-smoke-test --private \
  --description "omac 端到端冒烟测试目标仓库" --add-readme
```
- **校验**:`gh repo view xiaohei-info/omac-smoke-test --json url -q .url`

### A3. 注册 repo 进 workspace(供 daemon 检出)

```bash
multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 \
  repo add https://github.com/xiaohei-info/omac-smoke-test
```
- **校验**:`multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 repo list --output json` 里出现该 URL。

### A4. 新建 Multica project(关联 repo + 注入 omac 横幅)

```bash
git clone https://github.com/xiaohei-info/omac-smoke-test /tmp/omac-smoke && cd /tmp/omac-smoke
omac init --engine multica --workspace 410ade5e-8ae0-4402-b975-813dea2ff3e1
# 交互:project 选择处输入 n 新建;repo URL 回车用当前 origin;
# 角色阶段全部填 codex-ubuntu(planner/orchestrator/workers/acceptor)——
# config 稍后由 A5 精简版覆盖,这里主要是为了建 project + 注入横幅
```
产出的 project id 记为 `<PROJECT_ID>`。

- **校验**:`multica --workspace-id <ws> project list --output json` 里能看到该 project 且 resources 含该 repo。

### A5. 在测试 repo 填入 omac 配置,push

在 `/tmp/omac-smoke` 里写 `.omac/config.yaml`(config 随 repo 检出到 codex 工作目录,`omac work show/submit` 才找得到):

```yaml
engine: multica
workspace: 410ade5e-8ae0-4402-b975-813dea2ff3e1
project: <PROJECT_ID>        # 填 A4 产出
roles:
  planner: codex-ubuntu
  orchestrator: codex-ubuntu
  workers: [codex-ubuntu]
  acceptor: codex-ubuntu
  # 无 reviewers —— plan 侧用 --no-review,dag 验收用 acceptor
```

> **注意**:本次全链路**不预置 manifest**——manifest 由 `plan create` 从需求现场产出。这正是端到端要验证的东西。

```bash
git add .omac/config.yaml && git commit -m "chore: omac 端到端冒烟配置" && git push
```

---

## 3. 执行 · Part B —— 执行 agent 主流程

### B1. 取测试 repo + 前置自检

```bash
git clone https://github.com/xiaohei-info/omac-smoke-test /tmp/omac-smoke && cd /tmp/omac-smoke  # 已有则 git pull
omac --version                                    # omac 已安装
multica whoami 2>/dev/null || multica auth status # multica 已登录
multica daemon status 2>/dev/null                 # daemon 在跑(不在则另开终端 multica daemon start --foreground)
omac init --check                                 # config 就绪:engine/workspace/project/roles 齐
```
- `omac init --check` **必须体检通过**。

### B2. ★从需求制定计划(前台阻塞 + 人机确认门)

在测试 repo 根,前台跑:

```bash
omac plan create --goal "在仓库根新增 hello_omac.txt(单行内容 omac smoke ok),并对 base=main 开 PR" \
  --name smoke --no-review
```

- **这是前台阻塞进程**,会在两个环节**停下来等你确认**(人机门默认开)。**不要放后台。**
- 它内部依次:派「计划」issue 给 codex → codex 产出计划 → **停在人机门** → 你确认 → 派「验收文档」issue → codex 产出 → **停在人机门** → 你确认 → 派「拆解」issue → codex 产出 manifest(过 lint)→ 落盘。

**人机确认门怎么放行**(每个环节一次,共两次):
另开一个终端观察 + 确认。当计划/验收 issue 流转到 **in_review**(说明 codex 已产出、等你确认)时:

```bash
# 看当前待确认的 issue(标题带「计划」/「验收文档」、状态 in_review)
multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 issue list --output json \
  | python3 -c "import sys,json;[print(i['status'],i['title']) for i in json.load(sys.stdin).get('issues',[])]"

# 确认(把该产出流转到 done 放行;omac 识别到后 --no-review 直接收口该环节)
omac plan confirm --name smoke        # 方案3 手动放行(推荐)
# 或:直接在 Multica 里把该 issue 状态改为 done —— 效果等价
```

> ⏱ **时序要点**:必须**等 issue 到 in_review 后**再 `plan confirm`(codex 已产出);太早跑会报「未找到待确认 issue」。两个环节各确认一次:先计划,后验收文档。
> 🤖 **无人值守替代**:若不想手工确认,`plan create ... --no-review --no-confirm` 跳过人机门全自动跑(但就没验证人机门这一环)。

- **B2 成功判据**:命令退出码 0;`.omac/smoke.yaml` 与 `.omac/smoke.acceptance.yaml` 生成。
  ```bash
  echo $?                              # 期望 0
  ls -1 .omac/smoke.yaml .omac/smoke.acceptance.yaml
  # provenance 抽查:manifest 记录了源头 issue
  python3 -c "import yaml;print(yaml.safe_load(open('.omac/smoke.yaml'))['meta'].get('source_issues'))"
  ```

### B3. ★跑 DAG(前台阻塞,派发执行 + 总控验收)

```bash
omac dag run .omac/smoke.yaml --output table
```
- **前台阻塞**到 DAG 终态。派 develop issue 给 codex → codex 改代码 + 开 PR + `omac work submit --pr-url ... --verification-file <{}最小文件>` → 回收 done → 收敛 → **总控验收**:派 final-acceptance issue 给 acceptor(codex)→ 走查验收文档回 acceptance-results → 全 pass → 退出 0。
- 若担心跑太久:`--max-minutes 15` 分段跑,状态不丢,重跑即续跑。
- **注意**:develop 节点**无人机门**(人机门只在 plan/acceptance),这一段全自动。

### B4. 旁路观察(另开终端,推荐全程开着)

```bash
watch -n 5 'multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 issue list --output json | python3 -c "import sys,json;[print(i[\"status\"],i[\"title\"]) for i in json.load(sys.stdin).get(\"issues\",[])]"'
multica daemon logs 2>/dev/null | tail -30    # 看 daemon 唤醒 codex、codex 在干什么
```

---

## 4. 验收判定(逐条勾选)

| # | 判据 | 怎么看 |
|---|---|---|
| 1 | plan create 派出「计划」issue,codex 产出后转 in_review | issue list(标题含「计划」)|
| 2 | 人机门:你 `plan confirm` 后该 issue 转 done,流程继续 | issue 状态 = done |
| 3 | 同样走完「验收文档」环节(产出 → 确认 → done) | issue list(标题含「验收文档」)|
| 4 | orchestrator 产出 manifest 过 lint,`.omac/smoke.yaml` 生成 | 文件存在 + `omac plan show .omac/smoke.yaml` |
| 5 | provenance:`meta.source_issues` 含计划/验收/拆解 issue id | B2 的 python 抽查 |
| 6 | dag run 派 develop issue(带 `[DAG:]` 前缀)给 codex | issue list |
| 7 | 测试 repo 出现一个 PR(含 hello_omac.txt) | `gh pr list -R xiaohei-info/omac-smoke-test` |
| 8 | develop issue 转 done(证据门:pr_url) | issue 状态 + metadata |
| 9 | 总控验收:final-acceptance issue 派给 acceptor 并 pass | issue list(标题含验收)|
| 10 | **`omac dag run` 退出码 0** | `echo $?`(命脉全通的最终判据) |

全绿 = 端到端命脉打通:从一句话需求,经人机确认,到真实 PR 交付并通过总控验收。

---

## 5. 失败排查(按环定位)

| 现象 | 最可能原因 | 处置 |
|---|---|---|
| plan create 报 project/workspace/roles 缺失 | config 没读到 / roles 没配全 | 在测试 repo 根跑;`omac init --check`;确认 config 有 planner/orchestrator/workers/acceptor |
| `plan confirm` 报「未找到待确认 issue」 | 确认太早(codex 还没产出,issue 未到 in_review) | 等 issue 到 in_review 再确认;`issue list` 看状态 |
| plan create 卡在人机门不动 | 你还没确认,或确认的是错的 issue | 按 B2 `plan confirm --name smoke`;或 Multica 里把该 in_review 产出改 done |
| 计划/验收 issue 一直 todo/无人接 | daemon 没跑 / codex 没配 trigger | `multica daemon status`;查 agent 配置 |
| codex 被唤醒但 `omac: command not found` | omac 不在 daemon 子进程 PATH | 回 A1 软链到 /usr/local/bin |
| 拆解 exit 20(manifest 反复过不了 lint) | codex 产出的 manifest 结构不达标(worker 不在池/依赖悬空/缺字段) | 看报告里的 lint 错误;简化 --goal;或**降级**:`--doc <手写计划>` 跳过 planner,甚至回退到手写 manifest + 直接 `dag run`(见 §附降级) |
| 验收文档 exit 20 或 schema 报错 | codex 产出的验收文档不符 schema(flow/actions 结构) | 看错误;必要时 `--no-acceptance` 跳过验收文档环节(但会少验一环)|
| dag run 迟迟不 done | develop issue 未到 done(codex 没 submit) | 看 codex logs 卡在哪 |
| 总控验收 exit 20 | acceptor 判 fail 或 acceptance-results 不达标 | 看 acceptance-results;或 `dag run --no-acceptance` 先跳过总控验收单验前半段 |

---

## 6. 清理(幂等扫尾)

```bash
# 作废本次冒烟 issue(仅标题含 smoke 的:计划/验收/拆解/develop/final-acceptance)
# —— 按 name 过滤,避免误伤 workspace 里其它无关 issue
multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 issue list --output json \
  | python3 -c "import sys,json;[print(i['id']) for i in json.load(sys.stdin).get('issues',[]) if 'smoke' in i.get('title','')]" \
  | xargs -I{} multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 issue status {} cancelled
# 彻底移除测试基建(可选):
# gh repo delete xiaohei-info/omac-smoke-test --yes
```

---

## 附:降级与加深

**降级(端到端太糙时,逐步退到可控)**:
- **验收文档不稳** → `omac plan create ... --no-acceptance`:跳过验收文档 + 总控验收,只验「需求→计划→拆解→执行→PR→回收」。
- **planner 产出不稳** → `--doc <手写计划.md>`:跳过 planner 制定环节(仍走验收/拆解/人机门)。
- **orchestrator 产出不稳** → 回退到「手写 `.omac/smoke.yaml` 单节点 + 直接 `omac dag run`」的半链路(只验派活→干活→PR→回收),先把下半条命脉焊死再回来叠上半条。

**加深(端到端绿了之后)**:
1. **带 plan 侧 reviewer**:去掉 `--no-review`,配 `roles.reviewers`(用**不同 agent** 如 claude-ubuntu),验证计划/验收/拆解三环节的同一 issue 转派 reviewer 交接。
2. **带证据门**:让 orchestrator 产出带宽松 contract 的节点(`coverage_gate: 0` + 一条 `verification_commands` + 一个 integration_gate),验 `omac work submit` 左移证据校验。
3. **多节点并行 + 依赖**:需求拆成两个 blocked_by 相连的节点,验就绪计算与失败隔离。
4. **跨 agent 角色**:planner/orchestrator/worker/acceptor 分派给不同 agent,验真实的多 agent 协作与转派。
