/*
 * ESP32 -> внешний программатор SPI NOR flash (in-circuit дамп/чтение чипа RP2040-Zero и др.).
 *
 * ИДЕЯ: держим целевой MCU (RP2040) в СБРОСЕ (RUN=GND), тогда его QSPI-пады — входы,
 * шина свободна, ESP32 становится единственным SPI-мастером и читает W25Qxx напрямую.
 *
 * ПОДКЛЮЧЕНИЕ (VSPI, esp32dev). Ноги flash в нумерации SOIC-8/USON-8:
 *   flash1 /CS   -> ESP GPIO5  (CS)
 *   flash2 DO/IO1-> ESP GPIO19 (MISO)
 *   flash3 /WP   -> 10k на 3V3 (если чип «девственный», без QE-бита)
 *   flash4 GND   -> GND (общий с ESP и целью обязателен)
 *   flash5 DI/IO0-> ESP GPIO23 (MOSI)
 *   flash6 CLK   -> ESP GPIO18 (SCK)
 *   flash7 /HOLD -> 10k на 3V3
 *   flash8 VCC   -> 3V3  (питание — ЛИБО от ESP, ЛИБО USB в цель, не одновременно)
 *   RUN/RESET цели -> GPIO27 ESP (программно держим в сбросе) ИЛИ перемычка RUN->GND.
 *
 * !!! На RP2040-Zero кнопка BOOT сидит на /CS через ~1k — НЕ нажимать во время дампа.
 *
 * КОМАНДЫ (строка + Enter, ответ с префиксом для парсинга):
 *   i           JEDEC ID + определить объём               -> ID mfr=.. type=.. cap=.. size=..
 *   h[N]        hex-превью N байт с адреса 0 (по умолч.64) -> текстом
 *   d[SIZE]     дамп SIZE байт (по умолч. весь чип) в бинарном протоколе (для dump_flash.py)
 *   d0xADDR,LEN дамп диапазона: напр.  d0x0,0x100
 *   f           отпустить цель из сброса (RUN -> Hi-Z)
 *   x           снова удержать цель в сбросе (RUN -> GND)
 *   ?           статус
 *
 * БИНАРНЫЙ ПРОТОКОЛ дампа (команда d):
 *   ASCII-строка:  "DUMP START addr=%u len=%u\n"
 *   len сырых байт содержимого
 *   ASCII-строка:  "\nDUMP END crc32=0x%08X\n"
 * dump_flash.py читает ровно len байт между маркерами и проверяет CRC32.
 */
#include <Arduino.h>
#include <SPI.h>

// ---- пины ----
static const int PIN_CS    = 5;
static const int PIN_SCK   = 18;
static const int PIN_MISO  = 19;
static const int PIN_MOSI  = 23;
static const int PIN_RESET = 27;          // RUN целевого MCU
static const bool RESET_ACTIVE_LOW = true; // RUN=LOW -> цель в сбросе

// ---- скорость SPI ----
// 2 МГц — надёжно с проводами-соплями (после сбоя дампа на 8 МГц снизили).
static uint32_t SPI_HZ = 2000000;

// ---- команды flash ----
static const uint8_t CMD_RDID   = 0x9F; // JEDEC ID
static const uint8_t CMD_READ   = 0x03; // read data (1-1-1), без dummy
static const uint8_t CMD_RSTEN  = 0x66; // enable reset
static const uint8_t CMD_RST    = 0x99; // reset device
static const uint8_t CMD_CRMR   = 0xFF; // continuous-read mode reset / exit QPI
static const uint8_t CMD_RELPD  = 0xAB; // release power-down

SPIClass spi(VSPI);
uint32_t g_flashSize = 0; // определяется по JEDEC capacity

// ---------- низкий уровень ----------
static inline void csLow(){ digitalWrite(PIN_CS, LOW); }
static inline void csHigh(){ digitalWrite(PIN_CS, HIGH); }

void beginTxn(){ spi.beginTransaction(SPISettings(SPI_HZ, MSBFIRST, SPI_MODE0)); }
void endTxn(){ spi.endTransaction(); }

void readJEDEC(uint8_t id[3]){
  beginTxn(); csLow();
  spi.transfer(CMD_RDID);
  id[0]=spi.transfer(0); id[1]=spi.transfer(0); id[2]=spi.transfer(0);
  csHigh(); endTxn();
}

