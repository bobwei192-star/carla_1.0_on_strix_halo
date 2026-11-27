# 问题全记录（Incident Log）：Strix Halo gfx1151 跑通 CARLA 1.0 全过程中遇到的每一个坑

> 时间跨度：2026-11-25 ~ 11-28。每条含【现象 → 证据 → 根因 → 修复 → 出处】。
> 本文件是完整版；troubleshooting.md 是提炼版（按症状查）。日期不带年份默认 2026-11。

---

## A. GPU / 驱动层（11-25 ~ 11-27）

### A1. RADV gfx1151 GPU hang（本战役的核心问题）
- **现象**：UE 窗口模式 `ring gfx_0.0.0 timeout`，Frame 0-2 即死；SceneCapture（camera sensor）链 6 次 hang 全挂。
- **证据**：RADV 提交级插桩（ac_drm_cs_submit_raw2）——提交正常返回，`-ETIME` + emitted−signaled=2 在途提交楔死；420 条 trace 全部正常 → bug 在 CP/ring-fetch/固件层。挂点漂移：GPUSceneUpdate / RayTracingScene / SceneCapture AsyncCompute / Graphics FX。
- **排除项**：VSM/Lumen/RT/async compute/timeline semaphore 等 UE 侧 15+ 项实验；上游 gc_11_5_1 固件 5 文件升级（无效）；radv_wait_for_vm_map_updates 旋钮（无效）；lavapipe 软件 Vulkan（缺 SM6 Profile 必需特性，枚举即拒）。
- **修复（绕过）**：**AMDVLK 2025.Q2.1**，per-app `VK_ICD_FILENAMES=/etc/vulkan/icd.d/amd_icd64.json`。窗口 793+ tick 零 hang、camera 出图、端到端全绿。
- **定性**：RADV gfx1151 特有底层 bug，与 mesa#13411/#16943 机制均不同。Mesa issue 草稿 `docs/12-mesa-issue-draft.md`（最强证据形态），待提交。
- **注意**：`r.Vulkan.AllowAsyncCompute=0` 实测**有害**（frame 4-7 即死），勿加。

### A2. MES reset 全失败 → gpu_recovery=1 兜底
- **现象**：hang 后 MES reset 全部失败，只能整机楔死。
- **修复**：内核参数 `amdgpu.noretry=0 amdgpu.gpu_recovery=1`。11-27 20:30 一次 hang 首次被 ring reset 成功恢复（"device wedged, but recovered through reset"）。
- **运维铁律**：GPU 亚健康（同配置不同结果）→ 重启恢复，别硬扛。

### A3. UE 退出时 Signal 11 / 残留 UnrealTraceServer
- **现象**：SIGTERM 退出时偶发段错误；窗口模式残留 UnrealTraceServer 守护进程。
- **根因**：官方 Skills 记载 SIGTERM 下 SIGSEGV 属已知行为；trace 守护进程残留可致 Trace writer 线程 SIGSEGV（与一次莫名 Signal 11 吻合）。
- **修复**：启动加 `-notrace`（官方推荐）；清理 `pkill -f '[U]nrealTraceServer'`（字符类防自匹配）。

### A4. 端口冲突 → bind 失败 + Signal 11
- **根因**：官方明确：2000/2001/2002 任一冲突时 "bind: Address already in use" 后 Signal 11。
- **修复**：启动前三端口预检 + 显式 `-carla-rpc-port=2000 -carla-streaming-port=2001`。
- **衍生坑**：UE 优雅退出期端口残留（RPC 无响应但端口开着）——脚本复用判定必须用**真实 RPC 应答验证**（get_server_version 5s），不能只测端口。

---

## B. 启动 / 环境层

### B1. root 拒绝运行
- **现象**：`Refusing to run with the root privileges`。
- **修复**：项目自带 `fake_geteuid.so` LD_PRELOAD。

### B2. mutter XAUTHORITY 每次变名
- **修复**：动态取最新 `/run/user/1000/.mutter-Xwaylandauth.*`，写死必踩坑。

### B3. UE 驱动黑名单弹窗卡 240s（AMDVLK 特有）
- **现象**：AMDVLK 报 2.0.349，UE 建议 26.0.0 → zenity 弹窗阻塞启动 240s，致命错误文本不进 stdout（需截图诊断）。
- **修复**：`-ini:Engine:[ConsoleVariables]:r.WarnOfBadDrivers=0`（出处：UE DynamicRHI.cpp 驱动版本检查逻辑）。

