# 合台（hetai）第一版

同一个新机器人、同一套界面：群管理、目录投稿审核、群积分与全局信用都作为模块放在一个应用里，而不是两套站点。

本目录是 `Szchiji/shanxiu-copy` 在 `rewrite/platform` 分支上**新写的应用**。旧的 Flask 群管代码仍保留在仓库根目录作为基线，本阶段互不影响。不依赖、不引用 `Szchiji/yycj` 或 `Szchiji/shanxiu` 的实现代码。

## 已实现

- 单一 FastAPI 应用 + 单一 aiogram 3 Dispatcher + 唯一 webhook 入口 `POST /telegram/webhook`
- `/start` 菜单规划指向同一应用（`/app` 与管理员 `/admin`）
- 统一中文界面与导航：应用（目录 / 我的 / 投稿）与管理（概览 / 群组 / 成员 / 审核 / 任务）同一外壳、同一账号
- 群列表、详情、设置（租户隔离）
- 成员目录与角色（改角色仅超级管理员）
- 群积分账本（原子扣减、幂等键、跨群拒绝）与全局信用账本（分开存储、不对冲）
- 目录投稿、审核通过/拒绝，通过后出现在应用信息流
- 任务列表与配置；可手动跑一次，**不发送任何 Telegram 通知**
- 概览数字来自数据库
- 演示模式：仅绑定回环地址，SQLite 隔离库，夹具均标注「示例数据」
- 生产模式：失败关闭，禁止 SQLite 与演示回退；提供 initData 签名/过期校验（演示未连接 Bot 时不会伪装成已登录 Telegram）
- 健康检查 `GET /health`

## 明确推迟（界面不会假装已上线）

- 匿名会话中转
- 红包
- 多 Bot 克隆 / Userbot
- 自动向 Telegram 注册 webhook（setWebhook）；出站消息仅在生产显式设置 `HETAI_BOT_TRANSPORT=live` 时才会发送。演示与默认 dry-run 使用本地无 HTTP 会话处理 `/start` 等更新，不会外发

## 演示（非生产）

仅允许 `127.0.0.1`。无需 Telegram 登录。顶部会标明「未连接真实 Telegram Bot」。

```bash
python3 -m venv .venv-hetai
source .venv-hetai/bin/activate
pip install -r requirements-hetai.txt
export HETAI_MODE=demo
export HETAI_HOST=127.0.0.1
export HETAI_PORT=8765
export HETAI_WEBHOOK_SECRET=demo-local-only
python -m hetai
```

打开 http://127.0.0.1:8765/app 。可用顶部「示例身份」在示例管理员 / 运营 / 成员之间切换（仅演示）。默认无需登录。

### 建议截图路径

1. `/app` 目录信息流
2. `/app/listings/2` 条目详情
3. `/app/me` 同一账户下的信用与各群积分
4. `/app/submit` 投稿表单
5. `/admin` 数据概览
6. `/admin/groups` 与 `/admin/groups/1` 群设置和积分
7. `/admin/users` 与 `/admin/users/3` 成员与信用
8. `/admin/submissions` 与待审详情
9. `/admin/tasks` 任务配置
10. 切换示例身份为「示例成员」后访问 `/admin` 应被拒绝

Webhook 演示密钥（仅本地）：请求头 `X-Telegram-Bot-Api-Secret-Token: demo-local-only`。这不是生产密钥。 演示模式下 `POST /telegram/webhook` 会把 Update 交给同一个 Dispatcher（`/start`、`/help`、菜单指向同一应用），出站由本地无发送会话记录，不会访问 Telegram。

## 生产（尚未部署）

必须设置 `HETAI_MODE=production`、`HETAI_DATABASE_URL`（PostgreSQL）、`HETAI_BOT_TOKEN`、`HETAI_WEBHOOK_SECRET`、`HETAI_ADMIN_IDS`、`HETAI_SESSION_SECRET`。禁止 SQLite。缺少任一项都会拒绝启动。出站默认 `HETAI_BOT_TRANSPORT=dry-run`（处理更新、准备回复、不发送）；只有显式设置为 `live` 才会走真实 Bot API。仍不会在启动时自动注册 webhook。示例见 `docs/hetai/deploy.example.env`。

