"""Use production screen/power handlers to stop music before sleep and shutdown."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
def extract(src,name):
    at=re.search(r'static void '+name+r'\([^)]*\)\n\{',src).start()
    pos=src.index('{',at)+1;depth=1
    while depth:
        depth+=(src[pos]=='{')-(src[pos]=='}');pos+=1
    return src[at:pos]
class MusicPower(unittest.TestCase):
    def test_sleep_and_power_off_interrupt_before_rail_shutdown(self):
        src=(ROOT/'components/muse/muse_input.c').read_text()
        code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0 1
#define MUSE_MODE_OFF 1
#define MUSE_MODE_IDLE 0
#define ESP_OK 0
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define GOODBYE_MS 1200
#define pdMS_TO_TICKS(n) (n)
typedef int esp_err_t;
static int s_input=1,interrupts,notifications,wifi,shutdown;
static bool sleeping;
static void muse_music_interrupt(void) {interrupts++;}
static bool muse_state_asleep(void) {return sleeping;}
static void muse_state_set_asleep(bool on) {sleeping=on;}
static void xTaskNotifyGive(int task) {assert(task==1);notifications++;}
static bool muse_wifi_connected(void) {return false;}
static void muse_wifi_apply(void) {wifi++;}
static void update_power(void) {}
static void muse_state_set_progress(float x) {(void)x;}
static void muse_state_set_level(float x) {(void)x;}
static void muse_state_set_mode(int x) {(void)x;}
static void muse_state_set_caption(const char *s) {(void)s;}
static void vTaskDelay(int ms) {(void)ms;}
static int off(void) {assert(interrupts==2);shutdown++;return ESP_OK;}
static struct {int (*power_off)(void);} board={off},*muse_board=&board;
''' + extract(src,'power_off')+'\n'+extract(src,'set_asleep')+r'''
int main(void) {
 set_asleep(true,"test");assert(sleeping&&interrupts==1&&notifications==1&&!wifi);
 set_asleep(false,"test");assert(!sleeping&&interrupts==1&&notifications==2&&wifi==1);
 power_off();assert(shutdown==1&&interrupts==2);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'power.c').write_text(code)
            r=subprocess.run(['cc','-std=c11',str(p/'power.c'),'-o',str(p/'power')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'power')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
