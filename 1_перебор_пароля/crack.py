#!/usr/bin/env python3
# Замкнутый подбор с обратной связью по камере + графическая визуализация (кириллица через PIL).
#   python3 crack.py --code 1357
#   python3 crack.py --from 0000 --count 50
#   python3 crack.py --range 0000 9999
#   --no-gui без окна, --preview-file out.png писать кадр дашборда (отладка)
import serial, sys, glob, time, json, argparse, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
from grab import Camera
from detect_digit import load_coords, detect

BAUD=115200; BOOT_WAIT=0.25; SETTLE=0.10
CLICK_T=10; CLICK_G=110; BTN_P=40; BTN_H=90   # калибровано: щелчок и кнопка
MAX_CLICKS=30; VERDICT_MS=600
LOG_JSONL="crack_log.jsonl"; LOG_TXT="crack.log"
FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
leds, radius = load_coords()

def find_port():
    p=sorted(glob.glob("/dev/ttyUSB*")); return p[0] if p else None

class Log:
    def __init__(self): self.jf=open(LOG_JSONL,"a"); self.tf=open(LOG_TXT,"a")
    def ev(self,**kw): kw["t"]=round(time.time(),3); self.jf.write(json.dumps(kw,ensure_ascii=False)+"\n"); self.jf.flush()
    def say(self,m): line=time.strftime("%H:%M:%S ")+m; print(line); self.tf.write(line+"\n"); self.tf.flush()

class ESP:
    def __init__(self,port,log):
        self.s=serial.Serial(port,BAUD,timeout=0.3); self.log=log
        time.sleep(2.0)                      # ESP перезагружается при открытии порта (DTR)
        self.s.reset_input_buffer()
        self.s.write(b"\n"); time.sleep(0.2); self.s.reset_input_buffer()
    def cmd(self,c,ack,timeout=8.0,retries=2):
        lines=[]
        for _ in range(retries+1):
            self.s.reset_input_buffer()
            self.s.write((c+"\n").encode()); t0=time.time()
            while time.time()-t0<timeout:
                ln=self.s.readline().decode("utf-8","replace").strip()
                if not ln: continue
                lines.append(ln); self.log.ev(kind="esp_rx",cmd=c,line=ln)
                if any(ln.startswith(a) for a in ack): return ln,lines
                if ln.startswith("ERR"): break
        self.log.say(f"!! нет ответа '{c}': {lines[-4:]}"); return None,lines

def bgr(rgb): return (rgb[2],rgb[1],rgb[0])
WHITE=(235,235,235); CYAN=(255,215,0); GREEN=(40,210,40); RED=(230,40,40); GRAY=(150,150,150)

