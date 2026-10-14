"""Exercise standby shutdown against a bouncing wake button and RTC state."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AtkPowerOff(unittest.TestCase):
    def test_rtc_wake_and_retained_off_levels(self):
        source = (ROOT / 'components/muse/boards/board_atk_dnesp32s3_box0.c').read_text()
        start = source.index('static esp_err_t power_off(void)')
        end = source.index('static const muse_board_t', start)
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define TALK_GPIO 4
#define CODEC_POWER 14
#define POWER_HOLD 2
#define CHARGE_ENABLE 47
#define RTC_GPIO_MODE_INPUT_ONLY 1
#define RTC_GPIO_MODE_OUTPUT_ONLY 2
#define ESP_SLEEP_WAKEUP_ALL 0
#define ESP_RETURN_ON_ERROR(expr, ...) do { int e=(expr); if(e) return e; } while(0)
#define ESP_LOGI(...) ((void)0)
#define pdMS_TO_TICKS(x) (x)
static const char *TAG;
static void *s_panel;
static bool down, up, cleared, armed, slept, held[50], panel_off;
static int levels[50], modes[50], samples, stable_low, elapsed, fail_input;
static void set_brightness(int n) { assert(n==0); }
static int esp_lcd_panel_disp_on_off(void *p, bool on) { panel_off=!on; return 0; }
static int rtc_gpio_init(int pin) { return pin==TALK_GPIO && fail_input ? -5 : 0; }
static int rtc_gpio_set_direction(int pin,int mode) { modes[pin]=mode;return 0; }
static int rtc_gpio_pullup_dis(int pin) { assert(pin==TALK_GPIO);up=false;return 0; }
static int rtc_gpio_pulldown_en(int pin) { assert(pin==TALK_GPIO);down=true;return 0; }
static int rtc_gpio_get_level(int pin) {
 assert(pin==TALK_GPIO && down && !up && modes[pin]==RTC_GPIO_MODE_INPUT_ONLY);
 static const int bounce[]={1,1,0,1,0,0,0,0,0};
 int v=samples<9?bounce[samples]:0; samples++;
 stable_low=v?0:stable_low+1;return v;
}
static void vTaskDelay(int ms) { elapsed+=ms; }
static int esp_sleep_disable_wakeup_source(int src) {assert(src==0);cleared=true;return 0;}
static int esp_sleep_enable_ext0_wakeup(int pin,int v) {
 assert(pin==TALK_GPIO && v==1 && cleared && stable_low>=5);
 armed=true;return 0;
}
static int gpio_set_level(int pin,int v) {levels[pin]=v;return 0;}
static int rtc_gpio_set_level(int pin,int v) {levels[pin]=v;return 0;}
static int rtc_gpio_hold_en(int pin) {
 assert(modes[pin]==RTC_GPIO_MODE_OUTPUT_ONLY);
 assert(levels[pin]==(pin==POWER_HOLD ? 1 : 0));
 held[pin]=true;return 0;
}
static void esp_deep_sleep_start(void) {
 assert(armed && down && !up && held[POWER_HOLD] && held[CODEC_POWER]);
 assert(panel_off && elapsed>=100);slept=true;
}
''' + source[start:end] + r'''
int main(void) {
 up=true;levels[POWER_HOLD]=levels[CODEC_POWER]=1;
 assert(power_off()==ESP_FAIL); /* fake sleep returns; hardware does not */
 assert(slept && samples==9 && elapsed==180);
 slept=false;fail_input=1;
 assert(power_off()==-5 && !slept); /* no sleep after RTC setup failure */
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            c, exe = Path(tmp)/'power.c', Path(tmp)/'power'
            c.write_text(code)
            subprocess.run(['cc','-std=c11',str(c),'-o',str(exe)],check=True,capture_output=True)
            subprocess.run([str(exe)],check=True,capture_output=True)
