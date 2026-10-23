# 多平台自动签到工具（auto-checkin）

插件化、配置驱动的自动化签到工具，面向**桌面客户端场景**（Trae Code、CodeArts 码道、
WorkBuddy buddy加油站等签到入口在 IDE/客户端内，而非网页端）。

## 客户端场景的技术方案

桌面客户端（Trae、CodeArts 这类 VSCode 系 IDE）本质是 Electron 应用，
签到动作最终仍是 HTTP 请求，只是从客户端进程发出、携带客户端的登录态。
因此有三条可行路线：

| 路线 | 类型 | 原理 | 适用场景 | 推荐度 |
|------|------|------|---------|--------|
| **CDP 接入客户端** | `checker: cdp` | 客户端以 `--remote-debugging-port` 启动，Playwright 通过 CDP 连入，在**客户端真实上下文**点击/发请求，**自带登录态、免逆向签名** | Electron 客户端（Trae、CodeArts 等） | 首选 |
| **客户端抓包 + 直连 API** | `checker: http` | 用客户端内置 DevTools 抓签到接口，填进配置直接调用 | 接口无签名、Cookie 稳定 | 次选 |
| **桌面 UI 自动化** | `checker: desktop` | uiautomation 找窗口控件点击 / 图像匹配兜底 | 非 Electron 客户端（原生、Qt、JetBrains 插件宿主） | 兜底 |

```
┌──────────────────────────────────────────────────────┐
│                    main.py (CLI)                      │
│  run / daemon / login / cdp-targets / list / history  │
└───────────────┬──────────────────────────────────────┘
                │
         ┌──────▼──────┐
         │   Engine    │ 编排 · 随机延迟(防风控) · 失败重试
         └─┬────┬───┬──┘
           │    │   └──────────────┬──────────────┐
    ┌──────▼─┐  │         ┌────────▼───┐   ┌──────▼──────┐
    │  Cdp   │  │         │  HttpChecker│   │  Desktop    │
    │Checker │  │         │  (接口直连) │   │  Checker    │
    │客户端  │  │         └────────┬───┘   │ (UI自动化)  │
    └──────┬─┘  │                  │       └──────┬──────┘
           │    └── BrowserChecker ┘              │
           │        (网页/无头,备用)               │
           └──────────────┬───────────────────────┘
                          ▼
              Storage(SQLite) + Notifier(多渠道推送)
```

## 快速开始（推荐 CDP 路线）

### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

> 关于 playwright：
> - **CDP 模式不需要下载浏览器内核**——它连的是客户端自带的 Chromium/Electron 引擎，
>   装 `pip install playwright` 即可。
> - 只有 `browser` 模式（无头浏览器打开网页签到）才需要 `playwright install chromium`。
> - `desktop` 模式需要 `pip install uiautomation`（图像兜底时加装 `pyautogui`）。

### 2. 让客户端以调试端口启动

这是 CDP 模式的前提。两种方式二选一：

**方式 A：修改快捷方式（客户端常开时用）**
右键客户端快捷方式 → 属性 → 目标，末尾加参数：

```
"C:\...\Trae.exe" --remote-debugging-port=9222
```

**方式 B：让工具自动拉起（推荐，客户端不用常开）**
在配置里填 `launch_cmd`，工具会启动客户端并等待端口就绪，签到完可自动关闭。

> 注意：客户端如果已经运行，需要先退出，带参数重新启动才生效（Electron 单实例）。

### 3. 找到客户端的真实路径

- 右键桌面/开始菜单的客户端图标 → 属性 → 复制「目标」里的路径
- 或在任务管理器右键进程 → 打开文件所在位置

### 4. 确认签到页面（关键）

```powershell
python main.py cdp-targets --task trae-client
```

会列出客户端当前所有可调试页面的 title 和 url，找到积分/签到页，
把其中的关键字填到配置的 `target_match`。

### 5. 抓签到请求（可选，配合 eval 直接发请求）

1. 打开客户端，进入积分/签到页
2. VSCode 系 IDE：`Ctrl+Shift+P` → 输入 `Developer: Toggle Developer Tools`
   （或菜单 `帮助 → Toggle Developer Tools`）
