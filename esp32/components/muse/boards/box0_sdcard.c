/* SPDX-License-Identifier: Apache-2.0 */
#include "box0_sdcard.h"
#include "driver/spi_master.h"
#include "driver/sdspi_host.h"
#include "esp_vfs_fat.h"
#include "esp_log.h"
#include "sdmmc_cmd.h"
#include <string.h>

/* Exact BOX0 vendor app dnesp32s3b0_v2.1.bin, SHA256
 * 1ea586523f0c941c78d609a271dcb628060b81f057b36b414316313e8f7169a6.
 * InitializeSPISD at 0x42037cc8: MOSI=17, MISO=18, SCLK=16;
 * both spi_device_interface_config_t and sdspi_device_config_t set CS=15.
 * See docs/box0-tf-wiring.md for reproducible evidence. */
#define SD_HOST SPI3_HOST
#define SD_CS GPIO_NUM_15
#define SD_SCLK GPIO_NUM_16
#define SD_MOSI GPIO_NUM_17
#define SD_MISO GPIO_NUM_18

static const char *TAG = "box0_sd";
static sdmmc_card_t *s_card;
static bool s_bus;

esp_err_t muse_box0_storage_mount(const char *path)
{
    if (!path || strcmp(path, "/sdcard")) return ESP_ERR_INVALID_ARG;
    if (s_card) {
        if (sdmmc_get_status(s_card) == ESP_OK) return ESP_OK;
        /* Rescan after removal: close the old VFS/card before mounting again. */
        esp_err_t err = esp_vfs_fat_sdcard_unmount(path, s_card);
        s_card = NULL;
        if (err != ESP_OK) return err;
    }
    if (!s_bus) {
        const spi_bus_config_t bus = {
            .mosi_io_num = SD_MOSI, .miso_io_num = SD_MISO,
            .sclk_io_num = SD_SCLK,
            .quadwp_io_num = GPIO_NUM_NC, .quadhd_io_num = GPIO_NUM_NC,
            .data4_io_num = GPIO_NUM_NC, .data5_io_num = GPIO_NUM_NC,
            .data6_io_num = GPIO_NUM_NC, .data7_io_num = GPIO_NUM_NC,
            .max_transfer_sz = 4096,
        };
        esp_err_t err = spi_bus_initialize(SD_HOST, &bus, SPI_DMA_CH_AUTO);
        if (err != ESP_OK) return err;
        s_bus = true;
    }
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SD_HOST;
    host.max_freq_khz = 20000;
    host.command_timeout_ms = 1000;
    sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot.host_id = SD_HOST;
    slot.gpio_cs = SD_CS;
    const esp_vfs_fat_mount_config_t mount = {
        .format_if_mount_failed = false,
        .max_files = 4,
        .allocation_unit_size = 0,
        .disk_status_check_enable = true,
    };
    esp_err_t err = esp_vfs_fat_sdspi_mount(path, &host, &slot, &mount, &s_card);
    if (err != ESP_OK) {
        s_card = NULL;
        if (spi_bus_free(SD_HOST) == ESP_OK) s_bus = false;
        ESP_LOGW(TAG, "card mount failed: %s (card contents preserved)", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "TF mounted on SPI3: CS=15 CLK=16 MOSI=17 MISO=18, %llu MiB",
             (unsigned long long)s_card->csd.capacity * s_card->csd.sector_size / (1024 * 1024));
    return ESP_OK;
}
