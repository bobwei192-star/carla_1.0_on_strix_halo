#!/usr/bin/env bash
# 一键设置脚本（宿主机执行，sudo 权限）：Strix Halo gfx1151 跑通 CARLA 1.0 的全部系统配置
# 仅做系统配置与安装包校验，不代替 CARLA 源码构建（构建步骤见 README §5）
set -euo pipefail
echo "[setup] Strix Halo (gfx1151) CARLA 1.0 环境设置"

# 1. 内核参数（hang 恢复兜底）
if ! grep -q "gpu_recovery=1" /etc/default/grub; then
  echo "[setup] 添加 amdgpu.noretry=0 amdgpu.gpu_recovery=1 到 GRUB_CMDLINE_LINUX_DEFAULT"
  cp /etc/default/grub /etc/default/grub.bak.carla
  sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.noretry=0 amdgpu.gpu_recovery=1 /' /etc/default/grub
  update-grub
  echo "[setup] kernel 参数已写入，重启后生效（建议稍后 reboot）"
else
  echo "[setup] kernel 参数已存在，跳过"
fi

# 2. AMDVLK
if [ -f /etc/vulkan/icd.d/amd_icd64.json ]; then
  echo "[setup] AMDVLK ICD 已安装: $(cat /etc/vulkan/icd.d/amd_icd64.json | grep -o '/[^"]*libvk_swiftshader.so' || echo amdvlk)"
else
  echo "[setup] 错误：未找到 AMDVLK ICD。请安装 AMDVLK 2025.Q2.1（RPM 转 deb 或 apt 源）后重跑。"
  echo "[setup] 验证: ls /etc/vulkan/icd.d/amd_icd64.json && vulkaninfo --summary | grep AMDVLK"
  exit 1
fi

# 3. 国内环境常用依赖
for p in python3 cmake ninja-build build-essential xz-utils libvulkan-dev vulkan-tools; do
  dpkg -s "$p" >/dev/null 2>&1 || echo "[setup] 建议安装: sudo apt install -y $p"
done

# 4. 项目配置
ROOT=/home/aaa/carla-src
echo "[setup] MegaLights 默认关闭（无 HW RT）"
grep -q '^r.MegaLights.EnableForProject=False' "$ROOT/carla/Unreal/CarlaUnreal/Config/DefaultEngine.ini" || \
  sed -i 's/^r.MegaLights.EnableForProject=True/r.MegaLights.EnableForProject=False/' "$ROOT/carla/Unreal/CarlaUnreal/Config/DefaultEngine.ini"
echo "[setup] Low 档 VSM 页面 8192"
grep -A8 '\[ShadowQuality@0\]' "$ROOT/carla/Unreal/CarlaUnreal/Config/DefaultScalability.ini" | grep -q 8192 || \
  echo "[setup] 请手动把 DefaultScalability.ini 的 [ShadowQuality@0] MaxPhysicalPages 改成 8192（见 config/DefaultScalability.ini）"

echo "[setup] 完成。重启后验证内核参数，然后运行："
echo "  bash $ROOT/carla-src/amdvlk_carla_go.sh 600 1 3840 2160 Low"
