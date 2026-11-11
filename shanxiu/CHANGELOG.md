# 更新日志 (Changelog)


## [Unreleased] - 2026-10-07

### 🔧 优化改进 (Improved)

#### 小号登录：自动获取 API 创建应用失败（ERROR）
- 创建应用请求改成和官网表单（jQuery ajax）完全一致：带 `X-Requested-With: XMLHttpRequest`、`Referer: https://my.telegram.org/apps`、表单 Content-Type 和 Accept 头
- 应用名改为纯字母数字（不含空格）；短名为小写字母开头 + 小写字母/数字，长度 12，随机不重复；URL、描述留空
- 返回 ERROR 后先重新读取 /apps（应用可能其实已建好），没有的话用页面上的新 hash、新名字、平台改 Web 再试一次
- 如果仍然 ERROR，提示多半是 Telegram 不接受服务器机房 IP 发出的创建请求，请用自己的手机或电脑浏览器创建后手动填写；如果返回的是具体校验信息（如 Incorrect app name），原样显示
- 新增排查日志 `[my.telegram.org] …`（只记录状态码、页面标题和响应摘要，不记录手机号、验证码、hash）

#### 新功能：小号登录（同步其他机器人发的消息）
- Bot API 收不到其他机器人在群里发的消息，所以新增「小号」（Telegram 用户账号，Telethon）只负责监听，发送仍由主机器人完成
- 后台入口：侧边栏「小号登录」(`/core/userbot`)，仅主后台管理员可见
- 第一步获取 api_id / api_hash：通过 my.telegram.org 自动获取（验证码 → 读取已有应用，没有就自动创建）；失败（新号常见的 ERROR、限流、页面变化）时给出中文提示，也可以手动填写
- 第二步小号登录：验证码 → 两步验证密码（如有）→ 保存 StringSession。会话和 api_hash 用 SECRET_KEY 派生的 Fernet 加密保存；手机号、验证码、密码不保存、不写日志；日志脱敏新增 StringSession
- 运行：启动时有已保存会话才连接（默认延迟 20s，避免滚动部署时同一会话在两个 IP 同时使用）；收到 SIGTERM 先断开小号；会话失效时标记「会话失效」并在页面显示，不影响主机器人
- 同步规则：源群（小号所在群，可一键加载）、监听谁（默认仅机器人 / 指定机器人 / 全部消息）、目标群/频道、名字前缀（名字单独一行 / 转发样式）、媒体开关、屏蔽关键词
- 投递：主机器人在源群里就用 copy_message(s) 原样复制，不在就由小号下载后主机器人重新上传；相册复用现有缓冲整组发送；写入同步日志（`via='userbot'`），页面显示最近记录
- 防重复：同一条消息只转一次；我方主/克隆机器人发的消息不转；「全部消息」模式下普通成员的消息，如果主机器人已经在同步到同一目标，就自动跳过
- 依赖：`telethon==1.45.0`；新表 `userbot_accounts`、`userbot_sync_rules`，`sync_message_logs.via` 列

#### 同步：编辑旧帖不再重复发送单张图片
- **根因**：机器人的消息处理器也会收到「编辑」事件（edited_channel_post / edited_message）。频道里编辑旧相册（改文字/按钮）时，Telegram 按张逐条推送编辑事件，每条相隔数秒，同步把每一张都当新消息复制到目标群，于是出现只有一张图的重复帖（生产 15:10–15:15 实测：旧帖 27929–27932 被逐张单独发出）
- 频道同步与群组同步现在忽略编辑事件，只同步新发的消息；跳过时打印 `[sync] skip edited post …`
- 新发相册仍按整组发送（日志：新帖 30229–30232、30233–30236 均为一次 4 张）

#### 相册偶尔有一张被单独发出
- **根因**：Telegram 的 webhook 逐条投递更新、与机器人所在其它群的消息交错，同一相册的各张可能相隔数秒到达（生产 14:18 实测讨论群内同一批相差约 4.5s）；原缓冲窗口仅 1.2s（同步）/ 1.5s（转发规则），先到的那张被提前单独发送
- 相册缓冲静默窗口统一改为 6s（环境变量 `ALBUM_FLUSH_DELAY_SECONDS` 可调）；按 `media_group_id` 分组，延长窗口只增加延迟、不会误合并不同相册
- 凑满 10 张（Telegram 相册上限）立即发送
- 新增日志：每次 flush 一行 `[album] kind=sync|forward chat=… mgid=… flush items=N span=…s`，以及投递路径；窗口后才到的「迟到分片」打印 `LATE part`，便于排查

