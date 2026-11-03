# MFG Unlock Manager · v1.0.3

给 Steam 游戏一键安装 / 卸载 **MFGAdaUnlock-RenoDx**（RTX 40 系解锁 DLSS 3×–6× 多帧生成）的本地工具，Web 界面。

## 功能

- **全盘扫描 Steam 库**：先读注册表（HKLM/HKCU）、再读 `libraryfolders.vdf`，最后扫所有盘符的常见位置与任意顶层目录，不依赖固定盘符
- **只在首次启动扫一次盘**：结果存成快照，之后每次打开直接读快照（1.8 秒出界面），不再读硬盘。装了新游戏或想刷新时点「扫描」
- **环境体检**：检查系统、显卡驱动、窗口环境、网络、Steam 库、磁盘空间，缺什么直接给下载入口
- 游戏名**中文**显示，每行带从 exe 提取的**游戏图标**
- 按图形组件判定**能否解锁**，默认只列出能解锁的游戏（「可解锁 / 已安装」两个分类）
- **一键装全套**：DLSS-G 升级（需要时）+ ReShade 精简宿主（缺失时）+ 最新解锁插件
- **一键卸载**：插件和 ReShade 宿主一起清掉（移入备份，可恢复）
- 字体三档滑块：0.75× / 1.0× / 1.25×（带刻度，只能停三档），图标与布局一起缩放
- 扫不到的游戏可**手动添加**（选目录即可）
- **一键运行诊断**：只针对**当前选中的游戏**，输出 `桌面\MFG-诊断报告.txt`（每次覆盖，只留一份）。重点是运行状态——解析 ReShade.log 判断注入是否成功、插件是否被加载、有无报错行；再给出崩溃转储、Windows 应用错误日志、部署状态和针对性建议。**进游戏跑一次再生成**才有意义
- **悬停提示**：鼠标停在徽章、按钮、滑块、游戏行上会浮出小气泡，说明这个模块干什么、数据从哪来
- 卸载只把文件移入备份目录，可恢复

## 运行

双击 `MFGUnlockManager.exe` 即可，不需要装 Python 或 .NET。

窗口是**自己的原生窗口**（pywebview + WebView2）：标题栏和任务栏图标都是本程序的，不依赖浏览器。若原生窗口初始化失败，会自动退回 Edge `--app` 或默认浏览器。

