# 上架资料卡片布局评审（未上线）

基线：`origin/main` `ecce4e38fe5805035333b8b68307db0b91eb8d22`（含已合并的 PR61）。  
工作树：`/workspace/yycj-listing-layout`，分支 `feat/listing-card-layout`。  
这是对真实工作台「审核与上架 → 上架资料」列表卡片的局部布局实现，不是新业务功能。未推送、未开 PR、未部署。

对照用户反馈（Asia/Shanghai）：

1. 20:31 实机截图：小红「本次修改状态」被挤成 1–2 字竖排窄栏；主操作行把「修改资料 / 续期… / 重新上架 / 下架」挤在同一行，容易误点。已上架再点「重新上架」出现「资料已经上架；未重发频道或通知，有效期不变。」——**该提示文案是预期行为，未改**。
2. 看过第一轮评审截图后：「这样太占地方了  可不可以取消或者隐藏」。针对的是成功修改后仍钉在卡片上的大块「本次修改状态」（文字已保存 / 频道已同步 / 无需老师通知 / 查看本次频道帖 / 长段重试说明）。

## 卡片状态显隐规则

共享渲染在 `miniapp/edit-state-ui.js` 的 `__yycjEditUI.html(op, scope, opts)`。列表卡片（工作台 `admin-ops.js`、老师 `me-listings.js`、以及 `admin-kit.js` / `admin-refresh.js`）改为 `html(..., {card:true})`。发布确认页、代改确认、审核抽屉等仍走完整 `fullHtml`，成功细节和「查看本次频道帖」只留在那些页面。

「需要处理」（`actionable`）当且仅当本操作或任一未完成子项满足：

- `channel_retryable` / `notification_retryable` / `superseded`
- `review_status` ∈ {`pending`, `rejected`, `stale`}
- `media_status` === `pending`
- `channel_status` 或 `notification_status` ∈ {`pending`, `sending`, `failed`, `unknown`}

据此：

| 场景 | 卡片上怎么显示 |
|---|---|
| 安静成功（如文字已保存、审核 none、频道已同步、通知无需/已跳过、无重试标记、无待处理子项） | **不渲染** `.edit-result`。小红与小明一样紧凑：标题、meta、主操作行。刷新后也不会再占地方。卡片上不放「查看本次频道帖」，也不放「重试不重新保存资料…」说明。 |
| 失败 / 结果不明 / 需要重试 / 审核或新媒体待处理 | 通栏紧凑 `.edit-result.edit-result-card`，标题为「修改需要处理」或「重新上架需要处理」，只列问题行和重试按钮。长段重试说明收进 `<details>说明</details>`。成功重试后横幅按同一规则消失。 |

未做常驻 toast：成功状态直接从卡片上拿走，避免刷新后仍膨胀。

## 一并保留的布局修改

| 问题 | 实际修改 |
|---|---|
| 状态文字 1–2 字竖排 | `#adminListings` 卡片纵向 flex；失败状态 100% 宽、`overflow-wrap: break-word`。 |
| 续期被写成「续期…」，四键同行易误点 | 主操作：「修改资料 / 续期 / 下架（已上架）或重新上架（已下架）/ 更多」；触控高度 44px。 |
| 已上架把「重新上架」放在「下架」旁边 | 「重新上架」在「更多」里。已下架仍把「重新上架」作为主按钮。 |
| 列表双列把操作行挤换行 | 上架资料列表单列，去掉与页标题重复的 pane `h3`。 |

未改：续期 / 下架 / 重新上架 / 删除的 API、已上架再点重新上架的提示原文、有效期规则、频道或老师通知发送、`edit_state` / `relist_state` 服务端持久化、Telegram。

资源版本：布局样式仍为 `admin.css` / `workspace.css` `20261111e`；本次状态显隐改到 `edit-state-ui.js` / `edit-state-ui.css`、`admin-ops.js`、`admin-kit.js`、`admin-refresh.js`、`me-listings.js` 与访客 `app.bundle.js` / `app.bundle.css`，统一 `20261111f`。`desk.html` / `console.html` / `desk-legacy.html` / `go.html` / `index.html` 已对上。

