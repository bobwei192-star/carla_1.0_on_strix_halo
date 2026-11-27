# CARLA camera sensor 脱屏渲染验证：跟随 ego 车出图 + 俯瞰图
# 目的：1) 验证 GPU 渲染管线稳定（无 ring timeout）2) 产出可视化图片
import time, math, queue
import carla

HOST, PORT = "127.0.0.1", 2000
OUT = "/home/aaa/carla-src/carla_demo"

client = carla.Client(HOST, PORT)
client.set_timeout(30.0)
world = client.get_world()
bp_lib = world.get_blueprint_library()

# 1) 生成 ego 车（前一次 demo 已销毁）
tm = client.get_trafficmanager(8000)
sp_list = world.get_map().get_spawn_points()
sp = sp_list[12]
ego_bp = bp_lib.find("vehicle.tesla.model3")
ego = world.try_spawn_actor(ego_bp, sp)
if ego is None:
    for p in sp_list:
        ego = world.try_spawn_actor(ego_bp, p)
        if ego: break
assert ego is not None, "spawn 失败"
ego.set_autopilot(True, 8000)

# 2) 第三人称跟随相机（GPU 渲染，脱屏）
cam_bp = bp_lib.find("sensor.camera.rgb")
cam_bp.set_attribute("image_size_x", "1280")
cam_bp.set_attribute("image_size_y", "720")
cam_bp.set_attribute("fov", "90")
tf = carla.Transform(carla.Location(x=-6.5, z=2.8), carla.Rotation(pitch=-12))
cam = world.spawn_actor(cam_bp, tf, attach_to=ego)

img_q = queue.Queue()
cam.listen(img_q.put)

# 3) 跑 ~12 秒，取 4 张不同时刻的图
shots = []
t0 = time.time()
idx = 0
want = {2.0: "drive_cam_0.png", 5.0: "drive_cam_1.png",
        8.0: "drive_cam_2.png", 11.0: "drive_cam_3.png"}
while time.time() - t0 < 12.0:
    el = time.time() - t0
    for t, name in list(want.items()):
        if el >= t:
            img = img_q.get(timeout=10)
            img.save_to_disk(f"{OUT}/{name}")
            v = ego.get_velocity()
            shots.append((name, round(math.sqrt(v.x**2+v.y**2+v.z**2),1)))
            del want[t]
    time.sleep(0.2)

# 4) 俯瞰图：spectator 移到 ego 上方抓一张（X 窗口若在也能看）
spectator = world.get_spectator()
tf = ego.get_transform()
spectator.set_transform(carla.Transform(
    tf.location + carla.Location(z=60), carla.Rotation(pitch=-90)))

cam.stop()
cam.destroy()
ego.set_autopilot(False)
ego.apply_control(carla.VehicleControl(brake=1.0))
ego.destroy()

print("shots:", shots)
print("camera render OK — GPU 管线稳定出图")
