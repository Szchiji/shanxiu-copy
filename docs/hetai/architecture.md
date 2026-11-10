# 合台第一版设计说明

## 原则

- 只维护 `Szchiji/shanxiu-copy`。参考仓只读，不搬运源码。
- 一个新 Bot、一个 Dispatcher、一个 webhook 入口、一套 UI 与账号权限。
- 群管、目录审核、积分、信用是同一产品的模块，不是两个站点，也不嵌入旧界面。
- 积分与信用是两套账本，但属于同一用户资料。
- 现有源仓机器人、Railway、数据库、密钥一律不改。

## 进程与入口

- 进程：`python -m hetai`（Uvicorn，建议单 worker）。
- HTTP：同一 FastAPI 应用提供 `/app`、`/admin`、`/health`、`/api/auth/telegram`。
- Telegram：`POST /telegram/webhook`，校验 secret，将 Update 交给唯一 Dispatcher。演示强制 local 无发送会话；生产默认 dry-run（处理但不发送）；仅显式 `HETAI_BOT_TRANSPORT=live` 才出站。不会自动 setWebhook。失败的处理可重试，成功才标记 processed。
- 调度：仅当 `HETAI_RUN_SCHEDULER=true` 时启动**一个** APScheduler；默认关闭，避免多进程重复。

## 数据

见 `hetai/models.py` 与 `migrations/versions/0001_hetai_initial.py`。用户以 `telegram_id` 全局唯一；群成员以 `(group_id, user_id)` 为租户边界；积分账户按群落地，信用账户按用户落地。

## 认证

- 演示：仅回环，使用标注「示例数据」的夹具身份，不是 Telegram 登录。
- 生产：initData HMAC + 时效，签发 HttpOnly 令牌。无令牌则失败关闭。
