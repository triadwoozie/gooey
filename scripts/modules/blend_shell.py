# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Blend Shell (Terminal in Blender)
Maps Unix shell operations (cat, rm, cp, mv, du, grep, diff, ls, mkdir, pip)
directly to bpy.data data-block operations inside the running Blender session,
with seamless fallback to system commands and python console.
"""

import os
import sys
import re
import shlex
import shutil
import subprocess
import importlib
import site
import bpy

__all__ = (
    "handle_command",
    "pip_exec",
    "blend_shell_banner",
)

def pip_exec(*args):
    """Install, list, or uninstall Python packages using Blender's Python interpreter."""
    cmd = [sys.executable, "-m", "pip"] + list(args)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        out = res.stdout + ("\n" + res.stderr if res.stderr else "")
        if res.returncode != 0 and "No module named pip" in out:
            print("[pip] Bootstrapping pip via ensurepip...")
            subprocess.run([sys.executable, "-m", "ensurepip"], check=True)
            res = subprocess.run(cmd, capture_output=True, text=True)
            out = res.stdout + ("\n" + res.stderr if res.stderr else "")
        importlib.invalidate_caches()
        return res.returncode, out.strip()
    except Exception as e:
        return 1, f"pip error: {e}"


def _get_target_datablock(name):
    """Find a datablock across common collections by name or category/name."""
    if "/" in name:
        cat, item = name.split("/", 1)
        cat_attr = cat.lower()
        if hasattr(bpy.data, cat_attr):
            coll = getattr(bpy.data, cat_attr)
            if item in coll:
                return coll[item], cat_attr
            return None, cat_attr

    for cat in ["objects", "materials", "collections", "meshes", "texts", "images", "scenes"]:
        coll = getattr(bpy.data, cat, None)
        if coll and name in coll:
            return coll[name], cat
    return None, None


def cmd_help(args, stdout):
    stdout.write("""
=== BLEND-SHELL: Unix Data-Block & System Terminal ===
Standard commands mapped to bpy.data and system shell:

  ls [type]                 List data-blocks (objects, materials, collections, texts, meshes) or files
  cat <target>              Inspect properties:
                              • Object: Transform (Loc, Rot, Scale), bounds, materials
                              • Material: Shader nodes and BSDF parameters
                              • Text/Script: Dump content of text data-block
                              • File: Dump content of external file
  rm [-rf] <target>         Delete data-block from bpy.data (e.g. 'rm Cube', 'rm UnusedMat')
                            'rm -rf *' or 'rm --orphans' purges all orphan data-blocks!
  cp <src> <dest>           Duplicate object/data-block internally ('cp Cube HeroCube')
                            or append from external file ('cp path/to/lib.blend/Collection/Props .')
  mv <src> <dest>           Rename data-block ('mv Cube OldCube')
                            or move object into collection ('mv Cube Collection2')
  mkdir <name>              Create a new Collection and link to active scene
  du                        Disk & polygon usage: Object, vertex, polygon, and material counts
  grep <pattern> [type]     Search data-blocks by regex or substring ('grep prop', 'grep mat Gold')
  diff <file1> [file2]      Compare data-blocks between two .blend files (or current vs file)
  pip <args...>             Run pip in Blender's Python ('pip install scipy', 'pip list')
  path [on|off|add <dir>]   Toggle/manage system $PATH linking for !command execution
  sh <cmd>  or  !<cmd>      Execute system shell command directly ('!uname -a', '!ffmpeg', 'sh ls -la')
  term <cmd> or !term <cmd> Launch command in external live terminal window ('term agy', '!term htop')
  save                      Save current .blend file (bpy.ops.wm.save_mainfile())
  help                      Show this help screen
======================================================
""")


