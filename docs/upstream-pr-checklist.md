# 可提交上游 PR/issue 清单（2026-11-27 整理）

> 本次 Strix Halo + CARLA 1.0 排查的对外产出。按优先级与状态整理，附目标仓库与内容来源。

## 必提

### ① Mesa GitLab issue：RADV gfx1151 窗口/SceneCapture GPU hang（价值最高）
- **目标仓库**：gitlab.freedesktop.org/mesa/mesa（标记 `radv`、`gfx11`/Strix Halo）
- **内容**：`docs/12-mesa-issue-draft.md`（最强形态，可直接提交）：
  - RADV 插桩提交级证据：`-ETIME`、楔死时 2 个 gfx 在途提交、420 条提交 trace 结构正常
  - AMDVLK 2025.Q2.1 反证：同内核同硬件零 hang（内核/固件/硬件全排除）
  - 固件升级反证：上游 2026-10 版 gc_11_5_1 五件无效
  - UE 侧 15+ 项排除矩阵 + devcoredump 寄存器解读（RING_FETCHING_DATA、noretry、MES reset 失败）
- **上游情报**：我们的签名与 mesa#13411（Strix Halo 窗口挂，已关"不再复现"）、mesa#16943（gfx1151 DCC 挂，open）
  机制均不完全一致，无现成上游修复 → 值得单开
- **状态**：草稿就绪，**卡用户 GitLab OAuth 登录**（gitlab.freedesktop.org，账号 bobwei192）

### ② PR #70（CarlaUnreal/UnrealEngine）收尾：VSM direct-dispatch 改动去留
- **背景**：PR #70 主体（ShaderPrint Vulkan 用 global resource 规避 DXC 非法 SPIR-V）已单 commit `cbe416d53`
  push 并收到评论，**已上线**。
- **待决事项**：分支上还留着 VSM `InitializePhysicalPagesIndirect` indirect→direct dispatch 改写
  （USF 早退保护 + C++ 参数，语义等价）。它是针对 RADV hang 的无效 workaround（当时为验证假设所做）。
- **建议**：**从 PR #70 中移除**（主题无关、且已证无效），不随 PR 提交。若想保留价值，
  可在 Mesa issue ①中以"已试过的 workaround"一节提及，避免上游重复劳动。

### ③ CARLA 仓库 issue：Linux Wayland 会话下窗口模式行为损坏（窗口大小/全屏设置被无声忽略）
- **目标仓库**：github.com/carla-simulator/carla（含官方 Skills `run-carla-ue58-server` 的 RES/FULLSCREEN 行为）
- **内容**（2026-11-27 深夜实测，gfx1151/Ubuntu 24.04 GNOME Wayland）：
  1. UE 5.8 内嵌 SDL2 带 x11（recommended）与 wayland（suggested）双视频后端，
     Wayland 会话下自动选 wayland → UE 窗口为 Wayland 原生：
     - 不出现在 X 窗口树（xwininfo/xdotool/wmctrl/基于 X 的截图工具全部失效）
     - `GameUserSettings.ini` 的 `FullscreenMode=0`（真全屏）被无声回退成普通窗口，
       分辨率/全屏设置全部不生效 → `RES=WxH`/`FULLSCREEN=1`（官方 Skills 旋钮）在该会话下不可靠
     - 命令行 `-resx/-resy`（空格分隔形式）更是直接被忽略：日志铁证
       `LogPackageName: failed to resolve 3840`（值泄漏成地图名）+ `r.setres:1280x720`
  2. **佐证**：Epic 官方 FORT-1004112（UE 5.7 报告，target fix 5.8）确认 SDL Wayland
     在 UE 编辑器上"unusable"，社区结论 "SDL_VIDEODRIVER=x11 is a must"；
     引擎源码 `LinuxPlatformApplicationMisc.cpp:446 IsUsingWayland()` 直接读 SDL 驱动名。
  3. **建议修复**（文档级即可，代码级更好）：
     - 文档/官方 Skills 注明：Wayland 会话（Ubuntu 24.04 默认！）下窗口行为不可靠，
       推荐 `SDL_VIDEODRIVER=x11`；或 run_server.sh 检测 Wayland 会话时自动附加该变量
     - 注：`-ResX=3840 -ResY=2160`（**等号形式**，官方 Skills 所用）在本树未单独验证，
       提 issue 前应在宿主机实测一次（见"补证步骤"）
- **补证步骤（提 issue 前，宿主机执行）**：
  1. `SDL_VIDEODRIVER=x11` 启动后 `xwininfo -root -tree | grep -i unreal` 应能看到窗口
     （= X11 后端生效），确认 FullscreenMode=0 恢复生效
  2. 单独测 `-ResX=3840 -ResY=2160` 等号形式是否生效（官方 Skills 用的是等号形式）
- **状态**：脚本修复已提交（88fbaa4），**待宿主机重跑验证后提**

