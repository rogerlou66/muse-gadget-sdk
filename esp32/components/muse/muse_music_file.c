/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_music_file.h"
#define mp3dec_init muse_music_decoder_init
#define mp3dec_decode_frame muse_music_decoder_decode
#define MINIMP3_IMPLEMENTATION
#include "minimp3.h"
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef ESP_PLATFORM
#include "muse_mem.h"
#define MUSIC_ALLOC(n) heap_caps_calloc(1, (n), MUSE_BIG_CAPS)
#else
#define MUSIC_ALLOC(n) calloc(1, (n))
#endif
#define INPUT_BYTES 16384
#define MONO_FRAMES (MINIMP3_MAX_SAMPLES_PER_FRAME / 2)
#define OUTPUT_FRAMES (MONO_FRAMES * 6 + 8)

struct muse_music_file {
    FILE *fp;
    mp3dec_t decoder;
    uint8_t input[INPUT_BYTES];
    int16_t decoded[MINIMP3_MAX_SAMPLES_PER_FRAME];
    int16_t converted[OUTPUT_FRAMES];
    size_t input_len, ready, played;
    uint32_t step, pos;
    int16_t previous;
    int rate, input_rate;
    bool eof, heard;
    const char *error;
};

static bool skip_id3(FILE *fp)
{
    uint8_t h[10];
    size_t n = fread(h, 1, sizeof(h), fp);
    if (n == sizeof(h) && !memcmp(h, "ID3", 3)) {
        if ((h[6] | h[7] | h[8] | h[9]) & 0x80) return false;
        uint32_t bytes = ((uint32_t)h[6] << 21) | ((uint32_t)h[7] << 14) |
                         ((uint32_t)h[8] << 7) | h[9];
        if (h[3] == 4 && (h[5] & 0x10)) bytes += 10;
        if (bytes > 8 * 1024 * 1024) return false;
        /* Verify bounds without reading a multi-megabyte cover into RAM. */
        if (fseek(fp, 0, SEEK_END)) return false;
        long size = ftell(fp);
        return size >= 10 && bytes <= (unsigned long)(size - 10) &&
               !fseek(fp, (long)bytes + 10, SEEK_SET);
    }
    clearerr(fp);
    return !fseek(fp, 0, SEEK_SET);
}

muse_music_file_t *muse_music_file_open(const char *path, int rate)
{
    if (!path || rate < 8000 || rate > 48000) return NULL;
    FILE *fp = fopen(path, "rb");
    if (!fp) return NULL;
    if (!skip_id3(fp)) { fclose(fp); return NULL; }
    muse_music_file_t *f = MUSIC_ALLOC(sizeof(*f));
    if (!f) { fclose(fp); return NULL; }
    f->fp = fp;
    f->rate = rate;
    mp3dec_init(&f->decoder);
    return f;
}

static bool convert(muse_music_file_t *f, int samples, const mp3dec_frame_info_t *info)
{
    if (info->hz < 8000 || info->hz > 48000 || (info->channels != 1 && info->channels != 2)) {
        f->error = "UNSUPPORTED MP3"; return false;
    }
    if (info->channels == 2) {
        for (int i = 0; i < samples; ++i)
            f->decoded[i] = (int16_t)(((int32_t)f->decoded[2*i] + f->decoded[2*i+1]) / 2);
    }
    if (f->input_rate != info->hz) {
        f->input_rate = info->hz;
        f->step = (uint32_t)(((uint64_t)info->hz << 16) / f->rate);
        f->pos = 0; f->previous = 0;
    }
    f->ready = f->played = 0;
    while ((f->pos >> 16) < (size_t)samples) {
        size_t i = f->pos >> 16;
        int32_t a = i ? f->decoded[i-1] : f->previous, b = f->decoded[i];
        if (f->ready == OUTPUT_FRAMES) { f->error = "MP3 FRAME TOO LARGE"; return false; }
        f->converted[f->ready++] = (int16_t)(a + (((int64_t)(b-a) * (f->pos & 65535)) >> 16));
        f->pos += f->step;
    }
    f->pos -= (uint32_t)samples << 16;
    f->previous = f->decoded[samples-1];
    f->heard = true;
    return true;
}

static bool next_frame(muse_music_file_t *f)
{
    size_t skipped = 0;
    for (;;) {
        if (!f->eof && f->input_len < INPUT_BYTES) {
            size_t got = fread(f->input + f->input_len, 1, INPUT_BYTES - f->input_len, f->fp);
            f->input_len += got;
            if (ferror(f->fp)) { f->error = "SD READ FAILED"; return false; }
            f->eof = feof(f->fp);
        }
        if (!f->input_len) {
            if (!f->heard) f->error = "NO MP3 AUDIO";
            return false;
        }
        mp3dec_frame_info_t info;
        int samples = mp3dec_decode_frame(&f->decoder, f->input, (int)f->input_len, f->decoded, &info);
        size_t used = info.frame_bytes > 0 ? (size_t)info.frame_bytes : 0;
        if (used > f->input_len) { f->error = "INVALID MP3"; return false; }
        if (!used) {
            if (f->eof) {
                f->input_len = 0;
                if (!f->heard) f->error = "NO MP3 AUDIO";
                return false;
            }
            /* Keep enough bytes to find the next frame across an input boundary. */
            used = f->input_len > 4096 ? f->input_len - 4096 : 1;
        }
        memmove(f->input, f->input + used, f->input_len - used);
        f->input_len -= used;
        if (samples > 0) return convert(f, samples, &info);
        skipped += used;
        if (skipped > 64 * 1024) { f->error = "INVALID MP3"; return false; }
    }
}

int muse_music_file_read(muse_music_file_t *f, int16_t *out, size_t frames)
{
    if (!f || !out || !frames || f->error) return -1;
    size_t count = 0;
    while (count < frames) {
        if (f->played == f->ready && !next_frame(f)) break;
        size_t n = f->ready - f->played;
        if (n > frames - count) n = frames - count;
        memcpy(out + count, f->converted + f->played, n * sizeof(*out));
        f->played += n; count += n;
    }
    return count ? (int)count : f->error ? -1 : 0;
}
const char *muse_music_file_error(const muse_music_file_t *f) { return f && f->error ? f->error : ""; }
void muse_music_file_close(muse_music_file_t *f) { if (f) { fclose(f->fp); free(f); } }
