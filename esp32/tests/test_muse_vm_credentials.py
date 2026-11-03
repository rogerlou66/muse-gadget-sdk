"""Production authenticated-VM snapshot path, including low-memory failures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MuseVmCredentials(unittest.TestCase):
    def test_connected_vm_identity_lock_and_owned_copy(self):
        source = (ROOT / 'main/noise_control.cpp').read_text()
        start = source.index('extern "C" bool noise_ctrl_copy_vm_credentials(')
        end = source.index('{', start) + 1
        depth = 1
        while depth:
            depth += (source[end] == '{') - (source[end] == '}')
            end += 1
        code = r'''
#include <cassert>
#include <cstdlib>
#include <cstring>
#include "host_compat.h"
#define pdTRUE 1
#define pdMS_TO_TICKS(n) (n)
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
static bool s_connected=true,fail_alloc,fail_lock,disconnect_at_lock;
static int lock_object,locks,unlocks;
static int *s_connect_mutex=&lock_object;
static char s_vm_id[128]="vm-one";
static char token_storage[]="synthetic-vm-credential";
static char *s_auth_token=token_storage;
static int xSemaphoreTake(int *lock,int wait) {
 assert(lock==s_connect_mutex&&wait==100);locks++;
 if(disconnect_at_lock)s_connected=false;
 return fail_lock?0:1;
}
static void xSemaphoreGive(int *lock) {assert(lock==s_connect_mutex);unlocks++;}
static void *heap_caps_malloc(size_t n,int caps) {assert(caps==3);return fail_alloc?nullptr:malloc(n);}
''' + source[start:end] + r'''
int main(void) {
 char id[64]="unchanged";char *token=nullptr;
 assert(noise_ctrl_copy_vm_credentials(nullptr,id,sizeof(id),&token));
 assert(!strcmp(id,"vm-one")&&!strcmp(token,token_storage)&&token!=token_storage);
 token_storage[0]='S';assert(token[0]=='s');free(token);token=nullptr;
 assert(!noise_ctrl_copy_vm_credentials("vm-two",id,sizeof(id),&token)&&!token);
 assert(!noise_ctrl_copy_vm_credentials("vm-one",id,2,&token)&&!token);
 assert(noise_ctrl_copy_vm_credentials("vm-one",id,sizeof(id),&token));free(token);
 s_connected=false;assert(!noise_ctrl_copy_vm_credentials(nullptr,id,sizeof(id),&token)&&!token);
 s_connected=true;disconnect_at_lock=true;
 assert(!noise_ctrl_copy_vm_credentials(nullptr,id,sizeof(id),&token)&&!token);
 disconnect_at_lock=false;s_connected=true;
 fail_lock=true;int prior=unlocks;
 assert(!noise_ctrl_copy_vm_credentials(nullptr,id,sizeof(id),&token)&&unlocks==prior);
 fail_lock=false;s_auth_token=nullptr;assert(!noise_ctrl_copy_vm_credentials(nullptr,id,sizeof(id),&token));
 s_auth_token=token_storage;
#if CONFIG_SPIRAM
 fail_alloc=true;assert(!noise_ctrl_copy_vm_credentials(nullptr,id,sizeof(id),&token)&&!token);fail_alloc=false;
#endif
 assert(!noise_ctrl_copy_vm_credentials(nullptr,nullptr,0,&token));
 assert(!noise_ctrl_copy_vm_credentials(nullptr,id,sizeof(id),nullptr));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'cache.cpp').write_text(code)
            for spiram in (0, 1):
                result = subprocess.run(['c++', '-std=c++17', '-fsanitize=address,undefined',
                    f'-DCONFIG_SPIRAM={spiram}', '-I' + str(ROOT / 'tests'), str(p / 'cache.cpp'),
                    '-o', str(p / 'cache')], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(p / 'cache')], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