class Viz:
    DISP_W=640; PANEL=380
    def __init__(self,gui=True,preview=None):
        self.gui=gui; self.preview=preview; self.paused=False
        self.code=""; self.targets=[0]*4; self.active=-1; self.done=[False]*4
        self.total=0; self.idx=0; self.tried=0; self.red=0; self.anom=0; self.start=time.time()
        self.digit=None; self.action=""; self.recent=[]
        self.f_title=ImageFont.truetype(FONT,20); self.f=ImageFont.truetype(FONT,17)
        self.f_small=ImageFont.truetype(FONT,14); self.f_big=ImageFont.truetype(FONT,34)
        if gui:
            try: cv2.namedWindow("BRUTEFORCE",cv2.WINDOW_NORMAL); cv2.resizeWindow("BRUTEFORCE",1024,430)
            except Exception as e: print("GUI недоступен:",e); self.gui=False
    def compose(self,frame):
        s=self.DISP_W/frame.shape[1]; disp=cv2.resize(frame,(self.DISP_W,int(frame.shape[0]*s)))
        for i,l in enumerate(leds):
            x,y=int(l["x"]*s),int(l["y"]*s); rr=max(4,int(radius*s))
            lit=(self.digit==i); tgt=(self.active>=0 and i==self.targets[self.active])
            col=bgr(GREEN) if lit else (bgr(CYAN) if tgt else (60,60,60))
            cv2.circle(disp,(x,y),rr,col,3 if lit else 2)
        H=max(disp.shape[0],400); cv=np.full((H,self.DISP_W+self.PANEL,3),25,np.uint8)
        cv[:disp.shape[0],:self.DISP_W]=disp
        # цифровые ячейки-разряды (рамки cv2)
        x0=self.DISP_W+16; by=120; bx=x0
        for i in range(4):
            c=bgr(GREEN) if self.done[i] else (bgr(CYAN) if i==self.active else (90,90,90))
            cv2.rectangle(cv,(bx,by),(bx+60,by+60),c,2); bx+=70
        # текст через PIL (кириллица)
        pim=Image.fromarray(cv2.cvtColor(cv,cv2.COLOR_BGR2RGB)); dr=ImageDraw.Draw(pim)
        dr.text((10,disp.shape[0]-46),f"{self.digit if self.digit is not None else '-'}",font=self.f_big,fill=GREEN)
        dr.text((x0,20),"ПЕРЕБОР С ОБРАТНОЙ СВЯЗЬЮ",font=self.f_title,fill=CYAN)
        dr.text((x0,54),f"попытка {self.idx+1}/{self.total}    код {self.code}",font=self.f,fill=WHITE)
        for i in range(4):
            dr.text((x0+70*i+22,130),str(self.targets[i]),font=self.f_big,
                    fill=GREEN if self.done[i] else (CYAN if i==self.active else GRAY))
        el=int(time.time()-self.start); rate=self.tried/max(1,el)*60; y=196
        for t,c in [(f"проверено: {self.tried}    RED: {self.red}    аномалий: {self.anom}",WHITE),
                    (f"время: {el//60:02d}:{el%60:02d}    темп: {rate:.1f}/мин",WHITE),
                    (f"действие: {self.action}",(170,220,170))]:
            dr.text((x0,y),t,font=self.f,fill=c); y+=26
        dr.text((x0,y+4),"последние:",font=self.f_small,fill=GRAY); y+=26
        for code,v in self.recent[-6:][::-1]:
            dr.text((x0+8,y),f"{code}   {v}",font=self.f_small,fill=RED if v=="RED" else (GREEN if v=="NORED" else GRAY)); y+=20
        if self.paused: dr.text((x0,y+6),"[ПАУЗА] p-продолжить q-выход",font=self.f,fill=CYAN)
        return cv2.cvtColor(np.array(pim),cv2.COLOR_RGB2BGR)
    def show(self,frame):
        img=self.compose(frame)
        if self.preview: cv2.imwrite(self.preview,img)
        if self.gui:
            try:
                cv2.imshow("BRUTEFORCE",img)
                while True:
                    k=cv2.waitKey(1)&0xFF
                    if k==ord('q'): raise KeyboardInterrupt
                    if k==ord('p'): self.paused=not self.paused; cv2.imshow("BRUTEFORCE",self.compose(frame))
                    if not self.paused: break
            except KeyboardInterrupt: raise
            except Exception: self.gui=False

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--code"); ap.add_argument("--from",dest="frm")
    ap.add_argument("--count",type=int,default=None); ap.add_argument("--range",nargs=2)
    ap.add_argument("--center")
    ap.add_argument("--progress",default="crack_progress.txt"); ap.add_argument("--restart",action="store_true")
    ap.add_argument("--port"); ap.add_argument("--no-gui",action="store_true"); ap.add_argument("--preview-file")
    a=ap.parse_args()
    port=a.port or find_port()
    if not port: print("нет /dev/ttyUSB*"); return
    log=Log(); log.say(f"# порт {port}")
    esp=ESP(port,log); cam=Camera(); viz=Viz(gui=not a.no_gui,preview=a.preview_file)
    for c in (f"T{CLICK_T}",f"G{CLICK_G}",f"P{BTN_P}",f"H{BTN_H}"): esp.cmd(c,["#"])  # калибр. тайминги

    def read_digit():
        f=cam.read(); d,sc=detect(f,leds,radius)
        viz.digit=d; log.ev(kind="cam",digit=d,scores=[round(x,1) for x in sc])
        viz.show(f); return d,f,sc

    def set_digit(target,attempt,symbol,first_used):
        # ОТКРЫТЫЙ ввод: шлём все щелчки разом, потом ОДИН снимок на цифру.
        # Первый кликающий разряд компенсируем на инверсию первого фронта (+2).
        viz.active=symbol
        first = not first_used[0]
        if target==0:
            dch,n='+',0                          # ноль щелчков, первый фронт не тратится
        elif first:
            # первый фронт после сброса инвертирован: net(+k)=k-2, net(-k)=2-k
            kp=(target+2)%10 or 10; km=(2-target)%10 or 10
            dch,n=('+',kp) if kp<=km else ('-',km)
        else:
            kp=target; km=(10-target)%10          # обычный разряд: кратчайшее направление
            dch,n=('+',kp) if kp<=km else ('-',km)
        if n>0:
            first_used[0]=True
            viz.action=f"символ {symbol}: {dch}{n}"
            esp.cmd(f"{dch}{n}",["OK+"] if dch=='+' else ["OK-"])
        time.sleep(SETTLE)
        d,_,_=read_digit()                        # единственный снимок на цифру
        log.ev(kind="digit",attempt=attempt,symbol=symbol,target=target,clicks=f"{dch}{n}",seen=d,ok=(d==target))
        if d==target:
            viz.done[symbol]=True; return True
        # промах открытого ввода -> закрытая коррекция по одному клику
        viz.anom+=1; log.say(f"  [промах] символ {symbol}: ждал {target}, снимок {d} -> коррекция")
        first_used[0]=True
        cur=d
        for _ in range(12):
            if cur==target: viz.done[symbol]=True; return True
            if cur is None: time.sleep(SETTLE); cur,_,_=read_digit(); continue
            cw=(target-cur)%10; ccw=(cur-target)%10
            dc="+" if cw<=ccw else "-"; viz.action=f"коррекция {symbol}: {dc} ({cur}->{target})"
            esp.cmd(dc,["OK+"] if dc=="+" else ["OK-"]); time.sleep(SETTLE)
            cur,_,_=read_digit()
        log.say(f"  !! не довёл символ {symbol} до {target}"); return False

    def enter_code(code,attempt):
        viz.code=code; viz.targets=[int(c) for c in code]; viz.done=[False]*4; viz.active=-1
        log.say(f"=== #{attempt}: {code} ===")
        esp.cmd("r",["OKR"]); log.ev(kind="reset",attempt=attempt,code=code)
        viz.action="reset, жду загрузку"; t=time.time()
        while time.time()-t<BOOT_WAIT: read_digit()
        for _ in range(15):
            d,_,_=read_digit()
            if d is not None: break
        first_used=[False]
        for i,ch in enumerate(code):
            if not set_digit(int(ch),attempt,i,first_used): return "SETFAIL"
            esp.cmd("b",["OKB"]); log.ev(kind="button",attempt=attempt,symbol=i,digit=int(ch))
        viz.action="читаю вердикт"
        ln,_=esp.cmd(f"w{VERDICT_MS}",["VERDICT"],timeout=VERDICT_MS/1000+3)
        v="RED" if (ln and "RED" in ln and "NORED" not in ln) else ("NORED" if (ln and "NORED" in ln) else "UNK")
        viz.tried+=1; viz.recent.append((code,v))
        if v=="RED": viz.red+=1
        log.ev(kind="verdict",attempt=attempt,code=code,verdict=v,raw=ln); log.say(f"    вердикт: {v}")
        viz.action=f"вердикт {code}: {v}"; viz.show(cam.read()); return v

    if a.code:
        codes=[a.code]
    elif a.center:
        C=int(a.center); order=[C]
        for d in range(1,10000):
            if C-d>=0: order.append(C-d)
            if C+d<=9999: order.append(C+d)
            if len(order)>=10000: break
        if a.count: order=order[:a.count]
        codes=[f"{n:04d}" for n in order]
    elif a.range:
        codes=[f"{n:04d}" for n in range(int(a.range[0]),int(a.range[1])+1)]
    elif a.frm:
        end=(int(a.frm)+a.count) if a.count else 10000
        codes=[f"{n:04d}" for n in range(int(a.frm),min(end,10000))]
    else:
        print("укажи --code/--from/--range/--center"); return
    # --- резюме: пропускаем уже пройденные коды ---
    import os
    if a.restart and os.path.exists(a.progress): os.remove(a.progress)
    done=set()
    if os.path.exists(a.progress):
        done=set(x.strip() for x in open(a.progress) if x.strip())
    todo=[c for c in codes if c not in done]
    log.say(f"# резюме: всего {len(codes)}, уже пройдено {len(done)}, осталось {len(todo)} (файл {a.progress})")
    pf=open(a.progress,"a")
    viz.total=len(todo)
    try:
        for i,code in enumerate(todo):
            viz.idx=i
            v=enter_code(code,i)
            if v in ("RED","NORED"):
                pf.write(code+"\n"); pf.flush(); done.add(code)
            if v=="NORED":
                log.say(f"\n>>> ВОЗМОЖНО ВЕРНО: {code} <<<\n")
                open("FOUND.txt","a").write(code+"\n")
                viz.action=f"НАЙДЕНО? {code}"; viz.show(cam.read())
                if viz.gui: cv2.waitKey(0)
                break
    except KeyboardInterrupt: log.say("прервано (прогресс сохранён)")
    finally:
        cam.release()
        if viz.gui:
            try: cv2.destroyAllWindows()
            except Exception: pass

if __name__=="__main__": main()