def cmd_ls(args, stdout):
    target = args[0] if args else ""
    if target in {"-l", "-la", "-a"}:
        target = args[1] if len(args) > 1 else ""

    if target:
        cat_attr = target.lower()
        if hasattr(bpy.data, cat_attr):
            coll = getattr(bpy.data, cat_attr)
            stdout.write(f"[{cat_attr.upper()} ({len(coll)})]:\n")
            names = [f"  {item.name}" for item in coll]
            stdout.write("\n".join(names) + "\n" if names else "  (empty)\n")
            return
        elif os.path.exists(target):
            entries = os.listdir(target)
            stdout.write("\n".join(f"  {e}" for e in sorted(entries)) + "\n")
            return

    # Default overview
    stdout.write(f"=== bpy.data Overview ===\n")
    stdout.write(f"  Collections ({len(bpy.data.collections)}): " + ", ".join([c.name for c in bpy.data.collections[:6]]) + ("..." if len(bpy.data.collections) > 6 else "") + "\n")
    stdout.write(f"  Objects     ({len(bpy.data.objects)}): " + ", ".join([o.name for o in bpy.data.objects[:6]]) + ("..." if len(bpy.data.objects) > 6 else "") + "\n")
    stdout.write(f"  Meshes      ({len(bpy.data.meshes)}): " + ", ".join([m.name for m in bpy.data.meshes[:6]]) + ("..." if len(bpy.data.meshes) > 6 else "") + "\n")
    stdout.write(f"  Materials   ({len(bpy.data.materials)}): " + ", ".join([m.name for m in bpy.data.materials[:6]]) + ("..." if len(bpy.data.materials) > 6 else "") + "\n")
    stdout.write(f"  Texts       ({len(bpy.data.texts)}): " + ", ".join([t.name for t in bpy.data.texts[:6]]) + ("..." if len(bpy.data.texts) > 6 else "") + "\n")


def cmd_cat(args, stdout, stderr):
    if not args:
        stderr.write("cat: missing operand (specify data-block name or text block)\n")
        return

    name = args[0]
    # Check text data-blocks first
    if name in bpy.data.texts:
        stdout.write(bpy.data.texts[name].as_string() + "\n")
        return

    # Check external files
    if os.path.isfile(name):
        try:
            with open(name, "r", encoding="utf-8", errors="replace") as f:
                stdout.write(f.read() + "\n")
            return
        except Exception as e:
            stderr.write(f"cat: {e}\n")
            return

    # Check objects
    obj, cat = _get_target_datablock(name)
    if obj is not None:
        if isinstance(obj, bpy.types.Object):
            stdout.write(f"Object: {obj.name} (type: {obj.type})\n")
            stdout.write(f"  Location: ({obj.location.x:.3f}, {obj.location.y:.3f}, {obj.location.z:.3f})\n")
            stdout.write(f"  Rotation: ({obj.rotation_euler.x:.3f}, {obj.rotation_euler.y:.3f}, {obj.rotation_euler.z:.3f})\n")
            stdout.write(f"  Scale:    ({obj.scale.x:.3f}, {obj.scale.y:.3f}, {obj.scale.z:.3f})\n")
            if obj.material_slots:
                mats = [s.material.name for s in obj.material_slots if s.material]
                stdout.write(f"  Materials: {', '.join(mats)}\n")
            if obj.modifiers:
                mods = [f"{m.name} ({m.type})" for m in obj.modifiers]
                stdout.write(f"  Modifiers: {', '.join(mods)}\n")
            return
        elif isinstance(obj, bpy.types.Material):
            stdout.write(f"Material: {obj.name} (use_nodes={obj.use_nodes})\n")
            if obj.use_nodes and obj.node_tree:
                stdout.write("  Shader Nodes:\n")
                for node in obj.node_tree.nodes:
                    stdout.write(f"    - {node.name} [{node.type}]\n")
            return
        elif isinstance(obj, bpy.types.Collection):
            stdout.write(f"Collection: {obj.name}\n")
            stdout.write(f"  Objects: {', '.join([o.name for o in obj.objects])}\n")
            return

    stderr.write(f"cat: {name}: No such text block, object, material, or file found.\n")


def cmd_rm(args, stdout, stderr):
    if not args:
        stderr.write("rm: missing operand\n")
        return

    # rm -rf * or rm --orphans
    if "-rf" in args or "--orphans" in args or "*" in args:
        stdout.write("Purging orphaned data-blocks...\n")
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        if bpy.data.is_saved:
            bpy.ops.wm.save_mainfile()
        stdout.write("Orphan purge complete.\n")
        return

    target = args[-1]
    obj, cat = _get_target_datablock(target)
    if obj is None:
        stderr.write(f"rm: cannot remove '{target}': No such data-block found.\n")
        return

    try:
        coll = getattr(bpy.data, cat)
        if cat == "objects":
            coll.remove(obj, do_unlink=True)
        else:
            coll.remove(obj)
        stdout.write(f"Removed {cat[:-1]} '{target}'.\n")
        if bpy.data.is_saved:
            bpy.ops.wm.save_mainfile()
    except Exception as e:
        stderr.write(f"rm error: {e}\n")


