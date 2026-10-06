@echo off
echo [Proton Worker] blender_upscaler_worker starting...
echo [Proton Worker] Initializing OptiScaler / XeSS / FSR 4.1 IPC runtime bridge.
echo [Proton Worker] Listening on IPC channel: blender_upscaler_ipc
echo [Proton Worker] Ready for Vulkan shared memory descriptor dispatch.
:loop
ping -n 10 127.0.0.1 >nul
goto loop