## 测试

```bash
HETAI_PYTEST=1 pytest tests/hetai -q
```

只跑合台测试，不影响仓库里原有的旧测试套件。


## 公开预览（preview）

独立测试部署使用 `HETAI_MODE=preview`，不是本地 demo，也不是生产。

- 预览口令 `HETAI_PREVIEW_ACCESS_SECRET` 至少 12 个字符、至少 8 个不同字符，且不能有首尾空白；`HETAI_SESSION_SECRET` 仍须至少 24 个字符、至少 8 个不同字符，且不能有首尾空白。未满足则拒绝启动。
- 允许绑定 `0.0.0.0`；若环境提供 `PORT` 且未显式设置 `HETAI_PORT`，则使用 `PORT`。
- 未登录只能访问 `/health`、`/preview/login` 和静态资源。`/health` 只返回 `ok` / `app` / `mode`。
- 登录页：`GET/POST /preview/login`，字段 `username`（固定 `preview`，可忽略）与 `password`（预览口令），另有隐藏域 `csrf_token`、`next`。
- 登录后可切换示例身份并做受控的审核/积分等写操作。数据为示例，传输为 local，不发送 Telegram，不自动注册 webhook。
- 退出：`POST /preview/logout`。
- 不要把预览口令写入仓库或日志。本地可用隔离的权限 600 凭证文件，不要提交。

## 已实现（V2 预览切片）

移动优先的同一界面：主导航为「目录 / 投稿 / 我的」，有审核权的人额外看到「待办」。顶部上下文在「个人视图」与「当前可访问的群」之间切换；超级管理员的「全局工具」入口单独放在设置/工具里，不是日常底栏。群目录、投稿、设置、积分账本都跟当前群走。

授权以服务端成员关系与群角色为准：`group` / `group_id` / `chat_id` / `startapp` 只是提示，校验失败返回 403。不会把 `chat_instance` 当成 `chat_id` 或入群凭证。普通群管理只能看到自己管理的群，不能看全部成员或全部用户。隐藏按钮不是权限边界。

投稿流程：成员投稿 → 待审 → 有权的人审核 → 通过后进入该群目录。群设置里的 `publish_reward_points` 默认为 0；示例夹具里「华东运营」明确标注通过后奖励 10 积分，「测试交流」明确标注默认不奖励。奖励与发布在同一事务中完成，同一投稿重复通过不会再次入账；失败则整单回滚，不会出现已上架但积分写到一半。更正拒绝会追加冲销流水，不删除旧账本。全局信用只有超级管理员的独立调整入口，审核通过不会偷偷加信用分。审核记录可在待办详情和条目页查看。

Telegram Mini App 的 initData 校验（签名、`auth_date` 缺失或过期）失败关闭。登录成功后只会签发会话 Cookie；start_param / chat 若对应已有成员关系，仅返回上下文提示，不自动入群。预览口令政策不变（至少 12 个字符、至少 8 个不同字符、不能有首尾空白），现有预览登录保持可用。

示例身份（预览/演示夹具，均有「示例」标注）：

- 示例管理员（超级）：两个示例群都在，可开全局工具
- 示例运营（仅华东运营群）：可审核该群待办，不能看测试交流群或全部成员
- 示例成员（仅华东运营群）：可投稿和看该群目录
- 示例访客（仅测试交流群）：只能看到自己的群

主要路由：`/app` 目录，`/app/listings/{id}` 条目，`/app/submit` 投稿，`/app/me` 我的，`/app/inbox` 与 `/app/inbox/{id}` 待办审核，`/app/groups/{id}` 群主页，`/app/groups/{id}/settings` 设置，`/app/groups/{id}/ledger` 账本，`/admin?view=global` 超级管理员全局工具。`/admin/submissions` 仍指向待办，兼容原路径。

## 尚未实现（需另授权测试 Bot 后）

- 真实 Telegram Bot 登录、群内快捷操作、私聊入口与通知、在客户端内打开 Mini App 的完整验证
- 自动 `setWebhook`、向 Telegram 发送真实消息、inline / 站内分享
- 匿名会话、红包、多 Bot 克隆 / Userbot

当前预览和演示都不会假装已经连上真实 Bot。