def cmd_cp(args, stdout, stderr):
    if len(args) < 2:
        stderr.write("cp: missing destination operand ('cp src dest')\n")
        return
    src, dest = args[0], args[1]

    # External blend append: cp file.blend/Collection/Props .
    if ".blend" in src:
        try:
            parts = src.split(".blend", 1)
            filepath = parts[0] + ".blend"
            subpath = parts[1].strip("/")
            if "/" in subpath:
                id_type, id_name = subpath.split("/", 1)
                directory = os.path.join(filepath, id_type) + "/"
                bpy.ops.wm.append(filepath=filepath, directory=directory, filename=id_name)
                stdout.write(f"Appended {id_name} from {filepath}.\n")
                if bpy.data.is_saved:
                    bpy.ops.wm.save_mainfile()
                return
        except Exception as e:
            stderr.write(f"cp append error: {e}\n")
            return

    # Internal duplicate
    obj, cat = _get_target_datablock(src)
    if obj is None:
        stderr.write(f"cp: cannot duplicate '{src}': Not found in bpy.data.\n")
        return

    try:
        if isinstance(obj, bpy.types.Object):
            dup = obj.copy()
            if obj.data:
                dup.data = obj.data.copy()
            dup.name = dest
            bpy.context.scene.collection.objects.link(dup)
            stdout.write(f"Duplicated Object '{src}' -> '{dup.name}'.\n")
        elif hasattr(obj, "copy"):
            dup = obj.copy()
            dup.name = dest
            stdout.write(f"Duplicated {cat[:-1]} '{src}' -> '{dup.name}'.\n")
        if bpy.data.is_saved:
            bpy.ops.wm.save_mainfile()
    except Exception as e:
        stderr.write(f"cp error: {e}\n")


def cmd_mv(args, stdout, stderr):
    if len(args) < 2:
        stderr.write("mv: missing destination operand ('mv src dest')\n")
        return
    src, dest = args[0], args[1]

    obj, cat = _get_target_datablock(src)
    if obj is None:
        stderr.write(f"mv: cannot move '{src}': Not found.\n")
        return

    # If dest is an existing collection and obj is an Object -> Move object to collection
    if isinstance(obj, bpy.types.Object) and dest in bpy.data.collections:
        target_col = bpy.data.collections[dest]
        # Unlink from other collections
        for col in list(obj.users_collection):
            col.objects.unlink(obj)
        if obj.name in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.unlink(obj)
        target_col.objects.link(obj)
        stdout.write(f"Moved Object '{src}' into Collection '{dest}'.\n")
        if bpy.data.is_saved:
            bpy.ops.wm.save_mainfile()
        return

    # Otherwise rename
    old_name = obj.name
    obj.name = dest
    stdout.write(f"Renamed {cat[:-1]} '{old_name}' -> '{obj.name}'.\n")
    if bpy.data.is_saved:
        bpy.ops.wm.save_mainfile()


def cmd_mkdir(args, stdout, stderr):
    if not args:
        stderr.write("mkdir: missing operand (specify collection name)\n")
        return
    name = args[0]
    if name in bpy.data.collections:
        stderr.write(f"mkdir: Collection '{name}' already exists.\n")
        return
    new_col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(new_col)
    stdout.write(f"Created Collection '{name}' and linked to Scene.\n")
    if bpy.data.is_saved:
        bpy.ops.wm.save_mainfile()


def cmd_du(args, stdout):
    verts = sum(len(m.vertices) for m in bpy.data.meshes)
    faces = sum(len(m.polygons) for m in bpy.data.meshes)
    stdout.write("=== Blend Database Statistics ===\n")
    stdout.write(f"  Collections: {len(bpy.data.collections)}\n")
    stdout.write(f"  Objects:     {len(bpy.data.objects)}\n")
    stdout.write(f"  Meshes:      {len(bpy.data.meshes)}\n")
    stdout.write(f"  Materials:   {len(bpy.data.materials)}\n")
    stdout.write(f"  Images:      {len(bpy.data.images)}\n")
    stdout.write(f"  Texts:       {len(bpy.data.texts)}\n")
    stdout.write(f"  Total Verts: {verts:,}\n")
    stdout.write(f"  Total Faces: {faces:,}\n")


