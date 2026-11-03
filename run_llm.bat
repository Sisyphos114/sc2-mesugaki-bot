@echo off
cd /d "%~dp0"
set "MODEL=E:\model\Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
set "LLAMA=E:\llama.cpp\llama-server.exe"
"%LLAMA%" -m "%MODEL%" --host 127.0.0.1 --port 8080 -c 2048 -ngl 99 -t 8 --jinja
pause
