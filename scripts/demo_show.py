# CARLA 原生窗口演示：第三人称跟随视角看 Tesla Model 3 自动驾驶（TM）绕 Town10HD_Opt
# 用法: python demo_show.py [秒数=600]
# 依赖: 已运行的 CARLA server（RPC 2000），PythonAPI 已装 carla 模块
# 说明: spectator 视角直接显示在 UE 窗口里（server 必须是 -game -windowed 窗口模式）
#       车顶悬浮 HUD 用 world.debug.draw_string 实现（3D 锚定，随场景渲染）
# v3: 刚性锁定机位（无插值）——插值对 30Hz 阶梯目标会产生"追赶波纹"（30Hz 相机速度起伏，
#     车在画面里微微晃）；刚性锁定下相机与车每个 tick 精确对齐，车在画面中完全静止、
#     世界以均匀 30fps 平移 = 零相对抖动。v2 实测帧间隔 33.3ms 完美均匀仍"抖"即此原因。
# v3.1: 单实例锁（flock）——两个 demo 进程并发写 spectator 会造成画面在两个视角间闪烁
#      （2026-11-27 23:46 实锤：go 脚本 demo 未结束又手动起第二个 demo）。
# v3.2: 传送滤波——实测客户端 ego.get_transform() 会间歇返回全零位姿（server 侧数据
#      完好、容器侧 247/247 正常，仅 demo 客户端出现，根因未定位，疑似高频 RPC 与
#      快照流竞争）→ 相机跳到 (-9,0,7) 原点后上方 = 画面闪烁。修复：新位姿与上次
#      有效位姿距离 >15m 即判定为毛刺，本帧丢弃相机更新。
# v4:  tick 驱动 + 一 tick 速度外推——根治"车辆前后抖动（呼吸）"：
#      ① get_transform 返回上一 tick 快照（carla#1424 设计行为）
#      ② set_transform 下一 tick 才生效
#      ③ 100Hz 自由循环的读数相位在 tick 边界漂移 → 相机-车距在 0~v*Δt（~27cm）
#         之间波动 = 前后呼吸。
#      v4 改为 wait_for_tick() 每 tick 恰好更新一次相机（读数相位锁定在 tick 边界），
#      并用本 tick 速度外推一 tick（相机位姿 ≈ 车在 T+1 的真实位姿）→ 恒定零滞后。
# v4.1 实测定性（2026-11-28 00:08，容器双实验）：
#      实测一：用户反馈 v4 "前后抖动厉害"（比 v3.2 更重）。8s 高频采样前后误差 =
#      两离散值跳变——绝大多数 tick 精确 0.00，周期性（~0.5s）跳到 -0.28m
#      （= 恰好一个 tick 位移 8.3m/s×33ms）→ 相机在两个离散位姿间硬跳。
#      实测二：容器只读 wait_for_tick 10s——快照流完全干净（120/120 帧号间隔全 1，
#      无旧快照/漏帧），外推预测误差中位 0.1cm/最大 0.5cm、0/119 超 15cm
#      → 外推模型完美，嫌疑集中到【写路径】：
#      ① HUD draw_string/get_traffic_light RPC（0.25s 间隔）与跳变周期量级吻合，
#        可能偶发使 set_transform 延一 tick 应用（首要嫌疑）
#      ② set_transform RPC 在 server 侧偶发延一 tick
#      交接实验计划（见 PROGRESS.md「11-28 凌晨」段）：
#      Test A: DEMO_NO_EXTRAP=1 跑本脚本 + 容器误差采样 → 判外推 vs 写路径
#      Test B: 注释 HUD 块跑 2min → 定位 HUD RPC 触发
#      Test C: world.on_tick 回调替代 wait_for_tick
#      DEMO_NO_EXTRAP=1 = 关外推（恒定一 tick 滞后刚性锁定，理论零跳变，仅恒定 28cm 前向偏移）
import sys, time, math, fcntl, os

# 单实例锁：并发运行第二个 demo 会双写 spectator → 画面闪烁
_LOCK = open('/tmp/carla_demo_show.lock', 'w')
try:
    fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    pid = open('/tmp/carla_demo_show.lock').read().strip()
    print(f"[demo] 已有 demo 实例在跑（PID {pid}）。画面闪烁=两个进程在抢 spectator 相机。")
    print("[demo] 处置：去那个终端 Ctrl-C 旧 demo，或 pkill -f demo_show 后再跑本脚本。")
    sys.exit(1)