// Одиночная короткая команда без данных (напр. reset).
void cmd1(uint8_t c){
  beginTxn(); csLow();
  spi.transfer(c);
  csHigh(); endTxn();
}

// Полный сброс флешки в штатный 1-битный SPI: выход из continuous-read/QPI,
// software reset, release power-down. Именно это лечит битый дамп после boot2 RP2040.
void flashReset(){
  cmd1(CMD_CRMR); delayMicroseconds(50);   // выйти из continuous-read mode
  cmd1(CMD_RELPD); delayMicroseconds(50);  // release deep power-down
  cmd1(CMD_RSTEN); delayMicroseconds(5);
  cmd1(CMD_RST);   delay(1);               // reset device (tRST ~30us, берём с запасом)
}

// Чтение len байт с адреса addr в buf (командой 0x03). Проверенный метод:
// одна transferBytes(NULL,...) — так первые 4 КБ читались идеально.
void readData(uint32_t addr, uint8_t* buf, size_t len){
  beginTxn(); csLow();
  spi.transfer(CMD_READ);
  spi.transfer((addr>>16)&0xFF);
  spi.transfer((addr>>8)&0xFF);
  spi.transfer(addr&0xFF);
  spi.transferBytes(nullptr, buf, len);
  csHigh(); endTxn();
}

// ---------- управление сбросом цели ----------
void holdTargetReset(){ // цель В сброс -> шина свободна
  pinMode(PIN_RESET, OUTPUT);
  digitalWrite(PIN_RESET, RESET_ACTIVE_LOW ? LOW : HIGH);
}
void releaseTarget(){   // отпустить цель (Hi-Z, чтобы не мешать её собственному подтягу)
  pinMode(PIN_RESET, INPUT);
}

// ---------- CRC32 (как у zlib, полином 0xEDB88320) ----------
uint32_t crc32_update(uint32_t crc, const uint8_t* p, size_t n){
  crc = ~crc;
  while(n--){
    crc ^= *p++;
    for(int k=0;k<8;k++) crc = (crc>>1) ^ (0xEDB88320u & (-(int32_t)(crc&1)));
  }
  return ~crc;
}

// ---------- определение объёма ----------
uint32_t sizeFromCapacityByte(uint8_t cap){
  // У Winbond/большинства JEDEC третий байт = log2(размер в байтах): 0x15->2МБ и т.д.
  if(cap>=0x10 && cap<=0x1B) return 1u << cap;
  return 0;
}

bool identify(bool verbose){
  uint8_t id[3];
  readJEDEC(id);
  g_flashSize = sizeFromCapacityByte(id[2]);
  if(verbose){
    Serial.printf("ID mfr=0x%02X type=0x%02X cap=0x%02X size=%u bytes",
                  id[0], id[1], id[2], g_flashSize);
    if(id[0]==0xEF) Serial.print(" (Winbond)");
    if((id[0]==0xFF&&id[1]==0xFF)||(id[0]==0x00&&id[1]==0x00))
      Serial.print("  <- ПУСТО: цель НЕ в сбросе? проверь RUN и пайку");
    Serial.println();
  }
  return g_flashSize>0;
}

// ---------- hex-превью ----------
void hexPreview(uint32_t n){
  if(n==0) n=64; if(n>4096) n=4096;
  uint8_t buf[256];
  for(uint32_t base=0; base<n; base+=16){
    uint32_t chunk = min((uint32_t)16, n-base);
    readData(base, buf, chunk);
    Serial.printf("%06X  ", base);
    for(uint32_t i=0;i<16;i++){ if(i<chunk) Serial.printf("%02X ", buf[i]); else Serial.print("   "); }
    Serial.print(" ");
    for(uint32_t i=0;i<chunk;i++){ char c=buf[i]; Serial.write((c>=32&&c<127)?c:'.'); }
    Serial.println();
  }
}