### B4. Zen DDC 冻结
- **修复**：`-ini:Engine:[Zen]:AutoLaunch=false`（本机实测必需）。

### B5. WorkBuddy 容器限制（交接模式）
- 无 `/dev/dri`、root+no_new_privileges → UE 窗口进程无法从容器启动，需宿主机执行。
- 独立 PID namespace：**宿主机进程从容器 ps/pgrep 不可见**（11-27 深夜才踩实——曾据 pgrep 空结果误判"无 demo 在跑"）；setsid 后台进程会被清理；flock 的 /tmp 与宿主机不互通。
- 容器内可走 RPC 127.0.0.1:2000 操作 server（诊断脚本全可用）。

### B6. ALIVE 误判
- **教训**：固定秒数 kill 判"成功启动"不可靠（引擎还在建静态网格）。判据 = RPC 就绪 + 窗口存在 + tick 推进 + 无 hang。

### B7. ShaderCompileWorker fork 风暴误归因
- **教训**：曾误判 cgroup pids 配额（实测 pids.max=132781 从未触发）；真因是 SCW 批量拉起。先量化再归因。

---

## C. 窗口 / 平铺（11-27 深夜，两层根因逐层剥）

### C1. `-resx/-resy` 命令行被无声忽略
- **现象**：无论传什么分辨率窗口都 ~1066×600。
- **证据**：日志 `LogPackageName: failed to resolve 3840`（UE 把分辨率值当地图名解析失败）+ `r.setres:1280x720`；`GameUserSettings.ini` 被回写 `ResolutionSizeX=1066, FullscreenMode=2`。
- **根因**：本构建 UE `-game` 下窗口大小/模式只认 `GameUserSettings.ini`。
- **修复**：启动脚本每次启动前**覆写 ini**（UE 退出会回写，故每次都要覆写）。

### C2. SDL Wayland 后端自动选中 → 全屏规则失效
- **现象**：ini 写了 FullscreenMode=0 仍不平铺；xwininfo 看不到 UE 窗口（监控/截图工具全失效的根源）。
- **证据**：引擎二进制 feature manifest 实锤内嵌 SDL2 带 x11(recommended)/wayland(suggested) 双后端；源码 `LinuxPlatformApplicationMisc.cpp:446` `IsUsingWayland()` 读 SDL 驱动名。
- **根因**：GNOME Wayland 会话下 UE 窗口为 Wayland 原生 → 不受 X 全屏管理，FullscreenMode 被无声回退。
- **修复**：启动环境 `SDL_VIDEODRIVER=x11`。**验收标志：`xwininfo -root -tree | grep -i unreal` 能看到窗口**。
- **佐证**：Epic 官方 tracker FORT-1004112 确认 UE SDL Wayland "unusable"。
- **上游动作**：CARLA issue 待提（清单 ③）——Ubuntu 24.04（官方支持 OS）默认 Wayland 会话下 RES=/FULLSCREEN= 全部失效。

---

## D. 帧节奏 / 抖动（四层归因，11-27 深夜）

### D1. Epic 默认画质（层 1）
- **证据**：源码 `CarlaDeviceProfileSelectorModule.cpp` `kDefaultQualityLevel{"Epic"}`；实测 34fps 帧间隔 20~37ms 乱跳。
- **修复**：`-quality-level=Low|Medium|High|Epic`。**PascalCase 陷阱**：写错不报错，日志打 "unrecognised" 后静默按原样跑。

### D2. 4K 全分辨率算力不足（层 2）
- **现象**：4K Low 仅 19fps，43~63ms 乱跳；FrameRateLimit=30 失效（GPU 追不上限值时表现是帧间隔乱跳而非降档）。
- **修复**：`r.ScreenPercentage=50`（内部 ~1080p 放大）。**注意：影响 camera sensor 内部分辨率，采数据时去掉**。

### D3. 60fps 对 59.96Hz 相位漂移（层 3）
- **修复**：`FrameRateLimit=30`（显示器刷新率整因子，每帧 2 次刷新绝对均匀）+ `bUseVSync=True`。
- **实测**：三相位瓶颈隔离（空载/100Hz/30Hz RPC 负载）全部 30.0fps、帧间隔 33.3ms 纹丝不动，GPU busy 65~80%、APU ~50W——**gfx1151 算力充足**（19fps 是全分辨率+VSM 溢出的叠加结果，非芯片上限）。