## 离线评审与截图

```bash
/workspace/venv-yycj/bin/python /workspace/yycj-listing-layout/scripts/export_listing_layout_review.py --output /workspace/yycj-listing-layout-review
```

入口：`/workspace/yycj-listing-layout-review/review-index.html`。HTML 内嵌当前分支真实 CSS/JS，数据为标明的内存示例（小明已上架、小红已上架且文字修改已全部成功、小兰频道同步失败可重试、小青自己下架），CSP 禁止外连。截图由本机 headless Chrome 对上述静态页拍摄，**不是生产环境截图**。

### 截图绝对路径

- `/workspace/yycj-listing-layout-review/mobile-list-top.png` — 390×844，列表顶部（小明紧凑主操作行）
- `/workspace/yycj-listing-layout-review/mobile-listings.png` — 390×844，成功修改后的小红与小明同样紧凑，下方可见小兰失败状态
- `/workspace/yycj-listing-layout-review/mobile-listings-card.png` — 小红卡片裁切：无大块「本次修改状态」
- `/workspace/yycj-listing-layout-review/mobile-xiaoming-card.png` — 小明卡片裁切，高度与小红相同（手机上均为 175px）
- `/workspace/yycj-listing-layout-review/mobile-failure.png` — 390×844，小兰频道同步失败、可重试
- `/workspace/yycj-listing-layout-review/mobile-failure-card.png` — 小兰卡片裁切：通栏「修改需要处理」+「仅重试频道同步」，说明默认收起
- `/workspace/yycj-listing-layout-review/mobile-more.png` — 已上架小红「更多」展开，内含重新上架 / 删除
- `/workspace/yycj-listing-layout-review/mobile-more-card.png` — 同上卡片裁切
- `/workspace/yycj-listing-layout-review/mobile-relist-alert.png` — 已上架小红经「更多」点重新上架，提示原文未改
- `/workspace/yycj-listing-layout-review/desktop-listings.png` — 1280×900，桌面紧凑单列（小明 / 小红无状态横幅）
- `/workspace/yycj-listing-layout-review/desktop-listings-card.png` — 桌面小红卡片裁切

### 实测（headless Chrome，390 与 1280 视口）

- 成功态小红没有 `.edit-result`，卡片高度与小明相同：手机 175px，桌面 121px。
- 失败态小兰 `.edit-result-card` 约占卡片宽度 94%（手机 340/362 px）/ 98%（桌面 1014/1036 px），含「频道同步明确失败」和重试按钮，无「查看本次频道帖」；长说明在「说明」里默认收起。
- 主操作按钮高度均为 44px；「续期」完整显示。
- 已上架小红主行没有「重新上架」（在「更多」里）；已下架小青主行有「重新上架」。
- 提示文案精确为：`资料已经上架；未重发频道或通知，有效期不变。`

详细数字见 `REVIEW-MEASUREMENTS.json`。

## 验证

在 `/workspace/yycj-listing-layout` 中：

- `node --check miniapp/edit-state-ui.js miniapp/admin-ops.js miniapp/me-listings.js`
- `node tests/listing_layout_dom.cjs`（成功态不占卡片空间；失败态可见且可重试）
- `node tests/edit_ui_dom.cjs`（卡片成功 html 为空；完整确认页仍有「文字已保存」「查看本次频道帖」）
- `node tests/relist_ui_dom.cjs`
- `PATH=/workspace/venv-yycj/bin:$PATH node tests/workspace_dom.cjs`（9 个原集成场景）
- `python scripts/build_miniapp.py --check`
- `pytest tests/test_listing_layout_export.py tests/test_renewal_unit.py::test_production_preview_is_unreachable_and_versions_menu_are_consistent tests/test_miniapp_revise_unit.py::test_assets_built_and_versioned`（4 passed）
- `git diff --check`

未跑完整 PostgreSQL 回归（只改列表展示，不改 API）。未推送、未开 PR、未部署、未发送 Telegram。
