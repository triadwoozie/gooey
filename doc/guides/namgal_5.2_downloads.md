# NaMgAl Goo Engine 5.2.x upstream precompiled binaries download & build guide

This document provides instructions for downloading precompiled artifacts or building the Goo Engine 5.2 port directly on Linux and Windows.

---

## 1. Upstream Repositories & Release Channels

- **Upstream 5.2.0 / 5.2.1 Repository**: [NaMgAl-Studio/goo-engine-5.2.0](https://github.com/NaMgAl-Studio/goo-engine-5.2.0)
- **Target Upstream PR**: [dillongoostudios/goo-engine PR #125](https://github.com/dillongoostudios/goo-engine/pull/125)
- **Baseline Version**: Blender 5.2.1 LTS (`v5.2.1` / commit `9e2066aef7`) with Goo subversion `502.49`.

---

## 2. Precompiled Binaries

Precompiled releases are published under GitHub Releases:
- **Releases Page**: [NaMgAl-Studio/goo-engine-5.2.0 Releases](https://github.com/NaMgAl-Studio/goo-engine-5.2.0/releases)

### Automated Download (Linux CLI)
To fetch the latest precompiled release archive directly via terminal:
```bash
# Using GitHub CLI (gh)
gh release download --repo NaMgAl-Studio/goo-engine-5.2.0 --pattern "*.tar.xz" --pattern "*.zip"

# Or via curl (example for tag GooBlender5.2.1_2026.9.12)
curl -L -O https://github.com/NaMgAl-Studio/goo-engine-5.2.0/releases/download/GooBlender5.2.1_2026.9.12/blender-5.2.1-goo-linux-x86_64.tar.xz
tar -xf blender-5.2.1-goo-linux-x86_64.tar.xz
cd blender-5.2.1-goo-linux-x86_64
./blender
```

---

## 3. Building From Source (Linux)

### Build Dependencies
- **Compiler**: Clang / GCC with C++20 support
- **Build System**: CMake (>= 3.20), Ninja
- **Libraries**: Wayland / X11 dev headers, Vulkan SDK, OpenGL/Epoxy

### Fast Build with Mold Linker
```bash
# 1. Clone repository
git clone https://github.com/NaMgAl-Studio/goo-engine-5.2.0.git
cd goo-engine-5.2.0

# 2. Configure with Ninja, Clang, and Mold linker (drastically accelerates link times)
cmake -B ../build_linux_5.2 -S . -GNinja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++ \
  -DWITH_LINKER_MOLD=ON \
  -DWITH_COMPOSITOR_CPU=ON \
  -DWITH_VULKAN=ON

# 3. Compile
ninja -C ../build_linux_5.2 blender

# 4. Run
../build_linux_5.2/bin/blender
```
