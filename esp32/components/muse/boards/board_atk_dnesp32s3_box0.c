/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*
 * Alientek ATK-DNESP32S3-BOX0: 16 MB quad flash, 8 MB octal PSRAM,
 * 240x240 ST7789, ES8311 duplex audio, no touch.
 * Pins and panel setup: 78/xiaozhi-esp32 commit 0d576d3,
 * main/boards/alientek/atk-dnesp32s3-box0/config.h and atk_dnesp32s3_box0.cc.
 * Audio deliberately follows the reference's BCLK-derived codec clock:
 * MCLK and PA are not driven, despite legacy definitions in config.h.
 * Battery percentage/light sleep are deferred until calibrated on this board.
 */
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/ledc.h"
#include "driver/rtc_io.h"
#include "driver/spi_master.h"
#include "driver/usb_serial_jtag.h"
#include "esp_check.h"
#include "esp_codec_dev_defaults.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "esp_lv_adapter.h"
#include "esp_sleep.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "muse_audio.h"
#include "muse_board.h"
#include "muse_mem.h"
#include "box0_sdcard.h"

static const char *TAG = "board";

#define LCD_RES 240
#define LCD_HOST SPI2_HOST
#define LCD_SCLK GPIO_NUM_39
#define LCD_MOSI GPIO_NUM_40
#define LCD_CS GPIO_NUM_41
#define LCD_DC GPIO_NUM_38
#define LCD_RST GPIO_NUM_NC
#define LCD_BL GPIO_NUM_42
#define DRAW_BUF_LINES 32

#define I2C_SDA GPIO_NUM_11
#define I2C_SCL GPIO_NUM_12
#define I2S_MCLK GPIO_NUM_NC
#define I2S_WS GPIO_NUM_10
#define I2S_BCLK GPIO_NUM_5
#define I2S_DIN GPIO_NUM_6
#define I2S_DOUT GPIO_NUM_9
#define PA_EN GPIO_NUM_NC

#define TALK_GPIO GPIO_NUM_4       /* middle button; power/wake in vendor firmware */
#define AUX_GPIO GPIO_NUM_3        /* left button: two-button menu */
#define BACK_GPIO GPIO_NUM_0       /* right button: menu up; also BOOT during reset */
#define POWER_HOLD GPIO_NUM_2
#define CODEC_POWER GPIO_NUM_14
#define CHARGE_ENABLE GPIO_NUM_47
#define CHARGE_GPIO GPIO_NUM_48    /* active low */

static i2c_master_bus_handle_t s_i2c;
static esp_lcd_panel_handle_t s_panel;
static muse_gpio_button_t s_talk, s_aux, s_back;

static esp_err_t init(void)
{
    ESP_LOGI(TAG, "boot reset=%d wake=%d", esp_reset_reason(), esp_sleep_get_wakeup_cause());
    /* Shutdown retained the main supply and disabled the codec. Release their
     * holds; ext0 wake also leaves the button on the RTC mux. */
    rtc_gpio_hold_dis(POWER_HOLD);
    rtc_gpio_hold_dis(CODEC_POWER);
    rtc_gpio_deinit(POWER_HOLD);
    rtc_gpio_deinit(CODEC_POWER);
    rtc_gpio_deinit(TALK_GPIO);
    /* Vendor init keeps the system and codec rails high and enables charging. */
    const gpio_config_t power = {
        .pin_bit_mask = (1ULL << POWER_HOLD) | (1ULL << CODEC_POWER) | (1ULL << CHARGE_ENABLE),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&power), TAG, "power pins");
    gpio_set_level(POWER_HOLD, 1);
    gpio_set_level(CODEC_POWER, 1);
    gpio_set_level(CHARGE_ENABLE, 1);

    const i2c_master_bus_config_t i2c_cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = I2C_SDA,
        .scl_io_num = I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&i2c_cfg, &s_i2c), TAG, "i2c");
    ESP_RETURN_ON_ERROR(muse_gpio_button_init_high(&s_talk, TALK_GPIO), TAG, "talk button");
    /* The vendor's iot_button driver enables a pull-down for this active-high
     * key; muse_gpio_button_init_high assumes the pad is externally driven. */
    ESP_RETURN_ON_ERROR(gpio_set_pull_mode(TALK_GPIO, GPIO_PULLDOWN_ONLY), TAG, "talk pull-down");
    ESP_RETURN_ON_ERROR(muse_gpio_button_init(&s_aux, AUX_GPIO), TAG, "aux button");
    ESP_RETURN_ON_ERROR(muse_gpio_button_init(&s_back, BACK_GPIO), TAG, "back button");
    /* The power button is held at boot; don't count that as a press. */
    s_talk.pressed = gpio_get_level(TALK_GPIO) == 1;
    s_aux.pressed = gpio_get_level(AUX_GPIO) == 0;
    s_back.pressed = gpio_get_level(BACK_GPIO) == 0;
    ESP_LOGI(TAG, "buttons idle levels: left=%d middle=%d right=%d",
             gpio_get_level(AUX_GPIO), gpio_get_level(TALK_GPIO), gpio_get_level(BACK_GPIO));

    gpio_config_t chg = {
        .pin_bit_mask = 1ULL << CHARGE_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&chg), TAG, "charge pin");
    return ESP_OK;
}

