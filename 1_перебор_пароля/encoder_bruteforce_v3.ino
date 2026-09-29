/*
 * КОНСОЛЬНАЯ ПРОШИВКА ESP-WROOM-32: эмулятор энкодера под управлением с ПК + чтение WS2812.
 * ПК (crack.py) рулит по Serial, камера читает цифру, ESP щёлкает и докладывает вердикт.
 *
 * Пины: A->25, B->26, кнопка->27, reset цели->32, данные WS2812 от цели->34. Общий GND.
 *
 * КОМАНДЫ (строка + Enter), каждая печатает ответ с префиксом для парсинга:
 *   +[N]  N щелчков CW (по умолч.1)   -> OK+ pos=..
 *   -[N]  N щелчков CCW               -> OK- pos=..
 *   b     нажать кнопку               -> OKB sym=..
 *   r     reset цели                  -> OKR
 *   i     линии в покой               -> OKI
 *   v     мгновенный цвет WS2812      -> COLOR R=.. G=.. B=.. red=0/1
 *   w[ms] ждать красный (по умолч.2000) -> VERDICT RED|NORED R=.. G=.. B=..
 *   c     сброс лок. счётчика
 *   S/T/G/P/H/F/Z/W/D/O/U  параметры (как раньше)
 *   ?     статус
 */
#include <Arduino.h>
#include "driver/rmt.h"

const int PIN_A=25, PIN_B=26, PIN_BTN=27, PIN_RESET=32;
static const gpio_num_t PIN_WS_IN=GPIO_NUM_34;
static const rmt_channel_t RX_CH=RMT_CHANNEL_0;

// --- параметры эмуляции ---
int STEPS_PER_DETENT=1, STEP_DELAY_MS=5, DETENT_GAP_MS=20;
int BTN_PRESS_MS=120, BTN_GAP_MS=250, FIRST_OFFSET=0;
int BOUNCE_N=0, BOUNCE_US=300;
bool DIR_CW_INCREMENT=true, USE_OPEN_DRAIN=true, HIZ_PULLUP=false;
const bool RESET_ACTIVE_LOW=true, BTN_ACTIVE_LOW=true;

// --- WS2812 приём ---
const uint8_t CLK_DIV=8; const uint16_t BIT_THRESH_TICKS=6;
const uint8_t RED_R_MIN=12, RED_OTHER_MAX=8;
RingbufHandle_t rb=NULL;
uint8_t lastR=0,lastG=0,lastB=0;

const uint8_t GRAY[4]={0b00,0b01,0b11,0b10};
const int REST_IDX=2; int idx=REST_IDX;
int localPos=0, symbolNo=0;

// ---------- линии ----------
void setLine(int pin,int level){
  if(USE_OPEN_DRAIN){
    if(level==LOW){pinMode(pin,OUTPUT);digitalWrite(pin,LOW);}
    else pinMode(pin,HIZ_PULLUP?INPUT_PULLUP:INPUT);
  } else {pinMode(pin,OUTPUT);digitalWrite(pin,level?HIGH:LOW);}
}
void writeAB(int i){uint8_t s=GRAY[i&3];setLine(PIN_A,(s>>1)&1);setLine(PIN_B,s&1);}
void idleEncoder(){idx=REST_IDX;writeAB(idx);}
void oneEdge(bool cw){
  int prev=idx; idx=(idx+(cw?1:-1)+4)&3; writeAB(idx);
  for(int k=0;k<BOUNCE_N;k++){delayMicroseconds(BOUNCE_US);writeAB(prev);delayMicroseconds(BOUNCE_US);writeAB(idx);}
  delay(STEP_DELAY_MS);
}
void oneClick(bool cw){
  for(int i=0;i<STEPS_PER_DETENT;i++) oneEdge(cw);
  delay(DETENT_GAP_MS);
  localPos=(localPos+(cw==DIR_CW_INCREMENT?1:-1)+10)%10;
}
void clicks(int n,bool cw){for(int i=0;i<n;i++) oneClick(cw);}
void pressButton(){
  setLine(PIN_BTN,BTN_ACTIVE_LOW?LOW:HIGH); delay(BTN_PRESS_MS);
  setLine(PIN_BTN,BTN_ACTIVE_LOW?HIGH:LOW); delay(BTN_GAP_MS);
  symbolNo++; Serial.printf("OKB sym=%d pos_was=%d\n",symbolNo,localPos); localPos=0;
}
void resetTarget(){
  pinMode(PIN_RESET,OUTPUT);
  digitalWrite(PIN_RESET,RESET_ACTIVE_LOW?LOW:HIGH); delay(250);
  digitalWrite(PIN_RESET,RESET_ACTIVE_LOW?HIGH:LOW);
  idleEncoder(); localPos=0; symbolNo=0;
  Serial.println("OKR");
}

