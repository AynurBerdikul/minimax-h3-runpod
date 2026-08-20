@echo off
setlocal
set "PLUGIN_DIR=%~dp0"
set "PY=%PLUGIN_DIR%..\..\..\python_embeded\python.exe"
set "COMFY=%PLUGIN_DIR%..\.."

if not exist "%PY%" (
  echo ERROR: Portable ComfyUI Python was not found at:
  echo %PY%
  echo.
  echo Place this folder in:
  echo ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-RunPod-H3
  pause
  exit /b 1
)

"%PY%" -m pip install --disable-pip-version-check -r "%PLUGIN_DIR%requirements.txt"
if errorlevel 1 (
  echo ERROR: dependency install failed.
  pause
  exit /b 1
)

echo.
if exist "%COMFY%\comfy_extras\nodes_minimax_h3.py" (
  echo OK: this ComfyUI build contains native MiniMax H3 nodes.
) else (
  echo WARNING: MiniMax H3 core nodes were not found in this Portable ComfyUI build.
  echo Update ComfyUI once before opening the official H3 workflow.
)

echo.
echo INSTALL OK.
echo Restart ComfyUI, open RunPod H3 Settings, enter credentials once, then use RunPod Queue.
pause
