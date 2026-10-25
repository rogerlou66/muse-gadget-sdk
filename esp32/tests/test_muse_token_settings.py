"""Exercise the actual settings refresh without requiring LVGL or hardware."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MuseTokenSettings(unittest.TestCase):
    def test_paired_token_vm_fallback_and_masked_sdk_status(self):
        source = (ROOT / 'components/muse/muse_settings_ui.c').read_text()
        at = source.index('static void tick_hatch(void)')
        end = source.index('{', at) + 1
        depth = 1
        while depth:
            depth += (source[end] == '{') - (source[end] == '}')
            end += 1
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define MUSE_HATCH_HOST_MAX 96
#define MUSE_HATCH_VM_MAX 64
#define MUSE_HOST_MAX 96
#define MUSE_VM_MAX 64
#define MUSE_HATCH_REACHABLE 1
#define MUSE_HATCH_UNREACHABLE 2
#define COLOR_OK 1
#define COLOR_WARN 2
#define COLOR_DIM 3
typedef struct {int state;char detail[48],vm_id[64];} muse_hatch_status_t;
typedef struct {char value[100];} label_t;
static label_t labels[7];
static label_t *s_hatch=labels,*s_hatch_status=labels+1,*s_hatch_host=labels+2,
 *s_hatch_vm=labels+3,*s_hatch_token=labels+4,*s_hatch_sdk_token=labels+5;
static label_t *s_link_reset_lbl=labels;
static label_t *s_link_status=labels+6;
static int64_t s_link_reset_armed_us;
static int64_t s_sdk_token_bad_us,clock_us;
static bool paired,explicit_token,token_changed;
static char configured_vm[64];
static const char *sdk;
static muse_hatch_status_t state;
static void muse_hatch_status(muse_hatch_status_t *out) {*out=state;}
static const char *muse_hatch_state_name(int state) {(void)state;return "Connected";}
static void set_text(label_t *label,const char *text) {snprintf(label->value,sizeof(label->value),"%s",text);}
static int lv_color_hex(int color) {return color;}
static void lv_obj_set_style_text_color(label_t *label,int color,int sel) {(void)label;(void)color;(void)sel;}
static void muse_settings_hatch_host(char *out) {strcpy(out,"test-server");}
static void muse_settings_hatch_vm(char *out) {strcpy(out,configured_vm);}
static unsigned muse_settings_hatch_token_len(void) {return explicit_token?48:0;}
static bool muse_link_hatch_linked(void) {return paired;}
static bool muse_link_sdk_token_changed(void) {return token_changed;}
static int muse_link_state(void) {return 0;}
static const char *muse_link_state_name(int state) {(void)state;return "Online";}
static const char *muse_link_sdk_token(void) {return sdk;}
static int64_t esp_timer_get_time(void) {return clock_us;}
''' + source[at:end] + r'''
int main(void) {
 state.state=MUSE_HATCH_REACHABLE;strcpy(state.vm_id,"connected-vm");
 tick_hatch();assert(!strcmp(s_hatch_status->value,"Connected"));
 assert(!strcmp(s_hatch_vm->value,"connected-vm"));
 assert(!strcmp(s_hatch_token->value,"Not set"));
 assert(!strcmp(s_hatch_sdk_token->value,"Not set"));
 paired=true;tick_hatch();assert(!strcmp(s_hatch_token->value,"Set"));
 token_changed=true;tick_hatch();assert(strstr(s_link_status->value,"paired with another SDK token"));
 token_changed=false;tick_hatch();assert(!strstr(s_link_status->value,"another SDK token"));
 explicit_token=true;tick_hatch();assert(!strcmp(s_hatch_token->value,"Set here"));
 strcpy(configured_vm,"override-vm");tick_hatch();assert(!strcmp(s_hatch_vm->value,"override-vm"));
 sdk="mgst_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
 tick_hatch();assert(!strcmp(s_hatch_sdk_token->value,"mgst_AAAAAAA..."));
 assert(!strstr(s_hatch_sdk_token->value,sdk));
 s_sdk_token_bad_us=1;clock_us=1000000;tick_hatch();assert(!strcmp(s_hatch_sdk_token->value,"Not a token"));
 clock_us=6000000;tick_hatch();assert(!s_sdk_token_bad_us&&!strcmp(s_hatch_sdk_token->value,"mgst_AAAAAAA..."));
 strcpy(state.detail,"diagnostic");tick_hatch();assert(!strcmp(s_hatch_status->value,"Connected\ndiagnostic"));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'settings.c').write_text(code)
            result = subprocess.run(['cc', '-std=c11', '-fsanitize=address,undefined',
                                     str(p / 'settings.c'), '-o', str(p / 'settings')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(p / 'settings')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
