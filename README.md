# CARLA 1.0 on AMD Strix Halo (Ryzen AI Max 395 / gfx1151) — 从零跑通指南

> 本指南来自一台真实机器上的完整排查（2026-11-25 ~ 11-27，Ubuntu 26.04 + kernel 7.0.0-44 + Mesa RADV 26.0.8），  
> 最终以 **AMDVLK 2025.Q2.1** 完全跑通 CARLA 1.0：窗口渲染 + TrafficManager autopilot 物理 + camera sensor（SceneCapture）三链路共存，  
> 端到端验收零 GPU hang。所有结论均为本机实测，标注"出处"的部分给出源码/上游位置，未实测的不写。

---

## 0. TL;DR（一分钟版本）

| 结论                                                | 说明                                                                                                                                                                                      |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **RADV（Mesa）当前跑不了 UE5.8 窗口/相机路径**                 | gfx1151 特有底层 bug：窗口 present 或 SceneCapture 增加每帧提交频率后，GFX ring 在途提交楔死（`emitted - signaled = 2~3`），挂点在 Frame 0~2 漂移（VSM → Lumen RT Scene → GPUSceneUpdate → ShadowDepths），UE 侧 15+ 项排除均无效 |
| **换 AMDVLK 2025.Q2.1 即完全解决**                      | 同内核、同硬件、同 UE 版本下窗口 793+ tick 零 hang、camera sensor 正常出图 → 证明内核/固件/硬件全部无辜，bug 在 RADV                                                                                                      |
| **固件升级无效**                                        | 已试上游 2026-10 版 `gc_11_5_1_{pfp,me,mec,mes1,mes_2}` 五件，照挂                                                                                                                                |
| **`amdgpu.noretry=0 amdgpu.gpu_recovery=1` 建议常备** | 实测 hang 后 ring reset 首次自动成功恢复（此前 MES reset 全失败必死），兜底价值                                                                                                                                  |
| **UE5.8 Linux 只有 Vulkan RHI**                     | OpenGL 已移除（`LinuxDynamicRHI.cpp`）；CARLA 只 targeted `SF_VULKAN_SM6`；lavapipe 软件渲染不满足 SM6 Profile，软件兜底路线不可行                                                                               |

一键跑通（宿主机终端）：

```bash
bash /home/aaa/carla-src/strix-halo-carla-guide/scripts/amdvlk_carla_go.sh 600
```

---

## 1. 适用环境

- **APU**：AMD Ryzen AI Max+ 395（Strix Halo，gfx1151 / Radeon 8060S iGPU）
  > 术语说明：Strix Halo **没有独立显卡**——Radeon 8060S 就是集成在 APU 内的 gfx1151 本体，  
  > 与 CPU 共享系统内存（无独立 VRAM）。UE 日志 `DeviceName: Radeon 8060S Graphics`、  
  > 内核 `amdgpu 0000:c5:00.0` 指向同一块芯片。"iGPU"="本机唯一 GPU"，不存在"没跑 gfx1151"的可能。
- **OS**：Ubuntu 26.04，kernel 7.0.0-44
- **Mesa**：26.0.8（RADV）
- **CARLA**：1.0（UE 5.8 fork + CMake 构建）
- **显示**：GNOME + mutter，`DISPLAY=:0`，XWayland（注意：截图/窗口操作受 XWayland 限制，见 troubleshooting）
- 硬件无关的原则同样适用于其他 gfx11 APU（Phoenix/Hawk Point gfx1103/1104 未实测，请注意甄别）

---

## 2. 背景：为什么 RADV 不行（证据摘要）

完整证据链见 `docs/troubleshooting.md` 与 `docs/12-mesa-issue-draft.md`（可提交 Mesa issue 的最强形态）。核心三环：

1. **RADV 插桩取证**：在 Mesa 源码 `radv_amdgpu_cs.c`（winsys 层）加提交级 trace，失败时 dump 全部在途提交。  
   楔死时内核返回 `-ETIME`，`signaled seq=10232779, emitted seq=10232781`（恰好 2 个 gfx 在途提交），  
   420 条提交记录结构完全正常 → **RADV 发出的提交没有异常**，问题在 CP/ring-fetch/固件以下层。
2. **AMDVLK 反证**：同一内核、同一硬件，`VK_ICD_FILENAMES=/etc/vulkan/icd.d/amd_icd64.json` 切换用户态驱动即零 hang → 排除内核/固件/硬件。
3. **固件升级反证**：上游 2026-10 版 gc_11_5_1 固件（CP 微码 + MES 调度器）安装后照挂 → 固件层无修复。

