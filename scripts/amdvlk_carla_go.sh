#!/usr/bin/env bash
# CARLA 1.0 @ Strix Halo (gfx1151) 一键演示脚本 —— 须在【宿主机 shell】执行（需 /dev/dri + uid 1000）
# 用法: bash /home/aaa/carla-src/amdvlk_carla_go.sh [demo秒数=600] [demo开关=1] [窗口宽=3840] [窗口高=2160] [画质=Low]
#   第二参数传 0 = 只拉起 server 窗口，不跑跑车 demo
#   分辨率/平铺通过覆写 GameUserSettings.ini 实现（本构建忽略 -resx 命令行，见下）；
#   FullscreenMode=0 = 真全屏（覆盖整屏含顶栏；需 SDL_VIDEODRIVER=x11 才生效，见下）；
#   画质 Low|Medium|High|Epic（PascalCase）
#   本机实测：Epic@1066x600 34fps 帧间隔 20-37ms 抖动；Medium@1066x600 73fps 均匀；
#   4K 平铺后像素 ×13，Medium 会掉回 ~25fps → 默认 Low（约 50-60fps），帧率富余可传 Medium
# 配方来源: PROGRESS.md「11-27 晚三」本机标准启动配方（AMDVLK 2025.Q2.1, 已端到端验收）
set -u
ROOT=/home/aaa/carla-src
UE_BIN=$ROOT/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd
UPROJECT=$ROOT/carla/Unreal/CarlaUnreal/CarlaUnreal.uproject
PY=/home/aaa/miniconda3/bin/python
DEMO_SECS=${1:-600}
RUN_DEMO=${2:-1}
RESX=${3:-3840}
RESY=${4:-2160}
QUALITY=${5:-Low}
GUS=$ROOT/carla/Unreal/CarlaUnreal/Saved/Config/LinuxEditor/GameUserSettings.ini
LOG=$ROOT/ab-logs/amdvlk_go_$(date +%Y%m%d_%H%M%S).log
mkdir -p "$ROOT/ab-logs"

# 宿主机自检
if [ ! -e /dev/dri/renderD128 ] && ! ls /dev/dri/renderD* >/dev/null 2>&1; then
    echo "[ERROR] 找不到 /dev/dri/renderD* —— 本脚本必须在宿主机 shell 执行（不能在 WorkBuddy 容器里跑）"; exit 1
fi
if [ "$(id -u)" = "0" ]; then
    echo "[ERROR] 检测到 root —— UE 会拒绝运行。请在普通用户(uid 1000)的宿主机终端执行"; exit 1
fi

