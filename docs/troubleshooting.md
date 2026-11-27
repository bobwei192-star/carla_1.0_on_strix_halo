# 故障排查手册 — CARLA 1.0 @ Strix Halo (gfx1151)

> 全部条目来自 2026-11-25~11-27 本机实测。按"现象 → 诊断 → 处置"组织。

## 1. GPU hang 的标准签名

**现象**：UE 窗口模式或 camera sensor 启动后数秒内退出；UE 日志报
`vkQueueSubmit failed, VkResult=-4 VK_ERROR_DEVICE_LOST`；内核报：

```
amdgpu 0000:c5:00.0: ring gfx_0.0.0 timeout, signaled seq=..., emitted seq=...
```

**关键判读**：`emitted - signaled = 2~3` → 恰好 2~3 个在途提交楔死（不是调度积压）。
RADV 下该签名与提交频率相关：脱屏（-RenderOffScreen）2000+ 帧稳定；窗口 present 或
SceneCapture（CARLA camera sensor）加入后 Frame 0~2 即挂。

**挂点漂移 = 底层 bug 强证据**：UE breadcrumb 先后落在 VSM BuildPageAllocation →
Lumen RayTracingScene → GPUSceneUpdate → ShadowDepths → PostRenderOpsFX。
换 pass 无效、关 VSM 后挂点漂走 → 不是特定 pass 的问题。

**处置**：换 AMDVLK ICD（见 README §5），这是唯一实证有效的方案。

## 2. hang 后的恢复

| 级别 | 手段 | 实测效果 |
|---|---|---|
| 轻度 | `gpu_recovery=1`（内核参数） | 11-27 实测 ring reset 首次成功恢复（此前 MES reset 全失败） |
| 中度 | 重启系统 | 必成，但慢 |
| 无效 | MES pipe reset | `MES failed to respond to msg=RESET`，gfx1151 CPFW 不支持 pipe reset（内核日志原话：`The CPFW hasn't support pipe reset yet`） |

**运维铁律**：GPU 经历 hang+reset 失败后进入"亚健康"——同配置可能 4 帧即挂，
`vulkaninfo` 枚举正常不代表健康。多次 hang 后**先重启再实验**，否则结果不可比。

**devcoredump 抓取**（10 分钟窗口，只能读一次）：
```bash
sudo cat /sys/class/drm/card1/device/devcoredump/data > dump.txt
```
本机样本：`gfx1151_hang_devcoredump_20261125.bin`（6.1MB）。寄存器判读：
`CP_CPF_STALLED_STAT1=0x1`=RING_FETCHING_DATA；page fault client=10(SOC21_IH_CLIENTID_GFX)、
gfxhub、地址 `0x0000fe00fe00f000`（垃圾模式）。

## 3. UE 侧排除矩阵（不要重复试，全部无效）

VSM 开关、Lumen/RT/反射/GI 全降、DCC 禁用（`radv_disable_dcc=true` 仍挂 45s 内死）、
`r.Vulkan.AllowAsyncCompute=0`（**有害**！frame 4-7 即死，禁止使用）、indirect→direct dispatch 改写
（语义等价仍挂）、缩 VSM 池 1/4、`r.RDG.Debug.FlushGPU=1`、线性交换链（MESA_VK_WSI_DEBUG=linear）、
present mode 替换、`RADV_DEBUG=hang,noibchaining`、`r.Vulkan.Submission.AllowTimelineSemaphores=0`
（UE 5.8 里是 dummy cvar，`[[...]] deferred - dummy variable created`，实验无效）、
`radv_wait_for_vm_map_updates=true`、`MegaLights=0`、`r.Vulkan.AsyncCompute=0`。

结论：**UE 渲染子系统全排除，唯一不变量 = 高频提交序列**。UE 源码侧不可修。

## 4. 软件渲染兜底不可行（不要试）

lavapipe（`lvp_icd.json`）能枚举设备、支持 VK_KHR_ray_tracing_pipeline，但**不满足 UE 的
VP_UE_Vulkan_SM6 Profile 全部 criteria** → `None of the 1 devices meet all the criteria!` →
跳过唯一的 SF_VULKAN_SM6 → 无可用 RHI。CARLA 没有 SM5 着色器可退（`DefaultEngine.ini`
`-VulkanTargetedShaderFormats=SF_VULKAN_SM6`）。加 `-sm5` 会 zenity 弹窗卡死。

**弹窗诊断法**：UE 早期致命错误走 zenity，文本不进 stdout。用
`xwininfo -root -tree | grep -i zenity` 拿窗口 ID，再 `x11_window_shot.py <WID> out.png`
截图直接读错误文本。

## 5. 环境坑速查