#### 同步发送者前缀：名字单独一行 / 转发样式
- 开启「显示发送者前缀」后默认样式改为**名字单独一行**：第一行是可点击昵称 `<a href="tg://user?id=…">昵称</a>`（不再带方括号），下面是原内容（保留格式）
- 文本 / 单媒体说明 / 相册均适用；相册把名字放在带说明的那张（无说明则第一张），其它说明保留
- 超出 Telegram 长度（文本 4096、说明 1024）时：名字单独发一条，紧接原消息精确复制
- 新增可选样式「转发样式（显示“转发自”）」：用原生 `forward_message(s)`；转发失败（如源群禁止转发）自动回退到名字单独一行
- 新字段 `sender_prefix_style`（`newline` 默认 | `forward`），群同步与频道帖同步设置页均可选（仅在开启前缀时显示）；频道帖前缀默认仍关闭
- 迁移：`sync_group_messages.sender_prefix_style VARCHAR(20) DEFAULT 'newline'`

#### 日志不再泄露 Bot Token
- `httpx` / `httpcore` 日志级别降为 WARNING（不再逐条打印带 token 的 Telegram API URL）
- 新增 `app/log_redaction.py`：所有日志 handler 挂脱敏过滤器（消息、参数、异常堆栈），并包装 stdout/stderr，`print()` 与未捕获异常中的 `bot<id>:<token>` 统一替换为 `bot<redacted>`
- 在 `run.py`、`create_app()`、主 Bot `run_bot`、克隆 Bot `start_clone_bot` 均安装（幂等）
- 显式脱敏 requests 异常（含 URL）的打印/返回：resync getMe/getChat、点赞反应、成员标签、`fetch_chat_info`

#### 频道转发规则：相册整组转发
- 关联频道的相册在讨论群里是一条条自动转发消息；转发规则现在按 `media_group_id` 缓冲（最后一张到达后约 1.5s），对每个命中规则的目标用 `copy_messages` / `forward_messages` **整组**发送，不再拆成一条条
- 关键词规则对整组**按相册说明文字**判断一次（相册说明只挂在其中一张上）：命中则整组转发，不命中则整组不转
- 讨论群关键词过滤删掉相册说明那一条时，整组都不再转发；单条消息行为不变（立即 copy/forward）
- 「取消频道自动置顶」改为后台延迟取消，不再阻塞消息处理 1 秒（避免相册分片超出缓冲窗口）
- 相册缓冲与群/频道同步共用同一套防抖 helper

#### 同步相册整组投递 + 格式保留 + 可点击发送者前缀
- **相册**：同源 `media_group_id` 的多图/视频不再拆成一条条发送；短延迟缓冲后用 `copy_messages` / `sendMediaGroup` 整组投递到目标
- **格式**：无前缀时优先 `copy_message(s)` 保留原文实体；有前缀或回退时用 Telegram HTML（entities → HTML / `text_html`）
- **发送者前缀**：开启「显示发送者前缀」时渲染为可点击 `<a href="tg://user?id=…">昵称</a>`（点进私聊；受对方隐私/是否与 bot 有过互动限制）；默认仍关闭

### 🔧 优化改进 (Improved)

#### 群同步默认不加 `[发送者]` 前缀
- 群成员同步默认原文投递（与频道帖一致），避免目标侧出现「[昵称] 内容」观感怪异
- 同步设置页新增开关「显示发送者前缀」；勾选后仍可恢复 `[发送者名]` 前缀
- 字段 `include_sender_prefix`（默认 false）；`build_group_sender_prefix(..., include_prefix=False)` 默认空串

### 🐛 问题修复 (Fixed)

