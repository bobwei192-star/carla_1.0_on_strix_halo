# CARLA 脱屏 + PythonAPI 演示：自动驾驶 ego 车绕 Town10HD_Opt，采样轨迹/速度并绘图
import sys, time, math
import carla

HOST, PORT, TPS, SECS = "127.0.0.1", 2000, 20, 60
BP_FILTER = "vehicle.tesla.model3"
SPAWN = carla.Location(106, -12, 2)  # Town10HD 常用出生点

client = carla.Client(HOST, PORT)
client.set_timeout(30.0)
world = client.get_world()
bp_lib = world.get_blueprint_library()

traffic = world.get_actors().filter("traffic.*")
for a in traffic:
    if hasattr(a, "set_suspended"):
        a.set_suspended(False)

ego_bp = bp_lib.find(BP_FILTER)
tm = client.get_trafficmanager(8000)
tm.set_global_distance_to_leading_vehicle(2.5)
tm.set_synchronous_mode(False)

sp = next((p for p in world.get_map().get_spawn_points()
           if abs(p.location.x - SPAWN.x) < 3 and abs(p.location.y - SPAWN.y) < 3),
          world.get_map().get_spawn_points()[12])
ego = world.try_spawn_actor(ego_bp, sp)
if ego is None:
    ego = world.try_spawn_actor(ego_bp, world.get_map().get_spawn_points()[12])
assert ego is not None, "ego spawn 失败"
ego.set_autopilot(True, 8000)

cam = None
try:
    # 第三人称跟随相机仅作主视角占位（脱屏下不渲染输出，不影响物理）
    spectator = world.get_spectator()
    tf = ego.get_transform()
    spectator.set_transform(carla.Transform(tf.location + carla.Location(z=50), carla.Rotation(pitch=-90)))

    samples = []
    t0 = time.time()
    while time.time() - t0 < SECS:
        tf = ego.get_transform()
        v = ego.get_velocity()
        tl = ego.get_traffic_light()
        samples.append({
            "t": round(time.time() - t0, 2),
            "x": tf.location.x, "y": tf.location.y,
            "speed": round(math.sqrt(v.x**2 + v.y**2 + v.z**2), 2),
            "tl": bool(tl and tl.is_frozen),
        })
        time.sleep(1.0 / TPS)

    # 轻踩刹车收回车辆
    ego.set_autopilot(False)
    ctrl = carla.VehicleControl(throttle=0.0, brake=1.0)
    ego.apply_control(ctrl)

    import json
    with open("/home/aaa/carla-src/carla_demo/ego_samples.json", "w") as f:
        json.dump(samples, f)

    n_stop = sum(1 for s in samples if s["speed"] < 0.3)
    vmax = max(s["speed"] for s in samples)
    print(f"samples={len(samples)} vmax={vmax:.1f} m/s stopped_frames={n_stop}")
    print(f"start=({samples[0]['x']:.0f},{samples[0]['y']:.0f}) end=({samples[-1]['x']:.0f},{samples[-1]['y']:.0f})")
finally:
    if cam: cam.destroy()
    ego.destroy()
print("demo done")
