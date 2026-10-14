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

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/*
 * The board's mic and speaker codecs share one duplex I2S bus, so both run at
 * the same hardware format: 16-bit, 2 slots. Most boards use 16 kHz; the ATK
 * uses 48 kHz with filtered 16 kHz capture and native 48 kHz playback.
 * This module exposes mono buffers. Capture removes DC/rumble; ATK also
 * applies mild noise suppression and bounded software gain.
 */

#define MUSE_AUDIO_RATE 16000
#define MUSE_AUDIO_CHUNK 320   /* 20 ms of mono frames */

#if CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0
#define MUSE_AUDIO_HW_RATE 48000
#define MUSE_AUDIO_PLAY_RATE 48000
#else
#define MUSE_AUDIO_HW_RATE MUSE_AUDIO_RATE
#define MUSE_AUDIO_PLAY_RATE MUSE_AUDIO_RATE
#endif
#define MUSE_AUDIO_HW_CHUNK (MUSE_AUDIO_HW_RATE / 50)
#define MUSE_AUDIO_PLAY_CHUNK (MUSE_AUDIO_PLAY_RATE / 50)

esp_err_t muse_audio_init(int volume, int mic_gain_db);

/* Off closes the codecs: ADC, DAC, speaker amp and I2S stop. On reopens them
 * with the saved volume and mic gain. Reads and writes need it on. */
void muse_audio_power(bool on);

void muse_audio_set_volume(int volume);          /* 0..100 */
void muse_audio_set_mic_gain(int db);            /* 0..MUSE_MIC_GAIN_MAX */

/* Measures the real capture/playback rates and per-mic levels; logs the result. */
void muse_audio_selftest(void);

/* Plays tones while recording, across mic gains; logs levels. For tuning. */
void muse_audio_loopback_test(int volume);
/* Log raw ADC headroom for a recording, independently of downstream limiting. */
void muse_audio_capture_begin(void);
void muse_audio_capture_report(void);

/* Blocking read of `frames` mono samples (mic pair mixed down). */
esp_err_t muse_audio_read(int16_t *mono, size_t frames);

/* Blocking write of `frames` mono samples to the speaker. */
esp_err_t muse_audio_write(const int16_t *mono, size_t frames);
/* Native reply/MP3 rate; ordinary UI tones still use the 16 kHz wrapper. */
esp_err_t muse_audio_write_rate(const int16_t *mono, size_t frames, int rate);

/* RMS of a buffer in dBFS (-100 for silence). */
float muse_audio_dbfs(const int16_t *mono, size_t frames);

/* RMS of a buffer mapped to a 0..1 perceptual level. */
float muse_audio_level(const int16_t *mono, size_t frames);

/* Short UI chirp: rising for "go", falling for "done". */
void muse_audio_chirp(int rising);