### D4. VSM 页面溢出黄字警告
- **现象**：`[VSM] cache overflow. Performance may be affected`（4K 阴影面积大）。
- **修复**：`DefaultScalability.ini` Low 档 `r.Shadow.Virtual.MaxPhysicalPages` 3072→8192。

### D5. 官方画质参考（core_rendering 页）：High+25% / Medium+65% / Low+80% 相对 Epic；Low↔Medium 仅差 ~15 个百分点；Epic 以下强制关 camera 硬件光追。

---

## E. 跟随相机 / 客户端（11-27 深夜 ~ 11-28 凌晨，四轮迭代）

### E1. 插值"追赶波纹"（v2 → v3）
- **现象**：帧间隔完美 33.3ms、物理平滑（速度差中位 1mm/s）仍见车在画面里晃。
- **根因**：指数插值逼近 30Hz 阶梯目标 → 相机速度周期性起伏。
- **修复（v3）**：**刚性锁定机位**（相机 = 车位姿 + 固定偏移，零插值）。**通用教训：跟随相机不要对阶梯目标做指数插值。**

### E2. 双 demo 并发抢相机（v3.1 锁）
- **现象**：画面在"跟车视角"和"无车固定视角"间闪烁。
- **根因**：go 脚本 demo（600s 时限）未结束又手动起第二个 → 两进程 100Hz set_transform 抢 spectator。
- **易误判背景**：demo 退出后 TM autopilot 在 server 侧**继续开车**、spectator 留在最后位姿——不代表 demo 还在跑。
- **修复（v3.1）**：flock 单实例锁（`/tmp/carla_demo_show.lock`，LOCK_EX|LOCK_NB）。

### E3. get_transform 间歇全零位姿（v3.2 传送滤波）
- **现象**：锁后仍闪，异常视角固定在 (-9,0,7) yaw=0（= 全零车位姿代入跟拍公式）。
- **取证**：容器侧同 actor 247 次采样零异常；UE 日志干净 → 仅 demo 客户端 100Hz RPC 并发下出现。
- **上游出处**：**carla#1424**（维护者 nsubiron）：get_transform 返回上一 tick 客户端快照缓存，新 spawn actor 返回默认态。**已知设计行为，不提 issue**。
- **修复（v3.2）**：**传送滤波**（>15m/单 tick 判毛刺丢弃）+ spawn 后 `world.wait_for_tick()`（官方惯例）。

### E4. 一 tick 滞后呼吸（v4 tick 驱动 + 外推，11-28 凌晨）
- **现象**：闪烁消失后仍有"车辆前后抖动"（呼吸感），幅度随车速。
- **根因**：① get_transform = 上一 tick 快照；② set_transform 下一 tick 才生效；③ 100Hz 自由循环读数相位在 tick 边界漂移 → 相机-车距在 0~v·Δt（8.3m/s × 33ms ≈ 27cm）间波动。
- **修复（v4）**：`world.wait_for_tick()` 每 tick 恰好更新一次（相位锁定 tick 边界）+ 位姿取自 `snapshot.find(ego.id)` 权威单源 + **速度外推一 tick**（`loc += v·Δt`，set_transform 生效 tick 与车实际位姿对齐）→ 恒定零滞后。
- **API 注意**：`ActorSnapshot` 的访问器是 `get_transform()/get_velocity()` 方法，**没有** `.transform/.is_alive` 属性（is_alive 仅 C++ 侧）。
- **通用教训**：CARLA 跟随相机正确写法 = **tick 驱动 + snapshot 单源 + 一 tick 外推 + 传送滤波**，四件套缺一不可。

### E5. MegaLights 红字警告
- **现象**：窗口上方红字 "MegaLights is enabled, but has no ray tracing data"。
- **根因**：DefaultEngine.ini 默认 `r.MegaLights.EnableForProject=True`，无 HW RT 平台只刷警告零收益。
- **修复**：改 False。建议提 CARLA issue（清单 ④）。

---

## F. 监控 / 诊断工具链坑

