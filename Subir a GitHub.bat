@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Subir TrazaPan a GitHub
echo.
echo  Antes de subir, cierra la app (la ventana negra de python app.py)
echo  para que la base de datos quede completa.
echo.
pause
git add -A
git commit -m "Actualizacion %date% %time:~0,5%"
git push
if errorlevel 1 (
  echo.
  echo  No se pudo subir. Revisa el mensaje de arriba ^(internet o sesion de GitHub^).
) else (
  echo.
  echo  Listo: los cambios y los datos ya estan en GitHub.
)
pause
