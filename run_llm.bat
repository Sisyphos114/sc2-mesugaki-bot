@echo off
cd /d "%~dp0"
set "MODEL=E:\model\Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
set "LLAMA=E:\llama.cpp\llama-server.exe"

rem Kill any server already holding 8080. Two servers fight over the GPU and drop to ~1 token/s.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8080" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1

rem --parallel 1: one slot owns the whole KV cache. Multiple slots share it, so every new
rem request evicts the previous prompt and forces a full re-prefill (that was the slowdown).
"%LLAMA%" -m "%MODEL%" --host 127.0.0.1 --port 8080 -c 4096 --parallel 1 -ngl 36 -t 8 --jinja > "%~dp0llama-server.log" 2>&1
pause
