# t134 视觉 / 独立弹窗：未上线的真实 HTML 预览

初始 t134 评审记录。随后 t137 明确批准无需截图直接发布，t138 又明确要求未来续期重新发频道并通知老师；最终发布范围以 PR 与下述发布说明为准。红色 Error 根因独立在 PR #56。

## 启动

```sh
cd /workspace/yycj-followup-review
/workspace/venv-yycj/bin/python scripts/preview_review.py --port 8874
```

只绑定 `127.0.0.1`，没有 DB 或生产凭证。服务真实 `miniapp/go.html` / `desk.html` 与实际修改后的 CSS/JS/bundle，不是另一套原型。仅预览响应里注入 `scripts/preview/fixtures.js`；生产页面/bundle不包含此文件。预览移除真实 Telegram SDK，以本地安全 stub 代替；所有 API 均为浏览器内存 fixtures，外网 fetch/跳转、续期/发帖/上传等操作被阻断，服务端 `/api/*` 也一律403。后台设置保存只改变当前标签页的示例内存，刷新重置。

## 截图 URL

- 手机首页（建议浏览器 viewport **390×844**，100% zoom）：
  http://127.0.0.1:8874/app/go.html?preview=home
- 手机进入弹窗（同视口；fresh=1 清除本地示例的今日隐藏，便于重复评审）：
  http://127.0.0.1:8874/app/go.html?preview=popup&fresh=1
- 深圳后台弹窗设置（建议 viewport **1440×1000**）：
  http://127.0.0.1:8874/app/desk.html?preview=admin&city=%E6%B7%B1%E5%9C%B3&section=popup
- 全站后台弹窗设置（同视口）：
  http://127.0.0.1:8874/app/desk.html?preview=admin&scope=global&section=popup

预览 bootstrap 自动导航到站点设置/城市，展开真实「首页展示」组并滚到弹窗设置。完整站点设置请去掉 `section=popup`。若自动定位前截屏，请等待约2秒。

手机首页 `preview=home` 不返回弹窗数据，无需任何清存储步骤。弹窗点「关闭」只关本次进入；点「今日不再弹出」后，重新打开不带 `fresh=1` 的 popup URL 可验证当天抑制。真实功能按 Asia/Shanghai 日历日、用户及城市作用域记录；同一文档内部导航不重复弹。

## 示例数据

12张「示例资料 A–L」，4个精选轮播卡；SVG 抽象占位图清楚标示「评审占位图 · 非真实资料」。普通公告和独立弹窗分别供数；不伪造真实人物、群链接、凭证、评价数或榜单。角落标明「评审示例数据 · 未连接生产 · 未上线」。

使用实际生产 CSS，不注入模拟手机外框或重写 card/grid。首页卡片仍是原有117×224、三列；分页仍用既有逻辑。这里只调整 DOM 位置和增加独立公告。

## 验证

- 初始 Python：131 passed, 21 DB-dependent skipped；未连接生产 DB。
- DOM：后台全站/城市开关和独立文本保存、社区/地图/轮播位置、弹窗关闭/跨上海午夜/用户城市隔离/XSS/隐藏 app 等待均通过。
- 执行：`PATH=/workspace/venv-yycj/bin:$PATH NODE_PATH=/workspace/yycj-admin-ship/tests/node_modules npm --prefix tests run test:ui`（也可 `npm --prefix tests ci` 后去掉 NODE_PATH）。
- 未运行浏览器；截图由父代理桌面评审执行，不能把这些纯 DOM 检查当成视觉通过。

服务真实 HTML 的非浏览器 DOM 冒烟：`NODE_PATH=/workspace/yycj-admin-ship/tests/node_modules node tests/preview_dom_smoke.cjs`，覆盖以上四个 URL；jsdom 仅验证加载和交互，不验证像素布局。
