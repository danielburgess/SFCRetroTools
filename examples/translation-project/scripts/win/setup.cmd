@echo off
REM Double-click wrapper: runs setup.ps1 with the execution policy bypassed for
REM this single invocation (does NOT change the machine's global policy) so
REM Windows does not block the unsigned project script. Forwards any args.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 (
  echo.
  echo Setup failed. Review the messages above.
  pause
)
