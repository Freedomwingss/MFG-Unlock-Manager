# 发布流程

一次性配好之后，以后每次更新就是 3 条命令 + 网页点几下。

## 第一次：把代码推上去

仓库已经建好了（`Freedomwingss/MFG-Unlock-Manager`，空的）。

打开命令提示符（Win+R 输 `cmd`），逐行执行：

```bat
cd /d "J:\AI 制作\WorkBuddy\MFG-Unlocker"
git remote set-url origin https://github.com/Freedomwingss/MFG-Unlock-Manager.git
git push -u origin main
```

第一次 push 会弹窗让你登录 GitHub，选 **Sign in with your browser**，授权一次就好。之后 git 会记住凭据，不再问。

> 如果提示 `Permission denied`，说明弹窗里登的账号不对，去「凭据管理器 → Windows 凭据」删掉 github.com 那条再试。

## 每次更新：三步

### 1. 版本号 +1

```bat
cd /d "J:\AI 制作\WorkBuddy\MFG-Unlocker"
python tools\bump_version.py --bump
```

`VERSION` 里的数字会 +0.0.1，同时刷新 `tools\version_info.txt`（exe 属性里的版本）。
如果改了图标相关的东西，先跑 `python tools\make_icon.py`。

### 2. 重新打包

```bat
python -m PyInstaller --noconfirm --onefile --windowed ^
  --name MFGUnlockManager ^
  --add-data "web;web" --add-data "assets;assets" --add-data "VERSION;." ^
  --icon "assets\icon.ico" ^
  --version-file "tools\version_info.txt" ^
  --hidden-import webview.platforms.edgechromium ^
  --distpath "." --workpath build mfgunlocker_web.py
```

生成 `MFGUnlockManager.exe`（约 13 MB）。

### 3. 提交并推送

```bat
git add -A
git commit -m "v1.0.4：改了什么写这里"
git push
```

## 发版（让用户能下载 exe）

代码推送后，去仓库网页：

1. 右侧 **Releases** → **Draft a new release**
2. **Choose a tag** 输入 `v1.0.4`（和 VERSION 里一致），点 **Create new tag**
3. **Release title** 写 `v1.0.4`
4. 描述里列几条这版改了什么（用户看的就是这里）
5. **Attach binaries** 拖入 `MFGUnlockManager.exe`
6. 点 **Publish release**

完成后下载地址是：

```
https://github.com/Freedomwingss/MFG-Unlock-Manager/releases/latest
```

这个链接可以直接发人，永远指向最新版。

## 注意

- **exe 不要 commit 进仓库**（`.gitignore` 里已排除），只放 Release
- 仓库里的 `data/` 目录也被忽略，那是本机运行数据
- 提交信息写清楚改了什么，别写 "update"
- 忘了加文件时 `git status` 能看到，`git add` 前先看一眼