关掉窗口后本地服务自动退出。程序数据都在 exe 旁边的 `data\` 目录（便携式，不写注册表和 AppData）。

## 环境要求

| 项目 | 要求 | 不满足会怎样 |
|---|---|---|
| Windows | 10 / 11 64 位 | 无法运行 |
| 显卡 | NVIDIA RTX 20 系及以上 | 插件无意义（驱动层面限制） |
| NVIDIA 驱动 | 建议最新版 | 旧驱动可能拿不到 3×–6× 选项 |
| 浏览器 | Edge 或 Chrome | 自动退回默认浏览器标签页 |
| 网络 | 能访问 GitHub / NVIDIA | 拿不到插件与 ReShade，需手动放置 |

左下角「体检」按钮会逐项检查这些，缺的项后面有下载按钮。

## 安装状态

列表里的状态分三种：`已安装最新版`、`已安装可更新`（都是蓝色标签，与其它状态同字号）、`未安装`（按判定显示）。

顶部三个徽章统一默认配色，版本号写成「当前版本 / 最新版本」，例如 。

## MFG 1.8 的 XMX 模式

1.8.0 起插件有三种后端，都在同一个 `renodx-mfgunlock.addon64` 里：

| 模式 | 需要的额外文件 | 适用 |
|---|---|---|
| DLSS-G | 现代 `nvngx_dlssg.dll` | 兜底，1.8 之前的老路子 |
| **XMX** | DLSS-G 文件 + Intel `libxess_fg.dll` `libxell.dll` | **有原生 DLSS 帧生成的游戏，官方推荐**。延迟最低、Alt+Tab 更稳 |
| XeMFG | 仅 Intel 两个文件 | 没有原生帧生成的游戏 |

管理器会检查 Intel 运行时是否到位（`libxess_fg >= 1.3.1.78`、`libxell >= 1.3.2.10`），详情页显示「XMX 模式」状态。缺文件时进游戏在插件面板点 **Install / update files** 补齐——游戏更新或 Steam 校验会覆盖这两个 DLL，**每次游戏更新后要重装一次**。

## 判定含义

| 显示 | 含义 | 一键安装 |
|---|---|---|
| 支持 · 原生 3–6× | 有 310.x 的 `nvngx_dlssg.dll`，装完游戏里直接出 3×/4×/6× | 可 |
| 需替换 DLSS-G | 有 DLSS 帧生成管线但 DLL 太旧/缺失 → 点「更新 DLSS-G」 | 可 |
| 仅 XeMFG 后端 | 只有 XeSS 帧生成，走 XeMFG 后端 | 可 |
| 不支持 / 50 系原生 | 不显示（已过滤） | — |

判定：找主程序（`platform\steam`、`Binaries\Win64` 优先，再按体积 + 名字相似度）→ 读 PE 版本 → 扫 `nvngx_dlssg.dll` / `sl.*.dll` / `libxess_fg.dll` → 结合显卡架构。

## ReShade 精简宿主

不跑官方安装器，直接从 `ReShade_Setup_x.y.z_Addon.exe` 里解出内嵌的 `ReShade64.dll`（约 5 MB），改名为代理 dll（DX10/11/12 → `dxgi.dll`）放到 exe 目录，再写一份最小 `ReShade.ini`（效果搜索路径留空）。

**不装** shader / 预设 / 纹理 / 效果包 —— 画面零变化。同名 dll 被占用时自动换 `d3d12.dll`；纯 Vulkan 游戏无法这样部署，会明确提示。

## DLSS-G 更新

数据源是 DLSS Swapper 的公开 manifest（按官方 SDK 版本索引，带 md5）。下载单个 zip（约 3.7 MB）→ 解出 `nvngx_dlssg.dll` → md5 校验 → 备份原文件后替换到游戏原位置（找不到就放 exe 同级）。

## 下载加速

GitHub 直连在国内常只有十几 KB/s。插件下载会并发测速多个源（直连 / gh-proxy / ghfast / ghproxy），再用 6 连接分段下载，最后 sha256 校验。DLSS-G 清单走直连 + 镜像回退。

实测 10.5 MB 从 17 KB/s（10 分钟）→ 300+ KB/s（30 秒）。

## 文件位置

| 用途 | 路径 |
|---|---|
| 插件缓存 | `<程序目录>\data\cache\<版本>\` |
| ReShade 与 dll | `<程序目录>\data\reshade\` |
| DLSS-G 缓存 | `<程序目录>\data\dlssg\` |
| 图标缓存 | `<程序目录>\data\icons\` |
| 卸载备份 | `<程序目录>\data\backup\<时间>_<游戏>\` |
| 配置 | `<程序目录>\data\config.json` |

## 注意

- 只用于**单机**。改内存会被反作弊判定，联机前卸载。
- ReShade 的 add-on 版是未签名构建，SmartScreen / 杀软可能提示。
- DLSS OTA 更新后可能需要更新插件版本（工具每次安装都取最新 release）。
- 游戏内有倍率选项用游戏内的；只有开关时按 `Home` → MFG Unlock 面板设 `Force frame multiplier`。

## 主程序识别

按「名字匹配目录名 > UE 的 `-Shipping` > `Binaries/Win64` > 体积」打分，避免把画质模组的安装器误判成游戏主程序（例如 STALKER 2 目录里的 `PRZ_PHOTOREALISTIC ZONE.exe`）。

分析结果带 `CACHE_VERSION`，识别逻辑更新后旧缓存自动失效并重扫——升级版本后不用手动清缓存。

手动添加游戏用 Windows 原生的现代文件夹选择框（`IFileOpenDialog`）。

## 界面风格

深色 Fluent 设计（对齐 WinUI 3 应用观感）： 底、半透明圆角面板、4px 圆角控件、Segoe UI Variable、选中行左侧 accent 竖条、Win11 强调色 。

 下另有一个 WebView2 无边框宿主（C#，Mica + 自绘标题栏），已能编译运行，但高 DPI 下窗口尺寸被系统虚拟化（150%）导致布局异常，暂未启用。设环境变量  可试用（需先打包  目录）。

## 位置

整个程序和它的数据都在 ：exe、（插件缓存、ReShade、备份、图标、config.json）、源码。**不写 C 盘**。

唯一例外是 Edge 的窗口 profile（，纯英文路径，Edge 对中文目录不稳），也不需要可删。

## 界面承载方式

界面是 HTML/CSS/JS（Web UI），但**不需要用户开浏览器**：启动时用系统里的 Edge/Chrome 以 `--app=URL` 打开一个无地址栏、无标签栏的独立窗口，任务栏里就是一个应用窗口。关窗后靠心跳（45 秒无请求）自动退出，也可点「退出」立即结束。

之所以没用 pywebview：它依赖 `pythonnet`，而 `pythonnet` 目前没有 Python 3.14 的预编译 wheel，装不上。若要彻底不依赖系统浏览器，下一步是用 WebView2 + 一个 C# 宿主（约 100 行），把窗口换成自绘的；体积 +~10 MB。

## 源码结构

```
mfgunlocker_web.py  Web 版入口（当前发布形态）
mfg_web.py          HTTP 服务 + JSON API + 后台任务
web/index.html      单页界面（内联 CSS/JS）
mfg_core.py         扫描 / 判定 / GitHub / 下载 / 安装 / 卸载
mfg_reshade.py      ReShade 版本探测 / 解包 / 精简部署
mfg_dlssg.py        DLSS-G 清单 / 下载 / 替换
mfg_names.py        目录名 → 中文名
mfg_icons.py        exe 图标提取 + PNG 编码
mfgunlocker.py + mfg_ui.py   tkinter 版（旧，保留备用）
tools/              开发用：截屏、UI 冒烟测试
```

## 构建

图标由 `tools/make_icon.py` 生成（深色圆角底 + 三个递增三角，表示多帧倍增），输出 `tools/assets/icon.ico`（含 16/24/32/48/64/128/256 七种尺寸）。

版本号写在 `VERSION`，每次改动递增末位：

```bash
python tools/bump_version.py --bump     # 1.0.0 -> 1.0.1，同时刷新 version_info.txt

python -m PyInstaller --noconfirm --onefile --windowed ^
  --name MFGUnlockManager ^
  --add-data "web;web" --add-data "VERSION;." ^
  --icon tools/assets/icon.ico ^
  --version-file tools/version_info.txt ^
  --distpath "." --workpath build mfgunlocker_web.py
```

exe 会带上图标与文件属性版本；程序运行时页脚右下角显示同一个版本号。


## 体积控制

启动时自动清理（），让占用稳定在 50 MB 左右：

| 项 | 策略 |
|---|---|
| 插件包缓存 | 只留最新版本 |
| 备份 | 每个游戏留最近 2 份 |
| ReShade 安装包 | 解出 dll 后删除 exe |
| WebView2 数据 | 清 Crashpad / 组件缓存 / 着色器缓存 / Widevine 等，并限制磁盘缓存 32 MB |