| 坑 | 处置 |
|---|---|
| `Refusing to run with the root privileges` | 别用 root 跑；受限容器（无 /dev/dri）内需 LD_PRELOAD geteuid shim，但正确做法是在宿主机用户 shell 跑 |
| `sudo` 交互密码 / 容器内 setpriv 失败 | 容器 root + no_new_privileges 限制，无解；去宿主机终端 |
| setsid/nohup 后台进程被清 | 受限容器独立 PID namespace 会清理；宿主机正常。容器内需脚本内 wait 保活 |
| mutter ANR 杀窗口 | `gsettings org.gnome.mutter check-alive-timeout 0` 关闭 ANR 杀窗 |
| `fork: Resource temporarily unavailable` | 不是 cgroup pids 配额；是 ShaderCompileWorker 批量拉起。压 `[DevOptions.Shaders]` worker 数 |
| XAUTHORITY 失效 | mutter 重启换文件，动态 `ls -t /run/user/1000/.mutter-Xwaylandauth.* | head -1` |
| 窗口截图全黑/失败 | XWayland 拒绝 root window 抓取（PIL ImageGrab / ffmpeg x11grab / GNOME DBus 全败）；用 `x11_window_shot.py` 对具体 WID 的 XGetImage |
| pkill 误杀自己 | 模式里用字符类：`pkill -f "[U]nrealEditor"` 或锚定二进制绝对路径 |
| `ALIVE after 240s` 误判 | 固定秒数 kill 不构成稳定性证据（冷启动全耗在资产构建）；判据必须是 RPC+窗口+帧号+无 hang 四判据 |
| 蓝图 ID | `vehicle.tesla.model3`，不是 `model_3` |

## 6. 帧节奏与"画面抽搐"（运动镜头抖动）

**现象**：窗口渲染正常、车在动，但跟随镜头明显一顿一顿/抖动。

**诊断方法**（RPC 直接测 server 每帧耗时，比日志可靠）：
```python
import carla, time, statistics
c = carla.Client('127.0.0.1', 2000); c.set_timeout(10.0)
w = c.get_world()
d, f, t0 = [], [], time.time()
while time.time() - t0 < 12:
    s = w.get_snapshot()
    d.append(s.timestamp.delta_seconds); f.append(s.frame); time.sleep(0.3)
print(f"中位 {1/statistics.median(d):.1f} fps, 帧间隔 min/med/max = "
      f"{min(d)*1000:.0f}/{statistics.median(d)*1000:.0f}/{max(d)*1000:.0f} ms")
```

**本机实测演进（2026-11-27 深夜）：**

| 配置 | 帧节奏 | 处置 |
|---|---|---|
| Epic@1066x600 | ~34fps，帧间隔 20~37ms 乱跳（±50%） | 降画质档位 |
| Low@1066x600 | 73fps 均匀 | 升分辨率后自然解决 |
| Low@4K（SP=100，全分辨率） | ~19fps，43~63ms 乱跳（GPU 撑不动 8.3MP；之前小窗口 57fps 数据不可比） | **r.ScreenPercentage=50** |
| Low@4K（SP=50，最终态） | **30.0fps，帧间隔全部精确 33.3ms**，GPU busy 65~80% | 稳定；观感偏软可试 SP=60 |

**瓶颈隔离实测**（2026-11-27 23:40，RPC 三相位对照）：空载 / 100Hz 跟随 / 30Hz 跟随
全部 30.0fps、帧间隔 33.3ms 纹丝不动，GPU busy 65~80%、APU 功耗 ~50W——
**gfx1151 算力充足，RPC 负载对帧节奏零影响**。注意 GameUserSettings 的
FrameRateLimit 只在 GPU 实际帧率高于上限时才起作用；GPU 追不上时表现是帧间隔乱跳而非降档。

**处置原则**：GPU 实际帧率必须锁定在显示器刷新率的**整因子**（60/30）上——
"撑不满 60 的 57fps"比"稳定的 30fps"更抖。30fps 每帧恰好显示 2 次刷新，绝对均匀。
`-quality-level` 用 PascalCase（写错不报错，日志打 "unrecognised" 后静默按原样跑）。

**设备澄清**：UE `DeviceName: Radeon 8060S Graphics` = 内核 `amdgpu 0000:c5:00.0` = gfx1151
本体；Strix Halo 无独显，"iGPU"就是本机唯一 GPU。

### 6.0 跟随镜头的"追赶波纹"（demo v3 定案，2026-11-27 23:44）

即使 server 帧间隔完美（33.3ms）、车辆物理平滑（速度差中位 1mm/s），第三人称跟随视角
**仍可能看到车在画面里微微晃**——最后一层来源是客户端跟随相机的**指数插值**：

- 目标机位每 server tick（~33ms）阶梯式跳一次，插值逼近时相机移动速度忽快忽慢
  （30Hz 速度波纹）→ 车与画面的相对几何不稳定 → 肉眼可见微抖。
