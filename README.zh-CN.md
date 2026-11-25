# SoloForge

[English](README.md) | **简体中文**

给「一个人带一支 AI 团队、自己不写代码」的负责人用的 GitHub 工作流。

一家的模型写代码，另一家的模型审。优先级标签决定哪些事要找你。合并机器人只合「检查通过、审过」的改动。每个任务都留交接记录，所以晨报和收工单读的是任务黑板，不是聊天记录。

SoloForge 是从一套私有流程里抽出来的通用部分，那套流程一直在带几个小游戏项目。它只用到 GitHub Actions、Bash、Python 和 Node.js。AI 的登录或 API key 用你自己的。

## 怎么运转

1. **你派任务。** 把任务写在 issue 里，然后评论 `/codex <指令>` 或 `/claude <指令>`。默认只有仓库主人的评论能开工。
2. **派工器让「写」的那家开工。** 一个 GitHub Action 用你的 key 跑那家的命令行工具，把结果提交到任务分支，开一个 PR。它从不合并。
3. **门禁检查每个 PR。** 跑你项目自己的检查命令，再跑你写在 `docs/arch-rules.json` 里的架构规则。
4. **另一家来审。** 审查结果由仓库主人的一条评论记录，评论里写明审的是哪个提交。有新提交就要重新记录。
5. **合并机器人要么合，要么等。** 这个提交的门禁通过、PR 带 `P2` 或 `P3`、没有拦截标签、审查记录对得上最新提交，四条都满足才合。`P0` 和 `P1` 会停下来找你。fork 来的 PR 一律留给你自己处理。
6. **黑板保存交接。** 每个任务一张卡，记着现状、下一步，以及卡在哪、等你定什么。两份提示词把黑板变成晨报和收工单，收工单最多列 5 件要你拍板的事。

## 里面有什么