#### 群/频道同步实际不触发 + 同步日志为空
- **根因 1（群）**：同步设置页勾选「启用」只写 `SyncGroupMessages.enabled`，但 handler 还要求 `sync` 插件为开；群保存 API **不会**自动开插件（频道才会），插件默认关 → 消息被静默跳过、**不写任何日志**
- **根因 2（频道）**：主 Bot `set_webhook` / `start_polling` 未显式传 `allowed_updates`；Telegram 会保留**旧 webhook 过滤列表**，可能不含 `channel_post`，频道帖根本进不来
- **修复**：保存同步设置时群/频道一律镜像 `enabled` → 插件行；handler **只认** `SyncGroupMessages.enabled`（去掉双重门禁）；插件页开关 `sync` 时反向同步 settings.enabled；主 Bot webhook/polling 显式 `Update.ALL_TYPES`（含 channel_post）
- 同步设置页说明改为：启用并保存即可，无需再开插件；目标 ID 需为 `-100…` Telegram chat_id

### ✨ 新增功能 (Added)

#### 同步多目标 + getChat 显示名称
- 同一源群/频道可配置**多个**同步目标（多行 `SyncGroupMessages`，唯一约束 source+target）
- 设置页目标列表：添加 / 删除 / 刷新名称；共享选项（启用、媒体、转发、黑名单）一次保存应用到全部目标
- 新增 API：`/api/add_sync_target`、`/api/delete_sync_target`、`/api/refresh_sync_target_title`；保存选项仍走 `/api/save_sync_group_messages`
- 添加或刷新目标时调用 Telegram `getChat` 缓存 `target_title`（机器人须能访问该 chat）
- 群页与频道页 UI 分离保留；handler 本就按 `.all()` 投递，现与 UI 对齐
- 迁移：`target_title` 列 + 唯一索引

### ✨ 新增功能 (Added)

#### 频道帖同步 + 频道/群同步设置分离
- **真实频道帖同步**：新增 `handle_sync_channel_posts`，通过 `filters.UpdateType.CHANNEL_POSTS` 接收频道发帖并拷贝到配置的目标群/频道（原文同步、无 `[频道名]` 前缀）；与群成员同步共用 `SyncGroupMessages` 配置表，互不抢入口
- **频道后台 UX**：侧栏「同步消息」改为「频道帖同步」页（`/sync_channel_messages`），不再挂误导性的「同步群消息」；群组侧栏仍用原同步设置。群/频道互相访问设置页会自动 redirect
- **插件门禁**：频道保存「启用」时自动镜像 `sync` 插件开关（频道无插件页）；群组行为不变
- 抽取 `_deliver_sync_copy` 供群/频道共用发送逻辑，避免媒体类型分叉漂移

### 🐛 问题修复 (Fixed)

#### 频道帖同步去掉 `[频道名]` 前缀
- 频道帖同步投递改为原文（`build_channel_sender_prefix` 返回空串）；群成员同步仍带 `[发送者]` 前缀
- `_deliver_sync_copy` 在前缀为空时不再为 location/contact/venue 额外发空白消息

#### 跨群同步：媒体路径接通 + 插件默认关生效
- **媒体同步死代码**：此前 `handle_sync_group_messages` 仅从纯文本 `on_message` 调用，且入口 `if not msg.text: return`；现由 `on_non_text_message` 在反垃圾通过后同样调用，`sync_media` 开启时可同步图/视频/文档等
- **`sync` 插件 `default_enabled=False`**：无设置行时 `is_plugin_enabled` / `GroupPluginSettings.is_enabled` 改读注册表默认值（多数插件仍默认开，`sync` 默认关）
- **防环**：跳过 `user.is_bot` 消息；跳过源群 chat_id == 目标群 ID
- **关键词文案对齐黑名单**：模板说明改为「包含关键词则不同步」；命中时写 `SyncMessageLog.status=filtered`
- 首次打开同步页时，媒体/转发勾选与模型默认（开）一致


### 🔧 优化改进 (Improved)