devcoredump 寄存器解读（内核 `gc_11_5_0` 头文件对照）：`CP_CPF_STALLED_STAT1=0x1` = RING_FETCHING_DATA（CP 前端取指停顿）；  
page fault client=10（GFX）、地址呈 `0xfe00fe00…` 垃圾模式。另内核侧 `amdgpu_gmc_noretry_set()`：gc_ver ≥ 10.1 →  
noretry 默认开启 → VM fault 直接卡死不重试（出处：`drivers/gpu/drm/amd/amdgpu/amdgpu_gmc.c`）。

---

## 3. 第一步：内核参数（建议，非必需）

CARLA 本身不依赖这两个参数；它们是 hang 后的**自动恢复兜底**（gpu_recovery）与 retry 容忍（noretry=0）。

GRUB 方式（本机采用，`/proc/cmdline` 已验证生效）：

```bash
sudo sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.noretry=0 amdgpu.gpu_recovery=1 /' /etc/default/grub
sudo update-grub && sudo reboot
```

或 modprobe.d 方式（效果相同，出处：`amdgpu` 模块参数 `noretry`/`gpu_recovery`）：

```bash
echo "options amdgpu noretry=0 gpu_recovery=1" | sudo tee /etc/modprobe.d/amdgpu-workaround.conf
sudo update-initramfs -u && sudo reboot
```

> 注意 `gpu_recovery=1` 是 "dangerous option"，内核会打 taint 标记，属预期行为。

---

## 4. 第二步：构建 CARLA 1.0

按 CARLA 官方构建文档操作（<https://carla.readthedocs.io/en/latest/build_build_linux/>），本机实测补充以下要点：

1. **UE fork**：CARLA 1.0 依赖其官方 UnrealEngine fork（UE 5.8）。本机使用分支  
   `fix/shaderprint-radv-spirv`（HEAD `cbe416d53`，含 ShaderPrint Vulkan SPIR-V 修复 PR #70）。  
   该修复针对 RADV 下 DXC 生成非法 SPIR-V 的问题（关联 DXC#8417 / VUID-06924 / mesa#15904）——  
   用 AMDVLK 也不冲突，属引擎侧合法修正。
2. **Editor 编译命令**（注意 DLSS SDK 环境变量，缺了会链接失败）：
   ```bash
   cd ~/carla-src/UnrealEngine
   DLSS_SDK=disabled Engine/Build/BatchFiles/Linux/Build.sh CarlaUnrealEditor Linux Development \
     -project="$HOME/carla-src/carla/Unreal/CarlaUnreal/CarlaUnreal.uproject" -waitmutex
   ```
3. **CARLA 主体**：CMake 构建（1.0 新管线），`Build/` 目录已配置，LibCarla 全量通过。
4. **冷启动须知**：首次启动 UE 会做 AssetRegistry 扫描 + 静态网格/纹理构建，**2~4 分钟全部耗在初始化**，  
   不是卡死。判定 server 就绪以 RPC 端口监听为准（见 5）。DDC 预热后二次启动显著提速。
5. **ShaderCompileWorker fork 风暴**：UE 默认 16 个本地 worker 短时批量拉起可把瞬时 fork 挤爆  
   （报 `fork: Resource temporarily unavailable` 时不是 cgroup 配额问题），可在项目  
   `[DevOptions.Shaders]` 里压到 4 个 worker。

---

## 5. 第三步：安装 AMDVLK（关键一步）

```bash
# 下载（版本以 https://github.com/GPUOpen-Drivers/amdvlk/releases 为准；本机实测 2025.Q2.1）
wget https://github.com/GPUOpen-Drivers/amdvlk/releases/download/v-2025.Q2.1/amdvlk_2025.Q2.1_amd64.deb
sudo dpkg -i amdvlk_2025.Q2.1_amd64.deb
# 装完 ICD 位于 /etc/vulkan/icd.d/amd_icd64.json
```

**推荐 per-app 切换**（不要把 AMDVLK 设为系统默认，否则桌面所有 Vulkan 应用都换驱动）：

```bash
VK_ICD_FILENAMES=/etc/vulkan/icd.d/amd_icd64.json <启动命令>
```

验证切换生效：启动后看 UE 日志 `Saved/Logs/CarlaUnreal.log` 的 `RHI Adapter Info` 段——  
AMDVLK 报 `Driver Version: 2.0.349` 这种 2.0.x 格式；RADV 报 Mesa 26.x 格式。

---

## 6. 第四步：启动 CARLA（窗口模式）

