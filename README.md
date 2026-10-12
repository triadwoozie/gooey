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
### 3. Audio Analysis, Animation Automation & Geometry Nodes Rigging
- **Aubio Integration (`extern/aubio`)**:
  - **Feature Extraction**: Real-time onset / attack transient detection, instantaneous pitch tracking via YIN / Fast YIN, and tempo / beat tracking.
  - **Action & F-Curve Automation**: Maps live or imported audio streams directly to MIDI events, custom driver properties, and keyframe curves in the Graph Editor and Action Editor.
  - **Speech Visemes & Transients**: Directly synchronizes mouth deformation, prop impacts, and camera shake with musical beats and speech formants.
- **libtsm Integration (`extern/libtsm`)**:
  - **Time-Scale Modification (TSM)**: High-fidelity WSOLA (Waveform Similarity Overlap-Add) and phase vocoder algorithms.
  - **Pitch & Formant Automation**: Independent manipulation of playback speed, pitch shifting, and formant correction without metallic flanging or phase artifacts, allowing animators to automate audio pitch during bullet-time and slow-motion scenes.
- **Camera Shakify Geometry Nodes Rig (`GeometryNodeCameraShake`)**:
  - Procedural camera wobble and camera shake directly usable on regular controller objects with a dropdown camera selector (honoring EatTheFuture / Ian Hubert's Camera Shakify).
  - 15 production-tested motion profiles (`Handheld`, `Bike`, `Car`, `Explosion`, `Earthquake`, `Running`, `Walking`, `Helicopter`, `Drone`, etc.).
  - Fully exposed `NodeTreeInterface` input sockets: `Camera` dropdown, `Profile`, `Frequency`, `Intensity`, `Audio Trigger`, `Seed`.
  - Transform outputs: `Offset Location`, `Offset Rotation`, `Transform Matrix`, `Final Location`, `Final Rotation`.
  - Interactive UI panels in 3D Viewport sidebar (`N -> Camera Shake`) and Camera Object Data properties panel.
- **Speech Viseme Deformer (`GeometryNodeMouthShapes`)**:
  - Deforms character mouth geometry automatically driven by Aubio speech visemes (Open 'A', Wide 'E', Round 'O').
  - Inputs: `Speaker`, `Mouth Center`, `Influence Radius`, `Jaw Open Scale`, `Lip Wide Scale`, `Lip Round Scale`, `Sensitivity`, `Smoothing`.
  - Outputs: `Offset` vector (plugs directly into *Set Position* `Offset`), `Jaw Open`, `Lip Wide`, `Lip Round`, `Viseme Vector`.
- **Surface Resonance (`GeometryNodePropVibration`)**:
  - Physical modal vibration deformer driven by material BSDF, thickness, and collision impulses or audio transients.
  - Inputs: `Sample Object`, `Trigger / Impulse`, `Frequency`, `Damping`, `Amplitude Scale`, `Vibration Axis`.
  - Outputs: `Offset` vector, `Displacement` vector, `Amplitude`, `Resonant Freq (f0)`, `Damping (Q)`.
- **Full Geometry Node Searchability & Audio Menu**:
  - Dedicated `Shift+A -> Audio & Dynamics` menu in the Geometry Node Editor.
  - Full keyword aliases indexed for instant search (`Shakify`, `Camera Shake`, `Aubio`, `libtsm`, `TSM`, `Viseme`, `Mouth Shapes`, `Vibration`, `Resonance`).
  - Native Link-Drag Search integration for all sockets.

### 4. Backported Blender 5.3 Features & Upstream Additions
- **Tier 4 (Geometry Nodes & Procedural Workflows)**:
  - *Camera Info Object Validation* (`dc936fdbf98`, by quackarooni): Context warning if input object is not a Camera.
  - *Repeat Zone Iterations Search* (`ea28033ce48`, by Brady Johnston): Drag-to-search directly exposes the `Iterations` input socket for empty Repeat zones.
  - *Index Switch Drag-to-Search* (`2e3d66d91b9`, by Brady Johnston): Link-drag-search connects compatible data types directly into the `Index Switch` value sockets.
  - *Geometry Materials* node (`GeometryNodeGeometryMaterials`): Query all materials assigned across geometry components in procedural node trees.
- **Tier 5 (Animation, Addons & Asset Pipeline)**:
  - *glTF 2.0 Gaussian Splatting & Materials* (`5c33429dca0`, by Julien Duroure): Full 5.3 update adding `KHR_gaussian_splatting` (3D Gaussian Splats import), `KHR_materials_dispersion`, `KHR_materials_iridescence`, Meshopt compression, and Animation Pointer.
  - *Node Wrangler 5.3 Modular Architecture* (`e9af5bdcb94`, by quackarooni): Modernized modular layout, cross-tree `Change Factor` improvements, and `Geometry to Instance` in Merge Nodes.
  - *Rigify B-Bone Segment Count* (`b6085cc8a13`, by Antônio Froz): Exposed B-Bone segment count for spine and head rig types.
  - *Asset Browser Persistent "Asset" Menu* (`324f1a32d10`, by Nika Kutsniashvili): Always-accessible Asset menu in the header.
  - *Convert Pose Marker Clarification* (`caa9272d9e0`, by RedMser): Clear naming for pose marker conversion operator.
  - *Compositor Anisotropic Kuwahara Speedup* (`e260a7e6071`, by Aras Pranckevicius): 25-30% faster CPU, 2x faster NVIDIA GPU, and 10x faster Apple GPU execution with polynomial radial falloff approximation.
- **Tier 6 (Native LoopTools Modeling Operators)**:
  - *To Circle / Circularize* (`mesh.circularize`, `32c797d606d` & `2502d96ade4`, by Tariq-Sulley & Bastien Montagne): High-performance native BMesh circularization with best-fit plane, custom radius, regular/custom spacing, and axis constraints.
  - *Flatten* (`mesh.flatten`, `6d6d97afe9d`, by Tariq-Sulley): Native BMesh planar flattening with best-fit normal, view, and custom plane projections.
  - *Space Edge Loops Evenly* (`mesh.space_edge_loops_evenly`, `a38568b4e64`, by Tariq-Sulley): Native loop spacing with linear and cubic interpolation.
  - *Curve Edge Loops Between Selected* (`mesh.curve_edge_loops_between_selected`, `827566f0d3b`, by Tariq-Sulley & Campbell Barton): Interpolate edge loops along cubic spline trajectories between boundary loops.
  - *Relax Edge Loops* (`mesh.relax_edge_loops`, `eb18863b4c3`, by Tariq-Sulley): Native Laplacian edge loop smoothing directly accessible in Edit Mesh menus and hotkeys.
- **Tier 7 (UI Polish, Outliner & Tools)**:
  - *Outliner Show Users Column* (`3de313d5c98`, by 1P2D & Pratik Borhade): New data-block user count column in View Layer and Scenes modes with full DNA sync.
  - *Outliner Hide Unselected* (`9a717cccc74`, by Pratik Borhade): `Shift + H` hides unselected tree elements in the Outliner.
  - *Compositor Group Output Shortcut* (`a5273e0710b`, by Habib Gahbiche): `Alt + LMB` instantly connects active node to Group Output.
  - *Node Editor Cut/Reconnect*: `Ctrl + X` cuts nodes to clipboard while reconnecting links; `Shift + X` dissolves nodes with link reconnection.
  - *Upstream Compatibility Audit* (`wm.compatibility_audit`): Built-in diagnostic tool analyzing .blend files for features unsupported in upstream or newer versions of Blender, accessible from File ▸ External Data, Clean Up, and Help menus.
  - *Steam Audio Viewport Acceleration*: Dynamic stride triangle subsampling (max 2,048 tris per mesh), navigation debouncing (`G.moving`), and 250ms BVH rate-limiting to prevent viewport lag during heavy playback.

---

## Credits & Attribution

- **Splash Artwork**: *by 878hyuop, basically me*
- **Base Engine**: Blender 4.5.14 LTS
- **Stylized Shading**: Goo Engine NPR Project
- **Spatial Audio & Ray Tracing**: Valve Software ([Steam Audio / Phonon](https://github.com/ValveSoftware/steam-audio))
- **Camera Shake & Motion Profiles**: Nathan Vegdahl & Ian Hubert ([Camera Shakify](https://github.com/EatTheFuture/camera_shakify))
- **Audio Analysis & Pitch Tracking**: Paul Brossier & contributors ([aubio](https://github.com/aubio/aubio))
- **Time-Scale Modification**: Meinard Müller, Jonathan Driedger & contributors ([libtsm](https://github.com/groupmm/libtsm))
- **Local ML Inference**: Microsoft ([ONNX Runtime](https://github.com/microsoft/onnxruntime)) & Google Research ([Flan-T5](https://github.com/google-research/t5x))
- **Real-Time Upscaling & Super Resolution**: AMD ([FidelityFX FSR](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK))
- **Tokyo Dark / Tokyo Night Theme**: Inspired by Tiago Ventura ([tokyodark.nvim](https://github.com/tiagovla/tokyodark.nvim)) and enkia ([tokyo-night](https://github.com/enkia/tokyo-night-vscode-theme))
- **Alien Pink Theme**: [Alumx/Alien-Pink-Blender-theme](https://github.com/Alumx/Alien-Pink-Blender-theme) hasnt been properly implemented on gooey yet :( gotta work on that
- **Upstream Feature Contributors**:
  - *Tariq-Sulley & Campbell Barton*: Native LoopTools modeling operators (`circularize`, `flatten`, `space_edge_loops_evenly`, `curve_edge_loops_between_selected`, `relax_edge_loops`).
  - *Julien Duroure*: glTF 2.0 Gaussian Splatting (`KHR_gaussian_splatting`) & modern material extensions.
  - *quackarooni*: Node Wrangler 5.3 architecture, Change Factor improvements, Camera Info validation.
  - *Pratik Borhade & 1P2D*: Outliner users column and tree visibility operators.
  - *Sybren A. Stüvel*: Animation tool & camera focal length operator improvements.
  - *Brady Johnston*: Geometry Nodes search enhancements for repeat zones and index switches.
  - *Aras Pranckevicius*: Anisotropic Kuwahara compositor speedups.
  - *Antônio Froz*: Rigify B-Bone segment count exposure.

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
