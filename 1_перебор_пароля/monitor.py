#!/usr/bin/env python3
# Двусторонний монитор Serial. Печатает вывод платы и шлёт то, что ты набираешь.
# Запуск:  python3 monitor.py            (Enter отправляет строку; Ctrl+C выход)
import serial, sys, glob, threading
port = sys.argv[1] if len(sys.argv) > 1 else (sorted(glob.glob("/dev/ttyUSB*")) or [None])[0]
if not port:
    print("Не найден /dev/ttyUSB*. Подключите плату."); sys.exit(1)
print(f"# Порт {port} @115200. Вводи код (напр. 1357) и Enter. Ctrl+C — выход.")
s = serial.Serial(port, 115200, timeout=0.2)

def reader():
    while True:
        data = s.read(4096)
        if data:
            sys.stdout.write(data.decode("utf-8", "replace"))
            sys.stdout.flush()

threading.Thread(target=reader, daemon=True).start()
try:
    for line in sys.stdin:
        s.write(line.rstrip("\n").encode() + b"\n")
except KeyboardInterrupt:
    pass
finally:
    s.close()
