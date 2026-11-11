# 后台红色 Error：只读核对结果（未部署修复）

线上版本 #55 / 79c45fe。Railway web production 只读日志明确记录：
- 2026-11-10 18:09:32 Asia/Shanghai：GET `/api/admin/posts/pending?city=深圳` → 500；`asyncpg.exceptions.UndefinedColumnError: column posts.post_chat does not exist`。
- 18:09:50 Asia/Shanghai：广州待审请求同样500；不是深圳公告/社区配置错误。
- 18:09:36—18:10:50 Asia/Shanghai：独立的历史评价同步异常 `AttributeError: 'Review' object has no attribute 'post_mid'`。

当前 main 的 `bot/models.py:198–200` 把精选评价频道帖 `post_chat/post_mid` 映射到了 Post；Review（261行起）却没有这两个属性。`bot/db.py:75–76` 的旧表增量迁移正确添加到了 reviews，而非 posts。

因此已有 posts 表缺少错误映射列，`admin_ops.list_pending_posts()` 的 `select(Post)` 查询失败；`label_sync._review_counts()`（303行）读 Review.post_mid 也失败。这是同一模型归属错误的两种表现，不应给 posts 新补无关列。

前端 `miniapp/admin.js:78–85` 用 Promise.all 刷新全部后台数据；待审请求失败使整次刷新 reject。41–49行在非JSON500且空 statusText 时构造没有消息的 Error；catch 用 e.message || String(e)，最终状态文字为红色字面量“Error”。

此分支把两列从 Post 移到 Review，匹配现有 reviews 增量迁移；不删除表列/数据，不执行生产迁移。前端错误兜底包含失败路径和 HTTP 状态，避免再次显示无意义 Error。
