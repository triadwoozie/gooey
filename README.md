# Gooey (Blender 4.5.14 LTS Port)

**Gooey** is a modern, high-performance port of the **Goo Engine NPR** pipeline and advanced graphics toolkit to **Blender 4.5.14 LTS**. Built for stylized artists and technical directors, Gooey combines expressive non-photorealistic rendering workflows with real-time post-processing techniques, custom UI aesthetics, and native system cursor integration.

---

## Key Features

### 1. Goo Engine NPR Toolset
- **Shader Nodes**: Curvature, SDF Noise, Water Ripples, Hexagon Grid, Shader Info.
- **Light Groups**: Bitmask-based light linking and group isolation in real time.
- **Color Gradients**: Native OKLab color interpolation support in Color Ramps for perceptually uniform NPR gradients.
- **Signed Frames**: Unconstrained negative timeline frame support for pre-roll animations.

### 2. Advanced Graphics Pipeline & Post-FX Nodes
- **FSR 1 Spatial Upscaling (`CMP_NODE_FSR1`)**:
  - Dual-pass GLSL compute execution featuring **EASU** (Edge-Adaptive Spatial Upsampling) and **RCAS** (Robust Contrast-Adaptive Sharpening).
  - Pass isolation options: `Full (EASU -> RCAS)`, `EASU Only`, `RCAS Only`.
  - NPR detail preservation with an input `Mask` socket modulating sharpening attenuation along cel outlines.
- **LFGA - Linear Film Grain Applicator (`CMP_NODE_LFGA`)**:
  - Linear scene-space procedural film grain evaluated prior to view/display transforms.
  - Perceptual luminance weighting concentrated in midtones ($4.0 \cdot Y \cdot (1.0 - Y)$), keeping pure blacks and peak highlights clean.
- **TEPD - Temporal Energy Preserving Dither (`CMP_NODE_TEPD`)**:
  - High-precision anti-banding quantization dither using low-discrepancy Weyl sequence temporal phase shifts ($\phi \approx 0.6180339887 \cdot \text{frame\_idx}$).
  - Triangular probability distribution function (TPDF) kernel ensuring zero integrated DC energy drift.
- **AMD FSR 3.1.5 & Live Telemetry HUD**:
  - Full Render and Output panel exposure with Quality Mode presets, sharpness slider, and real-time upscaling telemetry (resolution, ratio, frametime, reconstructed frames).
- **Upstream 5.3 Geometry & Scatter Backports**:
  - Multi-threaded surface point distribution and mesh sampling optimizations (up to 6.4x faster scatter on dense geometry).
- **Native Viewport FSR 3.1 Temporal Pipeline**:
  - Temporal integration leveraging Viewport/Render Depth and Motion Vector passes via Vulkan/ffx_api_vk bridge for interactive 3D Viewport playback and final animation rendering.

### 3. Native System Cursors & UI Refinement
- **Native System Cursors**: Window manager cursor calls map directly to standard host desktop environment / OS system cursors (Wayland, X11, Win32, macOS) without software bitmap blitting.
- **Darkened & Tokyo Themes**: Polished interface themes featuring flat inactive workspace tabs, soft rounded active pills, and seamless header integration.

---

## Credits & Attribution

- **Splash Artwork**: *Art by 878hyuop*
- **Base Engine**: Blender 4.5.14 LTS
- **Stylized Shading**: Goo Engine NPR Project
- **Spatial Audio & Ray Tracing**: Valve Software ([Steam Audio / Phonon](https://github.com/ValveSoftware/steam-audio))
- **Audio Analysis & Pitch Tracking**: Paul Brossier & contributors ([aubio](https://github.com/aubio/aubio))
- **Time-Scale Modification**: Meinard Müller, Jonathan Driedger & contributors ([libtsm](https://github.com/groupmm/libtsm))
- **Local ML Inference**: Microsoft ([ONNX Runtime](https://github.com/microsoft/onnxruntime)) & Google Research ([Flan-T5](https://github.com/google-research/t5x))
- **Real-Time Upscaling & Super Resolution**: AMD ([FidelityFX FSR](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK))
- **Tokyo Dark / Tokyo Night Theme**: Inspired by Tiago Ventura ([tokyodark.nvim](https://github.com/tiagovla/tokyodark.nvim)) and enkia ([tokyo-night](https://github.com/enkia/tokyo-night-vscode-theme))
- **Alien Pink Theme**: [Alumx/Alien-Pink-Blender-theme](https://github.com/Alumx/Alien-Pink-Blender-theme)

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