#### 接线 commands.py 到生产注册（behavior-preserving）
- `run_bot` / `setup_clone_handlers` 现注册 `app.bot.handlers.commands` 中的实现
- 对齐线上行为：插件开关门禁、`_sched_del` 自动删回复、unmute 清除永久禁言标记、群管命令中「群主/管理员不可解除禁言」语义
- 从 `routes.py` 移除上述 13 个命令的内联实现，消除死 `_hcmd_*` noqa 导入；其余命令仍留在 `routes.py`

### 🐛 问题修复 (Fixed)

#### `/warn` 落库计次（Warning persistence）
- 此前 `/warn` 与关键词过滤 `action=warn` 仅发话，不落库、不计次
- 新增 `group_warnings` 表与 `warning_service`；回复中展示累计警告次数
- 同步写入 `admin_action_log`（`action_type=warn`，命令路径）
- **未做自动升级处罚**：模型/后台暂无警告阈值配置；`resolve_escalation` 为占位，避免上线后默默 mute/kick/ban

#### 备份页侧栏入口
- `/core/group/<id>/backup` 路由已存在但侧栏无链接；现于「群组功能」中「插件开关」旁增加「备份还原」

#### 抽奖 `random` 类型接入 lottery_service
- `validate_lottery_draw` / `run_lottery_draw` 支持 `random`；定时开奖路径改用 `pick_winners_random`（行为与原先 `random.sample` 等价）

#### README 失效文档链接
- 移除指向仓库中不存在的 DESIGN/FEATURES/TESTING 等文档的链接，保留 CHANGELOG

#### 插件开关真正生效（Plugin switches gate bot logic）
- 后台「插件开关」此前只写库、不拦逻辑；现已在消息入口 / 命令 / 相关定时任务接入 `is_plugin_enabled`
- 原则：无设置行或开关为开 → 行为与线上一致；仅在显式关闭时跳过对应功能（积分、抽奖、消息过滤、邀请、游戏、群管理、消息同步）

#### 关键词白名单真正生效（Keyword whitelist）
- `check_keyword_filter` 此前仅处理 `blacklist`；现支持白名单门禁
- 顺序：若存在活跃白名单规则，消息须先命中至少一条白名单，否则按该白名单规则的 action 处置；再应用黑名单命中

#### 强制订阅实时路径支持踢出/封禁（Forced subscription kick/ban）
- 实时 `check_forced_subscription` 此前仅处理 `mute`；现与定时任务一致支持 `kick` / `ban` / `mute`

## [Unreleased] - 2026-09-08

### ✨ 新增功能 (Added)

#### 认证用户 Telegram 昵称成员标签（setChatMemberTag）
- 认证用户发言后，机器人可自动点赞，并用 Telegram 官方 API `setChatMemberTag` 在**昵称后面**显示成员标签（如图中的「官方认证」「少妇」）
- 群设置新增「自动成员标签」开关与「默认标签」（默认「认证」）；单用户可在认证用户管理中覆盖（取首个自定义标签）
- 标签规则：最多 16 字、不能含 emoji；机器人需具备 `can_manage_tags` 管理权限
- `group_users` 增加 `applied_telegram_tag` 记录已同步标签，避免重复调用；删除认证用户时自动清除昵称标签
- 后台仍支持多标签存储与模板占位符 `{成员标签}`（列表/推送，旧占位符 `{标签}` 仍兼容）；导入导出 XLSX「成员标签」列（兼容旧表头「标签」）
- 认证用户编辑表单与列表中，上方「成员标签」与资料字段「标签」区分开，避免重名

### 🔧 优化改进 (Improved)

#### 成员标签模板占位符
- 列表/推送模板占位符由 `{标签}` 改为 `{成员标签}`（设置页变量按钮与认证用户表单说明同步更新；旧 `{标签}` 仍可替换）

### 🐛 问题修复 (Fixed)

#### 定时消息列表「备注」列偏高
- 根因：全局 `.remark-cell` 使用了 `display: inline-block`，却直接加在 `<td>` 上，破坏了表格单元格布局，导致备注文字比同行其他列更靠上
- 定时消息 / 自动回复列表改为在单元格内用 `<span class="remark-cell">` 做短预览；`base` 增加 `td.remark-cell` 兜底，避免再把表格单元格改成 inline-block

