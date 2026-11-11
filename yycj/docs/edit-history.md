# 月影车姬：修改记录与恢复上一版（待评审，不自动上线）

基线 main `9c67e2e`（PR #63）。不包含 Telegram 原生增强，不补造上线前历史，不自动恢复，不发布。

## 实际行为
- 老师只能查看和恢复自己的资料；管理员仍按现有城市权限。
- 只在现有修改流程真正写入可编辑字段后记录版本。第一次被记录的修改会先保存当时的资料原文作为起点，这是当时可证明的状态，不是对更早改动的补造。上线前没有记录的版本不会出现，也不能恢复。
- 记录内容仅限可编辑字段：城市、称呼、标签、价位、介绍、区域、大致位置、自定义栏、营业时间、图片/视频引用。不记录也不允许回滚上架状态、有效期、下架原因、已停用的信用/统计列、置顶、权限、频道发布引用、精确定位坐标或营业确认（今日开课/休息）等系统字段。
- 列表展示修改时间、可证明的来源/操作者、变更字段和是否仍可恢复。老师侧管理员只显示“管理员”，不编造姓名；管理员可见已记录的管理员数字 ID。
- 恢复必须先预览并明确勾选确认。恢复作为一次新的修改，走同一套校验、权限、审核、频道同步和通知重试，不续期、不自动上架。
- 文字恢复遵循现有文字规则（仍上架则立即保存并同步文字；未上架须整份审核）。媒体恢复仍须现有媒体审核，不绕过。若旧媒体既不在当前资料中，本机缓存也读不到，则媒体恢复明确失败，不会清空现有媒体；仍可仅恢复文字和营业时间。
- 使用现有资料锁、操作编号和 `updated_at` 校验，防止旧预览覆盖新内容、重复提交和越权。

## Schema
新增表 `listing_edit_versions`（CREATE TABLE / 唯一序号索引，无 DROP、无改现有列语义）。启动时 `create_all` 与 `_ALTER_STATEMENTS` 均可幂等补齐。硬删除资料时一并删除该资料的版本和修改检查点。旧数据无需回填，兼容现有修改/审核/重试行。

## 媒体限制（有意收窄）
现有媒体以 Telegram `file_id` 为主，本仓库没有可安全、离线求证历史文件仍有效的存储。因此完整媒体恢复不能被宣称可用：仅当该文件仍是当前资料媒体，或 `/tmp/yycj-media` 缓存中仍有文件时才允许恢复媒体。上线前需决定是否接受这一限制，或另做可审计的媒体持久化后再扩大范围。

## 验证
使用 box 独立 PostgreSQL（127.0.0.1:55441，`yycj_edit_test`），禁止真实 Telegram 与生产库。

```sh
YYCJ_TEST_RESET_PG=1 YYCJ_TEST_PG=postgresql://yycj_test@127.0.0.1:55441/yycj_edit_test DATABASE_URL=postgresql://yycj_test@127.0.0.1:55441/yycj_edit_test /workspace/venv-yycj/bin/pytest -q --disable-warnings tests/test_listing_history_unit.py tests/test_pg_edit_history.py
NODE_PATH=/workspace/yycj-admin-ship/tests/node_modules PATH=/workspace/venv-yycj/bin:$PATH npm --prefix tests run test:ui
python3 scripts/build_miniapp.py --check
```

## UI
入口放在老师「我的上架」和后台资料卡的「更多」中，不写入首页三列卡片，也不在资料卡主区域展开记录列表。桌面/手机操作目标 44px，配色保持墨绿金。缓存版本仅更新改动资产为 `20261111h`。