- **修复 = 刚性锁定机位**（`demo_show.py` v3）：相机位姿 = 车位姿 + 固定偏移，**零插值**，
  与渲染 tick 严格同源 → 车在画面中完全静止，世界以均匀帧率平移，相对抖动物理上为零。
- 教训：**跟随相机永远不要对阶梯目标做指数插值**；要平滑用"速度外推"，或者直接刚性锁定。
- 残余说明：刚性锁定后快速横移仍非"丝滑"——那是 30fps 在 59.96Hz 屏上的物理性频闪
  （strobe，非故障）。要真 60fps：窗口 2560×1440 + `r.ScreenPercentage=60` +
  `FrameRateLimit=60`（内部负载与 4K+SP50 相当，帧率预算翻倍）。

**车辆物理平滑度诊断**（区分"车抖"还是"镜头抖"）：
```python
# 连续采样 ego.get_velocity()：相邻速度差中位 <5mm/s = 物理平滑，抖动来自镜头/渲染
```

### 6.1 窗口不平铺（Wayland 陷阱，2026-11-27 23:20 实锤）

**现象**：`FullscreenMode=0` 写进 GameUserSettings.ini，窗口仍不铺满、全屏状态不生效，
且 `xwininfo -root -tree` 里**根本看不到 UE 窗口**。

**根因**：UE 5.8 内嵌 SDL2 带 x11（recommended）与 wayland（suggested）两个视频后端，
Wayland 可用时被优先选中 → UE 窗口是 **Wayland 原生**，不受 X/GNOME 全屏规则管理，
X 工具全部失效（这也是之前窗口截图/监控一直绕弯路的原因）。
依据：引擎二进制 feature manifest + `LinuxPlatformApplicationMisc.cpp:446 IsUsingWayland()`。

**修复**：启动环境加 **`SDL_VIDEODRIVER=x11`** 强制走 XWayland。验收标志：
启动后 `xwininfo -root -tree | grep -i unreal` 能看到窗口 = X11 后端生效，
此后 `FullscreenMode=0` 真全屏正常生效（连 GNOME 顶栏都被覆盖）。

## 7. 一次成功的启动长什么样

UE 日志（`Saved/Logs/CarlaUnreal.log`）关键行：

```
LogRHI: RHI Adapter Info:
LogRHI:             Name: Radeon 8060S Graphics
LogRHI:   Driver Version: 2.0.349 (internal:2.0.349, unified:)   ← AMDVLK 生效（RADV 是 Mesa 26.x 格式）
LogRHI: Warning: Running with bad GPU drivers but warning dialog will not be shown: ... r.WarnOfBadDrivers=0
LogLoad: (Engine Initialization) Total time: ...
LogCarla: Spawning actor 'vehicle.tesla.model3'
LogCarla: Sensor 3 'sensor.camera.rgb' created in primary server
```

内核侧同时间段 `journalctl -k` 无任何 `ring gfx.*timeout`。

### 6.2 画面在两个视角间闪烁（双 demo 并发，2026-11-27 23:46 实锤）

**现象**：跑 `demo_show.py` 后画面在"跟车视角"和"一个无车的固定视角"之间来回闪。

**根因**：**两个 demo 进程在同时写 spectator 相机**。常见触发：一键脚本
`amdvlk_carla_go.sh` 的 demo 阶段（600s 时限）还没结束，又在另一个终端手动启动了
demo——两个进程各自 100Hz `set_transform`，UE 每 tick 只采纳一个 → 视角交替。
"无车固定视角"就是另一个进程的相机（它的 ego 已被本实例的启动清理销毁）。

**易踩坑背景**：demo 退出/Ctrl-C 后，TrafficManager autopilot 在 **server 侧继续开车**
（客户端断连不影响 server 行为），spectator 也留在最后位姿——不要据此判断 demo 还在跑。
确认方法：`pgrep -f demo_show`。

**修复**：demo_show.py v3.1 加 flock 单实例锁（`/tmp/carla_demo_show.lock`）——
第二个实例直接退出并打印处置指引。手动处置：
```bash
pkill -f demo_show    # 杀掉所有 demo 实例后再跑
```

### 6.3 闪烁（二）：相机周期性跳到固定异位（v3.2 传送滤波，23:52）

v3.1 单实例锁排除了双进程后若仍闪烁，且异常视角固定在 **(-9,0,7) yaw=0**
（= 全零车位姿代入跟拍公式 `loc-fwd*9, +7` 的结果），则根因是
**客户端 `ego.get_transform()` 间歇返回全零位姿**（server 侧数据完好，仅 demo
客户端在高频 100Hz RPC 并发下出现，容器侧同 actor 247 次采样零异常）。

