@echo off
setlocal
cd /d "%~dp0"
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY (
  echo.
  echo [错误] 没找到 Python。请先安装 Python 3.10+，安装时勾选 Add Python to PATH。
  echo.
  pause
  exit /b 1
)
echo 正在打开"修改管理员"界面...
%PY% install.py --set-admin
if errorlevel 1 (
  echo.
  echo 运行出错，请把上面的信息截图反馈。
  pause
)
exit /b 0
