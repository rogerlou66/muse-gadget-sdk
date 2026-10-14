"""Exercise production gesture timing and ATK recording/menu integration."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    start = re.search(r'static void ' + name + r'\([^)]*\)\n\{', source).start()
    brace = source.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class ButtonGestures(unittest.TestCase):
    def test_timing_and_input_actions(self):
        production = (ROOT / 'components/muse/muse_input.c').read_text()
        code = r'''
#include <assert.h>
#include <stddef.h>
#include "muse_button_gestures.h"
#define MUSE_BTN_TALK_PRESS 1
#define MUSE_BTN_TALK_RELEASE 2
#define MUSE_PTT_DOWN 1
#define MUSE_PTT_UP 2
#define MUSE_MENU_DOWN 1
#define MUSE_MENU_SELECT 2
#define MUSE_LINK_CONFIRM 1
#define MUSE_MODE_LISTENING 1
#define ESP_LOGI(...) ((void)0)
static const char *TAG;
static muse_button_gestures_t s_gestures;
static bool s_talk_down, s_gesture_pair_press;
static uint32_t s_record_started_ms;
static int downs, ups, menus, selects, confirms, mode, link_state;
static bool asleep, menu;
static void post(int type, bool wake) { assert(!wake); if(type==1) downs++; else ups++; }
static int muse_link_state(void) { return link_state; }
static bool muse_link_talk_press(void) { if(link_state==1) { confirms++; link_state=0; return true; } return false; }
static int muse_state_mode(void *p) { return mode; }
static void muse_state_poke(void) {}
static bool muse_state_asleep(void) { return asleep; }
static bool muse_menu_is_open(void) { return menu; }
static void set_asleep(bool on, const char *why) { asleep=on; }
static void muse_menu_key(int k) { if(k==1) {menus++;menu=true;} else selects++; }
''' + function(production, 'finish_toggle_recording') + '\n' + function(production, 'gesture_talk_button') + r'''
static void clear(void) {
 s_gestures=(muse_button_gestures_t){0}; s_talk_down=s_gesture_pair_press=false;
 s_record_started_ms=0; downs=ups=menus=selects=confirms=mode=link_state=0;
 asleep=menu=false;
}
static void click(uint32_t t) { gesture_talk_button(1,t); gesture_talk_button(2,t+50); }
int main(void) {
 /* Single starts only after the double-click window; a later click stops. */
 clear(); click(0); gesture_talk_button(0,399); assert(downs==0);
 gesture_talk_button(0,400); assert(downs==1 && ups==0 && s_talk_down);
 mode=1; click(700); gesture_talk_button(0,1100); assert(ups==1 && !s_talk_down);
 /* Double click must open the menu without starting an accidental note. */
 clear(); click(0); click(200); gesture_talk_button(0,600);
 assert(menus==1 && downs==0 && ups==0 && selects==0);
 /* Long hold fires once; release must not also generate a single click. */
 clear(); gesture_talk_button(1,0); gesture_talk_button(0,999); assert(!asleep);
 gesture_talk_button(0,1000); assert(asleep); gesture_talk_button(0,2000); assert(asleep);
 gesture_talk_button(2,2050); gesture_talk_button(0,2500); assert(asleep && downs==0);
 gesture_talk_button(1,3000); gesture_talk_button(0,4000); assert(!asleep && downs==0);
 /* In a menu a single selects; a double doesn't change a setting. */
 clear(); menu=true; click(0); gesture_talk_button(0,400); assert(selects==1 && downs==0);
 clear(); menu=true; click(0); click(200); assert(selects==0 && downs==0);
 /* Opening a menu while latched recording finishes the note exactly once. */
 clear(); click(0); gesture_talk_button(0,400); mode=1;
 click(600); click(800); assert(ups==1 && menus==1 && downs==1);
 /* Long hold during recording stops before switching the screen off. */
 clear(); click(0); gesture_talk_button(0,400); mode=1;
 gesture_talk_button(1,600); gesture_talk_button(0,1600);
 assert(asleep && ups==1 && downs==1);
 /* Confirmation consumes the whole press instead of recording or sleeping. */
 clear(); link_state=1; gesture_talk_button(1,0); gesture_talk_button(0,1200);
 gesture_talk_button(2,1300); gesture_talk_button(0,1800);
 assert(confirms==1 && downs==0 && menus==0 && !asleep);
 /* A refused/finished recording clears its toggle latch. */
 clear(); click(0); gesture_talk_button(0,400); gesture_talk_button(0,1400);
 assert(!s_talk_down && ups==1);
 /* Pending gestures work across uint32 millisecond wrap and same-poll edges. */
 muse_button_gestures_t s={0};
 assert(muse_button_gestures_step(&s,true,true,UINT32_MAX-100)==0);
 assert(muse_button_gestures_step(&s,false,false,248)==0);
 assert(muse_button_gestures_step(&s,false,false,249)==MUSE_GESTURE_SINGLE);
 /* Holding the second tap replaces the double with a long gesture. */
 s=(muse_button_gestures_t){0}; muse_button_gestures_step(&s,true,true,0);
 muse_button_gestures_step(&s,true,false,200);
 assert(muse_button_gestures_step(&s,false,false,1200)==MUSE_GESTURE_LONG);
 assert(muse_button_gestures_step(&s,false,true,1250)==0);
 assert(muse_button_gestures_step(&s,false,false,1700)==0);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            c, exe = Path(tmp) / 'test.c', Path(tmp) / 'test'
            c.write_text(code)
            subprocess.run(['cc', '-std=c11', '-I', str(ROOT / 'components/muse'), str(c), '-o', str(exe)], check=True, capture_output=True)
            subprocess.run([str(exe)], check=True, capture_output=True)
