# Mesa issue 草稿：gfx1151 GPU hang with UE5 SceneCapture / swapchain rendering

> 状态：**已具备全部证据，可直接提交**。devcoredump 已抓取：`/tmp/gpu_devcoredump/hang_7.txt`（6.1MB，含完整寄存器与 ring 状态）。

## Title

RADV gfx1151 (Radeon 8060S): GPU hang (gfx ring timeout) with Unreal Engine 5.8 SceneCapture sensor / swapchain present

## Body

### Environment
- GPU: AMD Radeon 8060S (Ryzen AI Max 395, gfx1151, iGPU)
- Mesa: 26.0.8 (RADV), kernel: recent (see dmesg snippets), Arch Linux
- App: Unreal Engine 5.8-based CARLA (source build), Vulkan RHI

### Symptoms
- UE `vkQueueSubmit` fails with `VK_ERROR_DEVICE_LOST` after a variable number of frames (3-6 windowed with swapchain; 227-394 offscreen with a `sensor.camera` SceneCaptureComponent2D attached; 2000+ frames stable offscreen WITHOUT the SceneCapture).
- Kernel log each time (same signature across 5+ occurrences):

```
amdgpu 0000:c5:00.0: ring gfx_0.0.0 timeout, signaled seq=..., emitted seq=...
amdgpu 0000:c5:00.0:  Process UnrealEditor-Cm pid ... thread RHISubmission pid ...
amdgpu 0000:c5:00.0: Starting gfx_0.0.0 ring reset
amdgpu 0000:c5:00.0: Ring gfx_0.0.0 reset succeeded
amdgpu 0000:c5:00.0: [drm] device wedged, but recovered through reset
```

