# 双系统整合机器人（月影车姬 + 闪秀）

一个 Bot Token、一个进程、一个 webhook，同时提供两套功能：

| 来源 | 原来是什么 | 现在负责 |
|------|-----------|---------|
| **月影车姬**（yycj） | aiogram3 + FastAPI，Telegram 社区 Mini App | 私聊主入口、Mini App 首页/地图/详情、投稿与审核、匿名会话、评价举报、管理后台 `/app/desk.html` |
| **闪秀**（shanxiu） | python-telegram-bot 21 + Flask，群组管理平台 | 群/频道管理（踢人禁言置顶警告）、关键词过滤与自动回复、进群验证、积分/打卡/商店、抽奖投票答题红包、定时消息、消息同步、报告、管理后台 `/core/...` |

两边的数据库表名没有重叠，共用同一个 Postgres 库即可（实测 75 张表并存）。

---

## 为什么不能直接两个程序一起跑

Telegram 一个 Bot Token 只能有一个 webhook，也只有一个 `getUpdates` 队列。原来两个项目各自
`setWebhook` 或 polling，同时跑会互相把对方的 webhook 顶掉，表现是「消息一会儿回一会儿不回」。

整合版的做法：

```
                    ┌──────────────────────────────────────┐
Telegram ──update──▶│  /webhook（唯一入口，FastAPI）        │
                    │        │                             │
                    │        ▼                             │
                    │   dispatch.decide(update)            │
                    │      ├── 群/频道/成员变动/大部分命令 ─▶ 闪秀（PTB Application）
                    │      └── 私聊入口/会话/回调前缀      ─▶ 月影（aiogram Dispatcher）
                    └──────────────────────────────────────┘
                                同一个进程 · 同一个端口 8080
```

私聊里的普通消息会**先给月影**，月影没有任何处理器认领时自动转给闪秀（用 aiogram 的兜底
router 判定「有没有被接住」），所以闪秀的关键词自动回复、积分发言在私聊里照样生效。

分流规则写在 `dispatch.py`，有 10 条单测覆盖（`pytest tests`）。

---

## 分流规则一览

| 消息 | 去向 |
|------|------|
| 群组 / 超级群 / 频道的任何消息、频道帖、成员进群退群、加入申请 | 闪秀 |
| 私聊 `/start`、`/admin`、`/search`、`/session_messages` | 月影 |
| 私聊其它命令（`/checkin` `/points` `/mylink` `/rank` `/lottery` `/help` …） | 闪秀 |
| 私聊普通文字/图片/语音 | 先月影，没人接再闪秀 |
| 私聊按钮点击（callback） | 月影的 15 个前缀归月影，其余归闪秀 |

要改规则只动 `dispatch.py` 里的名单，不用碰两边源码。

---

## 部署（Docker，推荐）

```bash
cp .env.example .env
# 编辑 .env：BOT_TOKEN、PUBLIC_URL、ADMIN_IDS、POSTGRES_PASSWORD

docker compose up -d --build
docker compose logs -f bot
```

日志里出现这两行就算起来了：

```
月影车姬已就绪（Mini App=https://你的域名/app）
闪秀已就绪（群管理 / 积分 / 抽奖 / 报告）
Webhook -> https://你的域名/webhook | Bot @你的bot
```

### 前置条件

1. **https 域名反代到 8080 端口**（Telegram 只接受 https 的 webhook）。nginx 示例：

   ```nginx
   server {
       server_name bot.example.com;
       client_max_body_size 50m;          # 月影的图片视频上传
       location / {
           proxy_pass http://127.0.0.1:8080;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

2. **Postgres 14 以上，且带 contrib 扩展**（月影的模糊搜索会建 `pg_trgm`）。
   官方 `postgres:16` 镜像自带；自己装的话 `apt install postgresql-contrib`。
   扩展建不了不会致命，只是全表扫描搜索，启动日志里会有一条 ensure column failed。

3. **原来的两个机器人实例必须停掉**，否则会和新进程抢 webhook。

### 裸机部署（不用 Docker）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && vi .env
.venv/bin/python main.py
```

systemd 单元示例：

```ini
[Unit]
Description=Merged Telegram Bot
After=network.target postgresql.service

[Service]
WorkingDirectory=/opt/merged-bot
EnvironmentFile=/opt/merged-bot/.env
ExecStart=/opt/merged-bot/.venv/bin/python /opt/merged-bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## BotFather 需要给的权限

- 进群后设为**管理员**，勾选：删除消息、封禁用户、置顶消息、邀请用户（闪秀的群管要用）
- 群里发 `/start` 完成初始化
- Mini App 按钮由程序自动通过 `setChatMenuButton` 设置（需要有 https 的 `WEBAPP_URL`）

---

## 目录结构

```
merged/
├── main.py          # 唯一入口：FastAPI + /webhook 分流 + 挂 Flask 后台
├── boot.py          # 两个子系统的启动器（PTB Application / aiogram Dispatcher）
├── dispatch.py      # 分流规则（可单测）
├── config.py        # 环境变量
├── sx_schema.py     # 闪秀的建表/补列逻辑（从它的 run.py 抽出，不启动 Flask 服务）
├── tests/           # 分流规则单测
├── yycj/            # 月影车姬源码（原仓库）
└── shanxiu/         # 闪秀源码（原仓库）
```

两边源码都是原样放进去的，只做了两处外围处理：

1. 月影自带的 `POST /webhook` 路由在启动时被摘掉，换成整合层的分流入口；
2. 闪秀启动时会调用 `setWebhook` 指向它自己的 `/core/webhook`，整合层在启动窗口内把它变成空操作，
   最后由整合层统一注册一次全集 `allowed_updates`（否则频道帖、成员变动会被 Telegram 丢掉）。

---

## 常见问题

**Q：日志里 `TelegramUnauthorizedError` / `InvalidToken`**
Token 不对或者带空格。`BOT_TOKEN` 和 `TG_BOT_TOKEN` 由程序自动填，不需要你手动配两份。

**Q：私聊发消息闪秀不回**
先看日志有没有「闪秀已就绪」。另外闪秀的关键词自动回复需要先在后台配置规则，
不是所有消息都会回。

**Q：群管命令没反应**
机器人不是群管理员，或者没给「删除消息/封禁用户」权限。

**Q：Mini App 打开空白**
`PUBLIC_URL` 必须是 https 且能被外网访问；Mini App 的 `initData` 校验依赖 Bot Token 一致。

**Q：两个系统会不会互相回复同一条消息**
不会。群消息只走闪秀；私聊命令按名单走；私聊普通消息只有月影没接住时才给闪秀，
判定用的是 aiogram 的兜底 router，不是关键词猜测。
