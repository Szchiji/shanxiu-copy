# 月影车姬 (yycj)

> 本仓库当前主线在 PR #64 / `bc7640d` 之上，包含满内容区地图视图与兰花信用移除。访客资源版本为 `20261112b`。已知限制：短屏筛选面板「重置 / 查看结果」被底部导航遮挡的修复不在本批；实机视觉（含真实 OSM 底图与弱网）尚未作为已验收承诺。


Telegram 社区 Mini App 与薄门机器人：资料浏览与地图、投稿审核、匿名会话、评价与举报、管理后台。

**Slogan：** 月下寻花，影中见真

---

## 功能一览

| 模块 | 说明 |
|------|------|
| Mini App 首页 | `/app/go.html`：三列资料卡（117×224 规则未改）、城市与筛选、详情、收藏、评价 |
| 地图 | 公开地图按行政区/镇中心聚合与联动卡片，组合筛选与区域搜索。公开接口不返回精确坐标或 `approx_lat/lng`；真实 OSM 底图、手机定位与弱网未作为已验收承诺 |
| 投稿与上架 | 老师/商家在 Mini App 发布；游客不能发布。管理员审核通过后上架，可续期、下架、重上架（含既有推送通知） |
| 修改记录与恢复 | 可编辑字段会留下可证明版本。恢复需预览确认，并作为一次新修改走原审核/同步/通知，不直接改上架状态或有效期。媒体只能从当前资料或本地媒体缓存恢复；旧媒体不可用时媒体恢复失败，不会清空现有媒体 |
| 匿名会话 | 双方同意后由机器人中转；超时结束；消息落库，结束后按 `MESSAGE_RETENTION_HOURS` 清理。结束不再结算信用分 |
| 评价与举报 | 评价默认需审核后展示；举报由管理员采纳（下架资料）或驳回。拉黑独立于已停用的信用体系 |
| 防刷 | 投稿/报告/搜索频率限制与文本重复检测（优先 Redis，无 `REDIS_URL` 时内存回退） |
| 管理后台 | `/app/desk.html`（`/app/admin.html` 会跳转；仅 `ADMIN_IDS`）：待审资料/媒体/评价/举报、上架与续期、用户拉黑、站点与工作台配置。无网页独立登录 |

**已移除：兰花信用。** 不再计算、展示、调整用户信用分或「兰花令」等级，不再使用月影遮蔽做业务拦截，不再跑每日信用恢复任务，不再按信用分排序或奖惩。旧接口 `GET /api/me/credit`、`POST /api/admin/credit/adjust`、`GET /api/admin/shadow`、`POST /api/admin/users/shadow` 返回 **410**（需登录的仍先走鉴权）。历史版本曾经包含该功能；本仓库不再把它当作现行能力。详情见下方「已移除的兰花信用」。

没有独立的支付、余额或与信用分不同的积分账户。

## 地图：满内容区视图

从首页搜索行的「地图」进入独立地图视图，铺满 Mini App **内容区**（不调用 Telegram 全屏 API，也不另开系统浏览器）。

- 进入后隐藏首页无关块与底部导航（可恢复）。顶部为紧凑的返回 / 城市 / 搜索，筛选默认折叠。
- 地图最大化；右侧 ± 缩放；点选后底部出现紧凑资料卡，关闭后地图收回空间。聚合列表可滚动，短屏也能点到末项和关闭。
- 「搜索此区域」需显式点击，拖动地图不会自动重查。
- 返回恢复进入前的首页滚动和筛选；从详情返回恢复地图视野和选中项。浏览器 Back 先收起最上浮层，再退出地图回到首页。
- 仍只使用公开行政区 / 镇中心，候选上限 600 份。位置说明可折叠，但不会改成精确位置或距离承诺。不新增地图供应商或付费 API，也不在地图里增加联系 / 预约 / 交易。
- 既有加载、空态、错误、底图手动重试、旧回调抑制，以及用户主动定位权限规则保持不变。首页三列卡片布局不在本次改动范围内。

当前访客资源版本为 `20261112b`。



---

## 技术栈与结构

- **Bot**：aiogram 3.x，薄门（`/start`、会话中转、管理与开课提醒）。Webhook 由 FastAPI 接入
- **HTTP**：FastAPI + Uvicorn；Mini App 静态资源在 `/app`，接口在 `/api`
- **数据库**：PostgreSQL（SQLAlchemy 2 async + asyncpg）
- **缓存 / 限流**：Redis（可选；未配置则内存回退）
- **调度**：APScheduler（会话过期、会话消息清理、资料到期下架）。**没有**每日信用 tick
- **鉴权**：Telegram Mini App `initData` HMAC，服务端签发短时 Bearer，不会把 `BOT_TOKEN` 下发到前端