static lv_display_t *display_start(lv_indev_t **touch)
{
    (void)touch;
    const ledc_timer_config_t bl_timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 20000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    const ledc_channel_config_t bl_ch = {
        .gpio_num = LCD_BL,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
    };
    if (ledc_timer_config(&bl_timer) != ESP_OK || ledc_channel_config(&bl_ch) != ESP_OK) {
        return NULL;
    }

    const spi_bus_config_t bus = {
        .sclk_io_num = LCD_SCLK,
        .mosi_io_num = LCD_MOSI,
        .miso_io_num = GPIO_NUM_NC,
        .quadwp_io_num = GPIO_NUM_NC,
        .quadhd_io_num = GPIO_NUM_NC,
        .max_transfer_sz = LCD_RES * DRAW_BUF_LINES * 2,
    };
    if (spi_bus_initialize(LCD_HOST, &bus, SPI_DMA_CH_AUTO) != ESP_OK) {
        return NULL;
    }
    esp_lcd_panel_io_handle_t io;
    const esp_lcd_panel_io_spi_config_t io_cfg = {
        .cs_gpio_num = LCD_CS,
        .dc_gpio_num = LCD_DC,
        .spi_mode = 0,
        .pclk_hz = 40 * 1000 * 1000,
        .trans_queue_depth = 10,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    if (esp_lcd_new_panel_io_spi(LCD_HOST, &io_cfg, &io) != ESP_OK) {
        return NULL;
    }
    const esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = LCD_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .data_endian = LCD_RGB_DATA_ENDIAN_BIG,
        .bits_per_pixel = 16,
    };
    if (esp_lcd_new_panel_st7789(io, &panel_cfg, &s_panel) != ESP_OK) {
        return NULL;
    }
    esp_lcd_panel_reset(s_panel);
    esp_lcd_panel_init(s_panel);
    esp_lcd_panel_invert_color(s_panel, true);
    esp_lcd_panel_swap_xy(s_panel, false);
    esp_lcd_panel_mirror(s_panel, false, false);
    esp_lcd_panel_disp_on_off(s_panel, true);

    esp_lv_adapter_config_t adapter_cfg = ESP_LV_ADAPTER_DEFAULT_CONFIG();
    adapter_cfg.task_core_id = MUSE_UI_CORE;
    adapter_cfg.task_priority = MUSE_UI_PRIORITY;
    if (esp_lv_adapter_init(&adapter_cfg) != ESP_OK) {
        return NULL;
    }
    const esp_lv_adapter_display_config_t disp_cfg = {
        .panel = s_panel,
        .panel_io = io,
        .profile = {
            .interface = ESP_LV_ADAPTER_PANEL_IF_OTHER,
            .rotation = ESP_LV_ADAPTER_ROTATE_0,
            .hor_res = LCD_RES,
            .ver_res = LCD_RES,
            .buffer_height = DRAW_BUF_LINES,
            .use_psram = false,
            .require_double_buffer = true,
        },
        .tear_avoid_mode = ESP_LV_ADAPTER_TEAR_AVOID_MODE_NONE,
    };
    lv_display_t *disp = esp_lv_adapter_register_display(&disp_cfg);
    if (!disp || esp_lv_adapter_start() != ESP_OK) {
        return NULL;
    }
    return disp;
}

static bool display_lock(int timeout_ms)
{
    return esp_lv_adapter_lock(timeout_ms) == ESP_OK;
}

static void set_brightness(int pct)
{
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, pct * 1023 / 100);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

