# Blend Shell & Production Utilities Reference Guide

Blend Shell integrates an interactive Unix-style shell directly into the Blender Python Console editor (`SPACE_CONSOLE`), allowing real-time inspection, management, duplicate-handling, and manipulation of `.blend` data-blocks and host pipelines.

---

## 1. Blend Shell Commands

Access the console by opening any Python Console space. Commands can be run directly at the `>>>` prompt or prefixed with bash bypasses:

| Command | Syntax | Description | Example |
| :--- | :--- | :--- | :--- |
| **`ls`** | `ls [type]` | Lists active data-blocks (`objects`, `materials`, `meshes`, `collections`, `texts`) or host files. | `ls materials` |
| **`cat`** | `cat <name>` | Dumps full data inspection (object transforms, material node networks, text blocks, files). | `cat Material.001` |
| **`cp`** | `cp <src> <dest>` | Duplicates data-blocks internally or appends from external `.blend` files. | `cp lib.blend/Collection/Props .` |
| **`mv`** | `mv <src> <dest>` | Renames data-blocks or moves objects between scene collections. | `mv Cube Props_Collection` |
| **`rm`** | `rm [-rf] <name>` | Deletes data-blocks. Run with `--orphans` to recursively purge unused blocks. | `rm -rf ObsoleteMesh` |
| **`mkdir`** | `mkdir <name>` | Creates and links a new Collection into the active scene. | `mkdir Foreground_FX` |
| **`du`** | `du` | Summarizes geometry statistics (vertex count, face count, data-block instances). | `du` |
| **`grep`** | `grep <pattern>` | Regex/substring search across data-block inventories and materials. | `grep mat Cel_` |
| **`diff`** | `diff <f1> [f2]` | Compares data-block inventories between two `.blend` files or current scene. | `diff backup.blend` |
| **`path`** | `path <on\|off\|add>` | Configures host `$PATH` resolution for system shell commands. | `path add /usr/local/bin` |
| **`pip`** | `pip <args...>` | Runs pip inside Blender's embedded Python interpreter with auto-ensurepip. | `pip install scipy` |
| **`!cmd` / `sh`**| `!command` | Non-blocking execution of host bash commands with 6.0s timeout and PTY safety. | `!git status` |
| **`!term`** | `!term <cmd>` | Launches command in external native terminal emulator (ptyxis, konsole, etc.). | `!term htop` |
| **`help`** | `help` | Shows the quick reference cheat-sheet inside the console. | `help` |

---

## 2. Production Pipelines & 2D Canvas

### Canvas Subsystem (`canvas.create`)
- Accessible from the **3D Viewport Header** or **Render Menu** (`Render -> Create Canvas...`).
- Automatically creates:
  1. A segregated `Canvas` View Layer.
  2. An Orthographic Camera framed to project dimensions.
  3. A white background plate and transparent painting target image.
  4. Fully wired Compositor Alpha-Over node graph.

### Project Environment Awareness (`.blender_project`)
- Recognizes `.blender_project/settings.toml` or `variables.toml` in parent directory tree.
- Resolves tokens in file paths:
  - `{project_root}`: Root directory of the production project.
  - `{project_name}`: Project name string.
  - `{blend_name}`: Current `.blend` filename.
  - `{fps}`: Scene frame rate.
  - `{resolution_x}`, `{resolution_y}`: Render dimensions.

### Multi-Channel Texture Packing
- Sidebar panel in **Material Properties** and **Shader Node Editor** (`N -> Texture Bake`).
- Allows packing PBR maps into single RGBA targets (e.g., Base Color in RGB, Roughness in Alpha, or Roughness in R, Metallic in G, AO in B).