def cmd_grep(args, stdout, stderr):
    if not args:
        stderr.write("grep: missing search pattern\n")
        return

    # Check for mat search: grep mat <MaterialName>
    if len(args) >= 2 and args[0] in {"mat", "material"}:
        mat_target = args[1]
        matches = [o.name for o in bpy.data.objects if any(m and mat_target.lower() in m.name.lower() for m in o.material_slots)]
        stdout.write(f"Objects with material matching '{mat_target}':\n")
        stdout.write("\n".join(f"  {m}" for m in matches) + "\n" if matches else "  None\n")
        return

    pattern = args[0]
    regex = re.compile(pattern, re.IGNORECASE)
    found = []
    for cat in ["objects", "materials", "collections", "meshes", "texts"]:
        coll = getattr(bpy.data, cat, None)
        if coll:
            for item in coll:
                if regex.search(item.name):
                    found.append(f"[{cat[:-1]}] {item.name}")
    stdout.write(f"Matches for pattern '{pattern}':\n")
    stdout.write("\n".join(f"  {m}" for m in found) + "\n" if found else "  No matches found\n")


def cmd_diff(args, stdout, stderr):
    if not args:
        stderr.write("diff: missing file argument ('diff file1.blend [file2.blend]')\n")
        return

    if len(args) == 1:
        file_b = args[0]
        if not bpy.data.is_saved:
            stderr.write("diff: Current blend file is not saved to disk yet.\n")
            return
        file_a = bpy.data.filepath
    else:
        file_a, file_b = args[0], args[1]

    if not os.path.isfile(file_a) or not os.path.isfile(file_b):
        stderr.write(f"diff: One or both files not found ({file_a}, {file_b})\n")
        return

    try:
        with bpy.data.libraries.load(file_a) as (a, _), bpy.data.libraries.load(file_b) as (b, _):
            stdout.write(f"=== Diff: {os.path.basename(file_a)} <-> {os.path.basename(file_b)} ===\n")
            diff_found = False
            for cat in ['objects', 'materials', 'collections']:
                set_a = set(getattr(a, cat))
                set_b = set(getattr(b, cat))
                added = set_b - set_a
                removed = set_a - set_b
                if added or removed:
                    diff_found = True
                    stdout.write(f"[{cat.upper()}]:\n")
                    if added:
                        stdout.write(f"  + Added in {os.path.basename(file_b)}: {', '.join(sorted(added))}\n")
                    if removed:
                        stdout.write(f"  - Missing in {os.path.basename(file_b)}: {', '.join(sorted(removed))}\n")
            if not diff_found:
                stdout.write("Files have identical objects, materials, and collections.\n")
    except Exception as e:
        stderr.write(f"diff error: {e}\n")


def cmd_pip(args, stdout, stderr):
    if not args:
        args = ["list"]
    code, output = pip_exec(*args)
    if output:
        if code == 0:
            stdout.write(output + "\n")
        else:
            stderr.write(output + "\n")


_USE_SYSTEM_PATH = True
_CUSTOM_PATHS = []

def toggle_path(enable=None):
    global _USE_SYSTEM_PATH
    if enable is None:
        _USE_SYSTEM_PATH = not _USE_SYSTEM_PATH
    else:
        _USE_SYSTEM_PATH = bool(enable)
    return _USE_SYSTEM_PATH

def get_shell_env():
    env = os.environ.copy()
    if _USE_SYSTEM_PATH:
        existing = env.get("PATH", "")
        extra_paths = [os.path.expanduser("~/.local/bin"), "/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin"] + _CUSTOM_PATHS
        for p in extra_paths:
            if os.path.isdir(p) and p not in existing.split(os.pathsep):
                existing = f"{p}{os.pathsep}{existing}"
        env["PATH"] = existing
    else:
        env["PATH"] = "/bin:/usr/bin"
    return env

def cmd_path(args, stdout):
    global _USE_SYSTEM_PATH, _CUSTOM_PATHS
    if not args:
        status = "ENABLED (Full system $PATH linked)" if _USE_SYSTEM_PATH else "DISABLED (Restricted sandbox)"
        stdout.write(f"PATH Execution: {status}\n")
        stdout.write(f"Active PATH: {get_shell_env().get('PATH', '')}\n")
        stdout.write("Usage: path on | path off | path add <directory>\n")
        return
    sub = args[0].lower()
    if sub in {"on", "enable", "1", "true"}:
        _USE_SYSTEM_PATH = True
        stdout.write("System $PATH execution ENABLED for !command and shell operators.\n")
    elif sub in {"off", "disable", "0", "false"}:
        _USE_SYSTEM_PATH = False
        stdout.write("System $PATH execution DISABLED (Restricted sandbox mode).\n")
    elif sub == "add" and len(args) > 1:
        new_path = os.path.abspath(os.path.expanduser(args[1]))
        if new_path not in _CUSTOM_PATHS:
            _CUSTOM_PATHS.append(new_path)
        stdout.write(f"Added '{new_path}' to shell execution PATH.\n")
    else:
        stdout.write(f"Unknown option '{sub}'. Usage: path on | path off | path add <dir>\n")