static void panel_sleep(bool sleep)
{
    esp_lcd_panel_disp_sleep(s_panel, sleep);   /* SLPIN/SLPOUT; GRAM is kept */
}

static void display_pause(bool pause)
{
    if (pause) {
        esp_lv_adapter_pause(-1);
    } else {
        esp_lv_adapter_resume();
    }
}

/* One ES8311 does both directions over a duplex I2S bus, clocked from BCLK (use_mclk=false, as xiaozhi does). */
static esp_err_t audio_init(esp_codec_dev_handle_t *spk, esp_codec_dev_handle_t *mic)
{
    i2s_chan_handle_t tx, rx;
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &tx, &rx), TAG, "i2s channel");
    const i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(MUSE_AUDIO_HW_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_MCLK,
            .bclk = I2S_BCLK,
            .ws = I2S_WS,
            .dout = I2S_DOUT,
            .din = I2S_DIN,
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(tx, &std_cfg), TAG, "i2s tx");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(rx, &std_cfg), TAG, "i2s rx");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(tx), TAG, "i2s tx on");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(rx), TAG, "i2s rx on");

    audio_codec_i2s_cfg_t i2s_cfg = { .port = I2S_NUM_0, .rx_handle = rx, .tx_handle = tx };
    const audio_codec_data_if_t *data_if = audio_codec_new_i2s_data(&i2s_cfg);
    audio_codec_i2c_cfg_t i2c_cfg = { .port = I2C_NUM_0, .addr = ES8311_CODEC_DEFAULT_ADDR, .bus_handle = s_i2c };
    const audio_codec_ctrl_if_t *ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();
    ESP_RETURN_ON_FALSE(data_if && ctrl_if && gpio_if, ESP_ERR_NO_MEM, TAG, "codec interfaces");

    /* The reference firmware holds the codec's digital blocks in reset before
     * the generic ES8311 initialization releases them. This also handles a
     * warm restart, where the codec retains its previous register state. */
    uint8_t reset = 0x1f;
    ESP_RETURN_ON_FALSE(ctrl_if->write_reg(ctrl_if, 0x00, 1, &reset, 1) == ESP_CODEC_DEV_OK,
                        ESP_FAIL, TAG, "ES8311 reset");
    vTaskDelay(pdMS_TO_TICKS(5));
    ESP_LOGI(TAG, "ES8311 software reset complete");

    es8311_codec_cfg_t es_cfg = {
        .ctrl_if = ctrl_if,
        .gpio_if = gpio_if,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_BOTH,
        .pa_pin = PA_EN,
        .use_mclk = false,
        .hw_gain = { .pa_voltage = 5.0, .codec_dac_voltage = 3.3 },
    };
    const audio_codec_if_t *codec = es8311_codec_new(&es_cfg);
    ESP_RETURN_ON_FALSE(codec, ESP_FAIL, TAG, "ES8311 not responding");

    esp_codec_dev_cfg_t out_cfg = { .dev_type = ESP_CODEC_DEV_TYPE_OUT, .codec_if = codec, .data_if = data_if };
    esp_codec_dev_cfg_t in_cfg = { .dev_type = ESP_CODEC_DEV_TYPE_IN, .codec_if = codec, .data_if = data_if };
    *spk = esp_codec_dev_new(&out_cfg);
    *mic = esp_codec_dev_new(&in_cfg);
    return *spk && *mic ? ESP_OK : ESP_FAIL;
}

static unsigned poll_buttons(void)
{
    unsigned talk = muse_gpio_button_poll(&s_talk);
    unsigned aux = muse_gpio_button_poll(&s_aux);
    unsigned back = muse_gpio_button_poll(&s_back);
    if (talk) ESP_LOGI(TAG, "button middle: %s", talk & MUSE_BTN_TALK_PRESS ? "press" : "release");
    if (aux) ESP_LOGI(TAG, "button left: %s", aux & MUSE_BTN_TALK_PRESS ? "press" : "release");
    if (back) ESP_LOGI(TAG, "button right: %s", back & MUSE_BTN_TALK_PRESS ? "press" : "release");
    return talk | aux << 2 | (back & MUSE_BTN_TALK_PRESS ? MUSE_BTN_UP : 0);
}

static void wait_buttons(int timeout_ms)
{
    muse_gpio_buttons_wait((muse_gpio_button_t *const[]){ &s_talk, &s_aux, &s_back }, 3, timeout_ms);
}