- **pkill 自匹配**：`pkill -f UnrealEditor` 会杀掉匹配自身的 shell → 用 `[U]nrealEditor` 字符类。
- **X 树无 UE 窗口 ≠ 窗口没创建**：Wayland 原生窗口（C2），X 工具全失效；X11 后端后才可见。
- **zenity 弹窗诊断法**：UE 早期致命错误不进 stdout，靠截图抓弹窗。
- **诊断脚本**（容器可直接跑，走 RPC）：帧节奏采样（snapshot delta_seconds）、瓶颈隔离三相位、spectator 位姿序列、ego 物理平滑度——全部收录于 troubleshooting.md。

## 遗留事项（截至 11-28 00:00）
1. Mesa issue 提交（等 GitLab 登录 bobwei192）——证据最强形态在 docs/12-mesa-issue-draft.md
2. CARLA Wayland issue（清单 ③）——X11 后端验证 ✓ 已取得，待补 `-ResX=` 等号形式测试
3. PR #70 收尾（VSM direct-dispatch 改动移除）
4. 4K 下 Medium 画质 / SP=60 观感对比；60fps 需 1440p 窗口

### E5. v4 外推反而加重前后抖动（2026-11-28 00:08，未定案，交接中）
- **现象**：v4（wait_for_tick + 一 tick 速度外推）后用户实测前后抖动比 v3.2 更厉害。
- **证据**：容器 8s 高频采样——前后误差两离散值硬跳（多数 tick 精确 0.00，周期 ~0.5s
  跳 -0.28m = 恰一 tick 位移 8.3×0.0333）；侧向/高度近零。容器只读 10s：快照流干净
  （帧号间隔全 1），外推预测误差中位 0.1cm/最大 0.5cm → **外推模型完美，嫌疑在写路径**。
- **首要嫌疑**：HUD draw_string + get_traffic_light RPC（0.25s）偶发使 set_transform
  延一 tick 应用 → 相机保持旧位姿一 tick = 硬跳。备选：set_transform server 侧延迟。
- **修复状态**：demo v4.1 加 `DEMO_NO_EXTRAP=1` A/B 开关（无外推=恒定一 tick 滞后
  刚性锁定，理论零跳变，仅恒定 28cm 前向偏移）。实验计划 Test A/B/C 见
  PROGRESS.md「11-28 凌晨」段。
- **教训**：① 修正一版抖动前先量化误差形态（离散跳变 vs 连续呼吸指向完全不同根因）；
  ② 只读路径干净 ≠ 写路径干净，RPC 写时序也要测；③ 一 tick 滞后是恒定偏移不是抖动，
  别为消除恒定偏移引入时变误差。

### E6. v6 相机写入解耦（原子快照复测确认真实跳变后的修复，待验证）
- **复测**：改用单一 WorldSnapshot 原子读取 ego+spectator（排除客户端缓存异步假象），
  15s/744 点仍见 -0.27m 离散硬跳，相机 tick 位移相邻差最大 56cm（车 9cm）→ 跳变真实。
- **无原生接口**：spectator 是普通 Actor（只读 parent，无 attach_to），无法绑定车自动跟随。
- **修复**：主循环（读 tick/HUD）与相机写入解耦——独立 Client 连接 + 100Hz 线程
  持续推送同一目标位姿，消除单次 RPC 延迟造成的“一 tick 空窗”；HUD RPC 不争用。
- **验收标准**：原子快照前向误差 std <0.03m、峰峰 <0.1m（改前 0.089/0.368）。
- **状态**：编译通过，待宿主机验证；回退对照 `DEMO_NO_EXTRAP=1`。

### E7. HUD RPC = 每 8 tick 硬跳的触发器（周期实锤，v7 默认关 HUD）
- **证据**：v6 20s/601 tick 原子快照，68 次跳变的帧号间隔**全部为 8**（264ms ≈
  HUD_INTERVAL 0.25s），每次持续 1 tick，误差 -0.18~-0.27m；相机独立连接无效
  （server RPC 串行处理）。
- **根因**：HUD 的 draw_string + get_traffic_light 与 set_transform 落在同一 tick 时，
  server 串行处理让相机命令晚一 tick 生效。
- **修复**：HUD 默认关闭（DEMO_HUD=1 可选，文档说明代价）；保留相机专用线程+外推。
- **通用教训**：CARLA 高频跟随相机循环中，任何同 tick 的调试绘制/查询 RPC 都可能造成
  相机延迟一 tick；追求零抖动时调试 HUD 必须OFF或降到远低于相机频率。