#### 自动点赞换图标后不生效
- 根因：Telegram `setMessageReaction` 只接受官方「消息回应」白名单表情（如 `❤`/`👍`/`🔥`），任意图标、贴纸或自定义 emoji 会被 API 拒绝；旧默认值 `❤️`（带 FE0F）与部分粘贴变体也会导致失败，且失败被静默吞掉
- 新增 `normalize_like_emoji`：自动把 `❤️` 等变体映射为 API 合法形式，并拒绝无效图标
- 保存群设置时校验点赞表情；设置页增加白名单快捷选择与说明
- `do_like` 记录 API 失败日志，便于排查

## [Unreleased] - 2026-09-06

### 🔧 优化改进 (Improved)

#### 列表统一为「定时消息」展示风格
- 列表以紧凑元数据列为主（ID / 类型 badge / 状态 / 时间 / 操作），不再整段展开正文
- 长文本（关键词、内容、详情、描述）使用与定时消息「备注」相同的短预览 + 悬停查看全文
- 名称+副信息、目标聊天等保留双行元数据栈（与定时消息「目标」列一致）
- 频道模板 / 转发规则 / 优惠券 / 统计等表格结构对齐定时消息列表
- **认证用户列表**对齐：独立 ID 列、紧凑用户名栈、资料字段短预览、过期时间 `mm-dd HH:MM:SS`、外置批量操作栏、空状态行

## [Unreleased] - 2026-01-26

### ✨ 新增功能 (Added)

#### 邀请活动群内公告功能（Invitation Activity Group Announcement）
- 新增 `announce_in_group` 字段到 `invitation_activity` 表
- 支持在群内公告邀请成功消息，提升活动参与度
- 添加了用户界面开关，管理员可以选择是否启用群内公告
- 完整的数据库迁移脚本支持，使用 `IF NOT EXISTS` 确保迁移安全
- 后端代码具有完善的错误处理，兼容尚未迁移的数据库
- 使用安全的属性访问模式（`getattr`），防止属性不存在时报错

### 🔧 优化改进 (Improved)
- 优化邀请活动设置页面，新增"群内公告邀请成功"选项
- 改进数据库迁移的容错性，自动检测列是否存在
- 增强前端表单，支持保存 `announce_in_group` 配置

## [Unreleased] - 2026-01-05

### ✨ 新增功能 (Added)

#### 积分自动回复功能（Points-based Auto-Reply）
- 实现了积分自动回复系统，用户可通过消耗积分获取高级内容
- 自动检查用户积分余额，不足时显示友好的错误提示
- 积分扣除与内容发送采用事务处理，确保数据一致性
- 完整的交易日志记录，支持审计和追溯
- 扣除积分后自动通知用户剩余余额
- 异常情况下自动回滚，防止积分丢失

#### 抽奖消息跟踪系统（Lottery Message Tracking）
- 新增 `LotteryMessageCount` 数据模型，实时跟踪用户在抽奖期间的消息数
- 支持两种抽奖类型的消息跟踪：
  - **消息计数抽奖**：根据消息数量加权随机选择获奖者（发送越多，中奖概率越高）
  - **消息排名抽奖**：根据实际消息数量选择前 N 名活跃用户
- 仅在抽奖活动期间（start_time 到 end_time）跟踪消息
- 自动更新消息计数和时间戳
- 获奖者选择基于真实活动数据，确保公平性

#### 同步消息日志功能
- 新增 `SyncMessageLog` 数据模型，用于跟踪所有同步的消息
- 创建了同步消息日志页面，支持查看历史同步记录
- 显示消息发送者、类型、内容预览、目标群组和同步状态
- 完整的分页支持（10、20、50、100 条/页）
- 空状态提示和快速配置入口

#### 用户信息查询命令
- 新增 `/userinfo` 机器人命令，管理员专用
- 支持回复或转发用户消息查看详细信息
- 显示用户基本信息：ID、用户名、姓氏、是否机器人
- 显示群组信息：个人资料、到期时间、封禁状态、签到记录
- 显示积分信息：当前积分、总获得、总消耗（使用数据库聚合优化）
- 显示 Telegram 群组权限：群主、管理员、普通成员等状态