```
yycj/
├── bot/
│   ├── main.py              # FastAPI webhook + /api + /app；本地无 WEBHOOK_HOST 时 polling
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── api/                 # Mini App / Admin HTTP API
│   ├── handlers/            # start / session / admin / shift（注册于 handlers/__init__.py）
│   └── services/            # 用户档案、会话、搜索、审核、地图/位置、修改记录等
├── miniapp/                 # 静态资源；访客入口 go.html，后台 desk.html
├── scripts/                 # init_db.py、build_miniapp.py 等
├── tests/
├── .env.example
├── .env.staging.example     # 独立 staging 示例（仅 ENV=staging 不能证明已隔离）
├── docs/staging-isolation.md
├── Procfile / railway.toml / nixpacks.toml
└── README.md
```

访客改动后需运行 `python scripts/build_miniapp.py` 重建 `miniapp/app.bundle.js` 与 `app.bundle.css`，并在 `go.html` / `index.html` 使用**新的**资源版本号（当前为 `20261112b`）。不要把旧版本号复用在不同字节的文件上。

---

## 环境变量

见 `.env.example`。独立预发若要做，见 `.env.staging.example` 与 `docs/staging-isolation.md`：仅设置 `ENV=staging` 不能证明已隔离，也不要在生产环境填写 `STAGING_*`。

| 变量 | 必填 | 说明 |
|------|------|------|
| `BOT_TOKEN` | 是 | BotFather Token（也用于 WebApp initData HMAC） |
| `DATABASE_URL` | 是 | Postgres 连接串（`postgresql://` 或 `postgresql+asyncpg://`） |
| `ADMIN_IDS` | 强烈建议 | 管理员 Telegram 数字 ID，逗号分隔 |
| `REDIS_URL` | 生产建议 | 未设则防刷走进程内存并打警告（多实例不共享） |
| `MESSAGE_RETENTION_HOURS` | 否 | 会话结束后消息保留小时数，默认 `24` |
| `WEBAPP_URL` | Mini App 建议 | 如 `https://<域名>/app`；配置后菜单按钮打开 Mini App |
| `WEBHOOK_HOST` | 生产必填 | 如 `https://xxx.up.railway.app` |
| `WEBHOOK_PATH` | 否 | 默认 `/webhook` |
| `WEBHOOK_SECRET` | 建议 | 与 Telegram `secret_token` 一致 |
| `PORT` | 否 | 默认 `8080`（Railway 会注入） |
| `STORAGE_CHAT_ID` | 否 | 媒体上传落盘会话；未设则用第一个 `ADMIN_ID` |
| `ENV` | 否 | 运行环境名；生产请保持非 staging 用法，见隔离文档 |
| `LOG_LEVEL` | 否 | 默认 `INFO` |

本地不配 `WEBHOOK_HOST` 时自动走 **polling**。管理员可在后台查看会话落库消息（仅 `ADMIN_IDS`）。

---

## 管理员与老师操作

**管理员（`ADMIN_IDS`）**

- 在 Telegram 内打开 Mini App 管理入口（非管理员打开会提示无权限；没有单独的网页账号密码）
- 审核待审资料/媒体：通过后上架，拒绝则通知投稿人（best-effort 私聊，不阻断审核结果）
- 审核评价（通过 / 拒绝 / 拒绝刷评）；审核举报：采纳后下架相关资料，驳回则通知举报人。均不再加减信用分
- 用户搜索与拉黑 / 解除拉黑。拉黑后账号无法使用 Mini App（管理员除外）
- 资料续期、下架、重上架、代改、修改记录预览与恢复（恢复=新修改，走原流程）
- 站点公告、开放城市、工作台配置、优惠与置顶等既有运营能力

**老师 / 商家**

- 在「我的」确认身份后发布或修改自己的资料（游客会收到「仅老师或商家可发布」）
- 编辑走既有修改同步；可查看修改记录并在预览确认后恢复某一版本（作为新修改）
- 续期、下架后再申请重上架；相关通知沿用既有推送，不另加全局开关
- 角色切换受一周锁定限制，管理员可解锁

**客人**

- 浏览、筛选、查看详情与地图、收藏、发起会话邀请、提交评价与举报
- 不发布资料

---

## 地图与修改记录：能力与限制

- 首页三列卡片布局与未授权发布边界保持不变。
- 地图展示的是公开行政区/镇中心与聚合，不是资料精确位置；前端不接收精确坐标。
- 恢复已生效的可编辑文字后，公开地图的城市/区域/营业筛选跟随这些字段；上架状态和有效期不因恢复而改变，因此隐藏或过期资料不会仅因恢复重新出现在公开地图。
- 持续休息等内部营业确认字段不在可恢复快照中。
- `listing_edit_versions` 由启动时 `create_all` 与幂等 `CREATE TABLE / INDEX IF NOT EXISTS` 补齐，无 DROP。硬删除一条资料时会删除该资料的版本与修改检查点。旧库不必回填。
- 真实手机上的 OSM 渲染、定位、触摸与弱网没有在本说明里当作已验收结果。

---

## 已移除的兰花信用

现行代码不再实现用户「兰花令 / 兰花分 / 月影遮蔽」：