3. 在弹出的 DevTools → Network 面板，手动点一次签到
4. 复制该请求的 **URL / Method / 请求体**
5. 填到配置 `actions` 的 `eval` 里（相对路径即可，会自动带上客户端登录态）：

```yaml
- eval: "fetch('/api/user/sign_in', {method:'POST'}).then(r=>r.text())"
```

> 如果不想研究接口，也可以直接用 `click` 点客户端里的签到按钮：
> `- click: "text=签到"`

### 6. 运行

```powershell
copy config.example.yaml config.yaml      # 复制模板并填写
python main.py list                        # 查看任务
python main.py run                         # 立即执行
python main.py run --task trae-client      # 只跑指定任务
python main.py history                     # 查看历史
```

### 7. 每日自动执行

```powershell
# 方式一：常驻进程
python main.py daemon

# 方式二：Windows 任务计划（推荐，开机即跑，无需常驻）
schtasks /create /tn "AutoCheckin" /tr "python main.py run" /sc daily /st 08:30
```

GitHub Actions 不适合本方案（需要本机客户端在线），请在本机或常开的机器上运行。

## 配置说明

### CDP 模式（`checker: cdp`）

```yaml
settings:
  debug_port: 9222                    # 调试端口，各客户端用不同端口避免冲突
  launch_cmd: "客户端路径 --remote-debugging-port=9222"
  ready_timeout: 120                  # 等待端口就绪上限（秒）
  wait_after_launch: 15               # 端口就绪后再等待渲染完成
  target_match: "workbench"           # 页面 url/title 关键字，用 cdp-targets 确认
  kill_process: "xxx.exe"             # 可选：签到后关闭客户端
  kill_after_launch: true             # 仅在"本次由工具拉起"时才关闭，不打扰正在使用的客户端
  actions:
    - already_text: "已签到"           # 命中即视为今日已完成
    - click_selector: ".xxx"          # CSS 选择器点击（自动穿透 shadow DOM）
    - wait: 900
    - click_text: ["立即签到", "签到"]  # 按按钮文本点击（精确匹配）
    - expect_text: "已签到"            # 校验结果
    - screenshot: "logs/trae.png"
```

**支持的动作：**

| 动作 | 说明 |
|------|------|
| `already_text` | 页面出现该文本 → 判定"今日已签到"并结束（智能幂等） |
| `click_selector` | 按 CSS 选择器点击中心点（自动穿透 shadow DOM） |
| `click_text` | 按可见文本精确点击；值为数组时依次尝试 |
| `click_text_if_exists` | 同上，但找不到时跳过而不报错 |
| `wait_text` | 等待文本出现（可配 `timeout` 秒） |
| `expect_text` | 文本必须存在，否则任务失败 |
| `eval` | 页面上下文执行 JS（可发请求、取值） |
| `click` / `wait_for_selector` | Playwright 原生选择器操作 |
| `wait` / `press` / `goto` / `screenshot` | 等待/按键/跳转/截图 |

> 已适配 shadow DOM：Trae 的账户面板、WorkBuddy 的侧栏卡片都渲染在 shadow root 内，
> 文本查找与选择器点击均会递归穿透。

### 已验证的签到配方（本机实测）

**Trae（9222）** —— 右上角头像 → 面板 → 签到按钮：
```yaml
actions:
  - click_text_if_exists: ["立即签到"]
  - already_text: "已签到"
  - click_selector: ".icube-user-profile-wrapper"
  - wait: 900
  - already_text: "已签到"
  - click_selector: ".icube-user-card__checkin-button"
  - expect_text: "已签到"
```

**WorkBuddy（9444）** —— 左下角账户 → 菜单 Buddy加油站 → 卡片按钮：
```yaml
actions:
  - already_text: "今日已领取"
  - click_text_if_exists: "Buddy加油站"
  - click_selector: ".user-menu-trigger-wrapper"
  - wait: 1200
  - click_text: "Buddy加油站"
  - wait: 1500
  - already_text: "今日已领取"
  - click_selector: ".cb button.go"
  - expect_text: "今日已领取"
```

