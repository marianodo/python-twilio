@echo off
REM Relanza el script cada vez que termina con error (watchdog, demasiados
REM errores seguidos, modem no disponible al arrancar...).
REM Si se detiene con Ctrl+C (codigo 0) no se relanza.

:loop
echo [%date% %time%] Iniciando servidor SMS con Modem GSM...
python send_to_sms_modem.py
set EXITCODE=%ERRORLEVEL%
if "%EXITCODE%"=="0" goto end

echo [%date% %time%] El proceso termino con codigo %EXITCODE%. Reiniciando en 10 segundos...
timeout /t 10 /nobreak >nul
goto loop

:end
echo [%date% %time%] Servidor SMS detenido.
pause
