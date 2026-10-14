#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    float history[63];
    unsigned at, phase;
} muse_audio_fir_t;

typedef struct {
    float gain;
    float hp_x, hp_y, fade;
    unsigned silent_frames;
} muse_audio_gain_t;

/* 63-tap anti-alias filter: 48 kHz capture to 16 kHz speech. */
size_t muse_audio_decimate48(muse_audio_fir_t *s, const int16_t *in, size_t n, int16_t *out);
/* Band-limited 16 kHz UI-tone conversion to the 48 kHz codec. */
size_t muse_audio_interpolate16(muse_audio_fir_t *s, const int16_t *in, size_t n, int16_t *out);
/* Apply after noise suppression; boosts speech by at most 9.5 dB. */
void muse_audio_input_gain(muse_audio_gain_t *s, int16_t *pcm, size_t n);
/* 70 Hz speaker protection, soft peak limiter and gentle mute/start ramp. */
void muse_audio_output_process(muse_audio_gain_t *s, int16_t *pcm, size_t n, bool mute);