**CodeArts 码道（9333）** —— 标题栏"限时福利" → 福利面板 → 每日签到行：
```yaml
actions:
  - already_text: "今日已领"
  - click_selector: '[aria-label="限时福利"]'
  - wait: 1500
  - already_text: "今日已领"
  - click_near: { text: "每日签到领积分", target: "button" }   # 按行标题定位按钮
  - expect_text: "今日已领"
```
> `click_near` 会先找到"每日签到领积分"这行文字，再点同一行内的按钮，
> 因此按钮文案是"立即领取"还是"今日已领"都不影响，比写死文案更稳。

### 自动拉起与自动关闭

三个客户端均已配置 `launch_cmd` + `kill_after_launch: true`，日常行为是：

| 场景 | 行为 |
|------|------|
| 客户端未启动 | 工具带调试端口拉起 → 签到 → 自动关闭 |
| 客户端已启动（带端口） | 直接签到，不关闭你的窗口 |
| 客户端已启动（未带端口） | 提示"需完全退出后重启"，不会误操作 |

因此每天的自动签到不再需要你手动开客户端。

### 桌面自动化模式（`checker: desktop`）

```yaml
settings:
  window_title: "WorkBuddy"
  launch_cmd: "客户端路径"
  steps:
    - { type: dump, value: true }          # 先打印控件树到日志，确定按钮名称
    - { type: wait, value: 2 }
    - { type: click_text, value: "签到" }
    # - { type: image, value: "imgs/signin.png" }   # 控件定位不到时用图像兜底
```

### HTTP 模式（`checker: http`）

客户端抓包后填写。支持多步骤链、变量传递、积分提取、已签到识别：

```yaml
settings:
  steps:
    - name: "取token"
      method: GET
      url: "https://..."
      extract: { token: "$.data.token" }      # 后续步骤用 {token} 引用
    - name: "签到"
      method: POST
      url: "https://..."
      headers: { Cookie: "${XXX_COOKIE}" }
      json: { token: "{token}" }
      success:
        status: 200
        json_contains: { code: 0 }
      already:
        keywords: ["已签到"]
      extract_points: "$.data.points"
```

### 通知配置

支持 Server酱 / Bark / 钉钉 / 飞书 / 企业微信 / 自定义 Webhook，
在 `notify` 下把对应渠道 `enabled: true` 并填入密钥（建议用环境变量）。

## 目录结构

```
├── main.py                      # CLI 入口
├── config.example.yaml           # 配置模板（客户端场景）
├── checkin/
│   ├── config.py                # 配置加载 + 环境变量注入
│   ├── engine.py                # 引擎：编排/重试/延迟/通知
│   ├── models.py                # 数据模型
│   ├── notifier.py              # 多渠道通知
│   ├── storage.py               # SQLite 历史记录
│   └── checkers/
│       ├── cdp_checker.py       # 客户端签到（CDP 接入真实客户端）
│       ├── desktop_checker.py   # 桌面 UI 自动化
│       ├── http_checker.py      # HTTP 接口直连（配置驱动）
│       └── browser_checker.py   # 无头浏览器（网页端备用）
├── tests/                       # 本地模拟 API 与测试配置（可选）
├── data/checkin.db              # 签到记录（自动生成）
├── logs/                        # 日志与截图（自动生成）
└── state/                       # 浏览器登录态快照（自动生成）
```

## 本地测试（可选）

不想直接对真实客户端调试时，先用内置模拟 API 验证链路：

```powershell
python tests/mock_server.py &
python main.py --config tests/test_config.yaml run      # 第一次：签到成功 +15 积分
python main.py --config tests/test_config.yaml run      # 第二次：识别"今日已签到"
python main.py history
```

## 注意事项

1. **客户端需在线**：CDP 模式要求目标客户端处于运行状态（或配置 `launch_cmd` 自动拉起）。
2. **端口冲突**：多个客户端用不同 `debug_port`（如 9222/9333/9444）。
3. **Cookie 安全**：不要把 Cookie/Token 提交到公开仓库，`.gitignore` 已忽略 `config.yaml`。
4. **防风控**：保持随机延迟，避免高频连续请求；签到每天一次即可。
5. **接口变动**：客户端改版后重新抓包更新配置，工具本身不用动。
6. **Windows 控制台中文乱码**：先执行 `$env:PYTHONIOENCODING = "utf-8"` 或 `chcp 65001`。
7. **合规性**：仅用于个人账号的日常签到自动化，遵守各平台用户协议。