本机标准配方（`scripts/amdvlk_carla_go.sh` 的核心）：

```bash
XAUTH=$(ls -t /run/user/1000/.mutter-Xwaylandauth.* | head -1)
DISPLAY=:0 XAUTHORITY="$XAUTH" \
VK_ICD_FILENAMES=/etc/vulkan/icd.d/amd_icd64.json \
  <引擎目录>/Engine/Binaries/Linux/UnrealEditor-Cmd \
  <CARLA目录>/Unreal/CarlaUnreal/CarlaUnreal.uproject \
  -game -windowed -resx 1600 -resy 900 \
  -ini:Engine:[Zen]:AutoLaunch=false \
  -ini:Engine:[ConsoleVariables]:r.WarnOfBadDrivers=0
```

逐项解释：

- `-windowed`：出真实窗口（wayland/XWayland 合成器上显示跑车画面）。分辨率传屏幕原生值可平铺  
  整屏（本机 4K 屏 = `-resx 3840 -resy 2160`；窗口分辨率只占屏幕一部分时观感"窗口太小"）
- `-quality-level=Low|Medium|High|Epic`：**CARLA 官方画质参数**（出处：CARLA 1.0 源码  
  `CarlaDeviceProfileSelectorModule.cpp:164`，**缺省 = Epic**，PascalCase 严格大小写，写错会静默忽略）。  
  本机实测 Epic@1080p 只有 ~~34 fps 且帧间隔 20~~37ms 乱跳（±50%）→ 运动镜头"画面抽搐"；  
  8060S 是共享内存带宽的 iGPU，**4K 屏建议 Medium，帧率优先用 Low**
- `-ini:Engine:[Zen]:AutoLaunch=false`：规避 Zen DDC 冻结（本机实测必需）
- `-ini:Engine:[ConsoleVariables]:r.WarnOfBadDrivers=0`：AMDVLK 版本号（2.0.349）必然触发 UE  
  驱动黑名单弹窗卡 240s；该 CVar 抑制弹窗（出处：UE `DynamicRHI.cpp` 驱动版本检查逻辑）
- `XAUTHORITY` 动态取最新：mutter 每次重启都会换文件名，写死必踩坑

server 就绪判据（四判据，缺一不可）：

1. RPC 2000 监听（`(exec 3<>/dev/tcp/127.0.0.1/2000) 2>/dev/null` 测试）
2. UE 窗口存在
3. 日志帧号持续推进（`Saved/Logs/CarlaUnreal.log`）
4. `journalctl -k | grep "ring gfx.*timeout"` 无新增

---

## 7. 第五步：PythonAPI 演示

PythonAPI 安装按官方文档（`carla/PythonAPI/` 下 pip 装对应 wheel）。demo 脚本（`scripts/` 目录）：

| 脚本                                 | 内容                                                                       | 备注                                    |
| ---------------------------------- | ------------------------------------------------------------------------ | ------------------------------------- |
| `demo_show.py 600`                 | **第三人称跟随视角看红色 Model 3 自动驾驶**（TM autopilot，红绿灯遵守），spectator 视角直接显示在 UE 窗口 | 看跑车首选                                 |
| `demo_drive.py`                    | 60s autopilot + 轨迹/速度采样（ego_samples.json）                                | 物理链路验收                                |
| `cam_capture.py`                   | camera sensor 抓拍 4 帧（drive_cam\_*.png）                                   | SceneCapture 链验收（RADV 下必挂，AMDVLK 下正常） |
| `x11_window_shot.py <WID> out.png` | 针对**具体窗口 ID** 的 XGetImage 截图（绕过 XWayland 限制）                             | 诊断工具                                  |

TrafficManager 用法要点（蓝图 ID 极易踩坑）：车是 `vehicle.tesla.model3`（不是 `model_3`）；  
`client.get_trafficmanager(8000)` + `ego.set_autopilot(True, 8000)`，端口要与 TM 端口一致。

---

## 8. 已知问题与限制

1. **MegaLights 红字警告**：CARLA 1.0 `DefaultEngine.ini` 默认 `r.MegaLights.EnableForProject=True`，  
   在无硬件光追的平台屏幕上刷红字（功能关闭不影响渲染）。本机已改 `False`，一行搞定：
   ```bash
   sed -i 's/^r.MegaLights.EnableForProject=True/r.MegaLights.EnableForProject=False/' \
     <CARLA目录>/Unreal/CarlaUnreal/Config/DefaultEngine.ini
   ```
