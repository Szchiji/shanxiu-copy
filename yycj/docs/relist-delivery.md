# 重新上架交付与频道链接间隔修复

本批基于main41ba244，同时包含PR61批准的Unicode NBSP修复。只影响之后的合法重新上架/消息渲染；不补发历史，不删除旧帖，不批量同步。

## 规则

- 老师必须本人、当前hidden且下架原因teacher；管理员仍须城市授权，当前hidden。pending/rejected/gray不能绕过审核。过期仍须续期。
- `hidden -> active`及新操作记录在同一事务提交；绝不修改expires_at。目录仍用原有按城防抖钩子。
- 已active的新点击不发频道、不发私信；相同操作ID只返回检查点。再次合法下架后重新上架是新转换、新操作。
- 两入口都提交operation_id与expected_updated_at。缺失/过时请求明确提示刷新，防止迟到请求跨越后续下架周期恢复资料。
- 频道使用与媒体批准相同的明确新帖交付路径：当前城市模板、完整当前媒体、正文深链。发送确认后保存并复核新引用，才发送老师成功私信。私信使用既有城市继承开关、按钮和新频道相册链接。普通broadcast dedupe不改。
- “状态恢复”“频道”“通知”分步反馈，刷新仅读取持久结果；不以HTTP200代表频道成功。

## 重试与并发

`listing_relist_operations`仅为运行检查点（不是版本历史或恢复功能）。原有跨进程PG advisory listing_lock + 行锁串行化恢复、下架和重试。

每个外部发送前持久记录sending并关闭重试；Telegram明确拒绝可人工仅重试失败步骤；未配置频道可配置后仅重试频道。网络不确定、已确认但新引用/检查点落库失败、进程在sending中断均不得盲目重发。通知重试不重复频道，不再修改资料/期限。

下架、新恢复周期、城市/有效期变化或频道引用被之后操作替代，会使旧重试失效。通知重试继续检查当前开关；关闭则skipped。无后台自动历史重试/补发任务。

## API/UI/日志

- POST /api/me/listings/{id}/relist、/api/admin/listings/{id}/relist：版本化幂等请求，返回operation/message。
- GET原有我的/管理资料列表：增加relist_state及老师列表updated_at。
- POST /api/{me|admin}/relist-operations/{operation_id}/retry：`{step:"channel"|"notification"}`；本人/授权城市校验在服务端。
- 执行日志模块relist；重试仍由持久业务状态授权，不信任日志按钮。已有未知送达记录不会因此获得重发入口。
- 老师我的/后台资料卡显示重新上架交付状态，按钮仅针对明确安全步骤。旧打开页面缺少新版请求字段将提示刷新而不执行。

## 合批NBSP

LINK_SEP_HTML使用实际U+00A0（`\u00a0·\u00a0`），不再发送Telegram HTML不支持的`&nbsp;`；后台预览走同一分隔。新发/之后正常更新的帖才采用新渲染，旧帖不自动改。

## 部署与回退

唯一DB新增为空的listing_relist_operations表及索引，由既有Base.metadata.create_all创建；不改旧表数据，不删表/列。回退前版PR60部署9225fcf9-91c3-483e-ad2a-a6e72d9893b0时保留此新表，不做破坏性DB回滚。

必要JS版本20261111d：共享交付状态、老师我的（重建app.bundle.js）、admin-list/admin-ops/workspace；guest CSS/布局和未改资源不升版。Telegram首页菜单按go.html现有机制自动使用新JS版本。

测试只用独立PG16.6 127.0.0.1:55441和mock Telegram。生产仅health/readiness、静态字节/版本、匿名鉴权及启动日志验证；不真实点击上架/续期/审批，不发测试消息。没有浏览器像素/真机E2E。

## 本批验证证据

- 独立PostgreSQL16.6全套：281 passed，0 skipped；包括恢复事务回滚、跨请求并发、重复操作/重试、跨周期迟到请求、城市/本人授权、过期/审核限制、频道与通知明确失败/未知送达/中断及陈旧引用。
- 全部5组DOM测试通过（含workspace 9项集成场景及新relist组）；真实HTML/JS，非浏览器像素验收。
- build_miniapp --check、Python编译、JS语法、git diff --check通过；go/index的app.bundle.js版本一致，未改变guest CSS/布局。
- 上线后只读验证结果另存发布证据，不将运行日志或业务数据提交仓库。
