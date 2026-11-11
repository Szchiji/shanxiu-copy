# 整体管理后台布局评审（未上线）

基线：`origin/main` `18fba649f010938302af42f4e6671c24ce26651f`（已合并的 PR #62）。  
工作树：`/workspace/yycj-admin-layout-v2`，分支 `feat/admin-workbench-layout`。  
这是对真实六页运营工作台的整体布局升级，不是新信息架构，也不是只改上架资料卡片。未推送、未开 PR、未部署。

用户 2026-11-11（Asia/Shanghai）提出「升级优化整体管理后台布局」。此前 PR #59 建立六页工作台，PR #60 做了密度与分组，PR #62 修好了上架资料卡片；本批在此之上收紧整台 chrome，并修掉 PR #62 留下的「更多」菜单盖住标题/主按钮的问题。

## 原则与边界

保留：六页导航与页内分组；PR #60 桌面双列配置、手机单列与底部保存条；PR #62 卡片行为（成功状态隐藏、失败可重试、已上架「重新上架」在更多、已下架主按钮、44px、续期不截断、列表单列）；深蓝工作台配色。  
未改：访客首页（117×224、三列、墨绿金）、钱包/会员/预约金、API 与业务规则、j4 历史恢复、频道补发、Flask 重写。

资源版本（仅本分支）：`workspace.css` / `workspace.js` / `admin.css` → `20261111g`。`desk.html` / `console.html` / `desk-legacy.html` 已对上。访客 `app.bundle` 未动，仍为 `20261111f`。

## 各页改动摘要

| 页面 | 改了什么 | 为何 |
|---|---|---|
| 全局 | 顶栏 56px；页标题与说明更紧；页签在顶栏下吸顶；卡片与指标留白略减。手机仍单列、44px、底部保存条可用。 | 少滚一层壳子才能看到真正的列表/编辑器/日志。 |
| 总览 | 共用更紧的页头与指标间距；四指标 +「需要你决定的事」布局保留。 | 共享 chrome；本页原已较清晰，不做新架构。 |
| 审核与上架 · 上架资料 | 「资料状态」不再单独占一张大卡片，与搜索/筛选合成一条工具栏。「代上架 / 修改已选」默认收起，桌面并排成一行短摘要。PR #62 卡片行为不变。 | 用户刚在这页上花了很多时间；列表应更快出现。功能都还在。 |
| 审核与上架 · 媒体/待审 | 队列搜索条从厚卡片收成一条紧凑工具栏。 | 同样减少进队前的垂直占用。 |
| 审核与上架 · 卡片「更多」 | 菜单改为在整行主按钮下方展开（通栏），打开时卡片加高，不再盖住标题或「修改资料 / 续期 / 下架」。 | 修 PR #62 遗留的遮挡。 |
| 消息与按钮 | 共用更紧页头与吸顶页签；桌面仍是编辑 + 右侧预览，手机仍单列 + 底部保存条。 | 共享 chrome；编辑器结构保持 PR #60 compact。 |
| 站点与城市配置 | 共用 chrome；桌面仍双列分组（社区入口 / 位置入口等），复杂 JSON 仍按需展开。 | 不把手机改成双列，不打散现有字段分组。 |
| 执行日志 | 共用更紧页头；筛选仍是 PR #60 的一行主筛 + 时间范围默认收起。 | 本页已较紧凑，只吃到全局留白收紧。 |
| 系统与权限 | 共用 chrome；权限页仍是名单 + 边界说明的左右栏。 | 无新页、不改授权模型。 |

## 离线评审与截图

```bash
/workspace/venv-yycj/bin/python /workspace/yycj-admin-layout-v2/scripts/export_workspace_review.py --output /workspace/yycj-admin-layout-v2-review
```

入口：`/workspace/yycj-admin-layout-v2-review/review-index.html`。HTML 内嵌当前分支真实 CSS/JS，数据为标明的内存示例，CSP 禁止外连。截图由本机 headless Chrome 对上述静态页拍摄（390×844 与 1280×900），**不是生产环境截图**。页顶棕色条是离线评审横幅，线上没有。

### 截图绝对路径

- `/workspace/yycj-admin-layout-v2-review/desktop-overview.png`、`mobile-overview.png` — 总览
- `/workspace/yycj-admin-layout-v2-review/desktop-listings.png`、`mobile-listings.png` — 上架资料：紧凑工具栏 + 收起的代上架/修改 + 成功态卡片
- `/workspace/yycj-admin-layout-v2-review/desktop-listings-more.png`、`mobile-listings-more.png`、`mobile-listings-more-card.png` — 「更多」在主按钮下方通栏展开，不挡标题
- `/workspace/yycj-admin-layout-v2-review/mobile-listings-failure-card.png` — 失败/可重试状态仍可见（示例资料 C）
- `/workspace/yycj-admin-layout-v2-review/desktop-review-media.png`、`mobile-review-media.png` — 媒体审核队列
- `/workspace/yycj-admin-layout-v2-review/desktop-messages.png`、`mobile-messages.png` — 消息与按钮（手机可见底部保存条）
- `/workspace/yycj-admin-layout-v2-review/desktop-community.png`、`mobile-community.png` — 站点与城市配置 · 本城社区
- `/workspace/yycj-admin-layout-v2-review/desktop-channel.png` — 频道帖文案（桌面分组）
- `/workspace/yycj-admin-layout-v2-review/desktop-logs.png`、`mobile-logs.png` — 执行日志
- `/workspace/yycj-admin-layout-v2-review/desktop-permissions.png`、`mobile-permissions.png` — 系统与权限

实测（headless Chrome）：桌面上架资料第一张卡片约在 y=414；「更多」菜单与标题/主按钮无重叠，宽度等于操作行。页签 `position: sticky`。

## 验证

在 `/workspace/yycj-admin-layout-v2` 中：

- `node --check miniapp/workspace.js`
- `node tests/listing_layout_dom.cjs`（成功态仍隐藏、失败态可见、更多菜单向下展开）
- `node tests/edit_ui_dom.cjs`、`tests/relist_ui_dom.cjs`
- `PATH=/workspace/venv-yycj/bin:$PATH node tests/workspace_dom.cjs`（9 个集成场景，含工具栏与编辑器并排）
- `pytest tests/test_renewal_unit.py::test_production_preview_is_unreachable_and_versions_menu_are_consistent`（1 passed）

未跑完整 PostgreSQL 回归（只改工作台展示与资源版本，不改 API）。未推送、未开 PR、未部署、未发送 Telegram。
