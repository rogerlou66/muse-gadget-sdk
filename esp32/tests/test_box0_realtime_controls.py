"""Run BOX0's actual gesture handler with realtime enabled, not its note latch."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_button_gestures import function

ROOT = Path(__file__).resolve().parents[1]


class Box0RealtimeControls(unittest.TestCase):
    def test_click_call_edges_menu_sleep_pairing_and_music_interruption(self):
        src = (ROOT / 'components/muse/muse_input.c').read_text()
        code = r'''
#include <assert.h>
#include <stddef.h>
#include "muse_button_gestures.h"
#define CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0 1
#define CONFIG_MUSE_REALTIME_VOICE 1
#define MUSE_BTN_TALK_PRESS 1
#define MUSE_BTN_TALK_RELEASE 2
#define MUSE_PTT_DOWN 1
#define MUSE_PTT_UP 2
#define MUSE_MENU_DOWN 1
#define MUSE_MENU_SELECT 2
#define MUSE_LINK_CONFIRM 1
#define MUSE_MODE_LISTENING 1
#define ESP_LOGI(...) ((void)0)
static muse_button_gestures_t s_gestures;
static bool s_talk_down,s_gesture_pair_press,asleep,menu;
static uint32_t s_record_started_ms;
static int down,up,stopped,selected,opened,paired,link_state;
static void muse_music_suspend(void) {stopped++;}
static void post(int type,bool wake) {assert(!wake);if(type==1)down++;else up++;}
static int muse_link_state(void) {return link_state;}
static bool muse_link_talk_press(void) {if(link_state){link_state=0;paired++;return true;}return false;}
static int muse_state_mode(void *p) {(void)p;return 0;}
static void muse_state_poke(void) {}
static bool muse_state_asleep(void) {return asleep;}
static bool muse_menu_is_open(void) {return menu;}
static void set_asleep(bool on,const char *why) {(void)why;asleep=on;}
static void muse_menu_key(int key) {if(key==MUSE_MENU_DOWN){menu=true;opened++;}else selected++;}
''' + function(src, 'finish_toggle_recording') + '\n' + function(src, 'gesture_talk_button') + r'''
static void reset(void) {
 s_gestures=(muse_button_gestures_t){0};s_talk_down=s_gesture_pair_press=false;
 asleep=menu=false;down=up=stopped=selected=opened=paired=link_state=0;
}
static void click(uint32_t t) {gesture_talk_button(1,t);gesture_talk_button(2,t+50);}
int main(void) {
 reset();click(0);assert(stopped==1&&!down);gesture_talk_button(0,400);
 assert(down==1&&up==1&&!s_talk_down); /* call start, never a held recording */
 click(700);gesture_talk_button(0,1100);assert(down==2&&up==2&&!s_talk_down);
 reset();click(0);click(200);assert(opened==1&&!down&&!up);
 click(600);gesture_talk_button(0,1000);assert(selected==1&&!down);
 reset();gesture_talk_button(1,0);gesture_talk_button(0,1000);assert(asleep);
 gesture_talk_button(2,1100);gesture_talk_button(0,1600);assert(!down&&!up);
 gesture_talk_button(1,2000);gesture_talk_button(0,3000);assert(!asleep&&!down);
 reset();link_state=1;click(0);gesture_talk_button(0,400);assert(paired==1&&!down&&!up);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'controls.c').write_text(code)
            result = subprocess.run(['cc', '-std=c11', '-fsanitize=address,undefined',
                '-I' + str(ROOT / 'components/muse'), str(p / 'controls.c'), '-o', str(p / 'controls')],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(p / 'controls')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
