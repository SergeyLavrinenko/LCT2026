#!/usr/bin/env python3
# Захват кадров. Постоянный поток через OpenCV (быстро) + разовый capture() для CLI.
import cv2, sys, time

DEV_INDEX = 7          # /dev/video7 — внешняя камера над платой
W, H = 1280, 720

class Camera:
    def __init__(self, index=DEV_INDEX, w=W, h=H):
        self.cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError(f"камера {index} не открылась")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        try: self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception: pass
        for _ in range(5): self.cap.read()      # прогрев
    def read(self, flush=4):
        for _ in range(max(0, flush)):           # выкинуть накопленные кадры (антизадержка)
            self.cap.grab()
        ok, f = self.cap.retrieve()
        return f if ok else None
    def release(self):
        try: self.cap.release()
        except Exception: pass

def capture(dev=None):
    cam = Camera(); f = cam.read(); cam.release(); return f

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "snapshot.jpg"
    f = capture()
    if f is None: print("нет кадра"); sys.exit(1)
    cv2.imwrite(out, f); print(f"сохранён {out}: {f.shape[1]}x{f.shape[0]}")
