# Staging 隔离启动保护（本地准备，尚未部署）

本文件说明如何为「地图升级 + 修改记录」整合版准备一个独立测试环境，以及启动保护实际能挡住什么、不能证明什么。当前缺少专用测试机器人和新增资源确认，因此只完成仓库内的保护与配置模板，不创建收费资源、不注册机器人、不部署、不写入生产。

## 原则

- `ENV=staging` 或 Railway 环境名为 `staging` / `test` 只会进入 staging 模式，不能证明已经隔离。
- 进入 staging 模式后，默认拒绝启动，直到显式声明并通过对 token / 目标地址 / 已知生产标识的离线校验。
- 校验发生在数据库迁移、Redis 副作用、scheduler、审核补发、`setWebhook` / 菜单 / 命令 / `deleteWebhook` 之前。
- 生产默认行为不变：不设上述 staging 标志时，不增加必填项，认证与公开接口不变，也不新增调试入口。
- 本门禁不读取生产 token，不调用 Telegram `getMe` 或发送消息，异常和日志不打印 secret。

## 启用 staging 的必要条件

启动前必须同时满足：

| 项 | 说明 |
| --- | --- |
| `STAGING_ISOLATION=1` | 显式启用隔离门禁；仅 `ENV=staging` 不够 |
| `STAGING_CONFIRMED_ISOLATED=1` | 操作者确认这是独立测试环境 |
| `STAGING_DB_IS_ISOLATED=1` | 确认 `DATABASE_URL` 是新建的独立 Postgres，不是生产库或生产数据副本 |
| `STAGING_DATA_IS_SAMPLE=1` | 确认库内只有示例资料 |
| Redis 声明 | `STAGING_REDIS_IS_ISOLATED=1` 且配置独立 `REDIS_URL`，或 `STAGING_REDIS_UNUSED=1` 且留空 `REDIS_URL` |
| `STAGING_EXPECTED_BOT_ID` | 专用测试机器人的数字 id，必须与当前 `BOT_TOKEN` 冒号前的前缀一致（离线可校验） |
| `WEBHOOK_HOST` / `WEBAPP_URL` | 必须是该测试环境自己的 `https://` 公开地址，不能是 localhost 或已知生产域名 |

还需要：一个由 BotFather 新建的测试机器人（不要复用生产 token）、独立 Redis 或明确不用、一份可丢弃的示例数据、空的频道配置，以及独立的 HTTPS 主机 / Railway 环境（不要用生产环境或生产 Web 服务）。

完整占位见仓库根目录 `.env.staging.example`。不要把生产 secret 填进示例或提交到 git。

## 已知生产目标（硬阻止）

若 staging 模式检测到以下目标，将拒绝启动：

- 主机名 `web-production-46ed80.up.railway.app`
- Railway 环境名 `production` / `prod`
- 已知生产环境 ID `95d049a1-3156-4a57-8287-b3ddad73b7c2`
- 已知生产 Web 服务 ID `37c189b7-2717-4a7b-aee8-153d71f3ff37`

这些标识来自此前只读核对，不是 secret。生产若重建，需要更新代码中的名单，或用 `STAGING_BLOCKED_HOSTS`、`STAGING_BLOCKED_RAILWAY_ENV_IDS`、`STAGING_BLOCKED_RAILWAY_SERVICE_IDS` 补充。未列入名单的新生产域名无法被自动发现。若进程已运行在已知生产 Railway 环境或生产 Web 服务上，即使误设 `ENV=staging` 或 `STAGING_*`，也不会进入 staging 模式，以免生产拒启；staging 必须使用独立环境和服务。

## 无法自动证明的隔离项

门禁不能、也不会宣称下列事项已经核实：

- `DATABASE_URL` 是否真的指向独立 Postgres（而非生产库或生产转储）
- `REDIS_URL` 是否真的指向独立 Redis
- 库内数据是否只是示例资料
- 连接数据库之前，库内频道 / 必进群目标是否为空（连接后会再做一次频道检查，但仍不能证明库本身独立）
- token 虽然可离线核对 bot id 前缀，但不能证明它没有被误用到其他环境
- `STAGING_EXPECTED_BOT_USERNAME`（不调用 `getMe`，故无法离线证明）
- 未列入阻止名单的生产域名、私有 DNS，或重建后的 Railway 标识
- 任何 `STAGING_*_IS_ISOLATED` 人工声明（声明不等于事实）

因此部署前仍须由人核对连接串、Railway 项目/环境/服务、机器人和数据来源。这不是万无一失的保险。

## staging 默认关闭、需人工打开的任务

隔离校验通过后，HTTP 服务可以起来，但下列动作默认仍关闭，需单独打开：

| 标志 | 默认 | 打开后才会做 |
| --- | --- | --- |
| `STAGING_ENABLE_BOT_HOOKS` | 关 | `setWebhook`、菜单按钮、Bot 命令、`deleteWebhook`、启动时 `getMe` |
| `STAGING_ENABLE_TELEGRAM_JOBS` | 关 | 到期提醒、班次提醒、频道目录刷新、优惠帖收尾、标签同步、审核通知补发 |
| `STAGING_ENABLE_DB_JOBS` | 关 | 过期资料/会话清理、会话消息清理、价格回填 |
| `STAGING_ENABLE_DEMO_PURGE` | 关 | 启动时清理标题含「演示」的资料 |
| `STAGING_ALLOW_TEST_CHANNELS` | 关 | 允许库内已有频道或必进群配置；默认要求这些字段为空 |

测试环境的运营频道应保持为空，避免定时任务或审核流程写到真实频道。只有在确认目标确为测试频道后，才打开 `STAGING_ALLOW_TEST_CHANNELS` 和相应的 Telegram 任务。

认证、审核、通知与频道同步的既有规则没有被放宽；本保护只是在未确认隔离时拒绝启动或拒绝这些副作用。

## 部署前检查清单（未来启用时使用，本次不执行）

1. 已向 BotFather 申请专用测试机器人，并只把它的 token 配到测试环境；预期 bot id 与 token 前缀一致。
2. 已新建独立 Postgres 与（如需）独立 Redis，连接串不是生产值，库内是可丢弃的示例数据。
3. 已准备独立 Railway 环境或同等主机，环境名不是 `production`，服务也不是生产 Web 服务。
4. `WEBHOOK_HOST` / `WEBAPP_URL` 是该测试主机的 HTTPS 地址，不是已知生产域名。
5. 已填写全部隔离声明，并理解声明不能代替核对。
6. 频道、目录、优惠、评价推送目标为空，除非另有已确认的测试频道。
7. 先不打开 `STAGING_ENABLE_*`；确认进程能通过门禁后再按需要逐项打开 hook 或任务。
8. 打开 hook 前再次确认 webhook URL 指向测试主机，不会改写生产机器人。
9. 不在生产环境写入 `STAGING_*` 或把 `ENV` 改成 `staging`。

在专用测试机器人和独立资源齐备之前，应停在本地准备，不要用生产机器人、生产库或临时隧道绕过去。

## 本地验证

相关自动化测试在 `tests/test_staging_guard_unit.py`。这些测试只使用假 token 和假地址，并拦截真实 Telegram 调用。它们不创建云资源，也不证明某个将要部署的环境已经隔离。