_LOCK.write(str(os.getpid()))
_LOCK.flush()

import carla

HOST, PORT = "127.0.0.1", 2000
BP_FILTER = "vehicle.tesla.model3"
SPAWN = carla.Location(106, -12, 2)  # Town10HD 常用出生点（与 demo_drive.py 一致）
SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 600
CAM_HEIGHT, CAM_DIST, CAM_PITCH = 7.0, 9.0, -18  # 第三人称机位参数
HUD_INTERVAL = 0.25  # HUD 刷新间隔（秒）；life 略大于间隔，保证同屏最多 1~2 条不叠影
# A/B 实验开关：
#   DEMO_NO_EXTRAP=1 关闭速度外推（交接 Test A 用）
#   DEMO_HUD=1 开启车顶 HUD（默认关！2026-11-28 00:30 实锤：HUD 的 draw_string +
#   get_traffic_light RPC 每 0.25s 一次，server 侧串行处理导致 set_transform 晚一 tick，
#   跳变严格每 8 tick 一次 = 264ms ≈ HUD 间隔。关 HUD 是零抖动的必要条件）
NO_EXTRAP = os.environ.get("DEMO_NO_EXTRAP") == "1"
HUD_ENABLE = os.environ.get("DEMO_HUD") == "1"

client = carla.Client(HOST, PORT)
client.set_timeout(30.0)
world = client.get_world()
bp_lib = world.get_blueprint_library()
debug = world.debug

# 清理上一轮遗留的 ego，避免 spawn 冲突
for old in world.get_actors().filter(BP_FILTER):
    old.destroy()
    print(f"[demo] 清理遗留车辆 {old.id}")

for a in world.get_actors().filter("traffic.*"):
    if hasattr(a, "set_suspended"):
        a.set_suspended(False)

ego_bp = bp_lib.find(BP_FILTER)
ego_bp.set_attribute("color", "255,40,40")  # 红色车身，窗口里显眼
tm = client.get_trafficmanager(8000)
tm.set_global_distance_to_leading_vehicle(2.5)
tm.set_synchronous_mode(False)

spawn_points = world.get_map().get_spawn_points()
sp = next((p for p in spawn_points
           if abs(p.location.x - SPAWN.x) < 3 and abs(p.location.y - SPAWN.y) < 3),
          spawn_points[12])
ego = world.try_spawn_actor(ego_bp, sp) or world.try_spawn_actor(ego_bp, spawn_points[12])
assert ego is not None, "ego spawn 失败"
ego.set_autopilot(True, 8000)
# 官方已知行为（carla#1424，维护者 nsubiron 确认）：get_transform 返回客户端缓存的
# "上一 tick 快照"，刚 spawn 的 actor 未入快照流时返回默认值（全零位姿）→ 相机会跳
# (-9,0,7)。等一个 tick 让 actor 进入快照流再开始跟随（v3.2 传送滤波是第二道兜底）。
world.wait_for_tick()
print(f"[demo] ego {ego.id} 已生成并开启 TrafficManager autopilot (port 8000)")
print(f"[demo] HUD {'开启(DEMO_HUD=1, 可能每8tick跳一次)' if HUD_ENABLE else '关闭(零抖动优先; DEMO_HUD=1 可开启)'}  外推 {'开' if not NO_EXTRAP else '关'}")

# v8: 撤销 v6 的独立推送线程——35s/1043 tick 实测 v7 仍有 9 次 ±0.24~0.3m 偶发跳变，
# 速度恒定 8.2m/s（与加减速无关）、无 8-tick 周期 → 根因是 100Hz 推送线程自身与
# server tick 相位竞争（推送到的 target 在 tick 边界前后被覆盖/延迟）。
# 终态：每个 tick 在主循环内直接 set_transform 一次（tick 驱动、单写手、无线程竞争），
# 保持 v7 的 HUD 默认关闭（消除 8-tick 周期跳变）与一 tick 外推。
spectator = world.get_spectator()


