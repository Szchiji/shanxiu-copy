#!/usr/bin/env python3
"""Combined offline review pack for the local map+history integration. No Telegram, no production APIs, no live OSM."""
import argparse, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.export_map_review import export as export_map
from scripts.export_edit_history_review import export as export_history


def schematic_shots(dest: Path):
    """Labelled mock PNGs only. Not device captures, not live OSM/Telegram."""
    from PIL import Image, ImageDraw, ImageFont
    shot_dir = dest / "shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()

    def card(path, size, title, lines, badge="离线示意 · 非实机 · 非真实 OSM/Telegram"):
        img = Image.new("RGB", size, (15, 36, 32))
        d = ImageDraw.Draw(img)
        d.rectangle((16, 16, size[0]-16, 72), outline=(212, 178, 106), width=1)
        d.text((28, 28), title, fill=(241, 238, 228), font=font)
        d.text((28, 48), badge, fill=(212, 178, 106), font=font)
        y = 92
        for line in lines:
            d.text((28, y), line, fill=(200, 210, 200), font=font)
            y += 22
        img.save(path)

    card(shot_dir/"map-filters.png", (390, 844), "地图组合筛选（示意）", [
        "城市 深圳 · 区域 南山 · 营业中",
        "聚合圆点 + 底部资料卡，不显示精确门牌",
        "搜索此区域需手动点选，不自动刷新",
        "资源版本 20261111n",
    ])
    card(shot_dir/"history-entry.png", (390, 844), "修改记录入口（示意）", [
        "我的上架 → 更多 → 修改记录",
        "不写入首页三列 117x224 卡片",
        "44px 操作目标，墨绿金风格保留",
    ])
    card(shot_dir/"history-preview.png", (390, 844), "预览并确认恢复（示意）", [
        "显示可证明版本的字段差异",
        "必须勾选确认后才能提交",
        "作为一次新修改，不回滚状态/有效期",
        "旧媒体不可用则明确失败，可仅恢复文字",
    ])
    card(shot_dir/"admin-history.png", (1440, 1000), "管理员修改记录（示意）", [
        "入口在资料卡「更多」中",
        "按现有城市权限查看/恢复",
        "后台改动资产版本 20261111n",
    ])
    (shot_dir/"README.txt").write_text(
        "这些 PNG 是用调色板生成的离线示意图，不是浏览器实拍，也不能证明 OSM 或 Telegram 实机通过。\n"
        "请用 OPEN-FIRST.html 打开实际 HTML/CSS/JS 评审页（file://，禁止外网）。\n"
    )


def export(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    map_dir = out / "map"
    hist_dir = out / "history"
    export_map(map_dir, live_tiles=False)
    export_history(hist_dir)
    schematic_shots(out)
    shutil.copy2(ROOT / "docs/map-history-integration.md", out / "INTEGRATION.md")
    (out / "OPEN-FIRST.html").write_text(
        """<!doctype html><meta charset="utf-8"><title>月影车姬 · 地图+修改记录本地整合评审</title>
<style>body{background:#0F2420;color:#F1EEE4;font:18px/1.6 sans-serif;padding:40px;max-width:860px}a{color:#D4B26A}li{margin:14px 0}.note{border:1px solid rgba(212,178,106,.4);padding:14px 16px;border-radius:12px;margin:20px 0}</style>
<h1>地图升级 + 修改记录（本地整合，未上线）</h1>
<p>资源版本统一为 <strong>20261111n</strong>。本包是 file:// 评审材料，接口为内存示例或示意底图，禁止外网，不会发送 Telegram，也不会改生产数据。</p>
<div class="note"><strong>请区分模拟与实机：</strong>下面的 HTML 使用实际项目 CSS/JS 加标记示例数据；shots/ 里的 PNG 只是调色板示意图，不是实机截图，也不能当作 OSM 或 Telegram 验收证据。真实底图和手机定位仍未验收。</div>
<h2>实际 UI 评审页</h2>
<ul>
<li><a href="map/index.html">地图离线评审（示意底图，非真实道路；不要打开未生成的 live 页）</a></li>
<li><a href="history/OPEN-FIRST.html">修改记录离线评审（预览确认恢复）</a></li>
</ul>
<h2>示意图</h2>
<p>见 shots/ 与 shots/README.txt。</p>
<p><a href="INTEGRATION.md">整合说明</a></p>
"""
    )
    (out / "README.txt").write_text(
        "先打开 OPEN-FIRST.html。这是本地整合的离线评审包，不是上线或实机验收证明。\n"
        "不要把示意底图或调色板 PNG 当成真实 OSM/Telegram 结果。\n"
    )
    print(out / "OPEN-FIRST.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    export(parser.parse_args().out)
