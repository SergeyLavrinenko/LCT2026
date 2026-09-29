#!/usr/bin/env python3
# Детектор текущей цифры по горящему зелёному светодиоду.
# Использование:
#   python3 detect_digit.py            один замер (снимет кадр)
#   python3 detect_digit.py --live     непрерывно
#   python3 detect_digit.py img.jpg    по готовому изображению
import cv2, json, sys, numpy as np, time, os
from grab import capture

COORDS = "led_coords.json"
LIT_THRESHOLD = 25          # мин. "зелёность" чтобы считать LED горящим

def load_coords():
    d = json.load(open(COORDS))
    return d["leds"], d.get("radius", 18)

def greenness_score(img, x, y, r):
    h, w = img.shape[:2]
    x0, x1 = max(0, x - r), min(w, x + r)
    y0, y1 = max(0, y - r), min(h, y + r)
    roi = img[y0:y1, x0:x1].astype(np.int16)   # BGR
    if roi.size == 0: return 0.0
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
    B, G, R = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    green = np.clip(G - np.maximum(R, B), 0, 255)   # насколько "зелено"
    vals = green[mask]
    return float(vals.mean()) if vals.size else 0.0

def detect(img, leds, r):
    scores = [greenness_score(img, l["x"], l["y"], r) for l in leds]
    best = int(np.argmax(scores))
    lit = scores[best] >= LIT_THRESHOLD
    return (best if lit else None), scores

def annotate(img, leds, r, digit, scores):
    out = img.copy()
    for i, l in enumerate(leds):
        col = (0, 255, 0) if i == digit else (0, 0, 255)
        cv2.circle(out, (l["x"], l["y"]), r, col, 2)
        cv2.putText(out, f"{i}:{scores[i]:.0f}", (l["x"] - 12, l["y"] - r - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
    txt = f"ЦИФРА: {digit}" if digit is not None else "нет свечения"
    cv2.putText(out, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
    return out

def main():
    if not os.path.exists(COORDS):
        print(f"нет {COORDS} — сначала запусти mark_leds.py"); return
    leds, r = load_coords()
    args = sys.argv[1:]
    live = "--live" in args
    imgpath = next((a for a in args if not a.startswith("--")), None)

    if live:
        print("LIVE. Ctrl+C для выхода.")
        try:
            while True:
                img = capture()
                if img is None: continue
                d, sc = detect(img, leds, r)
                print(f"цифра={d}  scores=" + " ".join(f"{i}:{s:.0f}" for i, s in enumerate(sc)))
                time.sleep(0.3)
        except KeyboardInterrupt:
            pass
    else:
        img = cv2.imread(imgpath) if imgpath else capture()
        d, sc = detect(img, leds, r)
        cv2.imwrite("detected.jpg", annotate(img, leds, r, d, sc))
        print(f"ЦИФРА: {d}")
        print("scores: " + " ".join(f"{i}:{s:.0f}" for i, s in enumerate(sc)))
        print("аннотированный кадр -> detected.jpg")

if __name__ == "__main__":
    main()