port_open() { (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null; }
rpc_ok() {
    "$PY" -c "import carla;c=carla.Client('127.0.0.1',2000);c.set_timeout(5.0);c.get_server_version()" >/dev/null 2>&1
}

# 官方 Skills 预检（run-carla-ue58-server）：RPC + 两个 streaming 端口都要空闲，
# 否则 "bind: Address already in use" 后 Signal 11
for p in 2000 2001 2002; do
    if port_open "$p" && [ "$p" != "2000" ]; then
        echo "[ERROR] 端口 $p 被占用（CARLA 需要 2000/2001/2002 三个端口）: ss -ltnp | grep $p"; exit 1
    fi
done

if port_open && rpc_ok; then
    echo "[go] RPC 2000 server 健康（已验证 RPC 应答），直接复用"
else
    if port_open; then
        # SIGTERM 后 UE 优雅退出期间端口可能仍短暂开着，或旧 server 已楔死 —— 两种都清掉重来
        echo "[go] 端口 2000 被占用但 RPC 无响应（server 正在退出或已楔死）→ 清理后重启"
        pkill -f "UnrealEditor-Cmd" 2>/dev/null
        sleep 5
        port_open && pkill -9 -f "UnrealEditor-Cmd" 2>/dev/null
        for i in $(seq 1 30); do
            port_open || break
            sleep 2
            [ "$i" = "30" ] && { echo "[ERROR] 端口 2000 无法释放，请手动检查: ss -ltnp | grep 2000"; exit 1; }
        done
        echo "[go] 端口已释放"
    fi
    # 动态取最新 mutter XAUTHORITY（mutter 重启后会变）
    XAUTH=$(ls -t /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
    [ -n "$XAUTH" ] || { echo "[ERROR] 找不到 mutter XAUTHORITY"; exit 1; }
    echo "[go] XAUTHORITY=$XAUTH  日志=$LOG"
    T0=$(date '+%Y-%m-%d %H:%M:%S')

    # 覆写 GameUserSettings.ini（UE -game 的窗口大小/全屏模式只认这里；
    # 实测 -resx/-resy 命令行被忽略：日志 "failed to resolve 3840" + r.setres:1280x720）
    # FullscreenMode: 0=真全屏(覆盖整屏含顶栏, 平铺) 1=无边框全屏(受 GNOME 工作区约束会留边) 2=窗口化
    # VSync 关 + 帧率上限 30（2026-11-28 定稿）：本机 59.96Hz 显示器上
    # bUseVSync=True 时 server 的 dt 会在 0.0333s（2 个 VSync）与 0.04~0.05s
    # （丢一个 VSync）之间量化跳变；而 spectator 跟随用「一 tick 速度外推」，
    # 相机位移 = v*(2*dt[i-1]-dt[i-2])、车位移 = v*dt[i]，dt 一跳两者立刻失配
    # → 画面里车每 ~1s 前后硬跳 ±0.1~0.22m（RPC 逐 tick 实测 47 次/45s，dv=0、
    # 加速度为 0，跳变与 dt 尖峰严格同步）。关 VSync 后 FrameRateLimit=30 由
    # 定时器控制，dt 连续分布、不再量化跳变，抖动随之消失。
    # 若关 VSync 后出现画面撕裂，可改回 True 并把 FrameRateLimit 提到 60
    # （需 GPU 富余；SP=50 时 4K Low 实测 ~57fps 勉强够）。
    # UE 退出时会回写本文件，故每次启动前都覆写。
    cat > "$GUS" << INI
;METADATA=(Diff=true, UseCommands=true)
[/Script/Engine.GameUserSettings]
bUseVSync=False
bUseDynamicResolution=False
ResolutionSizeX=$RESX
ResolutionSizeY=$RESY
LastUserConfirmedResolutionSizeX=$RESX
LastUserConfirmedResolutionSizeY=$RESY
LastConfirmedFullscreenMode=0
PreferredFullscreenMode=0
DisplayID=
DisplayIndex=-1
LastUserConfirmedDisplayID=
LastUserConfirmedDisplayIndex=-1
AudioQualityLevel=0
LastConfirmedAudioQualityLevel=0
FrameRateLimit=30.000000
DynamicResolutionFrameTarget=0.000000
DesiredScreenWidth=$RESX
bUseDesiredScreenHeight=False
DesiredScreenHeight=$RESY
LastUserConfirmedDesiredScreenWidth=$RESX
LastUserConfirmedDesiredScreenHeight=$RESY
LastRecommendedScreenWidth=-1.000000
LastRecommendedScreenHeight=-1.000000
INI
    echo "[go] GameUserSettings.ini 已覆写: ${RESX}x${RESY} 真全屏(平铺) + VSync关 + 30fps 上限(定时器,消除dt量化跳变)"

    # 标准配方（已对照官方文档/Skills run-carla-ue58-server 2026-11-27）:
    #   SDL_VIDEODRIVER=x11 强制 X11 后端（默认 Wayland 原生窗口不受 X 管理员全屏
    #     规则控制，FullscreenMode=0 被无声回退成普通窗口 → 永远"没平铺"；
    #     X11 后端走 XWayland，GNOME 全屏状态可正常生效，窗口也可被 X 工具观察）
    #   -game + 编辑器二进制 = 官方推荐的 uncooked 树 server 模式
    #   -carla-rpc-port/-carla-streaming-port 显式声明（官方惯例 streaming=RPC+1）
    #   -notrace 官方强烈建议：窗口启动若残留 UnrealTraceServer 守护进程，
    #     Trace writer 线程 SIGSEGV（与 11-27 一次莫名 Signal 11 吻合）
    #   r.ScreenPercentage=50：4K 真全屏实测 GPU 只有 ~19fps（43~63ms 乱跳）——
    #     iGPU 撑不动 8.3MP×Low。折半后内部渲染 ~1080p 再放大到 4K，预计 ~45fps
    #     > 30fps 上限 → 帧节奏完美均匀。观感略软，可在 40~70 之间调（对应日志里
    #     "Set CVar [[r.ScreenPercentage:...]]" 确认未被 device profile 覆盖）。
    #     注意：会同样影响 camera sensor 内部渲染分辨率（demo 无所谓，采数时去掉）。
    #   Town10HD_Opt 位置参数指定地图（官方用法）
    #   AMDVLK ICD + 画质档位 + 驱动黑名单弹窗抑制 + Zen DDC 冻结规避（本机特有）
    #   （分辨率/全屏不传命令行——由上面的 GameUserSettings.ini 控制，本构建实测忽略 -resx）
    DISPLAY=:0 XAUTHORITY="$XAUTH" SDL_VIDEODRIVER=x11 \
    VK_ICD_FILENAMES=/etc/vulkan/icd.d/amd_icd64.json \
      "$UE_BIN" "$UPROJECT" Town10HD_Opt -game -notrace \
      -carla-rpc-port=2000 -carla-streaming-port=2001 \
      -quality-level="$QUALITY" \
      -ini:Engine:[ConsoleVariables]:r.ScreenPercentage=50 \
      -ini:Engine:[Zen]:AutoLaunch=false \
      -ini:Engine:[ConsoleVariables]:r.WarnOfBadDrivers=0 \
      > "$LOG" 2>&1 &
    UE_PID=$!
    disown "$UE_PID" 2>/dev/null || true
    echo "[go] UE 启动中 (pid $UE_PID)，等待 RPC 2000 …（DDC 已热，通常 60-120s）"

    for i in $(seq 1 120); do
        sleep 5
        if rpc_ok; then
            echo "[go] RPC 2000 已就绪（真实 RPC 应答验证通过，等待耗时 $((i*5))s）"
            break
        fi
        if ! kill -0 "$UE_PID" 2>/dev/null; then
            echo "[ERROR] UE 进程退出！日志尾部："; tail -30 "$LOG"; exit 1
        fi
        [ "$i" = "120" ] && { echo "[ERROR] 600s 超时，日志尾部："; tail -30 "$LOG"; exit 1; }
    done

    # GPU hang 自检（启动期间内核若有 ring timeout 则 AMDVLK 配方失效，明确报告）
    if journalctl -k --since "$T0" 2>/dev/null | grep -q "ring gfx.*timeout"; then
        echo "[WARN] 启动期间内核出现 gfx ring timeout！查看: journalctl -k --since '$T0' | grep -i gfx"
    fi
fi

# 跑车 demo（第三人称跟随视角，直接显示在 UE 窗口里）
if [ "$RUN_DEMO" = "1" ]; then
    echo "[go] 启动第三人称跑车 demo（${DEMO_SECS}s），看窗口里的红色 Model 3 …"
    echo "[go] 结束观看可 Ctrl-C 中断 demo（server 会继续留在后台）"
    # demo 走 RPC，不需要 X 环境变量
    "$PY" "$ROOT/carla_demo/demo_show.py" "$DEMO_SECS"
fi

echo
echo "[go] 完成。server 已留在后台（RPC 2000）。"
echo "     停止 server:  pkill -f 'UnrealEditor-Cmd.*CarlaUnreal'"
echo "     再看跑车:     $PY $ROOT/carla_demo/demo_show.py 600"
echo "     camera demo:  $PY $ROOT/carla_demo/cam_capture.py"
