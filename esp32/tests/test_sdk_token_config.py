"""Exercise production identity and serial token code with synthetic credentials."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'main'


class SdkTokenConfig(unittest.TestCase):
    def test_validation_boot_precedence_storage_failure_and_console_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            stubs = {
                'sdkconfig.h': '#define CONFIG_GADGET_SDK_TOKEN "mgst_' + 'C' * 42 + 'A"\n'
                               '#define CONFIG_HOMEHUB_BLE_NAME_PREFIX "Test"\n'
                               '#define CONFIG_HOMEHUB_BLE_NAME_SUFFIX ""\n',
                'esp_mac.h': '#include <stdint.h>\n#include "sdkconfig.h"\n'
                             '#define ESP_OK 0\n#define ESP_MAC_WIFI_STA 0\nint esp_read_mac(uint8_t *,int);\n',
                'esp_log.h': '#define ESP_LOGI(...) ((void)0)\n#define ESP_LOGW(...) ((void)0)\n'
                             '#define ESP_LOGE(...) ((void)0)\n',
                'esp_system.h': 'void esp_restart(void);\n',
                'freertos/FreeRTOS.h': '#define pdMS_TO_TICKS(n) (n)\n',
                'freertos/task.h': 'void vTaskDelay(int);\n',
                'soc/soc_caps.h': '#define SOC_USB_SERIAL_JTAG_SUPPORTED 0\n',
                'driver/uart.h': '', 'driver/uart_vfs.h': '',
            }
            for name, text in stubs.items():
                f = p / name
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text)
            code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "identity.h"
#include "config_store.h"
static const char valid[]="mgst_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
static const char other[]="mgst_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA";
static char stored[100];
static bool fail_write;
static int writes,restarts,delays;
int esp_read_mac(uint8_t *m,int type) {(void)type;memset(m,0,6);return 0;}
bool config_get_str(const char *key,char *out,size_t cap) {
 assert(!strcmp(key,"sdk_token"));snprintf(out,cap,"%s",stored);return stored[0]!=0;
}
bool config_set_str(const char *key,const char *value) {
 assert(!strcmp(key,"sdk_token"));writes++;if(fail_write)return false;
 snprintf(stored,sizeof(stored),"%s",value);return true;
}
void esp_restart(void) {restarts++;}
void vTaskDelay(int ms) {assert(ms==500);delays++;}
#include "sdk_token_console.c"
int main(int argc,char **argv) {
 assert(argc==2&&strlen(valid)==48&&strlen(other)==48);
 assert(identity_sdk_token_valid(valid)&&identity_sdk_token_valid(other));
 assert(!identity_sdk_token_valid(NULL)&&!identity_sdk_token_valid("")&&
        !identity_sdk_token_valid("mgst_short"));
 char bad[60];strcpy(bad,valid);bad[47]='B';assert(!identity_sdk_token_valid(bad));
 strcpy(bad,valid);bad[12]='%';assert(!identity_sdk_token_valid(bad));
 strcpy(bad,valid);bad[48]='A';bad[49]=0;assert(!identity_sdk_token_valid(bad));
 if(!strcmp(argv[1],"stored"))strcpy(stored,other);
 else if(!strcmp(argv[1],"invalid"))strcpy(stored,"invalid persisted token");
 identity_init();
 const char *before=identity_sdk_token();
 if(!strcmp(argv[1],"stored"))assert(before&&!strcmp(before,other));
 else if(CONFIG_GADGET_SDK_TOKEN[0])assert(before&&!strcmp(before,CONFIG_GADGET_SDK_TOKEN));
 else assert(!before);
 assert(!identity_set_sdk_token("bad")&&!writes);
 fail_write=true;assert(!identity_set_sdk_token(valid)&&writes==1);fail_write=false;
 assert(identity_set_sdk_token(valid)&&writes==2&&!strcmp(stored,valid));
 assert(identity_sdk_token()==before); /* a new token takes effect only at boot */
 assert(!sdk_token_console_command("music.status"));
 assert(!sdk_token_console_command("asr.status"));
 assert(sdk_token_console_command("sdk_token=bad")&&writes==2&&!restarts);
 char cmd[96];snprintf(cmd,sizeof(cmd),"sdk_token=%s",valid);
 fail_write=true;assert(sdk_token_console_command(cmd)&&!restarts);fail_write=false;
 assert(sdk_token_console_command(cmd)&&restarts==1&&delays==1);
 /* Non-Muse byte collector must discard an entire overlong line and recover. */
 line_reader_t r={0};feed(&r,'>');
 for(int i=0;i<200;i++)feed(&r,'A');feed(&r,'\n');assert(restarts==1);
 feed(&r,'>');for(const char *s=cmd;*s;s++)feed(&r,*s);feed(&r,'\r');feed(&r,'\n');
 assert(restarts==2&&delays==2);
}
'''
            (p / 'check.c').write_text(code)
            result = subprocess.run(['cc', '-std=c11', '-fsanitize=address,undefined',
                                     '-I' + str(p), '-I' + str(MAIN), str(MAIN / 'identity.c'),
                                     str(p / 'check.c'), '-o', str(p / 'check')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            def check_modes():
                for mode in ('none', 'invalid', 'stored'):
                    result = subprocess.run([str(p / 'check'), mode], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('@sdk_token error invalid', result.stdout)
                    self.assertIn('@sdk_token error storage', result.stdout)
                    self.assertEqual(result.stdout.count('@sdk_token ok'), 2)
                    self.assertNotIn('mgst_' + 'A' * 43, result.stdout)
            check_modes()
            # Web-flasher-style builds start without any embedded SDK token.
            config = p / 'sdkconfig.h'
            config.write_text(config.read_text().replace('mgst_' + 'C' * 42 + 'A', ''))
            result = subprocess.run(['cc', '-std=c11', '-fsanitize=address,undefined',
                                     '-I' + str(p), '-I' + str(MAIN), str(MAIN / 'identity.c'),
                                     str(p / 'check.c'), '-o', str(p / 'check')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            check_modes()