#### 统一分页系统
- 创建了统一的分页组件 (`pagination.html`)，可在所有列表页面重复使用
- 支持更大的每页显示数量：10、20、50、**100**
- 统一的翻页按钮样式，带有现代化的渐变效果和动画
- 响应式设计，在移动设备和桌面设备上都有良好体验

#### 群管理机器人功能 (Group Management Bot)
添加了完整的群管理机器人命令，仅限群组管理员使用：

- `/kick` - 踢出群成员（可重新加入）
- `/ban` - 永久封禁群成员
- `/unban` - 解除用户封禁
- `/mute [分钟]` - 禁言用户指定时间（默认60分钟）
- `/unmute` - 解除用户禁言
- `/pin` - 置顶消息
- `/unpin` - 取消置顶（不回复消息则取消所有置顶）
- `/warn [原因]` - 警告用户
- `/userinfo` - 查看用户详细信息（新增）

所有命令都需要回复目标用户的消息使用，并自动检查操作者是否为群组管理员。

### 🎨 UI/UX 改进 (Improved)

#### 同步消息模块 UI 升级
- 同步设置页面新增"查看日志"按钮，快速访问历史记录
- 同步日志页面新增"同步设置"按钮，快速返回配置
- 侧边栏导航分离显示"同步设置"和"同步日志"
- 响应式设计优化，移动端显示更友好
- 页面头部按钮支持自动换行和间距调整

#### 移动端全面优化
- **同步消息设置页面**：
  - 响应式页面头部（1rem → 1.5rem 标题字体）
  - 优化表单控件字体大小（0.85rem 移动端）
  - 按钮尺寸自适应（0.85rem → 0.95rem）
- **同步消息日志页面**：
  - 隐藏移动端不重要列（发送者、类型）
  - 内容预览宽度自适应（150px → 300px）
  - 表格字体大小优化（0.85rem → 0.95rem）
  - 卡片圆角和阴影自适应

#### 分页 UI 升级
- **现代化按钮样式**：使用渐变色和阴影效果
- **流畅动画**：悬停时按钮会有上升动画和颜色变化
- **一致的设计语言**：所有列表页面（用户、自动回复、定时消息、启动消息、同步日志）使用统一的分页组件
- **禁用状态**：清晰的禁用状态样式，用户体验更好

#### 设置页面增强
- 新增"群管理机器人命令"板块，展示所有可用的管理命令
- 使用彩色渐变卡片展示每个命令，视觉效果更佳
- 详细说明每个命令的功能和用法
- 新增 `/userinfo` 命令文档

### 🔧 技术改进 (Changed)

#### 后端优化
- 所有分页路由支持 100 条/页的选项
- 统一的参数验证逻辑
- 更好的代码复用
- **积分计算优化**：使用 SQL 聚合函数（CASE + SUM）替代内存循环，显著提升性能
- **异常处理改进**：使用 `except Exception` 替代裸 `except`，避免捕获系统级异常

#### 数据库优化
- 新增 `SyncMessageLog` 模型，支持同步消息追踪
- 添加索引：`synced_at` 字段索引，优化时间范围查询
- 外键关系：`source_group_id` 关联 `bot_groups` 表

#### 前端优化
- 移除重复的 JavaScript 函数
- 使用 Jinja2 宏 (macro) 实现组件复用
- 减少代码冗余，提高可维护性

### 📄 文件变更 (Files Changed)

**新增文件：**
- `app/modules/core/templates/pagination.html` - 统一分页组件
- `app/modules/core/templates/sync_message_logs.html` - 同步消息日志页面

**修改文件：**
- `app/models.py` - 添加 `SyncMessageLog` 模型
- `app/modules/core/routes.py` - 添加同步日志路由、`/userinfo` 命令、积分计算优化
- `app/modules/core/templates/base.html` - 更新侧边栏导航（同步设置/日志分离）
- `app/modules/core/templates/sync_group_messages.html` - 添加"查看日志"按钮，移动端优化
- `app/modules/core/templates/settings.html` - 添加 `/userinfo` 命令文档
- `app/modules/core/templates/users.html` - 使用新的分页组件
- `app/modules/core/templates/auto_replies.html` - 使用新的分页组件
- `app/modules/core/templates/start_messages.html` - 使用新的分页组件
- `app/modules/core/templates/scheduled_messages.html` - 更新分页样式

