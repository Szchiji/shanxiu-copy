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