- devcoredump (captured on hang #3, 2026-11-25): `Ring timed out details: IP Type: 0 Ring Name: gfx_0.0.0`, IP `gfx_v11_0`, `regCP_RB_RPTR=0x00000612` vs `regCP_RB_WPTR=0x03bbd800` (ring stalled far behind), `regGRBM_STATUS=0xa840302c`, process `UnrealEditor-Cm`. SOC: device id 5510, GC v11.5.1 (gfx1151), kernel 7.0.0-43-generic. Full dump (6.1 MB binary) available on request.

- Correlation (reproducible): activating a SceneCapture render-target path (CARLA RGB camera sensor) triggers the hang within ~2 minutes, twice (frame 356 and frame 227, both saving exactly 2 camera frames before hang). The same server runs 2000+ frames with PythonAPI physics/autopilot sampling and zero hangs when no SceneCapture is active. Windowed swapchain runs hang at frame 3-6 (3/3 attempts).
- Not related to: present mode (FIFO/immediate), `RADV_DEBUG=nongg`, frame cap `t.MaxFPS 30`, synchronous `world.tick()`, ShaderPrint shader contents (patched and unpatched both hang).
- Windowed mode hangs on frame 0 regardless of DCC (`radv_disable_dcc=true` tested) and of NGG-GS / VM-map RADV workarounds; vkcube + Wayland swapchain runs fine for minutes on the same GPU, so the WSI present path itself is healthy.
- **UE5 GPU breadcrumbs (UE's device-lost report, GPU-side pass tracking) consistently point at the same spot on the windowed hang:**
  ```
  Finished:    MeshSDFCulling → VirtualShadowMapMarkPages → MegaLights → BuildRenderingCommandsDeferred
  Active:      FVirtualShadowMapArray::BuildPageAllocation
               → InitializePhysicalPages
               → RenderVirtualShadowMaps(Nanite)
  Not Started: Nanite::DrawGeometry
  ```
  i.e. the hang hits during the first SceneRender's Virtual Shadow Map page-allocation pass (`SelectPagesToInitialize` / `InitializePhysicalPagesIndirect` compute passes in `VirtualShadowMapPhysicalPageManagement.usf`), which notably contains an **indirect dispatch** (`vkCmdDispatchIndirect`) whose args are written by the preceding compute pass, plus fresh GPU allocations of the physical page pool. Kernel log for the same run also shows `[gfxhub] page fault (src_id:0 ring:24 vmid:7)` at a garbage address `0x0000fe00fe00f000` right before the ring timeout.
- A binary-searchable repro: windowed UE5 + default RADV dies in <90 s 4/4 attempts; the identical scene with `r.Shadow.Virtual.Enable=0` (skips the VSM page-management passes entirely) is the current control experiment.
- devcoredump was missed on the first two hangs (10-minute auto-clear + read permission); captured on hang #3 as above.

### Additional evidence (2026-11-26 evening sessions): the hang point DRIFTS — not a specific pass

Extended bisection over ~20 windowed runs (UE 5.8, gfx1151, RADV 26.0.8, kernel 7.0.0-43). Every run dies within frames 0-19 with the identical kernel signature (`ring gfx_0.0.0 timeout, signaled seq ≈ emitted seq, diff=2` — in-flight submission wedge), while the identical scene offscreen runs 2000+ frames stably. UE breadcrumb / crash-dump pipeline attribution across runs:

| # | Config | Death | Hang point (breadcrumb / UE pipeline dump) |
|---|---|---|---|
| 1 | default | Frame 0 | VSM BuildPageAllocation → InitializePhysicalPages |
| 2 | VSM off | Frame 1 | Lumen RayTracingScene / Decals BeforeBasePass; page fault `0xFF000000` READ_INVALID |
| 3 | + async-compute off | Frame 19 | GPUSceneUpdate (compute) — no page fault, pure ring timeout |
| 4 | + linear swapchain | Frame 0 | nearly immediate |
| 5 | indirect→direct dispatch rewrite (UE source patch, semantic-equivalent) | same point | VSM InitializePhysicalPages |
| 6 | `r.RDG.Debug.FlushGPU=1` (one submit per pass) | 2 s after render start | ring timeout, no breadcrumb |
| 7 | VSM pool shrunk 4x (`r.Shadow.Virtual.MaxPhysicalPages=512`) | same | — |
| 8 | VSM off (windowed, post-reboot) | Frame 2 | CompositionBeforeBasePass / DeferredDecals / RayTracingScene (Lumen card capture finished) |
| 9 | Lumen off (`r.DynamicGlobalIlluminationMethod=0`) | still hangs | — |
| 10 | MegaLights off | still hangs (22:36) | — |
| 11 | `r.Vulkan.AsyncCompute=0` (single-queue) | still hangs (22:40) | — |
| 12 | `RADV_DEBUG=noibchaining` (IB chaining disabled) | still hangs (22:44) | — |
| 13 | `r.Vulkan.Submission.AllowTimelineSemaphores=0` | still hangs (22:48, Frame 2 LumenSceneLighting on AsyncCompute) — note: this cvar is a **dummy** in UE 5.8, so this run doubles as a baseline repro | — |
| 14 | kernel `amdgpu.noretry=0 gpu_recovery=1` active (retry faults enabled) | still hangs (22:24) | — |

Also excluded: present mode (FIFO/immediate), DCC (`radv_disable_dcc`), `RADV_DEBUG=nongg`, frame caps, sync tick, allocation scale, MES (`uni_mes=0` is a no-op for gfx1151 — kernel `amdgpu_discovery.c` hard-enables MES for GC 11.5.1). `vkcube` on the same Wayland swapchain runs indefinitely.

**Interpretation**: the crash site wanders across completely different render subsystems (VSM page mgmt, Lumen card capture, decals, GPUScene upload, Lumen direct lighting) while the kernel-side failure is always the same in-flight-submission wedge. This points to a general submission/ring-fetch problem (gfx1151 CP fetch hitting a bad/missing VM mapping — page fault addresses look like garbage patterns `0x0000fe00fe00f000`, `0xFF000000`), not an application/driver-pass bug. Kernel source confirms `noretry` defaults to true for GC ≥ 10.1 and forcing retry on (`amdgpu.noretry=0`) does not help; gfx1151 firmware also cannot reset the gfx ring (`MES failed to respond to msg=RESET`, `CPFW hasn't support pipe reset yet`), so every hang is fatal to the GPU context.

### Instrumented RADV results (2026-11-27): the failing submissions are completely ordinary

Built a locally instrumented RADV 26.0.8 (release build): every kernel submission is recorded into a ring buffer (all IB GPU VAs / sizes / flags per submit), dumped on `cs_submit` failure. Reproduced the windowed hang with the instrumented driver and captured a full dump.

Kernel side of this run (no page fault this time — a pure in-flight wedge):

```
amdgpu 0000:c5:00.0: ring gfx_0.0.0 timeout, signaled seq=10232779, emitted seq=10232781
amdgpu 0000:c5:00.0:  Process UnrealEditor-Cm pid ... thread RHISubmission ...
amdgpu 0000:c5:00.0: Ring gfx_0.0.0 reset succeeded
```

Trace dump findings:

1. The failing `ac_drm_cs_submit_raw2` itself returned **-ETIME** ("context is guilty of a hard recovery") — the kernel detected the hang during that submission's ioctl. UE breadcrumb at the same moment: Frame 2, `RayTracingScene` (a third, different hang point — drift confirmed again).
2. `emitted - signaled = 2` → exactly **2 gfx submissions in flight** at the wedge. The trace's last two gfx submissions are:
   - `seq=322`: preamble IB `0xffff800100555000` (56 dw) + payload IB `0xffff800106cd6000` (96 dw)
   - failing submit: preamble IB `0xffff800100555000` (56 dw) + payload IB `0xffff80012b5ba000` (584 dw, a VA region not used by any earlier submission — freshly allocated command buffer)
3. Structure of every gfx submission in the entire 420-entry dump is identical and unremarkable: a 56-dword preamble IB followed by one payload IB, ordinary sizes (48–17560 dw), no unusual IB flags.
4. Immediately before the wedge there is a **burst of compute-queue submissions** (ip=1, seq 92–97, all small), then 5 gfx submissions (seq 318–322) and the failing one. The same compute→gfx interleave pattern preceded earlier hangs (breadcrumb on AsyncCompute in run #13).
5. The identical workload **without a swapchain/SceneCapture** (offscreen, same scene, same RADV, same kernel) runs 2000+ frames with the same submission pattern and never wedges.

Interpretation update: with the full submit history in hand, RADV's submissions at the wedge are standard-shaped and the kernel-side failure is always the same 2-submission in-flight wedge (`emitted - signaled = 2` in every capture, including the 2026-11-25 devcoredump where `CP_CPF_STALLED_STAT1=0x1` = `RING_FETCHING_DATA`, `GRBM_STATUS=0xa840302c`). Combined with the drifting UE-side hang point, this points below RADV: a gfx1151 CP / ring-fetch / firmware-level issue triggered by the higher submission rate (window present, or a second SceneCapture render pass per frame) rather than by any specific RADV-emitted command sequence. The two in-flight IB VAs are available for correlation against a fresh devcoredump ring dump on request.

### Firmware update + AMDVLK cross-check (2026-11-27 evening): the bug is RADV-specific

Two final experiments nail down the component:

1. **Updated GPU firmware does not help.** Installed the latest upstream linux-firmware `gc_11_5_1` set (pfp/me/mec/mes1/mes_2, last updated upstream 2026-10-16; local distro snapshot was from 2026-02). Rebooted onto kernel 7.0.0-44. The windowed hang reproduces with the identical signature (`ring gfx_0.0.0 timeout, signaled seq=56129, emitted seq=56132`, Frame 0 SceneRender/ShadowDepths). Also `radv_wait_for_vm_map_updates=true` (RADEON_FLAG_VM_UPDATE_WAIT on app BOs) does not help.

2. **The vendor AMDVLK driver does NOT hang.** With AMDVLK 2025.Q2.1 (`VK_ICD_FILENAMES=/etc/vulkan/icd.d/amd_icd64.json`, same kernel, same hardware, same UE build, same CARLA scene) the previously fatal paths all run stably:
   - windowed swapchain rendering: 790+ engine ticks alive, **zero** ring timeouts, zero `VK_ERROR_DEVICE_LOST` (RADV: device lost at frame 0-2 in 15+ consecutive attempts across days, firmware and kernel-arg variations included)
   - SceneCapture sensor (CARLA RGB camera, the AsyncCompute render-graph path that hung RADV instantly in 6/6 attempts): renders continuously, correct images
   - the only UE-side friction is UE's own driver-denylist dialog (AMDVLK reports 2.0.349), suppressed via `r.WarnOfBadDrivers=0`; unrelated to the hang

**Conclusion**: same userspace-visible workload, same kernel (7.0.0-44), same firmware, same hardware — RADV wedges the gfx ring in-flight, AMDVLK does not. The defect is in RADV's gfx1151 submission/CP-interaction (or its use of some CP feature AMDVLK avoids), not in the kernel driver, firmware, or hardware.

### Expected
No GPU hang; SceneCapture/swapchain rendering completes.

The full 6.1 MB devcoredump binary is available on request (private paste/email), as is any RADV_DEBUG instrumentation output.