### ③b 抖动（frame pacing）：**不提 issue**
- 4K Low 实测 ~57fps 撑不满 60Hz → 周期性丢帧 judder，是硬件性能与显示时序的**正确行为**，
  非 bug；官方文档也未承诺 vsync/帧节奏语义
- 处置（`FrameRateLimit=30` 锁显示器整因子）属 UE 通用配置，无上游可改点
- 若 ③ 提交时可顺带在 issue 正文附一句"建议文档补充 frame pacing 指引
  （帧率锁显示器刷新率整因子）"，作为文档改进建议并入，不单开

## 建议提（次要，低开销）

### ④ CARLA 仓库 issue：无硬件光追平台 MegaLights 红字警告
- **目标仓库**：github.com/carla-simulator/carla
- **内容**：CARLA 1.0 `Unreal/CarlaUnreal/Config/DefaultEngine.ini` 默认
  `r.MegaLights.EnableForProject=True`，在无 HW RT 的 Linux/Vulkan 平台（如 gfx1151 + AMDVLK）
  每次启动屏幕刷三行红字（"MegaLights is enabled, but has no ray tracing data…"），影响观感但不影响功能。
- **建议修复**：默认改 `False`，或仅在 `r.RayTracing=1` 且平台支持 HW RT 时启用。
- **本机处置**：已自行改 `False`（2026-11-27）。

### ⑤ Mesa MR（先咨询维护者，勿直接开）：RADV 提交级 trace 插桩
- **内容**：`radv_amdgpu_cs.c` winsys 层提交环形缓冲 trace（`RADV_SUBMIT_TRACE=1/DEPTH/FILE/FLUSH`
  环境变量，DEVICE_LOST 时 dump 全环），对 gfx11 类 wedge 定位极其有效（本次定案的关键工具）。
- **风险**：侵入 amdgpu winsys 热路径，官方未必接受常驻特性。
- **建议路径**：先在 issue ①评论中附 patch 链接，问维护者是否愿意收（或降级为 issue 附件即可，不强求 MR）。

## 可选（低优）

### ⑥ amd-gfx / 内核邮件列表观察报告：gfx1151 pipe reset 不支持
- **内容**：`MES failed to respond to msg=RESET` + `The CPFW hasn't support pipe reset yet`
  （内核日志原话）+ ring reset failed 全过程记录，devcoredump 寄存器解读齐全。
  属已知固件限制，价值在给 "gpu_recovery=1 能救活 gfx1151" 提供实例数据。
- **建议**：仅在 Mesa issue ①需要补充内核侧信息时附上，不单独开线程。

### ⑦ UE/CARLA 附注：`r.Vulkan.Submission.AllowTimelineSemaphores` 是 dummy cvar
- **内容**：UE 5.8 中该 cvar 未接实际逻辑（日志 `[[...]] deferred - dummy variable created`），
  按现文档设置会静默无效。可并入 ④ 一并提，或放弃（文档级小问题）。

## 不提

| 项 | 原因 |
|---|---|
| AMDVLK | 工作正常，无产出 |
| linux-firmware / 固件 | 升级实测无效，但上游固件本就在持续更新，无 bug 可报；结论并入 Mesa issue ① |
| PR #70 的 VSM 改动 | 已证无效，见 ② |

## 账号与工具备忘

- GitHub token：`~/.prflow/github-token`；GitLab PAT 在对话记录中（gitlab.freedesktop.org / bobwei192 / id 1270989）
- prflow 条目 12（Mesa issue）stage=replied，看板 localhost:8833

### 更新（2026-11-27 23:59）：get_transform 全零位姿 = 上游已记载，不提
社区核查确认：demo 客户端间歇全零位姿 = [carla#1424](https://github.com/carla-simulator/carla/issues/1424)
官方设计行为（get_transform 返回上一 tick 客户端快照缓存，新 spawn actor 返回默认态）。
已在 demo v3.2 用官方惯例 wait_for_tick + 传送滤波兜底。**不重复提 issue**；
Wayland issue（清单 ③）不受影响，仍待提交。

### 更新（2026-11-28 00:45）：实际提交状态
- ✅ **③ Wayland issue → github.com/carla-simulator/carla#10408**（已提交）
- ✅ **④ MegaLights issue → github.com/carla-simulator/carla#10409**（已提交，含 dummy cvar 附注 ⑦）
- ✅ **① Mesa issue → gitlab.freedesktop.org/mesa/mesa#17036**（https://gitlab.freedesktop.org/mesa/mesa/-/work_items/17036，已提交）
- ✅ **② PR #70**：API 确认 diff 仅 `Engine/Shaders/Private/ShaderPrint.ush`（9+/1-），VSM workaround 已不在 PR 中，无需额外收尾
- 误建的测试 issue #10407 已关闭（诊断 token 权限时产生）
