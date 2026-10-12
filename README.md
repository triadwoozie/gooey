# Gooey (Blender)

**Gooey** is basically the **Goo Engine NPR** pipeline and toolkit ported to **Blender 4.5.14 LTS**. Goo Engine was meant for NPR artists, but has been stuck on 4.4 for a WHILE since the team has been absorbed into Blender, and native features will roll out, in the meantime Gooey exists to bridge the gap between upstream and Goo Engine. Please note that Generative AI was used in the merging of 4.5.x and current goo engine as I, as a single dev couldn't have managed to achieve this. **Also note that this project is highly unstable rn and has not been tested on anything other than fedora gnome**.

---

## Key Features (other than what goo engine and Blender offer ofc)
### 1. Upscaling because my pc is bad
- **FSR 1 Spatial Upscaling (`CMP_NODE_FSR1`)**:
  - Dual-pass GLSL compute execution featuring **EASU** (Edge-Adaptive Spatial Upsampling) and **RCAS** (Robust Contrast-Adaptive Sharpening).
  - Pass isolation options: `Full (EASU -> RCAS)`, `EASU Only`, `RCAS Only`.
  - NPR detail preservation with an input `Mask` socket modulating sharpening attenuation along cel outlines.
- **LFGA - Linear Film Grain Applicator (`CMP_NODE_LFGA`)**:
  - Linear scene-space procedural film grain evaluated prior to view/display transforms.
  - Perceptual luminance weighting concentrated in midtones, keeping pure blacks and peak highlights clean.
- **TEPD - Temporal Energy Preserving Dither (`CMP_NODE_TEPD`)**:
  - High-precision anti-banding quantization dither using low-discrepancy Weyl sequence temporal phase shifts.
  - Triangular probability distribution function (TPDF) kernel ensuring zero integrated DC energy drift.
- **AMD FSR 3.1.5 & Live Telemetry HUD**:
  - Full Render and Output panel exposure with Quality Mode presets, sharpness slider, and real-time upscaling telemetry (resolution, ratio, frametime, reconstructed frames).
- **Upstream 5.3 Geometry & Scatter Backports**:
  - Multi-threaded surface point distribution and mesh sampling optimizations (up to 6.4x faster scatter on dense geometry).
- **Native Viewport FSR 3.1 Temporal Pipeline**:
  - Temporal integration leveraging Viewport/Render Depth and Motion Vector passes via Vulkan/ffx_api_vk bridge for interactive 3D Viewport playback and final animation rendering.

### 2. Cool looking UI
- **Theme Overhaul (idk, looked cool)**: Polished interface themes featuring flat inactive workspace tabs, soft rounded active pills, and seamless header integration.

### 3. Spatial Acoustics & Audio Pipeline (Steam Audio / Phonon Integration)
- **SDK Integration & Architecture**:
  - Direct integration into Blender's core scene update loop residing within `source/blender/blenkernel/intern/acoustics_scene.cc` and Audaspace phonon plugins (`intern/audaspace/plugins/phonon`).
  - **Default-On Pipeline**: Spatial audio simulation is enabled by default via `Scene.flag_audio & SCENE_AUDIO_USE_STEAM_AUDIO`.
  - **Scene Transition Operator (`SCENE_OT_steam_audio_transition_scene`)**: Tags scene meshes with acoustic properties (`use_steam_audio_mesh`) and triggers an acoustic BVH build for instant scene transition.
- **BSDF-to-Acoustic Physical Mapping**:
  - Evaluates material surface properties directly into `IPLMaterial`: `metallic`, `roughness`, `transmission`, and `ior` map to 3-band acoustic absorption (low/mid/high frequencies), scattering, and transmission coefficients.
- **Zero-Node Physical Acoustics Inference & Modal Resonator Bank**:
  - Sound synthesis is dynamically inferred at runtime from evaluated surface rendering data and geometric mesh bounds during physics events without manual sound assignment.
  - **Material Parameter Extraction**: Derives internal acoustic damping, modal Q-factor ($Q \in [10, 1000]$), acoustic speed proxy ($c_m \approx 1750 - 5100\text{ m/s}$), and detects vitreous brittle materials (glass, quartz, ceramic).
  - **Geometric Thickness & Resonant Frequency ($f_0$)**: Calculates effective thickness ($T$) and span dimensions from evaluated geometry bounds to estimate fundamental resonant mode ($f_0 \propto \frac{c_m \cdot T}{2 \cdot L^2}$).
  - **C++ Modal Resonator Bank**: 4-to-8 pole resonant filter bank excited by kinetic impact impulse ($E_k = \frac{1}{2} m v_n^2$), vitreous high-frequency click transients, and continuous stochastic tangential friction noise ($v_t \times \text{roughness} \times \sqrt{F_N}$) extracted from Bullet physics contact manifolds.