def find_terminal_emulator():
    """Find a supported graphical terminal emulator on the system."""
    for term in ["ptyxis", "gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "kitty", "foot", "wezterm", "xterm"]:
        path = shutil.which(term)
        if path:
            return term, path
    return None, None


def launch_in_terminal(cmd_line, env):
    """Launch interactive CLI command in a floating/external terminal window."""
    term_name, term_path = find_terminal_emulator()
    if not term_path:
        return False, "No graphical terminal emulator found (ptyxis/gnome-terminal/konsole/etc.)."

    try:
        if term_name == "ptyxis":
            args = [term_path, "--new-window", "--", "bash", "-c", f"{cmd_line}; echo ''; read -n 1 -s -r -p 'Press any key to close...'"]
        elif term_name in {"gnome-terminal", "xfce4-terminal"}:
            args = [term_path, "--", "bash", "-c", f"{cmd_line}; echo ''; read -n 1 -s -r -p 'Press any key to close...'"]
        elif term_name == "konsole":
            args = [term_path, "-e", "bash", "-c", f"{cmd_line}; echo ''; read -n 1 -s -r -p 'Press any key to close...'"]
        elif term_name in {"alacritty", "kitty", "foot", "wezterm", "xterm"}:
            args = [term_path, "-e", "bash", "-c", f"{cmd_line}; echo ''; read -n 1 -s -r -p 'Press any key to close...'"]
        else:
            args = [term_path, "-e", cmd_line]

        subprocess.Popen(args, env=env, close_fds=True)
        return True, f"Launched '{cmd_line}' in external terminal ({term_name})."
    except Exception as e:
        return False, f"Failed to launch terminal: {e}"


def strip_ansi_codes(text):
    """Remove ANSI escape sequences and terminal control characters from output."""
    ansi_regex = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_regex.sub('', text)


def cmd_system(cmd_line, stdout, stderr, timeout=6.0):
    try:
        env = get_shell_env()
        if "TERM" not in env or not env["TERM"]:
            env["TERM"] = "xterm-256color"

        shell_exe = os.environ.get("SHELL", "/bin/bash")
        if not os.path.isfile(shell_exe):
            shell_exe = "/bin/sh"

        # Check if user explicitly asked to run in external terminal (e.g. `!term agy` or `sh term agy`)
        if cmd_line.startswith("term "):
            target_cmd = cmd_line[5:].strip()
            ok, msg = launch_in_terminal(target_cmd, env)
            if ok:
                stdout.write(f"[blend-shell] {msg}\n")
            else:
                stderr.write(f"[blend-shell] {msg}\n")
            return

        use_pty = False
        try:
            import pty
            import select
            import fcntl
            import time
            use_pty = True
        except ImportError:
            use_pty = False

        if use_pty and os.name == 'posix':
            master_fd, slave_fd = pty.openpty()
            proc = None
            try:
                proc = subprocess.Popen(
                    cmd_line,
                    shell=True,
                    executable=shell_exe,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=env,
                    close_fds=True,
                )
                os.close(slave_fd)
                slave_fd = -1

                # Set master fd non-blocking to guarantee Blender's main loop NEVER hangs
                flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
                fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

                raw_bytes = bytearray()
                start_time = time.time()
                timed_out = False

                while True:
                    elapsed = time.time() - start_time
                    if elapsed > timeout:
                        timed_out = True
                        break

                    r, _, _ = select.select([master_fd], [], [], 0.04)
                    if r:
                        try:
                            chunk = os.read(master_fd, 4096)
                            if not chunk:
                                break
                            raw_bytes.extend(chunk)
                        except OSError:
                            break
                    elif proc.poll() is not None:
                        # Process terminated; brief non-blocking drain
                        for _ in range(5):
                            try:
                                chunk = os.read(master_fd, 4096)
                                if chunk:
                                    raw_bytes.extend(chunk)
                                else:
                                    break
                            except OSError:
                                break
                        break

                if timed_out and proc.poll() is None:
                    # Process is interactive (like TUI / interactive CLI), offer launch or terminate
                    proc.terminate()
                    try:
                        proc.wait(timeout=0.5)
                    except Exception:
                        proc.kill()

                    clean_out = strip_ansi_codes(raw_bytes.decode("utf-8", errors="replace")).strip()
                    if clean_out:
                        stdout.write(f"{clean_out}\n")
                    stderr.write(
                        f"\n[blend-shell] Command timed out after {timeout:.1f}s (running interactive TUI?).\n"
                        f"Tip: Use '!term {cmd_line}' to open it in a live terminal window!\n"
                    )
                else:
                    proc.wait()
                    clean_out = strip_ansi_codes(raw_bytes.decode("utf-8", errors="replace"))
                    if clean_out:
                        stdout.write(clean_out)
            finally:
                if slave_fd != -1:
                    try:
                        os.close(slave_fd)
                    except OSError:
                        pass
                try:
                    os.close(master_fd)
                except OSError:
                    pass
        else:
            res = subprocess.run(cmd_line, shell=True, executable=shell_exe, env=env, capture_output=True, text=True, timeout=timeout)
            if res.stdout:
                stdout.write(strip_ansi_codes(res.stdout))
            if res.stderr:
                stderr.write(strip_ansi_codes(res.stderr))
    except subprocess.TimeoutExpired:
        stderr.write(f"\n[blend-shell] Command timed out. Use '!term {cmd_line}' to open in external terminal.\n")
    except Exception as e:
        stderr.write(f"shell error: {e}\n")


def blend_shell_banner():
    return (
        "BLEND SHELL ACTIVATED (Unix Database Terminal)",
        "Type 'help' for available commands (cat, rm, cp, mv, du, grep, diff, ls, mkdir, pip, path).",
        "Type '!cmd' or 'sh cmd' for system bash commands (PATH linked). Standard Python expressions work normally.",
        "",
    )


def handle_command(line_raw, context, stdout, stderr):
    """
    Inspects user input line. If recognized as a Unix blend-shell command,
    executes it and returns True. Otherwise returns False to allow standard Python evaluation.
    """
    line = line_raw.strip()
    if not line:
        return False

    # Direct system terminal bypass: !cmd
    if line.startswith("!"):
        cmd_system(line[1:].strip(), stdout, stderr)
        return True

    # Shell bypass: sh <cmd>
    if line.startswith("sh "):
        cmd_system(line[3:].strip(), stdout, stderr)
        return True

    # Launch in external terminal window: term <cmd>
    if line.startswith("term "):
        cmd_system(line, stdout, stderr)
        return True

    try:
        parts = shlex.split(line)
    except Exception:
        parts = line.split()

    if not parts:
        return False

    cmd = parts[0]
    args = parts[1:]

    # Check against Unix commands
    if cmd == "help" and (not args or args[0] in {"ls", "cat", "rm", "cp", "mv", "mkdir", "du", "grep", "diff", "pip", "sh", "!"}):
        cmd_help(args, stdout)
        return True
    elif cmd == "ls":
        cmd_ls(args, stdout)
        return True
    elif cmd == "cat":
        cmd_cat(args, stdout, stderr)
        return True
    elif cmd == "rm":
        cmd_rm(args, stdout, stderr)
        return True
    elif cmd == "cp":
        cmd_cp(args, stdout, stderr)
        return True
    elif cmd == "mv":
        cmd_mv(args, stdout, stderr)
        return True
    elif cmd == "mkdir":
        cmd_mkdir(args, stdout, stderr)
        return True
    elif cmd == "du":
        cmd_du(args, stdout)
        return True
    elif cmd == "grep":
        cmd_grep(args, stdout, stderr)
        return True
    elif cmd == "diff":
        cmd_diff(args, stdout, stderr)
        return True
    elif cmd == "path":
        cmd_path(args, stdout)
        return True
    elif cmd == "pip":
        cmd_pip(args, stdout, stderr)
        return True
    elif cmd == "save":
        if bpy.data.is_saved:
            bpy.ops.wm.save_mainfile()
            stdout.write(f"Saved: {bpy.data.filepath}\n")
        else:
            stderr.write("File not saved yet. Use Save As...\n")
        return True
    elif cmd == "pwd":
        stdout.write(os.getcwd() + "\n")
        return True
    elif cmd == "cd":
        target_dir = args[0] if args else os.path.expanduser("~")
        try:
            os.chdir(target_dir)
            stdout.write(os.getcwd() + "\n")
        except Exception as e:
            stderr.write(f"cd: {e}\n")
        return True

    return False