// ---------- WS2812 ----------
void setupRmt(){
  rmt_config_t c={};
  c.rmt_mode=RMT_MODE_RX; c.channel=RX_CH; c.gpio_num=PIN_WS_IN; c.clk_div=CLK_DIV;
  c.mem_block_num=4; c.rx_config.filter_en=true; c.rx_config.filter_ticks_thresh=8;
  c.rx_config.idle_threshold=100;
  rmt_config(&c); rmt_driver_install(RX_CH,4096,0);
  rmt_get_ringbuf_handle(RX_CH,&rb); rmt_rx_start(RX_CH,true);
}
static inline uint16_t highTicks(const rmt_item32_t&it){
  if(it.level0==1)return it.duration0; if(it.level1==1)return it.duration1; return 0;
}
bool frameHasRed(const rmt_item32_t*items,int n){
  uint8_t cur=0,bc=0; int ib=0; uint8_t g=0,r=0; bool red=false;
  for(int i=0;i<n;i++){
    uint16_t h=highTicks(items[i]); if(h==0)continue;
    cur=(cur<<1)|(h>BIT_THRESH_TICKS?1:0);
    if(++bc==8){uint8_t v=cur;cur=0;bc=0;int p=ib%3;
      if(p==0)g=v; else if(p==1)r=v; else {uint8_t b=v; lastG=g;lastR=r;lastB=b;
        if(r>=RED_R_MIN&&g<=RED_OTHER_MAX&&b<=RED_OTHER_MAX)red=true;} ib++;}
  }
  return red;
}
bool pollColor(uint32_t ms,bool stopOnRed){
  size_t sz; void* j;
  while((j=xRingbufferReceive(rb,&sz,0))!=NULL) vRingbufferReturnItem(rb,j);
  uint32_t t0=millis(); bool red=false;
  while(millis()-t0<ms){
    rmt_item32_t* it=(rmt_item32_t*)xRingbufferReceive(rb,&sz,pdMS_TO_TICKS(50));
    if(!it)continue;
    if(frameHasRed(it,sz/sizeof(rmt_item32_t))){red=true; if(stopOnRed){vRingbufferReturnItem(rb,it);break;}}
    vRingbufferReturnItem(rb,it);
  }
  return red;
}

int clicksForSymbol(int pos,int digit){return (pos==0)?(digit+FIRST_OFFSET):digit;}
void playCode(const char*s,int len){
  for(int p=0;p<len;p++){int d=s[p]-'0';int n=clicksForSymbol(p,d);clicks(n,DIR_CW_INCREMENT);pressButton();}
}
void status(){
  Serial.printf("# STEPS=%d dir=%s mode=%s%s T=%d G=%d P=%d H=%d off=%d Z=%d W=%d pos=%d sym=%d\n",
    STEPS_PER_DETENT,DIR_CW_INCREMENT?"CW+":"CW-",USE_OPEN_DRAIN?"OD":"PP",
    (USE_OPEN_DRAIN&&HIZ_PULLUP)?"+pu":"",STEP_DELAY_MS,DETENT_GAP_MS,BTN_PRESS_MS,BTN_GAP_MS,
    FIRST_OFFSET,BOUNCE_N,BOUNCE_US,localPos,symbolNo);
}

void handleLine(String s){
  s.trim(); if(!s.length())return; char c=s[0];
  if(c=='+'||c=='-'){int n=s.length()>1?s.substring(1).toInt():1; if(n<1)n=1;
    bool cw=(c=='+')?DIR_CW_INCREMENT:!DIR_CW_INCREMENT; clicks(n,cw);
    Serial.printf("OK%c pos=%d\n",c,localPos); return;}
  if(c>='0'&&c<='9'){playCode(s.c_str(),s.length()); Serial.println("OKCODE"); return;}
  int v=s.substring(1).toInt();
  switch(toupper(c)){
    case 'B': pressButton(); return;
    case 'R': resetTarget(); return;
    case 'I': idleEncoder(); Serial.println("OKI"); return;
    case 'C': localPos=0;symbolNo=0; Serial.println("OKC"); return;
    case 'V':{bool red=pollColor(250,false);
      Serial.printf("COLOR R=%u G=%u B=%u red=%d\n",lastR,lastG,lastB,red?1:0); return;}
    case 'W':{uint32_t ms=v>0?(uint32_t)v:2000; bool red=pollColor(ms,true);
      Serial.printf("VERDICT %s R=%u G=%u B=%u\n",red?"RED":"NORED",lastR,lastG,lastB); return;}
    case 'S': STEPS_PER_DETENT=max(1,v); break;
    case 'T': STEP_DELAY_MS=max(0,v); break;
    case 'G': DETENT_GAP_MS=max(0,v); break;
    case 'P': BTN_PRESS_MS=max(1,v); break;
    case 'H': BTN_GAP_MS=max(0,v); break;
    case 'F': FIRST_OFFSET=v; break;
    case 'Z': BOUNCE_N=max(0,v); break;
    case 'D': DIR_CW_INCREMENT=!DIR_CW_INCREMENT; break;
    case 'O': USE_OPEN_DRAIN=!USE_OPEN_DRAIN; idleEncoder(); break;
    case 'U': HIZ_PULLUP=!HIZ_PULLUP; idleEncoder(); break;
    case '?': status(); return;
    default: Serial.println("ERR ?"); return;
  }
  status();
}

String inbuf;
void setup(){
  Serial.begin(115200); delay(300);
  setLine(PIN_BTN,BTN_ACTIVE_LOW?HIGH:LOW); idleEncoder(); setupRmt();
  Serial.println("READY console+ws2812");
}
void loop(){
  while(Serial.available()){char ch=(char)Serial.read();
    if(ch=='\n'||ch=='\r'){if(inbuf.length()){handleLine(inbuf);inbuf="";}}
    else if(inbuf.length()<40) inbuf+=ch;}
}
