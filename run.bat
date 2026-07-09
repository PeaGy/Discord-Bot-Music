@echo off
title Discord Music Bot - Auto Restart
color 0A

:loop
echo [ %time% ] Dang khoi dong bot...
python bot.py

echo.
echo [ %time% ] Bot bi sap hoac tat dot ngot! 
echo Dang thu khoi dong lai sau 5 giay...
timeout /t 5 >nul
goto loop