// ---------- дамп ----------
void dumpRange(uint32_t addr, uint32_t len){
  static uint8_t buf[4096];
  // без авто-flashReset (ломал чтение)
  if(len==0){
    if(g_flashSize==0) identify(false);
    len = g_flashSize ? g_flashSize : 0x200000; // фолбэк 2 МБ
  }
  Serial.printf("DUMP START addr=%u len=%u\n", addr, len);
  Serial.flush();
  uint32_t crc = 0, done = 0;
  while(done < len){
    uint32_t chunk = min((uint32_t)sizeof(buf), len-done);
    readData(addr+done, buf, chunk);
    Serial.write(buf, chunk);
    crc = crc32_update(crc, buf, chunk);
    done += chunk;
  }
  Serial.flush();
  Serial.printf("\nDUMP END crc32=0x%08X\n", crc);
}

// ---------- парсер строки дампа ----------
// поддерживает:  d  |  d1048576  |  d0x100000  |  d0x0,0x100  |  d0,256
void handleDump(const String& arg){
  uint32_t addr=0, len=0;
  int comma = arg.indexOf(',');
  auto parse=[](String s)->uint32_t{
    s.trim();
    if(s.startsWith("0x")||s.startsWith("0X")) return (uint32_t)strtoul(s.c_str(),nullptr,16);
    return (uint32_t)strtoul(s.c_str(),nullptr,10);
  };
  if(comma>=0){ addr=parse(arg.substring(0,comma)); len=parse(arg.substring(comma+1)); }
  else if(arg.length()){ len=parse(arg); }
  dumpRange(addr, len);
}

void status(){
  Serial.printf("# CS=%d SCK=%d MISO=%d MOSI=%d RESET=%d SPI=%uHz size=%u\n",
    PIN_CS,PIN_SCK,PIN_MISO,PIN_MOSI,PIN_RESET,SPI_HZ,g_flashSize);
}

// Самопроверка стабильности: читаем регион дважды и сверяем CRC. y[LEN] (по умолч.64К).
void selfCheck(uint32_t len){
  if(len==0) len=0x10000;
  static uint8_t b1[4096], b2[4096];
  uint32_t c1=0,c2=0,done=0,diffs=0,first=0xFFFFFFFF;
  while(done<len){
    uint32_t chunk=min((uint32_t)sizeof(b1), len-done);
    readData(done,b1,chunk);
    readData(done,b2,chunk);
    c1=crc32_update(c1,b1,chunk); c2=crc32_update(c2,b2,chunk);
    for(uint32_t i=0;i<chunk;i++) if(b1[i]!=b2[i]){ if(first==0xFFFFFFFF)first=done+i; diffs++; }
    done+=chunk;
  }
  Serial.printf("SELFCHECK len=%u crcA=0x%08X crcB=0x%08X diffs=%u", len,c1,c2,diffs);
  if(diffs) Serial.printf(" firstDiff=0x%X -> НЕСТАБИЛЬНО (снизь SPI_HZ)\n",first);
  else Serial.println(" -> СТАБИЛЬНО (два чтения совпали)");
}

void handleLine(String s){
  s.trim(); if(!s.length()) return;
  char c = s[0]; String arg = s.substring(1);
  switch(tolower(c)){
    case 'i': identify(true); return;
    case 'h': hexPreview(arg.toInt()); return;
    case 'd': handleDump(arg); return;
    case 'y': selfCheck(arg.length()?strtoul(arg.c_str(),nullptr,0):0); return;
    case 'r': flashReset(); Serial.println("OK flash reset -> 1-bit SPI"); return;
    case 'f': releaseTarget(); Serial.println("OK target RELEASED (RUN Hi-Z)"); return;
    case 'x': holdTargetReset(); Serial.println("OK target HELD in reset"); return;
    case '?': status(); return;
    default: Serial.println("ERR ? (i/h/d/y/r/f/x/?)"); return;
  }
}

String inbuf;
void setup(){
  Serial.begin(115200); delay(300);
  pinMode(PIN_CS, OUTPUT); csHigh();
  holdTargetReset();                 // сразу держим цель в сбросе
  spi.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);
  delay(10);
  // flashReset выключен: авто-сброс ломал чтение. Доступен командой r при нужде.
  Serial.println("READY flash_dump v2. Команды: i h d y r f x ?");
  identify(true);
}
void loop(){
  while(Serial.available()){
    char ch=(char)Serial.read();
    if(ch=='\n'||ch=='\r'){ if(inbuf.length()){ handleLine(inbuf); inbuf=""; } }
    else if(inbuf.length()<40) inbuf+=ch;
  }
}