**修复**：demo_show.py v3.2 传送滤波——新位姿与上次有效位姿距离 >15m 即判毛刺丢弃。
**通用教训**：CARLA 跟随相机代码必须对 actor 读数做传送滤波（teleport filter），
不要盲信每次 get_transform；位置突变超出物理可能时沿用上一帧。

**上游出处（无需另提 issue）**：这是 CARLA 官方已记载的设计行为——
[carla#1424](https://github.com/carla-simulator/carla/issues/1424)（维护者 nsubiron）：
*"get_transform instead of calling the simulator returns the transform that was received
last tick... returns a defaulted actor state."* 即 get_transform 读客户端缓存快照，
新 spawn 的 actor 未入流时返回全零。社区惯例 `world.wait_for_tick()` after spawn；
本指南 demo 用 wait_for_tick + 传送滤波双保险。相关：set/get transform 异步延迟
（async 模式下 set_transform 下一 tick 才生效，属设计而非 bug）。

### 6.4 残余"前后呼吸"（v4 tick 驱动 + 外推，11-28 00:00）

刚性锁定后仍有轻微前后抖（幅度随车速）——这是**一个 tick 的相机滞后 + 100Hz
自由循环相位漂移**的合成：

1. `get_transform()` 返回上一 tick 快照（carla#1424）
2. `set_transform()` 下一 tick 才生效
3. 自由循环读数相位在 tick 边界漂移 → 相机-车距在 0~v·Δt（8.3m/s×33ms≈27cm）波动

**修复（demo v4）**：`world.wait_for_tick()` 每 tick 恰好一次（相位锁定）+
`snapshot.find(ego.id)` 权威单源位姿 + **速度外推一 tick**（`loc += v·Δt`）。
**跟随相机四件套：tick 驱动、snapshot 单源、一 tick 外推、传送滤波。**
API 注意：`ActorSnapshot` 用 `get_transform()/get_velocity()` 方法，无 `.transform` 属性。

**完整问题链见 [incident-log.md](incident-log.md)（全部 20+ 问题逐条归档）。**

**v4 交接更新（00:15）**：v4 外推实测反而产生周期性 -0.28m（恰一 tick）离散硬跳；
容器只读实验证明快照流干净、外推模型完美 → 嫌疑集中在写路径（HUD RPC 触发 set_transform
延一 tick）。A/B 开关 `DEMO_NO_EXTRAP=1` 已备；实验计划 Test A/B/C 与测量脚本见
PROGRESS.md「11-28 凌晨」交接段。

**v6 修复（00:20）**：原子快照复测证明 -0.28m 跳变真实（非缓存假象），spectator 无原生
attach 接口 → 相机写入解耦：独立 Client + 100Hz 推送线程 + HUD 分离连接，消除 RPC 延迟
空窗。验收：原子快照前向误差 std <0.03m（改前 0.089m）。

## 7. v8 之后仍偶发抖动的隐藏根因：VSync 帧时间量化（2026-11-28 01:05）

**现象**：跟随相机已是 v8（单写手 + snapshot 单源 + 一 tick 外推 + HUD 关闭），静止/短时观察零抖动，但长时间行驶每隔约 1 秒仍有 ±0.1~0.22m 的前后硬跳。

**诊断方法**（只读，不改任何 actor）：容器侧每 tick 取 `snap.find(ego.id)` 与 `snap.find(spectator.id)`，算相机相对车的前向距离 `along`。结果：
- 47 次 >4cm 跳变集中在两段，其余 tick 精确 9.0005m；
- 跳变时车速恒定 8.25m/s、`dv=0`（排除物理/加减速/HUD/线程竞争）；
- dt 从稳定 0.0333s 跳到 0.04~0.05s，跳变严格与 dt 尖峰同步。

**模型**：相机位移 = `v*(2*dt[i-1]-dt[i-2])`（set_transform 延迟一 tick 生效的必然结果），车位移 = `v*dt[i]`。dt 恒定时两者相等 → 零抖；dt 量化跳变时失配 → 硬跳。

**根因**：`bUseVSync=True` + 30fps 上限 + 59.96Hz 显示器 = 帧时间被量化成 33ms（2 个 VSync）/ 50ms（丢 1 个 VSync）两档乱跳。

**修复**：`bUseVSync=False`，保留 `FrameRateLimit=30` 由定时器控制。
- 新启动：`scripts/amdvlk_carla_go.sh` 已默认关闭 VSync；
- 正在跑的窗口免重启热修复（X11 可达时）：`xdotool windowactivate <UE窗口ID>; xdotool key grave; xdotool type 'r.VSync 0'; xdotool key Return`；
- 验收：45s/1334 tick 跳变 0 次、dt 尖峰消失。
- 兜底：若出现撕裂，改回 `bUseVSync=True` + `FrameRateLimit=60`。
