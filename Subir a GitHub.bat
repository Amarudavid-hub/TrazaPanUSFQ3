@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Subir TrazaPan a GitHub
echo.
echo  Se sube SOLO el programa (app, pantallas, fotos de productos).
echo  Los datos (TrazaPanUSFQ3\datos: registros y fotos) NO se suben:
echo  asi una subida no reemplaza lo que se registro en la pagina de Render.
echo.
pause
git add -A
rem Los datos se quedan fuera de la subida (en GitHub queda la ultima version que ya estaba).
git reset -q -- TrazaPanUSFQ3/datos
git diff --cached --quiet
if not errorlevel 1 (
  echo.
  echo  No hay cambios del programa para subir. No se subio nada.
  echo.
  pause
  exit /b
)
git commit -m "Actualizacion del programa %date% %time:~0,5%"
git push
if errorlevel 1 (
  echo.
  echo  No se pudo subir. Revisa el mensaje de arriba ^(internet o sesion de GitHub^).
) else (
  echo.
  echo  Listo: el programa ya esta en GitHub. Los datos no se subieron.
)
pause
