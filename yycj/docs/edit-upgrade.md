# 月影车姬：资料修改第一批（待评审，不自动上线）

基线 main `62f356f`。不包含审计历史/版本恢复，不进行历史补推，不修改续期规则。

## 实际行为
- 老师仍上架且未过期：文字立即保存，同步现有频道消息；不新发、不续期。上传图片/视频是**整组替换申请**；不上传保留当前媒体（在锁内读取，避免旧快照回写）。原媒体与上架状态在待审/拒绝时不变。
- 媒体审批只使用媒体提案，不应用旧文字。事务内锁定 Lamp/Post，检查媒体修订号和状态，并原子保存审核决定、媒体与同步检查点。随后使用最新文字/本城模板完整新发频道帖，更新新引用，再发送现有成功通知/按钮。不删除旧帖。
- 非上架/已过期：整份修改待审；通过只更新资料，保留状态和到期日。状态/有效上架性改变、修订号过期时使申请失效，不恢复上架。
- 管理员在授权原城市和目标城市内仍免审。上架媒体替换立即生效并新发/通知；非上架只保存。管理员替换媒体使旧待审申请失效。旧版整份待审快照禁止直接批准，需拒绝后重新提交。
- 资料保存、频道同步、媒体审核、通知各自持久化。只有明确失败/未配置/发送前中断的待处理步骤可单独重试；成功步骤不重做、不重新保存/续期。刷新只读。较早的失败步骤/待审申请在后续文字修改后仍能从待处理列表查看。
- 新帖/私信超时或发送确认后保存引用失败属于不确定送达；检查点仍 sending/unknown 时禁止盲目重发，需要人工核对。Telegram 无 exactly-once 保证。
- 跨进程每条资料 advisory lock 覆盖编辑、审核、重试、手动上下架和续期入口；事务行锁/修订号及唯一待审索引形成额外保护。拒绝旧审批和不可重试状态不会泄漏内部错误。

## 验证与边界
使用 box 独立 PostgreSQL16.6（127.0.0.1:55441，yycj_edit_test），非生产数据/凭证。测试中禁用真实 aiogram 请求；所有发送由 FakeBot/AsyncMock 替代。新增22个真实 PG 测试覆盖并发提交/审批、approve-reject竞争、事务回滚、取消与锁释放、已确认发送后的检查点中断、权限、失效、实际新引用保存，以及失败步骤重试；10个适配器单测覆盖媒体种类、原帖编辑、失败分类及通知参数。

```sh
YYCJ_TEST_RESET_PG=1 YYCJ_TEST_PG=postgresql://yycj_test@127.0.0.1:55441/yycj_edit_test DATABASE_URL=postgresql://yycj_test@127.0.0.1:55441/yycj_edit_test /workspace/venv-yycj/bin/pytest -q --disable-warnings
NODE_PATH=/workspace/yycj-admin-ship/tests/node_modules PATH=/workspace/venv-yycj/bin:$PATH npm --prefix tests run test:ui
python3 scripts/build_miniapp.py --check
```

`YYCJ_TEST_RESET_PG=1` 仅允许精确的上述本地临时库，并在每个 PG 测试前清理测试数据。新 schema 为两个默认0的修订号列、操作检查点表、条件唯一索引；无 DROP。

## 实际 UI 评审包（非截图）
运行 `python scripts/export_edit_review.py --out /workspace/yycj-edit-upgrade-review`，打开输出目录 `OPEN-FIRST.html`。4个页面由实际 go/desk HTML 和实际 CSS/JS 内联生成，只替换浏览器内存 API/登录；明确示例数据、CSP禁止外网，不需要服务器或生产账号。老师建议390×844，后台1440×1000。Node/jsdom已执行这些资产做DOM检查，但不是浏览器视觉验收，file://渲染仍须人工/桌面评审。预览脚本与示例不进入生产 miniapp 目录或 bundle。

版本只更新已修改的 guest/admin 资产为 `20261110g`。首页卡片117×224、三列、分页、墨绿金配色及地图/弹窗/续期既有功能不改。当前PR应保持draft/unmerged，等待评审和另一次明确上线授权。
