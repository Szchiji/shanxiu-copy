# 2026-11-10 后续发布范围

用户 t137 批准 Error 修复 + 首页位置 + 独立弹窗直接上线，免截图评审。t138 随后明确要求**未来**管理员续期成功后重新推送到频道最新，并发送老师上架成功通知。

- Review 的 post_chat/post_mid 移回 Review，匹配旧有 reviews 增量迁移；无 DROP/改写旧数据。
- 首页社区左上角、地图搜索后、轮播搜索前；卡片尺寸/三列/分页不改。
- 后台「站点设置 → 全站默认/城市 → 首页展示」：独立弹窗开关/标题/内容。默认关闭、内容空；不填示例到生产。关闭本次/按上海日历日今日隐藏。
- 未来续期专用 force_new=True，普通广播去重不改。新帖仍使用当前城市模板、原媒体、文字深链接，无频道内联按钮，不删除历史帖。新帖引用落库后才发送老师通知，复用已审核上架通知及当前城市配置、Mini App/频道按钮，指向新帖。
- 新增 listing_renewals 表，Base.metadata.create_all 仅做新增表。operation_id 主键持久防重，归属管理员/资料/天数；每次外部发送前落检查点，同操作重试不重复续期/频道发送/老师通知。Telegram 无发送幂等 API：超时或进程中断可能无法判定是否送达，此时**不自动重试**，后台明确提示部分成功/结果不明，需人工核对，不能保证外部服务意义的 exactly-once。
- 老后台缓存页没有操作编号会先返回“请刷新后台”，不执行续期。新版保存操作编号至 sessionStorage，网络错误/部分失败保留同编号；刷新只 GET，不做续期。正常完成后再点续期是新的一次明确操作。
- 不历史补发、不触发测试续期，不发送任何手工/测试 Telegram 消息。数据库依赖集成测试在本地无PG而跳过，自动发送仅用 mocks 验证。
- Preview helper仅在 scripts/ 下，生产只挂载 miniapp/；生产 go/desk/bundle没有fixture注入，/preview-fixtures.js 与 /scripts/... 均404。示例不进入真实API/auth/配置。
- 缓存：guest bundle JS/CSS、admin-city.js=20261110e；admin.js=20261110d；admin-list.js/admin-ops.js=20261110f。go.html 的版本解析与 首页 MenuButton URL保持工作。