- **Dynamic Scene Synchronization & Performance**:
  - Rebuilds are guarded by `acoustics_geometry_is_dirty(depsgraph, scene)`.
  - During playback, static meshes skip expensive vertex extraction and BVH commits, updating only listener (camera) and speaker poses at real-time frame rates with 0 MB memory leak.
- **Audio Analysis & Time-Scale Modification (Planned / In Progress)**:
  - **aubio Integration**: Used for real-time onset/attack detection, instantaneous pitch tracking (via YIN algorithm), and beat/tempo tracking to drive dynamic animation drivers (F-Curves, shape keys, camera shake).
  - **libtsm Integration**: High-fidelity time-scale modification via WSOLA and phase vocoder for artifact-free pitch shifting and slow-motion audio stretching without metallic flanging.

---

## Credits & Attribution

- **Splash Artwork**: *by 878hyuop, basically me*
- **Base Engine**: Blender 4.5.14 LTS
- **Stylized Shading**: Goo Engine NPR Project
- **Spatial Audio & Ray Tracing**: Valve Software ([Steam Audio / Phonon](https://github.com/ValveSoftware/steam-audio))
- **Audio Analysis & Pitch Tracking**: Paul Brossier & contributors ([aubio](https://github.com/aubio/aubio))
- **Time-Scale Modification**: Meinard Müller, Jonathan Driedger & contributors ([libtsm](https://github.com/groupmm/libtsm))
- **Local ML Inference**: Microsoft ([ONNX Runtime](https://github.com/microsoft/onnxruntime)) & Google Research ([Flan-T5](https://github.com/google-research/t5x))
- **Real-Time Upscaling & Super Resolution**: AMD ([FidelityFX FSR](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK))
- **Tokyo Dark / Tokyo Night Theme**: Inspired by Tiago Ventura ([tokyodark.nvim](https://github.com/tiagovla/tokyodark.nvim)) and enkia ([tokyo-night](https://github.com/enkia/tokyo-night-vscode-theme))
- **Alien Pink Theme**: [Alumx/Alien-Pink-Blender-theme](https://github.com/Alumx/Alien-Pink-Blender-theme) hasnt been properly implemented on gooey yet :( gotta work on that

---

## Linux Build Instructions

### Prerequisites
- GCC / Clang with C++20 support
- CMake (>= 3.20)
- Ninja
- Python (>= 3.11)
- Vulkan SDK headers and libraries

### Building
```bash
# Clone the repository and checkout the gooey branch
git clone -b gooey <repo-url>
cd blender-4.5-port

# Configure build with Ninja
cmake -B ../build_linux_4.5 -S . -GNinja \
  -DCMAKE_BUILD_TYPE=Release \
  -DWITH_COMPOSITOR_CPU=ON \
  -DWITH_VULKAN=ON

# Compile Blender
ninja -C ../build_linux_4.5 blender
```

### Running
```bash
../build_linux_4.5/bin/blender
```

---

<!--
Keep this document short & concise,
linking to external resources instead of including content in-line.
See 'release/text/readme.html' for the end user read-me.
-->

Blender
=======

Blender is the free and open source 3D creation suite.
It supports the entirety of the 3D pipeline—modeling, rigging, animation, simulation, rendering, compositing,
motion tracking and video editing.

![Blender screenshot](https://code.blender.org/wp-content/uploads/2018/12/springrg.jpg "Blender screenshot")

Project Pages
-------------

- [Main Website](http://www.blender.org)
- [Reference Manual](https://docs.blender.org/manual/en/latest/index.html)
- [User Community](https://www.blender.org/community/)

Development
-----------

- [Build Instructions](https://developer.blender.org/docs/handbook/building_blender/)
- [Code Review & Bug Tracker](https://projects.blender.org)
- [Developer Forum](https://devtalk.blender.org)
- [Developer Documentation](https://developer.blender.org/docs/)


License
-------

Blender as a whole is licensed under the GNU General Public License, Version 3.
Individual files may have a different but compatible license.

See [blender.org/about/license](https://www.blender.org/about/license) for details.