- 身份与角色逻辑已迁到 `bot/services/users.py` 与 `user_admin.py`（代称、拉黑、搜索）。不要再引用已删除的 `credit_service`
- 会话结束、投稿通过、举报采纳入站或驳回，都不再写入信用流水，通知文案也不再出现分值变动
- 用户对象与资料公开字段不再下发 `lanhua_score`、`tier`、`is_shadowed`、`authenticity_score`、`credit_boost` 等信用展示字段
- 资料详情仍展示「已审核评价条数」等评价信息，这是评价审核，不是用户信用分
- 后台已去掉「调整口碑分」「月影遮蔽」「遮蔽名单」

**兼容残留（有意保留，不做破坏性迁移）：**

| 残留 | 策略 |
|------|------|
| `users` 表的 `lanhua_score`、`tier`、`shadow_*`、`recovery_progress`、`total_earned`、`total_deducted`、`is_shadowed`、`last_recovery_at` | 列保留，应用不再读写业务含义；历史上已被遮蔽的账号**不再**因此被禁止发布或改稿 |
| `credit_history` 表与 `CreditHistory` 模型 | 表保留，不再新增流水；不批量删除历史流水。硬删除某条资料时，仍可能清理与该资料/会话 `related_id` 相关的残留行（既有硬删除行为，不是清库） |
| `lamps.authenticity_score`、`lamps.credit_boost` | 列保留，创建资料时仍可能写入默认值，但不再对用户展示或作为口碑分 |
| `sessions.quality_score`、`sessions.has_praise` | 列保留；好评按钮仍可记录感谢，不再参与加分 |

若以后要 DROP 这些列或表，需要单独确认后再做破坏性 migration。不要在未确认时改生产数据或删除历史流水。

---

## Mini App / HTTP API（常用）

- 静态：`GET /app/` 或 `GET /app/go.html`（`WEBAPP_URL` 建议指向 `/app`）
- 管理控制台：`GET /app/desk.html`（`/app/admin.html` 重定向到控制台；非管理员无权限）
- 健康检查：`GET /health` → `ok`
- Webhook：`POST /webhook`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth` | body `{ "initData": "..." }` → token |
| GET | `/api/me` | 当前用户（不含信用字段） |
| GET | `/api/me/credit` | **已停用，410** |
| GET | `/api/home` | 首页 Feed / 公告 / 城市 |
| GET | `/api/lamps`、`GET /api/lamps/{id}` | 搜索与详情 |
| POST | `/api/sessions/request` | 发起匿名会话邀请 |
| POST | `/api/posts` | 老师/商家投稿（媒体图+视频合计默认最多 6 个，站点设置可调，上限 9；支持上传或 URL） |
| POST | `/api/reports` | 创建举报 |
| GET/POST | `/api/admin/posts/...`、`/api/admin/reports/...` | 待审与审核（不再加减信用分） |
| POST | `/api/admin/credit/adjust` | **已停用，410** |
| GET | `/api/admin/shadow`、`POST /api/admin/users/shadow` | **已停用，410** |
| GET | `/api/admin/sessions/{id}/messages` | 会话落库消息 |

除 `/api/auth` 外需 `Authorization: Bearer <token>`。Bot 审核回调与 HTTP 审核共用 `bot/services/admin_ops.py`。

---

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env：BOT_TOKEN、DATABASE_URL（不要设 WEBHOOK_HOST）
# 可选：REDIS_URL、MESSAGE_RETENTION_HOURS、WEBAPP_URL、ADMIN_IDS

python scripts/init_db.py
python -m bot.main
```

本地若要用 Mini App，需 HTTPS 公网隧道（如 Cloudflare Tunnel / ngrok）并把 `WEBAPP_URL` 指到 `https://<隧道>/app`。本说明不承诺某种隧道或真机验收已经完成。

访客前端有改动时：

```bash
python scripts/build_miniapp.py
# 检查 bundle 是否与源文件一致：
python scripts/build_miniapp.py --check
```

---

## Railway 部署

1. Railway → New Project → Deploy from GitHub
2. **+ New → Database → PostgreSQL**
3. **+ New → Database → Redis**（推荐；在变量中引用插件提供的 `REDIS_URL`）
4. 在 Web 服务变量中配置上表所列项：`BOT_TOKEN`、`DATABASE_URL`、`REDIS_URL`、`ADMIN_IDS`、`WEBHOOK_HOST`、`WEBAPP_URL`、`WEBHOOK_SECRET` 等。`PORT` 一般由平台注入
5. Settings → **Generate Domain**（需要 HTTPS 公网域名）
6. Start Command：`python -m bot.main`（见 `railway.toml` / `Procfile`）
7. 部署成功后 `/health` 应返回 `ok`；`/app/` 打开 Mini App
8. 日志出现 webhook 设置成功后，在 Telegram 向机器人发 `/start`，并用 BotFather 菜单按钮打开 Mini App

不要把真实 Token、生产账号或主机名写进仓库文档。

---

## 免责声明

仅用于技术演示与社区信息整理。遵守当地法律，不得用于违法用途。机器人不参与交易撮合。