vmax, t0 = 0.0, time.time()
last_valid_loc = None       # 上次有效 ego 位置（传送滤波基准）
last_valid_yaw = None
last_hud, last_tl_free, tl_now = 0.0, True, ""
try:
    while time.time() - t0 < SECS:
        # tick 驱动：每个 server tick 恰好更新一次相机（读数相位锁定 tick 边界，
        # 消除 100Hz 自由循环的相位漂移呼吸）。位姿取自本 tick 权威快照（单源，
        # 免受 get_transform 缓存竞争影响）。
        snap = world.wait_for_tick()
        asnap = snap.find(ego.id)
        if asnap is None:
            print("[demo] ego 快照缺失，跳过本 tick")
            continue
        dt = snap.timestamp.delta_seconds
        raw = asnap.get_transform()
        v = asnap.get_velocity()
        speed = math.sqrt(v.x**2 + v.y**2 + v.z**2)
        vmax = max(vmax, speed)

        # 传送滤波：与上次有效位置的位移超过 15m（一个 tick 内物理不可能）=
        # 全零/脏位姿毛刺，丢弃本次读数，沿用上次有效位姿驱动相机，画面不跳。
        if last_valid_loc is not None and raw.location.distance(last_valid_loc) > 15.0:
            base_loc, base_yaw = last_valid_loc, last_valid_yaw
        else:
            base_loc, base_yaw = raw.location, raw.rotation.yaw
            last_valid_loc, last_valid_yaw = raw.location, raw.rotation.yaw

        # 一 tick 速度外推：本循环发出的 set_transform 在下一 tick(T+1) 生效，
        # 车在 T+1 的位置 ≈ T 位姿 + v·Δt → 相机与车恒定零滞后，前后呼吸消失。
        # （DEMO_NO_EXTRAP=1 时跳过：恒定一 tick 滞后，理论零跳变，交接 Test A）
        if NO_EXTRAP:
            loc = base_loc
        else:
            loc = carla.Location(x=base_loc.x + v.x * dt,
                                 y=base_loc.y + v.y * dt,
                                 z=base_loc.z + v.z * dt)
        fwd_x = math.cos(math.radians(base_yaw))
        fwd_y = math.sin(math.radians(base_yaw))
        # 单写手：每 tick 一次直接 set_transform（无线程、无 HUD 争用）
        spectator.set_transform(carla.Transform(
            carla.Location(x=loc.x - fwd_x * CAM_DIST,
                           y=loc.y - fwd_y * CAM_DIST,
                           z=loc.z + CAM_HEIGHT),
            carla.Rotation(pitch=CAM_PITCH, yaw=base_yaw)))

        # 车顶悬浮 HUD（3D 文字，随场景渲染在 UE 窗口里；本循环 30Hz 天然节拍）
        # 默认关闭：实测其 RPC 与相机写同 tick 时造成相机晚一 tick（每 8 tick 硬跳）。
        # 需要看车辆参数时设 DEMO_HUD=1（接受可见跳变）。
        if HUD_ENABLE and time.time() - last_hud > HUD_INTERVAL:
            now = time.time()
            last_hud = now
            tl = ego.get_traffic_light()
            tl_free = not (tl and tl.is_frozen)
            if tl_free != last_tl_free:
                print(f"[demo] t={now-t0:6.1f}s {'绿灯通行' if tl_free else '红灯等待'} @ "
                      f"({base_loc.x:.0f},{base_loc.y:.0f}) {speed:.1f} m/s")
                last_tl_free = tl_free
            tl_now = "" if tl_free else "  [RED LIGHT]"
            hud = (f"{speed*3.6:5.1f} km/h   vmax {vmax*3.6:5.1f} km/h\n"
                   f"t {now-t0:6.1f}s   pos ({base_loc.x:.0f},{base_loc.y:.0f}){tl_now}")
            debug.draw_string(
                base_loc + carla.Location(z=3.0), hud,
                draw_shadow=True, color=carla.Color(255, 220, 0),
                life_time=HUD_INTERVAL * 1.2, persistent_lines=False)

except KeyboardInterrupt:
    print("\n[demo] 用户中断")
finally:
    ego.set_autopilot(False)
    ego.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
    print(f"[demo] 结束: 时长 {time.time()-t0:.0f}s, vmax={vmax:.2f} m/s, "
          f"终点 ({ego.get_transform().location.x:.0f},{ego.get_transform().location.y:.0f})")
    print("[demo] 车辆已刹停留在场景中（继续观看可再跑本脚本或 demo_drive.py）")