/* Do not estimate a percentage using another board's battery calibration. */
static esp_err_t read_power(muse_power_t *out)
{
    *out = (muse_power_t) {
        .battery_pct = -1,
        .battery_mv = 0,
        .charging = gpio_get_level(CHARGE_GPIO) == 0,
        .usb = usb_serial_jtag_is_connected(),
    };
    return ESP_OK;
}

static esp_err_t power_off(void)
{
    set_brightness(0);
    esp_lcd_panel_disp_on_off(s_panel, false);
    /* Digital pulls do not supply the RTC ext0 wake circuit in deep sleep.
     * GPIO4 is active high: keep it low until a deliberate button press. */
    ESP_RETURN_ON_ERROR(rtc_gpio_init(TALK_GPIO), TAG, "wake RTC input");
    ESP_RETURN_ON_ERROR(rtc_gpio_set_direction(TALK_GPIO, RTC_GPIO_MODE_INPUT_ONLY), TAG, "wake direction");
    ESP_RETURN_ON_ERROR(rtc_gpio_pullup_dis(TALK_GPIO), TAG, "wake pull-up off");
    ESP_RETURN_ON_ERROR(rtc_gpio_pulldown_en(TALK_GPIO), TAG, "wake pull-down");
    int released = 0;
    while (released < 5) {
        released = rtc_gpio_get_level(TALK_GPIO) == 0 ? released + 1 : 0;
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    ESP_RETURN_ON_ERROR(esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL), TAG, "clear wake sources");
    ESP_RETURN_ON_ERROR(esp_sleep_enable_ext0_wakeup(TALK_GPIO, 1), TAG, "wake button");
    /* Software off uses deep sleep. Cutting the SYS rail on this board led to
     * a fresh power-on reset shortly afterward, so keep the main supply held
     * on while disabling the codec and display. Wake only through GPIO4. */
    gpio_set_level(CHARGE_ENABLE, 0);
    ESP_RETURN_ON_ERROR(rtc_gpio_init(CODEC_POWER), TAG, "codec RTC output");
    ESP_RETURN_ON_ERROR(rtc_gpio_set_direction(CODEC_POWER, RTC_GPIO_MODE_OUTPUT_ONLY), TAG, "codec direction");
    ESP_RETURN_ON_ERROR(rtc_gpio_set_level(CODEC_POWER, 0), TAG, "codec off");
    ESP_RETURN_ON_ERROR(rtc_gpio_hold_en(CODEC_POWER), TAG, "codec hold");
    ESP_LOGI(TAG, "power off: deep sleep, RTC wake on middle button, main supply retained");
    ESP_RETURN_ON_ERROR(rtc_gpio_init(POWER_HOLD), TAG, "power RTC output");
    ESP_RETURN_ON_ERROR(rtc_gpio_set_direction(POWER_HOLD, RTC_GPIO_MODE_OUTPUT_ONLY), TAG, "power direction");
    ESP_RETURN_ON_ERROR(rtc_gpio_set_level(POWER_HOLD, 1), TAG, "retain supply");
    ESP_RETURN_ON_ERROR(rtc_gpio_hold_en(POWER_HOLD), TAG, "power hold");
    esp_deep_sleep_start();
    return ESP_FAIL;
}

static const muse_board_t s_board = {
    .name = "Alientek ATK-DNESP32S3-BOX0",
    .width = LCD_RES,
    .height = LCD_RES,
    .round = false,
    .touch = false,
    .talk_gestures = true,
    .talk_button = "middle",
    .aux_button = "left",
    /* Left moves down, right moves up; middle selects in the menu. */
    .talk_hint = { LV_ALIGN_BOTTOM_MID, 0, -4 },
    .aux_hint = { LV_ALIGN_BOTTOM_LEFT, 15, -4 },
    .frame_ms = 40,
    .init = init,
    .display_start = display_start,
    .display_lock = display_lock,
    .display_unlock = esp_lv_adapter_unlock,
    .set_brightness = set_brightness,
    .panel_sleep = panel_sleep,
    .display_pause = display_pause,
    .audio_init = audio_init,
    .mic_slot = 0,              /* one mic, on the left slot */
    .poll_buttons = poll_buttons,
    .wait_buttons = wait_buttons,
    .read_power = read_power,
    .power_off = power_off,
    .storage_mount = muse_box0_storage_mount,
};

/* Home Link's app_main starts Muse with this board (main/main.c). */
const muse_board_t *muse_board_get(void)
{
    return &s_board;
}
