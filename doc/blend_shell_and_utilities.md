# Gooey Utilities & Blend Shell Documentation

This document covers the custom tooling, database shell, material lighting pipelines, project awareness, baking, and sequencer workflows implemented in **Gooey** (Blender 4.5.14 LTS).

---

## 1. Blend Shell: Unix Database Terminal

Because a `.blend` file is an embedded relational object database, Blend Shell maps Unix terminal operations directly to data-blocks in `bpy.data`, saving changes seamlessly back to the file or operating interactively inside Blender's Python Console.

### Activation & Modes
- In the **Python Console**, Blend Shell is activated by default.
- Standard Python code executes normally.
- Commands starting with shell keywords or `!`/`sh` run in shell mode.

### Command Reference

| Command | Usage | Description |
| :--- | :--- | :--- |
| `cat <target>` | `cat Cube`<br>`cat Material`<br>`cat script.py` | Inspects data-block properties. Dumps scripts, transforms (Location, Rotation, Scale), materials, or node graphs. |
| `rm [-rf] <target>` | `rm Cube`<br>`rm -rf *`<br>`rm --orphans` | Removes data-blocks from `bpy.data`. `rm -rf *` performs an immediate recursive orphan purge. |
| `cp <src> <dest>` | `cp Cube Cube_Copy`<br>`cp lib.blend/Collection/Char .` | Duplicates data-blocks internally or appends directly from external `.blend` files. |
| `mv <src> <dest>` | `mv Cube HeroCube`<br>`mv HeroCube Props` | Renames data-blocks or relocates objects across collections. |
| `mkdir <name>` | `mkdir Characters` | Creates a new Scene Collection linked to the active scene. |
| `ls [type]` | `ls`<br>`ls objects`<br>`ls materials` | Lists data-blocks by type (`objects`, `materials`, `meshes`, `collections`, `texts`) or local files. |
| `du` | `du` | Dumps scene complexity statistics (total vertices, polygons, object count, and data-block inventory). |
| `grep <pattern>` | `grep Cube`<br>`grep mat Gold` | Substring and regex filter for data-block names or materials across the database. |
| `diff <file1> [file2]` | `diff project.blend`<br>`diff v1.blend v2.blend` | Compares data-block inventories between two `.blend` files or against the current open session. |
| `pip <args...>` | `pip install scipy`<br>`pip list` | Package manager inside Blender's embedded Python interpreter with auto `ensurepip` bootstrapping. |
| `path <subcommand>` | `path on`<br>`path off`<br>`path add /opt/bin` | Toggles or customizes system `$PATH` linking for external bash commands. |
| `!<cmd>` / `sh <cmd>` | `!git status`<br>`!ffmpeg -version` | Executes system bash terminal commands directly, using the host `$PATH` when enabled. |

---

## 2. Material Lighting Nodes (In-Material Stylization / NPR Light Object Contribution)

Gooey introduces custom shader nodes designed for non-photorealistic surface shading that evaluates and accumulates individual light object contributions:

### Nodes Overview

1. **Light Accumulator (`ShaderNodeLightAccumulator`)**:
   - **Inputs**: `Diffuse` (Color), `Glossy` (Color), `Transmission` (Color)
   - **Outputs**: `Closure` (Shader)
   - **Description**: Recombines stylized light evaluation results into surface closures and directs them into diffuse, glossy, and transmission render passes.

2. **Light Data (`ShaderNodeLightData`)**:
   - **Outputs**: `Color`, `Power`, `Position`, `Direction`, `Distance`, `Attenuation`
   - **Description**: Extracts physical and geometric light properties from targeted or evaluating lights for custom BSDF calculations.

3. **Shadow (`ShaderNodeShadow`)**:
   - **Properties**: `Ray Count` (1 to 128)
   - **Inputs**: `Position`, `Direction`, `Length`, `Spread` (cone aperture)
   - **Outputs**: `Color`
   - **Description**: Evaluates dedicated cone-filtered raycasted shadow masks, allowing independent shading and shadowing coordinates (e.g. shadow outlines, ripple effects).

4. **Light Evaluation (`ShaderNodeLightEvaluation`)**:
   - **Modes**:
     - *Light Mode*: `Area` (physical LTC), `Point` (infinitesimal point)
     - *Function Mode*: `Cosine Hemisphere` (Lambert), `Cosine Sphere` (Half-Lambert), `Uniform Hemisphere` (Toon), `Uniform Sphere` (Smooth distance)
     - *Parameter Mode*: `Reflection`, `Refraction`, `Custom`
     - *Anisotropy*: On / Off
   - **Inputs**: `Position`, `Normal`, `Roughness`, `Size`, `Size Y`, `IOR`, `Tangent`, `BiTangent`
   - **Outputs**: `Color`, `Energy`, `Factor`, `Light Elevation`, `Light Spherical Area`

