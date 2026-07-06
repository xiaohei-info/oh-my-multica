# omac 冒烟测试计划(端到端命脉验证)

> 目的:用一个**最小、可预期**的真实任务,验证 omac 全链路命脉在 Multica + codex-ubuntu 上跑得通。
> 读者是**执行该测试的 agent / 操作员**——照本文从上到下执行即可,无需额外背景。

## 0. 这次验证什么

一条命脉,逐环打通:

```
控制机 omac dag run          # 确定性 loop 派发
   → multica issue(带 [DAG:] 前缀)assign 给 codex-ubuntu
   → multica daemon 唤醒 codex-ubuntu,检出测试 repo 到工作目录,注入 issue + project 描述
   → codex 跑 omac work show <id>    # 取任务上下文与协议
   → codex 改代码 + 开 PR
   → codex 跑 omac work submit <id> --pr-url <url> --verification-file <min>
   → omac loop 回收(证据门:本次只校验 pr_url)→ 节点 done
   → DAG 收敛 → dag run 退出 0
```

**成功判据(§4 有可勾选清单)**:issue 被建且带 `[DAG:smoke-A]` 前缀 → 被 codex 认领 → 出现一个 PR → issue 状态到 done → `omac dag run` 退出码 0。

**这次不验证**(留作后续更深的冒烟):reviewer 评审、CI 门、自动 merge、总控验收、多节点并行、契约证据门。第一次只打通"派活→干活→开 PR→回收"这半条命脉。

---

## 1. 角色与固定参数(已核实)

| 项 | 值 |
|---|---|
| 控制机(跑 `omac dag run`) | **本机** |
| 执行 worker | **codex-ubuntu**(id `8dc91607-6825-41c6-b0de-45c001afc58d`,任务跑在本机) |
| reviewer | **无**(worker-only,review 关闭) |
| Multica workspace | `410ade5e-8ae0-4402-b975-813dea2ff3e1`(guantik-aiteam) |
| 测试 GitHub repo | `xiaohei-info/omac-smoke-test`(private,gh 已认证为 xiaohei-info) |
| Multica project | **走新建流程**(A4 产出 id,关联上面的 repo,自动注入 omac 编排横幅) |
| Multica daemon | 本机常驻(`multica daemon start --foreground`) |

**认证红线**:token 一律留在各自 CLI(`gh` / `multica` 自管认证),**绝不写进 `.omac/config.yaml` 或任何 `.env`**。config 里只有 engine/workspace/project,无任何密钥。

---

## 2. 前提准备 · Part A —— 控制机一次性预置

> 需要 `gh` + `multica` owner 认证。若执行 agent 不具备这些认证,这一部分由控制机操作员预先跑完,
> 执行 agent 直接从 Part B 开始。每步都给**命令 + 预期 + 校验**。

### A1. 安装 omac 到「daemon 子进程可见」的 PATH ★硬阻塞

daemon 唤醒 codex 后,codex 子进程要能直接调 `omac`。

```bash
# 从本仓库源码安装
python3 -m pip install --user pipx 2>/dev/null; python3 -m pipx ensurepath
cd <oh-my-agent-cluster 仓库路径>
pipx install .        # 或 pipx reinstall omac 更新
```

- **预期**:`omac --version` 有输出。
- **校验(关键)**:omac 必须在 **daemon 环境**里也可见,不只当前 shell。
  ```bash
  which omac            # 期望在 PATH 上(pipx 默认 ~/.local/bin)
  ```
  若 daemon 的 PATH 不含 `~/.local/bin`,做下面任一补救:
  ```bash
  sudo ln -sf "$HOME/.local/bin/omac" /usr/local/bin/omac   # 软链到公共 PATH
  # 或:重启 daemon 时确保其环境 export PATH 含 ~/.local/bin
  ```

### A2. 建测试 GitHub repo

```bash
gh repo create xiaohei-info/omac-smoke-test --private \
  --description "omac 冒烟测试目标仓库" --add-readme
```
- **预期**:返回 repo URL。
- **校验**:`gh repo view xiaohei-info/omac-smoke-test --json url -q .url`