2. **偶发宿主侧 Signal 11**：一次 8192² 纹理构建期间段错误（非 GPU hang，干净重跑即过），观察项。
3. **GPU 亚健康**：GPU 连续 hang + reset 失败后，同配置可能 4 帧即挂（vulkaninfo 枚举正常≠健康）。  
   **多次 hang 后先重启系统再继续实验**，否则结果不可比。
4. **AMDVLK 长期稳定性**：本机验收 17+ 分钟零 hang；更长 soak 与 RADV 性能对比未做。
5. **不要加 `-sm5`**：UE 5.8 只 targeted SF_VULKAN_SM6，`-sm5` 会 zenity 弹窗卡死（本机实测）。

---

## 8.5 与官方文档/Skills 核对记录（2026-11-27）

本指南启动配方已逐项对照 CARLA 1.0 官方文档（carla-ue5.readthedocs.io）与源码树自带的  
官方 Skills 库 `Skills/ue58/run-carla-ue58-server`（官方称为启动流程的 source of truth）。

**一致项（无需改动）：**

| 配置项                                                                     | 官方出处                                     | 结论           |
| ----------------------------------------------------------------------- | ---------------------------------------- | ------------ |
| `-game` + 编辑器二进制                                                        | 官方 Skills `game` 模式，"the working server" | 一致           |
| `-quality-level=Low\|Medium\|High\|Epic`（PascalCase，缺省 Epic）            | 官方文档 core_rendering 页                    | 一致           |
| 无法识别的档位值静默回退并打日志                                                        | 官方文档明确记载                                 | 与本机观察一致      |
| 档位在 UE 载入已保存 scalability 后重新应用                                          | 官方文档（旧配置不会覆盖你指定的档位）                      | 一致           |
| `-RenderOffScreen` 为无窗口官方参数；无 GPU 用 `-nullrhi`（**生成 camera 会 SIGSEGV**） | 官方 quickstart + Skills                   | 本指南用窗口模式，未涉及 |

**已采纳的官方推荐（本指南脚本已加入）：**

1. **`-notrace`**：官方 Skills 在窗口模式默认传它——残留 `UnrealTraceServer` 守护进程时，  
   Trace writer 线程 SIGSEGV（与本机 11-27 一次莫名 Signal 11 吻合）。清理命令：  
   `pkill -f "[U]nrealTraceServer"`。
2. **显式端口 + 三端口预检**：官方要求 2000/2001/2002 三端口空闲（streaming = RPC+1），  
   冲突时 "bind: Address already in use" 后 Signal 11。脚本已加预检与显式  
   `-carla-rpc-port=2000 -carla-streaming-port=2001`。
3. **位置参数指定地图** `Town10HD_Opt`（官方用法；注意 packaged 包会忽略该参数，源码树不受影响）。

**有意偏离项（记录原因）：**

- **分辨率/全屏走 GameUserSettings.ini 而非官方 `RES=WxH` 命令行**：官方 Skills 传  
  `-ResX/-ResY`，但本机源码树实测该命令行被忽略（日志 `failed to resolve 3840`），  
  只认 ini 覆写。可能是本树与官方打包流程差异，其他机器可先试命令行。
- **FullscreenMode=0（真全屏）而非官方建议的"定尺寸窗口"**：官方刻意不用独占全屏  
  （覆盖窗口如录屏终端无法置顶、部分采集路径闪烁）；本指南目标是桌面平铺观看，故选全屏。
- **`SDL_VIDEODRIVER=x11` 强制 X11 窗口后端（平铺的关键）**：UE 5.8 内嵌 SDL2 同时带  
  x11（recommended）与 wayland（suggested）后端，Wayland 可用时被优先选中 → UE 窗口是  
  Wayland 原生的，不出现在 X 窗口树（xwininfo/xdotool 全部失效），GNOME 的全屏状态  
  对它不生效，`FullscreenMode=0` 被无声回退成普通窗口（"没平铺"的总根源）。  
  强制 X11 后走 XWayland，GNOME 全屏状态正常生效，窗口重新可被 X 工具观察/截图。  
  依据：引擎源码 `LinuxPlatformApplicationMisc.cpp:446 IsUsingWayland()` 读 SDL 驱动名。
- **AMDVLK + `r.WarnOfBadDrivers=0`**：官方文档要求 NVIDIA RTX 3000+/16GB VRAM，  
  AMD 平台不在支持范围——这是本指南存在的意义，属已知且必要的偏离。