| 部件 | 做什么 | 从这里看起 |
| --- | --- | --- |
| 门禁 | 可复用的 workflow，在每个 PR 上跑你的检查和架构规则。支持 Node.js、Python、Godot。 | [门禁 workflow](.github/workflows/gate.yml)、[CI 模板](templates/ci.yml) |
| 合并机器人 | 合并符合条件的 PR；需要你处理、需要 rebase、需要架构审查的，贴上对应标签。 | [合并 workflow](.github/workflows/merge-bot.yml)、[审查记录格式](rules/REFERENCE.md#review-record-and-architecture-review) |
| 派工器 | 把一条 `/codex` 或 `/claude` 评论变成任务分支和 PR，带时长、轮数、token 和每小时开工次数的上限。 | [派工器说明](docs/dispatcher.md)、[派工模板](templates/dispatch.yml) |
| 适配器 | 每家 AI 一个可执行文件，按一份很小的约定接入。自带 Codex、Claude 和一个测试用适配器。 | [适配器约定](adapters/README.md) |
| 黑板 | 每个任务一张 JSON 卡，放在独立的 `board` 分支上，五种状态，配一个命令行工具。 | [黑板规则](rules/BOARD.md) |
| 报告和提示词 | 晨报、收工单、健康检查、模型月检、架构审计、每月复盘。 | [晨报](prompts/morning-brief.md)、[收工单](prompts/evening-wrap.md) |
| 规则 | 角色分工、任务卡、审查步骤、`P0`–`P3`，以及 AI 什么时候自己定、什么时候问你。 | [AI 项目规则](rules/AI-PROJECT-RULES.md)、[参考手册](rules/REFERENCE.md)、[模型选择](rules/MODELS.md) |
| 额度巡检 | 读用量账本，在额度偏低、定时任务失败或卡住时给出提醒文案。 | [额度巡检](docs/quota-patrol.md) |
| 起步模板 | 一个微信小游戏项目，门禁和派工器已经接好。 | [模板说明](templates/wechat-minigame/README.md)、[对齐清单](templates/PARITY.md) |

以上链接指向的文档目前只有英文版。

## 快速上手

三步互相独立，可以只用前面的。建议从门禁开始。

### 1. 门禁和合并机器人

1. 把 [CI 模板](templates/ci.yml) 复制到你项目的 `.github/workflows/ci.yml`。只改两处 `uses:` 和 `stack`、`check` 两个输入：

   ```yaml
   # gate job
   uses: renjih/SoloForge/.github/workflows/gate.yml@v0
   with:
     stack: node            # node、python 或 godot
     check: npm run check   # 你项目的完整检查命令

   # merge job
   uses: renjih/SoloForge/.github/workflows/merge-bot.yml@v0
   ```

   想用自己审过的版本，就 fork 本仓库，把路径换成你的 fork，并固定到某个提交的 SHA。
2. 在默认分支上把 `gate / gate` 设成必过的检查。
3. 用[标签脚本](scripts/labels.sh)建标签：`scripts/labels.sh --owner YOUR_ORG --repo example-game`。需要先装 GitHub CLI。
4. 要自动合并，就加一个名为 `OPS_TOKEN` 的 secret，权限是这个仓库的 Actions 读、Contents 写、Pull requests 写。不加的话只用门禁，自己手动合。
5. 可选：把 [docs/arch-rules.json](docs/arch-rules.json) 复制到你项目的同一路径，把里面 3 条示例规则换成你自己的。门禁会在每个 PR 上执行它们。

### 2. 评论派工

API key 按用量计费，钱由你的 AI 服务商收。开启派工之前，先在服务商后台设好每月消费上限。每个任务默认 30 分钟超时、21 轮（或 21 个工作项）、244,000 个上报 token；详见[费用控制及其局限](docs/dispatcher.md#cost-controls)。

1. 把[派工模板](templates/dispatch.yml)复制到 `.github/workflows/dispatch.yml`。
2. 在仓库 secrets 里加 `OPENAI_API_KEY`（用 Codex）或 `ANTHROPIC_API_KEY`（用 Claude）。也可以用 Codex 的登录文件，见 [Codex 登录刷新](docs/dispatcher.md#codex-login-refresh)。
3. 在仓库设置里允许 GitHub Actions 创建 PR，或者加一个只限本仓库的 `SOLOFORGE_TOKEN`。
4. 在 issue 下评论：

   ```text
   /codex Fix the failing parser test and run the project checks.
   ```

派工器对每个 issue 或 PR 每小时最多开工 3 次，失败后不会自己重试。fork 来的 PR 在拉代码之前就会被拒绝。

### 3. 黑板和每日报告

派工模板已经会在每个任务前后往你仓库的 `board` 分支写卡。想看黑板，先克隆这个分支，再在 SoloForge 的目录里运行黑板工具：

```bash
git clone --branch board --single-branch https://github.com/YOUR_ORG/example-game.git /tmp/soloforge-board
export SOLOFORGE_OWNER=YOUR_ORG BOARD_REPO=example-game BOARD_DIR=/tmp/soloforge-board
python3 tools/board/board.py report morning
python3 tools/board/board.py report evening --max 5
```

把[晨报](prompts/morning-brief.md)和[收工单](prompts/evening-wrap.md)两份提示词交给向你汇报的那个助手。[维护 workflow](.github/workflows/board.yml) 会把已合并 PR 的卡标成完成、执行你授权过的默认选项、归档旧卡。v0.1 里它只能跑在带有 `tools/board/` 的仓库里，想在自己项目里用，就把这个目录和这个 workflow 一起复制过去。

## 安全和费用

加 key 之前先看这一节。

- **适配器没有沙箱。** AI 命令行工具在关闭权限确认的状态下运行，和派工器是同一个系统用户。把凭据从它的环境变量里清掉只是减少暴露，不是安全边界。见[执行信任边界](docs/dispatcher.md#execution-trust-boundary)和[安全策略](SECURITY.md)。
- **只派你信得过的活。** 用 GitHub 托管的 runner、只限单个仓库的细粒度令牌，并在服务商那边设消费上限。
- **fork 来的 PR 拿不到你的 secrets，也用不到你自己的 runner。** 它们的门禁跑在 `ubuntu-latest` 上。派工器会拒绝它们，合并机器人把它们留给你。
- **自建 runner 只适合私有仓库。** 见[自建 runner 说明](docs/self-hosted-runner.md)。
- **这些上限不是账单保证。** 已经发出去的请求可能超过 token 上限。

## 现状

v0.1 是早期版本。

- 门禁、合并机器人和黑板由 [scripts/check.sh](scripts/check.sh) 里的测试覆盖。
- 派工器的测试用的是测试适配器和本地仓库。它还没有在 GitHub 上用 Codex 或 Claude 完整跑过一个真实任务。默认上限来自一次很小的 Codex 命令行实测。
- 默认上限偏紧。正常大小的任务可能停在上限上；跑过几次之后请按实际情况调高 `max-turns` 和 `max-tokens`。

v0.2 计划：

- 把适配器和发布拆成两个 job，让 AI 命令行工具和 GitHub 令牌不在同一台机器上。
- 定时任务提示词自动瘦身，以及每月一次的压力测试。
- 用 SoloForge 自己的派工器和合并机器人维护 SoloForge。
- 更多技术栈模板。

## 参与贡献

最需要的是两类贡献：其他 AI 服务商的适配器，其他技术栈的模板。请先用 [issue 模板](.github/ISSUE_TEMPLATE/)，并读一下 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

在本仓库里干活的 AI 从 [AGENTS.md](AGENTS.md) 读起。

## 许可

[MIT](LICENSE)