### A3. 注册 repo 进 workspace(供 daemon 检出)

```bash
multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 \
  repo add https://github.com/xiaohei-info/omac-smoke-test
```
- **校验**:`multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 repo list --output json` 里出现该 URL。

### A4. 新建 Multica project(关联 repo + 注入 omac 横幅)

**推荐用 `omac init` 走新建流程**(会自动把 omac 编排横幅写进 project 描述):

```bash
cd <测试 repo 的本地 clone>          # 先 clone,见 A5
omac init --engine multica --workspace 410ade5e-8ae0-4402-b975-813dea2ff3e1
# 交互到 project 选择时输入 n 新建;标题默认取 repo 名;repo URL 回车用当前 origin;
# 角色随意填(worker 至少选 codex-ubuntu)——config 稍后会被 A5 的精简版覆盖
```
产出的 project id 记为 `<PROJECT_ID>`。

> 也可纯 CLI 建(但不会注入横幅,需手动补 `--description`):
> `multica --workspace-id <ws> project create --title omac-smoke-test --repo https://github.com/xiaohei-info/omac-smoke-test --description "<omac 横幅,见 dispatch.OMAC_PROJECT_DESCRIPTION>"`

- **校验**:`multica --workspace-id <ws> project list --output json` 里能看到该 project 且 resources 含该 repo。

### A5. 在测试 repo 填入 omac 配置 + 冒烟 manifest,push

在测试 repo 本地 clone 里创建两个文件(config 随 repo 检出到 codex 工作目录,`omac work show/submit` 才找得到):

`.omac/config.yaml`:
```yaml
engine: multica
workspace: 410ade5e-8ae0-4402-b975-813dea2ff3e1
project: <PROJECT_ID>        # 填 A4 产出
# 注意:无 reviewers / ci / merge —— worker-only,证据过门即 done
```

`.omac/smoke.yaml`(**已本地 lint 通过**:单节点、无 contract、worker=codex-ubuntu):
```yaml
meta:
  title: "omac 冒烟:单节点 worker-only"
nodes:
  - id: smoke-A
    title: "冒烟任务:在仓库根新增 hello_omac.txt(单行内容 omac smoke ok),提交并对 base=main 开 PR。本节点无 contract,证据门只校验 pr_url;omac work submit 的 --verification-file 传一个内容为 {} 的最小文件即可"
    worker: codex-ubuntu
    blocked_by: []
```
> 说明:manifest 节点的 `description` 会被派发模板覆盖,**任务只能写在 `title`**(worker 从 `omac work show` 的简报里读到它),所以此处 title 写得很详细——这是有意为之。

```bash
git add .omac/config.yaml .omac/smoke.yaml && git commit -m "chore: omac 冒烟配置与单节点 manifest" && git push
```

---

## 3. 执行 · Part B —— 执行 agent 主流程

### B1. 取测试 repo

```bash
git clone https://github.com/xiaohei-info/omac-smoke-test /tmp/omac-smoke && cd /tmp/omac-smoke
# 若已 clone:git pull
```

### B2. 前置自检(任一失败先修再往下)

```bash
omac --version                                   # omac 已安装
multica whoami 2>/dev/null || multica auth status # multica 已登录
multica daemon status 2>/dev/null                # daemon 在跑(不在则 multica daemon start --foreground 另开一终端)
omac init --check                                # config 就绪:engine/workspace/project 齐、project 在列表内
```
- `omac init --check` **必须体检通过**(它会校验 project 存在于 workspace)。

### B3. 前台跑 omac dag run(★这是控制机 loop,不能放后台)

```bash
omac dag run .omac/smoke.yaml --output table
```
- 这是**前台阻塞**进程,一直跑到 DAG 终态(节点 done 或收口)才返回。**不要加 `&`、不要放后台**。
- 若担心跑太久,用 `omac dag run .omac/smoke.yaml --max-minutes 15` 分段跑;分段之间状态不丢(manifest + 平台幂等),重跑即续跑。

