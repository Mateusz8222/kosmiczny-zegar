import subprocess
import sys
import time

processes = []


def start_process(script_name: str):
    print(f"[RUNNER] Uruchamiam {script_name}")
    return subprocess.Popen([sys.executable, script_name])


def main():
    global processes

    processes = [
        start_process("bot.py"),
        start_process("public_bot.py"),
    ]

    try:
        while True:
            time.sleep(5)

            for i, process in enumerate(processes):
                if process.poll() is not None:
                    script_name = "bot.py" if i == 0 else "public_bot.py"
                    print(f"[RUNNER] {script_name} zakończył działanie. Uruchamiam ponownie...")
                    processes[i] = start_process(script_name)

    except KeyboardInterrupt:
        print("[RUNNER] Zatrzymywanie procesów...")
        for process in processes:
            process.terminate()


if __name__ == "__main__":
    main()
