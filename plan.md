# Technical Architecture Plan: Gooey Physical Acoustics, Audio Automation & Safe Local AI

## Overview
This architectural roadmap details the integration of physical acoustics, audio-driven animation tooling, offline AI semantic auto-naming, and spatial wave tracing into **Gooey** (Blender 4.5.14 LTS Port).

---

## SECTION 1: PHYSICAL ACOUSTICS INFERENCE FROM BSDF & MESH THICKNESS

### 1. Zero-Node Runtime Pipeline
- Sound synthesis is inferred dynamically from evaluated surface rendering data and geometric mesh bounds during physics events:
  - Rigid body collisions
  - Particle and cloth contact events
  - Continuous surface friction and scraping
- Eliminates manual sound assignment by converting material rendering properties and mesh physics into audio synthesis parameters at runtime.

### 2. Material Parameter Extraction (`BKE_material_eval`)
- **Internal Damping & Modal Q-Factor**:
  - `metallic` and `roughness` extract physical material structure.
  - High metallicity + low roughness $\rightarrow$ low internal damping, ultra-high modal Q-factor (clear, ringing metal tones with prolonged decay tails).
  - Low metallicity + high roughness $\rightarrow$ heavy internal acoustic damping, low Q-factor (wood, organics, plastics with short, muted decay).
- **Brittle Transients**:
  - High `transmission` + specific `ior` (e.g. 1.45–1.52) detects brittle vitreous materials (glass, quartz, ceramic).
  - Triggers fast-attack, inharmonic high-frequency click/chime transients on initial contact.

### 3. Geometric Thickness & Resonant Frequency Estimation
- **Thickness Estimation**:
  - Evaluated via inverted surface normal raycasts ($-N$) against the manifold geometry or localized volume-to-surface-area ratios.
- **Fundamental Resonant Frequency ($f_0$)**:
  - $f_0$ scales inversely with local thickness and effective modal mass ($f_0 \propto \frac{1}{\text{thickness}}$).
  - Thin plates / sheet structures $\rightarrow$ high-frequency flexural vibrations and ringing chimes.
  - Thick, solid geometry $\rightarrow$ low-frequency fundamental modes (deep thuds, heavy impacts).

### 4. C++ Modal Resonator Bank (`intern/audaspace`)
- **Resonator Architecture**:
  - 4-to-8 pole resonant bandpass filter bank per contact body.
  - Excited by:
    1. Kinetic impact impulse: $E = \frac{1}{2} m v^2$ (normal velocity vector).
    2. Tangential friction noise: stochastic noise modulated by slip velocity and contact roughness.
- **Audio Output**:
  - Streams synthesized PCM buffers directly into active 3D speaker entities or the primary Audaspace spatial mixing stream.

---

## SECTION 2: AUDIO ANALYSIS & ANIMATION AUTOMATION (Aubio & libtsm)

### 1. Aubio Integration (`extern/aubio`)
- **Feature Extraction**:
  - Real-time onset / attack detection, instantaneous pitch estimation (YIN / Fast YIN), and tempo / beat tracking.
- **Action & F-Curve Automation**:
  - Converts live or imported audio streams directly into MIDI events, custom driver properties, and keyframes in the Graph Editor and Action Editor.
  - Synchronizes character mouth shapes, prop vibrations, and camera shake directly with musical beats and transient impacts.

### 2. libtsm Integration (`extern/libtsm`)
- **Time-Scale Modification (TSM)**:
  - High-fidelity WSOLA (Waveform Similarity Overlap-Add) and phase vocoder algorithms.
- **Pitch & Formant Automation**:
  - Enables independent manipulation of playback speed, pitch shifting, and formant correction.
  - Allows animator automation curves (F-Curves / Dope Sheet) to pitch-shift audio clips during bullet-time or slow-motion sequences without unwanted metallic flanging or phase artifacts.

---

## SECTION 3: SEMANTIC AUTO-NAMING & SAFETY WORKFLOW (Flan-T5 Base)

### 1. Offline Model Runtime
- **Inference Engine**:
  - Quantized **Flan-T5 Base** executed locally via **ONNX Runtime** (CPU / DirectML / Vulkan).
  - 100% offline with zero external network connectivity or telemetry.
- **Asynchronous Execution**:
  - Runs inside non-blocking background workers using thread pools to prevent UI stutter or viewport frame drops.

### 2. User Preferences & Global Opt-in
- **Preference Toggle**:
  - Located under **Edit > Preferences > System** (or dedicated **Gooey / AI Assist** tab).
  - Property: `enable_ai_auto_naming` (Boolean, default: `False`).
  - When disabled (`False`), all related AI buttons, context menu entries, and background initialization remain completely hidden and dormant.

### 3. UI Trigger Points
- **Material Slot & Shader Editor**:
  - Magic wand icon button beside the material name input.
  - Right-click context menu: `Auto-Name Material (Local AI)`.
- **Geometry Node Editor**:
  - Header button on the NodeTree selector and context menu.
- **Outliner**:
  - Right-click context menu on Collections: `Auto-Name Collection (Local AI)`.

### 4. Safety Confirmation Dialog
- **Modal Confirmation**:
  - On the first trigger per session, prompts the user via `invoke_props_dialog`:
    - **Title**: `Load Local AI Model?`
    - **Message**: *"This action loads a local language model (Flan-T5 Base via ONNX) into memory to infer semantic names. Inference runs 100% offline. Proceed?"*
    - **Options**:
      - `[ ] Don't ask again this session` (checkbox)
      - `Proceed` (loads weights and executes inference)
      - `Cancel` (aborts execution)

---

## SECTION 4: SPATIAL WAVE TRACING VIA STEAM AUDIO (PHONON)

### 1. Core Integration (`extern/phonon` & `intern/audaspace/plugins/`)
- Integrates Valve's **Steam Audio (Phonon)** C SDK into Audaspace's spatial audio engine.
- Direct scene geometric integration using evaluated meshes (`mesh_get_eval_final()`), ensuring identical acoustic boundaries across both Cycles and EEVEE.

### 2. BSDF-to-Acoustic Material Mapping
- Maps surface BSDF parameters directly into `IPLMaterial` properties:
  - Low, Mid, and High acoustic absorption coefficients calculated from BSDF porosity and roughness.
  - Acoustic scattering coefficient derived from geometric surface roughness.
  - Transmission / isolation factors derived from material density and thickness.

### 3. Environmental Wave Propagation
- **Physics Simulation**:
  - Real-time geometric ray-traced occlusion and sound diffraction around corners.
  - Material transmission low-pass filtering through interior and exterior walls.
  - Binaural HRTF (Head-Related Transfer Function) spatial audio for headphone monitoring.
  - Dynamic convolution reverb reflecting real room geometries and listener positions.

### 4. Tracing Backends
- Multi-threaded CPU ray-tracer for deterministic background calculations.
- Optional Vulkan compute ray-tracing path sharing device context with the native FSR 3.1 rendering pipeline.
