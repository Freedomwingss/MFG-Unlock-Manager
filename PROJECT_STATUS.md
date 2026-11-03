# 项目现状速查（新会话先读这个）

> 位置 `J:\AI 制作\WorkBuddy\MFG-Unlocker` ｜ 版本见 `VERSION` ｜ exe 同名目录

## 它是什么

给 Steam 游戏一键装/卸 **MFGAdaUnlock-RenoDx**（RTX 40 系解锁 DLSS 3×–6× 多帧生成）的本地工具。
Python 后端起 HTTP 服务，前端是单页 HTML，用 **pywebview（WebView2）** 开原生窗口。

## 结构

```
mfgunlocker_web.py   入口（windowed 下 stdout 为 None，已做保护）
mfg_web.py           HTTP 服务 + 各业务 job（扫描/安装/卸载/报告/体检）
mfg_core.py          库发现、exe 探测、DLL 版本、判定、安装
mfg_reshade.py       ReShade 精简宿主
mfg_dlssg.py         DLSS-G 310.x 升级
mfg_icons.py         exe 图标提取（无 Pillow 依赖）
shell_window.py      pywebview 外壳
web/index.html       全部前端（单文件，无构建）
assets/icon.ico      图标（tools/make_icon.py 生成）
tools/bump_version.py 版本号 +1 并刷新 version_info.txt
```

## 已定的设计（别再改回去）

| 决策 | 原因 |
|---|---|
| 首次启动才扫盘，之后读 `cfg["snapshot"]` | 冷启动 9.5s → 1.8s，不伤硬盘 |
| `CACHE_VERSION` 改判定逻辑时必须 +1 | 否则旧缓存不重算 |
| XMX 的 Intel 文件只看 **exe 所在目录** | 插件只认那里，递归扫描会误报 |
| 安装后自动 `place_intel_files()` 补齐 | 游戏内通常已有合适版本，复制即可 |
| 原生窗口优先，失败退 Edge `--app` / 默认浏览器 | 兜底保证能打开 |
| `native_shell=True` 时停用 watchdog | 窗口自己管生命周期 |
| 启动跑 `prune_storage()` | 占用稳定在 ~60 MB |
| 数据全在 `data/`，便携不写 AppData | 换机拷目录即可 |

## 常用命令

```bash
python tools/bump_version.py --bump     # 版本末位 +1
# 图标：python tools/make_icon.py
# 打包：见 README「构建」章节（--icon / --version-file / --hidden-import webview.platforms.edgechromium）
```

## 待观察

- pywebview 窗口长时间空闲是否会被系统回收（测试中出现过一次，未复现）。

## 排查入口

- `data/shell.log`：窗口生命周期、清理记录
- 判断注入：游戏目录 `ReShade.log`
- 判定依据：详情页「判定」下方那段文字
