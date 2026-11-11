# 消息配置与执行记录（PR58，待审核，未上线）

沿用 FastAPI + aiogram + SQLAlchemy，静态后台共用编辑组件；不引入 Flask/Jinja，不新增通用定时消息或历史版本恢复。

## 按钮与开关
- 入口：站点设置 → 规则与通知。欢迎消息仅全站/超管；老师成功通知支持全站默认与城市继承，城市后台沿用现有城市权限。
- 欢迎 `bot_welcome_buttons` 保留 list，单项 `{type: webapp|url,label,target,enabled?,new_row?}`。原有数据保持顺序和每行两个的效果；新编辑可增删/上下移动、启停、另起一行，每行最多4个。欢迎最多12项，成功通知最多16项。
- WebApp 仅已知 home/me，成功通知另支持 detail；URL 仅 HTTPS 或 t.me；成功通知可用 `{album}`/`{detail}` 动态目标，无目标隐藏。没有任意 callback 或服务端命令。WebApp 按钮仅私聊，频道仍仅正文文字链接，常驻首页菜单不变。
- 成功通知 `approval_buttons={mode:default|custom,items:[]}`。缺省/default 保留 `approve_attach_detail`、`approve_attach_channel_btn` 原行为；custom 空列表＝无按钮，**不等于关闭消息**。城市 null＝继承全站。空频道链接标签＝隐藏，语义不变。
- `bot_welcome_enabled`、`approval_notify_enabled` 默认 true 保持原行为，在执行服务判断。后者统一覆盖上架审核、续期和媒体批准成功通知；关闭记录 skipped，不影响频道续期重发，也不能通过失败重试绕过开关。拒绝通知保持独立原行为。欢迎配置读取失败时不发送，不绕过可能已关闭的开关；欢迎开关不屏蔽详情深链、入群验证或管理员独立入口。
- 现有社区、弹窗、优惠、评价频道推送、目录开关不重复建库，由 feature_registry 提供字段/权限/生效时点元数据。
- 预览调用真实 Python 校验/排布器，使用示例目标，不发送。离线评审包的 API 为明确标注的浏览器内示例；不是线上发送验证。

## 生效与记录
配置仍存 SiteSettings JSON/城市覆写；新增配置版本与可用时的修改人，保存沿用行锁。消息配置只影响后续执行，不改历史消息。小程序已有10/60秒客户端缓存仍在；频道标签同步仍约30秒合并；目录仍下次北京时间12:00，不宣称所有配置立即生效。

后台新导航“执行日志”，城市/功能/状态/北京时间范围/资料/操作筛选与分页。后端列表、详情、重试都校验城市；全站/私聊欢迎日志仅超管（包括旧的未分配城市运营也不可见）。
覆盖：欢迎发送/跳过，老师成功通知，新增上架/续期频道推送，资料修改频道/通知步骤，频道链接同步汇总。**优惠、精选评价、目录的详细执行尚未接入此中心，继续看运行日志**。不伪造上线前记录。

记录 operation/record ID、模块、步骤、城市、资料、可用的 actor/config_revision、起止时间、状态、标准原因码/安全说明。不是完整配置审计或历史版本。禁止记录/返回消息正文、原始 Telegram 请求/异常文本、token/initData/session/密码；错误只按安全分类展示。日志写入故障不把已发送结果改为发送失败。

仅以下安全业务适配允许手动重试：PR58 edit 的明确失败步骤、续期明确失败的老师通知、标签同步已知失败的原帖编辑。每次重查持久化状态与权限；资料锁和发送检查点防双击；通知重试不重发频道、不重保存或延长期限。unknown/sending/成功/开关跳过不可通用重发。其他无可靠检查点的日志不提供重试。

进程中断于 Telegram 发送后、确认前仍可能处于 sending/unknown：禁止盲目再发，需人工核对，不声称 Telegram exactly-once。

## 验证与评审
测试仅独立本地 PostgreSQL16.6（127.0.0.1:55441/yycj_edit_test），所有 Telegram 调用 mock，未连接生产数据库/修改生产设置/触发真实续期。
离线评审导出：`/workspace/venv-yycj/bin/python scripts/export_edit_review.py --out /workspace/yycj-edit-upgrade-review`。产物不属于线上路由或生产 bundle，CSP 禁止网络；六个页面采用真实项目 HTML/CSS/JS，标明示例数据。
