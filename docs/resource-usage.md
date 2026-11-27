# demo_show.py + CARLA server 资源占用（本机实测，2026-11-28）

## 测试场景
- 宿主机：AMD Ryzen AI Max+ 395（Strix Halo / gfx1151 / Radeon 8060S），128 GiB 统一内存，32 逻辑核
- CARLA 1.0 server：AMDVLK 2025.Q2.1，Town10HD_Opt，3840×2160 无边框全屏，`-quality-level=Low`
- 关键配置：`r.ScreenPercentage=50`（内部 ~1080p 渲染放大到 4K）、`FrameRateLimit=30`、`bUseVSync=True`、`SDL_VIDEODRIVER=x11`、VSM Low 档 8192 页
- 客户端：`/home/aaa/miniconda3/bin/python demo_show.py 600`（TrafficManager autopilot，车顶 HUD 关闭）

## 实测数据（10 秒连续采样，容器侧读取宿主 sysfs，值代表整机）
| 指标 | 实测 | 说明 |
|---|---|---|
| **GPU busy** | **83~95%**（10 样本：86/83/84/85/88/95/94/87/86/87） | `card1/device/gpu_busy_percent`；30fps 锁定下 GPU 有余量但接近满负荷 |
| **APU 整机功耗** | **83.3~86.3 W**（均值 ~85 W） | `hwmon5/power1_average`；Strix Halo 是 APU 整芯片（CPU+GPU+NPU）功耗，非独显 |
| **CPU load average** | **12.1~12.4 / 32 核**（1-min） | `/proc/loadavg`；约 38% 的 32 逻辑核等效负载，含 UE 渲染线程与客户端 |
| **内存（整机）** | 48 GiB used / 121 GiB total（约 40%） | `free -h`；含 GNOME 桌面、UE、CARLA、Python 客户端、page cache

## 各组件资源说明
- **CARLA server（UE 进程）**：内存主要消耗在 DDC 缓存、Shader 编译、Town10HD 场景与纹理池，约 20~35 GiB 量级（整机 used 48 GiB 含桌面与其他进程）；CPU 为多线程渲染/物理，承担 load average 的主要部分。
- **demo_show.py 客户端**：纯 Python RPC 客户端，每 tick 一次 `get_snapshot()` + 一次 `set_transform()`（v8 单写手），CPU 占用很低（<1 核的很小部分），常驻内存 ~100 MB 量级。
- **显存**：无独立 VRAM，统一内存架构；VSM 8192 页池 + 纹理池（Low 档）占用 1~3 GiB 量级。

## 复现实测
```bash
# 宿主机终端，demo 运行时：
for i in $(seq 1 10); do
  printf "gpu=%s%% power=%sW load=%s\n" \
    "$(cat /sys/class/drm/card1/device/gpu_busy_percent)" \
    "$(awk '{printf "%.1f",$1/1000000}' /sys/class/hwmon/hwmon5/power1_average)" \
    "$(cut -d' ' -f1 /proc/loadavg)"
  sleep 1
done
free -h
```
> 注：容器内 `/proc` 为独立 PID namespace，看不到宿主机 UE 进程逐进程 RSS；上表为整机/sysfs 读数。需要逐进程 RSS 时请在宿主机用 `ps -o pid,pcpu,pmem,rss,comm -p $(pgrep -f UnrealEditor-Cmd)`。

## 与画质的关系（官方相对帧率 + 本机实测）
- Epic（默认）：~34fps@1080p 小窗口，帧间隔乱跳；4K 真全屏更差。
- Low + SP=50 + 30fps 上限：稳定 30.0fps、帧间隔全 33.3ms、GPU 83~95%、85W——**本机最终稳定态**。
- 想提升清晰度：`r.ScreenPercentage` 50→60（GPU 仍约 95%，先确认能锁 30fps）。
- 想降功耗：FrameRateLimit 30→24（60Hz 屏非整因子，可能引入 judder；不推荐）。
