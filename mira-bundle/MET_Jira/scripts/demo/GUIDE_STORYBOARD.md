# User Guide 录屏分镜（循环动图）

静帧与短片由 `capture_guide.py` 自动刷新（默认账号 `houcheng.fan`，先跑 `seed_my_work.py --reset`）。Guide 页展示的是循环 **animated WebP**（`*-demo.webp`，GIF 同款体验、体积更小），由录制的短 MP4 再编码得到。

```bash
METJIRA_DATA_DIR=~/.met_jira_staging METJIRA_DEPLOY_ENV=staging \
  python3 scripts/demo/seed_my_work.py --user houcheng.fan --reset

PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" \
  METJIRA_GUIDE_USER=houcheng.fan \
  python3 scripts/demo/capture_guide.py

# 仅从已有 mp4 重生成动图：
python3 scripts/demo/capture_guide.py --anims-only
```

自动覆盖：全部 `*.webp` 静帧、中间产物 `*.mp4` / `*-poster.jpg`，以及 Guide 使用的 `*-demo.webp`（需本机 `ffmpeg` + `libwebp_anim`）。

| 文件 | 自动 | 路径与操作 |
|------|------|------------|
| `home.webp` / `my-work.webp` + `my-work-demo.webp` | 是 | `/` 滚动与「需关注」类芯片 |
| `create-still.webp` + `create-demo.webp` | 是 | 创建弹窗；尽量点「拆分」 |
| `board-still.webp` + `board-demo.webp` / `workflow-demo.webp` | 是 | `ELEC` 看板：把卡片拖到下一列（真实 DnD） |
| `list-still.webp` + `list-demo.webp` | 是 | 列表轻滚 |
| `filters-still.webp` + `filters-demo.webp` / `ai-filter-demo.webp` | 是 | 常用查询 + 「列」菜单 |
| `login` / `summary` / `reports` / `backlog` / `timeline` / `settings-workflow` / `user-settings` / `admin` / `issue` / `org` / `notifications` / `palette-still` | 是 | 对应路由静帧 |
| `ai-assistant-demo.webp` / `palette-demo.webp` | 是 | 打开助手并输入示例、Ctrl+K 搜索 |

Glossary「界面示例」依赖 `filters-still.webp` 等新静帧。