---

## 使用说明 (Usage)

### 如何查看同步消息日志

1. 在侧边栏导航中点击"同步日志"
2. 或在"同步设置"页面点击"查看日志"按钮
3. 日志显示所有同步记录，包括：
   - 同步时间
   - 发送者信息
   - 消息类型（文本、图片、视频等）
   - 内容预览
   - 目标群组
   - 同步状态（成功、失败、过滤）

### 如何使用 /userinfo 命令

1. 确保机器人在群组中拥有管理员权限
2. **方式一**：回复目标用户的消息，然后发送 `/userinfo`
3. **方式二**：将目标用户的消息转发给机器人，然后发送 `/userinfo`
4. 机器人会返回详细的用户信息，包括：
   - 基本信息（用户名、ID、姓名）
   - 群组信息（个人资料、到期时间、封禁状态）
   - 积分信息（当前积分、总获得、总消耗）
   - 群组权限（群主、管理员、普通成员等）

### 管理员如何使用群管理命令

1. 确保机器人在群组中拥有管理员权限
2. 回复目标用户的任意消息
3. 发送相应的命令，例如：
   ```
   /kick          # 踢出用户
   /mute 30       # 禁言30分钟
   /warn 违反群规  # 警告用户
   /userinfo      # 查看用户详细信息
   ```

### 如何查看所有命令

在群组设置页面中，找到"群管理机器人命令"板块，查看所有可用命令及其详细说明。

---

## 技术细节 (Technical Details)

### 同步消息日志模型

```python
class SyncMessageLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    target_group_id = db.Column(db.String(50), nullable=False)
    source_message_id = db.Column(db.BigInteger, nullable=True)
    target_message_id = db.Column(db.BigInteger, nullable=True)
    user_id = db.Column(db.BigInteger, nullable=True)
    username = db.Column(db.String(255), nullable=True)
    message_type = db.Column(db.String(20), default='text')
    content_preview = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='success')
    error_message = db.Column(db.Text, nullable=True)
    synced_at = db.Column(db.DateTime, default=datetime.now, index=True)
```

### 积分计算优化

使用 SQL 聚合函数进行高效计算：

```python
from sqlalchemy import func, case

result = db.session.query(
    func.sum(case((PointsLog.points_change > 0, PointsLog.points_change), else_=0)).label('earned'),
    func.sum(case((PointsLog.points_change < 0, func.abs(PointsLog.points_change)), else_=0)).label('spent')
).filter(
    PointsLog.group_id == group.id,
    PointsLog.user_id == target_user.id
).first()
```

### 分页组件 API

```jinja2
{% from 'pagination.html' import pagination %}
{{ pagination(current_page, total_pages, per_page, total_items) }}
```

参数说明：
- `current_page`: 当前页码
- `total_pages`: 总页数
- `per_page`: 每页显示数量
- `total_items`: 总记录数

### 支持的每页数量
- 10 条/页
- 20 条/页（默认）
- 50 条/页
- 100 条/页（新增）

---

## 测试检查清单 (Testing Checklist)

- [x] Python 语法检查通过
- [x] Jinja2 模板语法验证通过
- [x] 所有列表页面使用统一分页组件
- [x] 分页按钮样式统一
- [x] 响应式设计在移动端正常工作
- [x] 群管理命令已注册
- [x] 管理员权限检查逻辑正确
- [ ] 在真实群组环境中测试管理命令
- [ ] 在真实环境中测试分页功能
- [ ] 移动设备浏览器测试

---

## 后续优化建议 (Future Improvements)

1. **性能优化**
   - 考虑为大数据量添加缓存机制
   - 优化数据库查询

2. **功能增强**
   - 添加批量操作功能
   - 添加操作日志记录
   - 群管理命令添加权限配置

3. **用户体验**
   - 添加键盘快捷键支持
   - 实现无刷新分页（AJAX）
   - 添加页面跳转输入框

4. **移动端优化**
   - 考虑添加下拉刷新
   - 优化触摸操作体验
   - PWA 支持