### B4. 旁路观察(另开终端,可选但推荐)

```bash
# 看 omac 建出的 issue 及其状态流转
watch -n 5 'multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 issue list --output json | python3 -c "import sys,json;[print(i[\"status\"],i[\"title\"]) for i in json.load(sys.stdin).get(\"issues\",[]) if \"[DAG:smoke\" in i.get(\"title\",\"\")]"'
# 看 daemon 是否唤醒了 codex、codex 在干什么
multica daemon logs 2>/dev/null | tail -30    # 或 ~/multica_workspaces/ 下对应 task 的 logs/
```

---

## 4. 验收判定(逐条勾选)

| # | 判据 | 怎么看 |
|---|---|---|
| 1 | omac 建出 issue 且标题带 `[DAG:smoke-A]` 前缀 | `multica issue list` |
| 2 | issue 被 assign 给 codex-ubuntu,daemon 唤醒它 | daemon logs / issue assignee |
| 3 | codex 跑了 `omac work show`(读到任务) | codex task 的 logs |
| 4 | 测试 repo 出现一个 PR(含 hello_omac.txt) | `gh pr list -R xiaohei-info/omac-smoke-test` |
| 5 | codex 跑了 `omac work submit`,issue 转 done | issue 状态 = done + metadata 有 pr_url |
| 6 | **`omac dag run` 退出码 0** | `echo $?`(命脉全通的最终判据) |

全绿 = 冒烟通过,omac 全链路命脉在真实环境跑通。

---

## 5. 失败排查(按环定位)

| 现象 | 最可能原因 | 处置 |
|---|---|---|
| dag run 报 project/workspace 缺失 | config 没被读到(不在 repo 根跑) | 确认在测试 repo 根、`.omac/config.yaml` 存在;`omac init --check` |
| issue 建出但一直 todo/无人接 | daemon 没跑,或 codex-ubuntu 没配 trigger | `multica daemon status`;查 agent 配置 |
| codex 被唤醒但报 `omac: command not found` | omac 不在 daemon 子进程 PATH(A1 未做全) | 回 A1 软链到 /usr/local/bin |
| codex 不跑 omac、直接乱改 | 裸 codex 没遵从 issue body 的 omac bootstrap | 看 project 描述横幅是否注入;必要时强化 issue title / 给 agent instructions(需 owner 同意) |
| submit 报 exit 5 缺参数 | verification-file 没给 | 无 contract 时传 `{}` 的最小文件即可;`omac work show` 有精确模板 |
| dag run 迟迟不 done | issue 未到 DONE(codex 没 submit) | 看 codex logs 卡在哪;issue 评论里有无 codex 反馈 |

---

## 6. 清理(幂等扫尾)

```bash
# 作废本次冒烟 issue(无硬删,soft cancel)
multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 issue list --output json \
  | python3 -c "import sys,json;[print(i['id']) for i in json.load(sys.stdin).get('issues',[]) if '[DAG:smoke' in i.get('title','')]" \
  | xargs -I{} multica --workspace-id 410ade5e-8ae0-4402-b975-813dea2ff3e1 issue status {} cancelled
# 需要彻底移除测试基建时(可选):
# gh repo delete xiaohei-info/omac-smoke-test --yes
# multica ... project / repo 相应移除
```

---

## 附:后续加深(第二次冒烟,可选)

第一次绿了之后,按需逐步加验证面:
1. **带证据门**:给节点一个宽松 contract(`coverage_gate: 0` + 一条 `verification_commands` + 一个 integration_gate),验证 `omac work submit` 的左移证据校验闭环。
2. **带 review**:配 `roles.reviewers` + 给节点 `reviewer`(须 ≠ worker,如 claude-ubuntu),验证同一 issue 转派 reviewer 的阶段交接。
3. **多节点并行 + 依赖**:两个 blocked_by 相连的节点,验证就绪计算与失败隔离。
4. **agent 作为入口**:把"跑 `omac plan create && omac dag run`"作为一条 issue 派给入口 agent,验证 §1.4 第二形态(见 `omac guide workflow`「入口形态」)。