**画质档位官方参考数据**（Town10HD_Opt 空场景，相对 Epic 帧率）：High +25%，  
Medium +65%，Low +80%。Low 与 Medium 差距仅 ~15 个百分点（昂贵的光照在 Medium 已关掉），  
Medium 只是少了 1000MB 流式纹理池/反射捕获减半/FXAA——4K 下若 Low 帧率富余，  
Medium 观感更好且代价不大，值得试。Epic 以下档位强制关闭 camera 硬件光追  
（`carla.Camera.UseRayTracing=0`），对无 RT 平台反而正确。

---

## 9. 文档索引

- `docs/troubleshooting.md` —— 故障排查手册（hang 签名、devcoredump、排除矩阵、环境坑）
- `docs/12-mesa-issue-draft.md` —— Mesa issue 草稿（可提交形态，RADV gfx1151 hang 全证据链）
- `docs/upstream-pr-checklist.md` —— 本次排查产出的可提交上游 PR/issue 清单
- `scripts/` —— 一键启动 + 全部 demo/诊断脚本

## 10. 许可与出处

脚本部分按 MIT 许可发布。指南中的上游事实均注明出处（Mesa/UE/内核源码位置或 issue 编号）；  
未注明处为本机实测记录。CARLA 商标归 CARLA 团队所有，本指南与其无隶属关系。


---

## 9. 最终稳定态（2026-11-28 01:05 定案）与资源占用

**窗口演示的终态配置**：AMDVLK + `SDL_VIDEODRIVER=x11` + 4K 无边框全屏 + `-quality-level=Low` + `r.ScreenPercentage=50` + `FrameRateLimit=30` + **`bUseVSync=False`** + VSM 8192 页。

> **为什么 VSync 必须关（11-28 新增，曾被误判为 v8 已零抖动）**：在 59.96Hz 屏上 `bUseVSync=True` + 30fps 会把 server 的 dt 量化成 33ms/50ms 两档（丢一个 VSync 就跳到 50ms）。跟随相机用一 tick 速度外推，相机位移 `v*(2*dt[i-1]-dt[i-2])` 与车位移 `v*dt[i]` 在 dt 跳变时立刻失配，画面里车每约 1 秒前后硬跳 ±0.1~0.22m（RPC 逐 tick 实测 47 次/45s）。关 VSync 后帧率上限由 UE 定时器控制、dt 连续分布，**同一窗口热下发 `r.VSync 0` 后 45s/1334 tick 跳变 0 次**。若出现画面撕裂，可退回 `bUseVSync=True` 并把上限提到 60fps（需 GPU 富余）。

**跟随相机终态（demo_show.py v8）**：每个 server tick 主循环内**只写一个** `spectator.set_transform`（无线程、无 HUD RPC 争用），位姿取自 `WorldSnapshot.find(ego.id)` 单源、速度外推一 tick、传送滤波兜底。配合 `bUseVSync=False` 后容器侧原子快照验收 **45s/1334 tick，前向跳变 0 次**——车辆前后抖动彻底归零。

**演示命令**（server 已运行时，只需客户端）：
```bash
pkill -f demo_show; sleep 1
/home/aaa/miniconda3/bin/python /home/aaa/carla-src/carla_demo/demo_show.py 600
```
如需重新启动 server（含窗口）：`bash /home/aaa/carla-src/amdvlk_carla_go.sh 600 1 3840 2160 Low`。

**资源占用**（本机 10 秒实测，详见 `docs/resource-usage.md`）：GPU busy **83~95%**、APU 整机功耗 **83~86W**、CPU load average **12.1~12.4 / 32 核**（约 38%）、整机内存 **48GiB/121GiB**（约 40%）。demo_show.py 客户端 CPU <1 核、内存 ~100MB 量级；无独立 VRAM，统一内存。

**上游提交状态**（2026-11-28）：
- CARLA issue #10408（Linux Wayland 窗口行为损坏）、#10409（MegaLights 无 RT 平台红字）已提交；误建的测试 issue #10407 已关闭。
- Mesa RADV gfx1151 hang issue → **gitlab.freedesktop.org/mesa/mesa#17036**（已提交，证据见 `docs/12-mesa-issue-draft.md`）。
- PR #70（CarlaUnreal/UnrealEngine）主体已合入；VSM direct-dispatch 无效 workaround 已不在该 PR（diff 仅 ShaderPrint.ush）。

**仓库结构**：`scripts/`（一键设置 + 启动 + 演示/采集脚本）、`config/`（Scalability 配置）、`docs/`（incident-log 全量问题档案、troubleshooting、resource-usage、Mesa issue 草稿、上游 PR/issue 清单）。
