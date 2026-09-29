#!/usr/bin/env python3
# Приёмник дампа SPI-flash от прошивки flash_dump.ino.
# Шлёт команду дампа, ловит бинарный поток между маркерами DUMP START/END,
# пишет в файл и сверяет CRC32.
#
# Примеры:
#   python3 dump_flash.py                      # весь чип -> dump.bin
#   python3 dump_flash.py -o boot2.bin -c d0x0,0x100
#   python3 dump_flash.py -p /dev/ttyUSB0 -b 115200
import argparse, glob, sys, time, zlib, re
import serial

def find_port():
    p = sorted(glob.glob("/dev/ttyUSB*") or glob.glob("/dev/ttyACM*"))
    return p[0] if p else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port", default=None)
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-o", "--out", default="dump.bin")
    ap.add_argument("-c", "--cmd", default="d", help="команда дампа: d | d0x0,0x100 | d1048576")
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        sys.exit("Не найден порт (/dev/ttyUSB* или /dev/ttyACM*). Укажи -p.")

    s = serial.Serial(port, args.baud, timeout=2)
    time.sleep(0.3)
    s.reset_input_buffer()
    s.write(args.cmd.strip().encode() + b"\n")

    # ждём строку заголовка DUMP START addr=.. len=..
    hdr = b""
    t0 = time.time()
    while b"\n" not in hdr:
        chunk = s.read(1)
        if not chunk:
            if time.time() - t0 > 5:
                sys.exit(f"Нет ответа. Получено: {hdr!r}")
            continue
        hdr += chunk
        # выкинем возможный шум/эхо до START
        if b"DUMP START" in hdr:
            hdr = hdr[hdr.index(b"DUMP START"):]
    m = re.search(rb"DUMP START addr=(\d+) len=(\d+)", hdr)
    if not m:
        sys.exit(f"Не понял заголовок: {hdr!r}")
    addr, length = int(m.group(1)), int(m.group(2))
    print(f"# addr={addr} len={length} -> {args.out}")

    # читаем ровно length байт
    data = bytearray()
    last = time.time()
    while len(data) < length:
        chunk = s.read(min(65536, length - len(data)))
        if chunk:
            data += chunk
            last = time.time()
            done = len(data) * 100 // length
            sys.stdout.write(f"\r# {len(data)}/{length} байт ({done}%)")
            sys.stdout.flush()
        elif time.time() - last > 10:
            print()
            sys.exit(f"Таймаут: получено {len(data)}/{length} байт")
    print()

    # хвост с CRC
    tail = s.read(64)
    mt = re.search(rb"DUMP END crc32=0x([0-9A-Fa-f]{8})", tail)
    dev_crc = int(mt.group(1), 16) if mt else None
    host_crc = zlib.crc32(bytes(data)) & 0xFFFFFFFF

    with open(args.out, "wb") as f:
        f.write(data)

    print(f"# CRC32 host=0x{host_crc:08X} dev={'0x%08X'%dev_crc if dev_crc is not None else '?'}")
    if dev_crc is None:
        print("! маркер DUMP END не пойман — проверь целостность вручную")
    elif dev_crc != host_crc:
        sys.exit("! CRC НЕ СОВПАЛА — дамп повреждён (снизь скорость SPI/baud, проверь пайку)")
    else:
        print(f"# OK, записано {len(data)} байт в {args.out}")
    s.close()

if __name__ == "__main__":
    main()
