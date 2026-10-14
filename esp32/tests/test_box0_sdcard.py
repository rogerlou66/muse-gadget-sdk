"""Run the production mount/recovery path with failing card/driver boundaries."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Box0Card(unittest.TestCase):
    def test_mount_missing_card_cleanup_and_reinsert(self):
        source = (ROOT / 'components/muse/boards/box0_sdcard.c').read_text()
        body = '\n'.join(line for line in source.splitlines() if not line.startswith('#include'))
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 1
#define SPI3_HOST 2
#define SPI_DMA_CH_AUTO 3
#define GPIO_NUM_NC -1
#define GPIO_NUM_15 15
#define GPIO_NUM_16 16
#define GPIO_NUM_17 17
#define GPIO_NUM_18 18
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
typedef int esp_err_t;
typedef struct {int mosi_io_num,miso_io_num,sclk_io_num,quadwp_io_num,quadhd_io_num,
 data4_io_num,data5_io_num,data6_io_num,data7_io_num,max_transfer_sz;} spi_bus_config_t;
typedef struct {int slot,max_freq_khz,command_timeout_ms;} sdmmc_host_t;
typedef struct {int host_id,gpio_cs;} sdspi_device_config_t;
typedef struct {bool format_if_mount_failed;int max_files,allocation_unit_size;bool disk_status_check_enable;} esp_vfs_fat_mount_config_t;
typedef struct {struct {uint64_t capacity;unsigned sector_size;} csd;} sdmmc_card_t;
#define SDSPI_HOST_DEFAULT() {0}
#define SDSPI_DEVICE_CONFIG_DEFAULT() {0}
static int init_calls,free_calls,mount_calls,unmount_calls,init_err,mount_err,status_err,unmount_err;
static sdmmc_card_t card;
static int spi_bus_initialize(int host,const spi_bus_config_t *b,int dma) {
 assert(host==SPI3_HOST&&dma==SPI_DMA_CH_AUTO);
 assert(b->mosi_io_num==17&&b->miso_io_num==18&&b->sclk_io_num==16);
 assert(b->quadwp_io_num==-1&&b->quadhd_io_num==-1&&b->data4_io_num==-1&&b->data7_io_num==-1);
 assert(b->max_transfer_sz>=4096);init_calls++;return init_err;
}
static int spi_bus_free(int host) {assert(host==SPI3_HOST);free_calls++;return 0;}
static int sdmmc_get_status(sdmmc_card_t *c) {assert(c==&card);return status_err;}
static int esp_vfs_fat_sdcard_unmount(const char *p,sdmmc_card_t *c) {
 assert(!strcmp(p,"/sdcard")&&c==&card);unmount_calls++;return unmount_err;
}
static int esp_vfs_fat_sdspi_mount(const char *p,const sdmmc_host_t *h,
 const sdspi_device_config_t *s,const esp_vfs_fat_mount_config_t *m,sdmmc_card_t **c) {
 assert(!strcmp(p,"/sdcard")&&h->slot==SPI3_HOST&&s->host_id==SPI3_HOST&&s->gpio_cs==15);
 assert(h->max_freq_khz==20000&&h->command_timeout_ms==1000);
 assert(!m->format_if_mount_failed&&m->disk_status_check_enable&&m->max_files>=2);
 mount_calls++;if(!mount_err)*c=&card;return mount_err;
}
static const char *esp_err_to_name(int e) {(void)e;return "test";}
'''
        suffix = r'''
int main(void) {
 assert(muse_box0_storage_mount(NULL)==ESP_ERR_INVALID_ARG);
 assert(muse_box0_storage_mount("/other")==ESP_ERR_INVALID_ARG&&!init_calls);
 init_err=7;assert(muse_box0_storage_mount("/sdcard")==7&&!mount_calls);
 init_err=0;mount_err=8;assert(muse_box0_storage_mount("/sdcard")==8&&free_calls==1&&!s_bus&&!s_card);
 mount_err=0;assert(!muse_box0_storage_mount("/sdcard")&&s_card&&s_bus);
 int prior_init=init_calls,prior_mount=mount_calls;
 assert(!muse_box0_storage_mount("/sdcard")&&init_calls==prior_init&&mount_calls==prior_mount);
 status_err=9;mount_err=10;
 assert(muse_box0_storage_mount("/sdcard")==10&&unmount_calls==1&&!s_card&&!s_bus);
 status_err=0;mount_err=0;assert(!muse_box0_storage_mount("/sdcard"));
 status_err=9;unmount_err=11;assert(muse_box0_storage_mount("/sdcard")==11&&!s_card);
 unmount_err=0;status_err=0;assert(!muse_box0_storage_mount("/sdcard"));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'card.c').write_text(prefix + body + suffix)
            result = subprocess.run(['cc', '-std=c11', '-fsanitize=address,undefined',
                                     str(p / 'card.c'), '-o', str(p / 'card')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(p / 'card')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