5. **Texture Filters (`ShaderNodeFilterBlur`, `ShaderNodeFilterSharpen`)**:
   - **Filter Blur**: Spatial/layered blur kernel for texture streams.
   - **Filter Sharpen**: Edge contrast enhancement for stylized cel surfaces.

---

## 3. Project Environment & Awareness (`.blender_project`)

Production setups can share project-wide settings and variables across multiple `.blend` files without external TD scripts.

### Folder Structure
When initialized, Blender creates:
```text
project_root/
└── .blender_project/
    ├── settings.toml
    └── variables.toml
```

### Path Template Variables
Filepaths and render output paths automatically expand template tokens:
- `{project_root}`: Absolute path to the folder containing `.blender_project`
- `{project_name}`: Name of the project directory
- `{blend_name}`: Base filename of the current `.blend` file (without `.blend`)
- `{fps}`: Scene frame rate
- `{resolution_x}`, `{resolution_y}`: Render dimensions
- `{var_name}`: Any user-defined variable from `variables.toml`

### UI & Python API
- **Topbar Indicator**: When inside a project, the active project name displays in the Topbar with a checkmark.
- **File Menu / Project Menu**: `Project -> Setup Project...`, `Project Settings...`, and `Evaluate Output Path`.
- **Python Context**: Access project information via `bpy.context.project.name`, `bpy.context.project.root`, and `bpy.context.project.variables`.

---

## 4. Texture Baking & Channel Packing Revamp

Located in **Properties -> Material -> Texture Baking & Channel Packing** and **Node Editor -> Sidebar -> Bake**:

- **Native Channel Packing**:
  Pack channels into single textures:
  - Base Color -> `RGBA` (Token: `base_color`)
  - Roughness -> `R` (Token: `roughmetalao`)
  - Metallic -> `G` (Token: `roughmetalao`)
  - AO -> `B` (Token: `roughmetalao`)
  - Normal -> `XYZ` (Token: `normal_map`)
- **Tokens**: Supports `<CHANNEL>` and `<UDIM>` tokens.
- **Operators**:
  - `object.bake_material`: Bake active material channels.
  - `object.bake_mesh`: Bake all materials on active mesh.
  - `object.bake_scene`: Multi-object batch bake.

---

## 5. VSE Media Bin & Media Management

Organize and stage media footage before placing it into the timeline:
- **Media Bin Panel**: Located in the Video Sequencer sidebar (**N -> Media Bin**).
- **Categories**: Organize footage into bins (e.g. `Footage`, `Audio`, `VFX`, `Graphics`, `Scenes`).
- **In/Out Marks**: Define per-media frame ranges before inserting into the timeline.
- **Add Menu Integration**: Sequencer `Add -> Media...` provides single-click import for movies, sounds, or image sequences with automatic type detection.

---

## 6. Canvas Feature

The **Canvas** operator enables instant creation of orthographic 2D/3D projection stages:
- **Button**: Click the **Canvas** button in the **3D Viewport Header**.
- **Action**:
  1. Creates a new Scene View Layer with a customizable name.
  2. Sets up an Orthographic Camera (`type='ORTHO'`) framing the canvas.
  3. Allocates a dedicated render target Image datablock (`<name>_Target`).
  4. Automatically wires the dedicated Compositor pipeline linking the render layers, composite, and viewer nodes.

---

## 7. Compositor Essentials & Presets

- **Layout Matching Upstream (`reference_compositor.png`)**:
  - Top-Left: Image Editor displaying Viewer Node output.
  - Top-Right: Properties Format panel.
  - Center: Compositor Node Editor.
  - Bottom: Collapsed 1-row Timeline and **Asset Shelf** containing all 17 upstream presets:
    - `Camera & Lens Effects` (Chromatic Aberration, Lens Flare, Vignette, etc.)
    - `Creative` (Color Grading, Glow, Posterize, etc.)
    - `Utilities` (Denoise, Split Viewer, Switch, etc.)
- **Camera Shakify Out-of-the-Box Compatibility**:
  - Autoloads EatTheFuture's `camera_shakify` drivers on file load to ensure zero broken driver or missing property errors in legacy and shared files.
