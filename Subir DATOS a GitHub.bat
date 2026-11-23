@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Subir DATOS a GitHub (reemplaza los datos de Render)
echo.
echo  ===============================================================
echo   OJO: esto sube los DATOS de esta laptop a GitHub y la pagina
echo   de Render se reinstala con ellos. Lo que se haya registrado en
echo   Render y NO este en esta laptop se BORRA.
echo.
echo   Antes de seguir:
echo    1. Descarga el respaldo de Render (Administrar - Respaldo)
echo       y que ya este puesto en esta laptop.
echo    2. Cierra la app de la laptop (la ventana negra de Python).
echo  ===============================================================
echo.
set /p RESP= Escribe SI para subir los datos: 
if /i not "%RESP%"=="SI" (
  echo.
  echo  No se subio nada.
  echo.
  pause
  exit /b
)
git add -A
git diff --cached --quiet
if not errorlevel 1 (
  echo.
  echo  No hay cambios para subir. No se subio nada.
  echo.
  pause
  exit /b
)
git commit -m "Datos y programa %date% %time:~0,5%"
git push
if errorlevel 1 (
  echo.
  echo  No se pudo subir. Revisa el mensaje de arriba ^(internet o sesion de GitHub^).
) else (
  echo.
  echo  Listo: los datos y el programa ya estan en GitHub.
  echo  Render tarda unos minutos en reinstalarse; no registres nada en Render hasta que termine.
)
pause
