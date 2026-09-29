#!/usr/bin/env python3
# Ручная разметка 10 светодиодов кликами. Кликай ПО ПОРЯДКУ цифр: 0,1,2,...,9.
# Использование: python3 mark_leds.py [image.jpg]
#   без аргумента — снимет свежий кадр с камеры.
# Клавиши: s=сохранить  z=отменить последний  r=сброс  +/-=радиус  c=новый кадр  q=выход
import cv2, json, sys, os
from grab import capture

COORDS = "led_coords.json"
pts = []          # [(x,y), ...] в порядке цифр 0..9
radius = 18

def load_img(path):
    if path and os.path.exists(path):
        return cv2.imread(path)
    print("снимаю кадр с камеры...")
    return capture()

def on_mouse(event, x, y, flags, param):
    global pts
    if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 10:
        pts.append((x, y))

def redraw(base):
    img = base.copy()
    for i, (x, y) in enumerate(pts):
        cv2.circle(img, (x, y), radius, (0, 0, 255), 2)
        cv2.putText(img, str(i), (x - 6, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    n = len(pts)
    msg = f"кликни LED цифры {n}" if n < 10 else "готово (10/10) -> s сохранить"
    cv2.putText(img, f"{msg} | r={radius}px  s/z/r/+/-/c/q",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return img

def main():
    global pts, radius
    path = sys.argv[1] if len(sys.argv) > 1 else None
    base = load_img(path)
    if base is None: print("нет изображения"); return
    cv2.namedWindow("mark LEDs", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("mark LEDs", on_mouse)
    while True:
        cv2.imshow("mark LEDs", redraw(base))
        k = cv2.waitKey(20) & 0xFF
        if k == ord('q'): break
        elif k == ord('z') and pts: pts.pop()
        elif k == ord('r'): pts = []
        elif k in (ord('+'), ord('=')): radius += 2
        elif k == ord('-'): radius = max(4, radius - 2)
        elif k == ord('c'):
            nb = capture()
            if nb is not None: base = nb
        elif k == ord('s'):
            if len(pts) != 10:
                print(f"нужно 10 точек, сейчас {len(pts)}"); continue
            data = {"device": "/dev/video7", "radius": radius,
                    "leds": [{"digit": i, "x": x, "y": y} for i, (x, y) in enumerate(pts)]}
            json.dump(data, open(COORDS, "w"), indent=2)
            cv2.imwrite("marked.jpg", redraw(base))
            print(f"сохранено в {COORDS} (+ marked.jpg)")
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
