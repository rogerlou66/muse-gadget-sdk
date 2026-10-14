/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_music.h"
#include "muse_music_file.h"
#include "muse_audio.h"
#include "muse_board.h"
#include "muse_mem.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#ifndef MUSE_MUSIC_DIRECTORY
#define MUSE_MUSIC_DIRECTORY "/sdcard/music"
#endif
#ifdef ESP_PLATFORM
/* FAT has no symlinks; ESP-IDF does not export lstat. */
#define MUSIC_STAT stat
#else
#define MUSIC_STAT lstat
#endif
static const char *TAG = "muse_music";
typedef struct { muse_music_action_t action; unsigned track; } command_t;
typedef struct {
    char names[MUSE_MUSIC_SONGS][MUSE_MUSIC_NAME];
    int16_t pcm[MUSE_AUDIO_PLAY_CHUNK];
    muse_music_status_t status;
    bool interrupted, suspended, reset;
} music_t;
static music_t *s;
static QueueHandle_t s_commands;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static muse_music_file_t *s_file;  /* voice-task owned */

static void error(const char *message)
{
    portENTER_CRITICAL(&s_lock);
    snprintf(s->status.error, sizeof(s->status.error), "%s", message);
    s->status.revision++;
    portEXIT_CRITICAL(&s_lock);
    if (message[0]) ESP_LOGW(TAG, "%s", message);
}
void muse_music_status(muse_music_status_t *out)
{
    if (!out) return;
    memset(out, 0, sizeof(*out));
    portENTER_CRITICAL(&s_lock);
    if (s) *out = s->status;
    portEXIT_CRITICAL(&s_lock);
}
void muse_music_name(unsigned track, char *out, size_t cap)
{
    if (!out || !cap) return;
    out[0] = 0;
    portENTER_CRITICAL(&s_lock);
    if (s && track < s->status.count) snprintf(out, cap, "%s", s->names[track]);
    portEXIT_CRITICAL(&s_lock);
}
bool muse_music_init(void)
{
    if (s) return true;
    music_t *next = heap_caps_calloc(1, sizeof(*next), MUSE_BIG_CAPS);
    if (!next) return false;
    QueueHandle_t queue = xQueueCreateWithCaps(8, sizeof(command_t), MUSE_BIG_CAPS);
    if (!queue) { free(next); return false; }
    snprintf(next->status.error, sizeof(next->status.error), "Select Rescan card");
    portENTER_CRITICAL(&s_lock);
    s = next; s_commands = queue;
    portEXIT_CRITICAL(&s_lock);
    return true;
}
bool muse_music_request(muse_music_action_t action, unsigned track)
{
    if ((unsigned)action > MUSE_MUSIC_PREVIOUS || (action == MUSE_MUSIC_PLAY && track >= MUSE_MUSIC_SONGS)) return false;
    command_t cmd = {action, track};
    portENTER_CRITICAL(&s_lock);
    QueueHandle_t queue = s_commands;
    portEXIT_CRITICAL(&s_lock);
    return queue && xQueueSend(queue, &cmd, 0) == pdTRUE;
}
void muse_music_suspend(void)
{
    portENTER_CRITICAL(&s_lock);
    if (s) s->suspended = true;
    portEXIT_CRITICAL(&s_lock);
}
void muse_music_interrupt(void)
{
    portENTER_CRITICAL(&s_lock);
    if (s) s->interrupted = true;
    portEXIT_CRITICAL(&s_lock);
}
void muse_music_stop_owned(void)
{
    muse_music_file_close(s_file); s_file = NULL;
    if (!s) return;
    command_t unused;
    while (xQueueReceive(s_commands, &unused, 0) == pdTRUE) {}
    portENTER_CRITICAL(&s_lock);
    if (s->status.playing || s->status.paused) s->reset = true;
    s->status.playing = s->status.paused = false;
    s->interrupted = s->suspended = false;
    s->status.revision++;
    portEXIT_CRITICAL(&s_lock);
}
bool muse_music_take_reset(void)
{
    bool reset = false;
    portENTER_CRITICAL(&s_lock);
    if (s) { reset = s->reset; s->reset = false; }
    portEXIT_CRITICAL(&s_lock);
    return reset;
}
bool muse_music_busy(void)
{
    muse_music_status_t st; muse_music_status(&st);
    portENTER_CRITICAL(&s_lock);
    bool interrupted = s && (s->interrupted || s->suspended);
    QueueHandle_t queue = s_commands;
    portEXIT_CRITICAL(&s_lock);
    return st.playing || interrupted || (queue && uxQueueMessagesWaiting(queue));
}
static int compare_names(const void *a, const void *b) { return strcasecmp(a, b); }
static bool mp3_name(const char *name)
{
    size_t n = strlen(name);
    return n > 4 && n < MUSE_MUSIC_NAME && name[0] != '.' &&
           !strchr(name, '/') && !strchr(name, '\\') && !strcasecmp(name + n - 4, ".mp3");
}
static void scan(void)
{
    muse_music_stop_owned();
    portENTER_CRITICAL(&s_lock);
    s->status.count = 0; s->status.name[0] = 0;
    portEXIT_CRITICAL(&s_lock);
    {
        /* No fallback pin map: only the exact board may configure its card bus. */
        if (!muse_board->storage_mount) { error("SD WIRING NOT CONFIGURED"); return; }
        esp_err_t rc = muse_board->storage_mount("/sdcard");
        if (rc != ESP_OK) {
            portENTER_CRITICAL(&s_lock); s->status.mounted = false; portEXIT_CRITICAL(&s_lock);
            error("SD MOUNT FAILED"); return;
        }
        portENTER_CRITICAL(&s_lock); s->status.mounted = true; portEXIT_CRITICAL(&s_lock);
    }
    DIR *directory = opendir(MUSE_MUSIC_DIRECTORY);
    if (!directory) { error("NO /music FOLDER OR CARD REMOVED"); return; }
    char (*names)[MUSE_MUSIC_NAME] = heap_caps_calloc(MUSE_MUSIC_SONGS, MUSE_MUSIC_NAME, MUSE_BIG_CAPS);
    if (!names) { closedir(directory); error("MUSIC LIST OUT OF MEMORY"); return; }
    unsigned count = 0;
    struct dirent *entry;
    while (count < MUSE_MUSIC_SONGS && (entry = readdir(directory))) {
        if (!mp3_name(entry->d_name)) continue;
        char path[sizeof(MUSE_MUSIC_DIRECTORY) + MUSE_MUSIC_NAME + 1];
        snprintf(path, sizeof(path), "%s/%.*s", MUSE_MUSIC_DIRECTORY, MUSE_MUSIC_NAME - 1, entry->d_name);
        struct stat st;
        if (MUSIC_STAT(path, &st) || !S_ISREG(st.st_mode)) continue;
        memcpy(names[count++], entry->d_name, strlen(entry->d_name) + 1);
    }
    closedir(directory);
    qsort(names, count, MUSE_MUSIC_NAME, compare_names);
    portENTER_CRITICAL(&s_lock);
    memcpy(s->names, names, count * MUSE_MUSIC_NAME);
    s->status.count = count; s->status.track = 0; s->status.revision++;
    portEXIT_CRITICAL(&s_lock);
    free(names);
    error(count ? "" : "NO MP3 IN /music");
    ESP_LOGI(TAG, "catalog: %u MP3 files", count);
}
static void play(unsigned track)
{
    if (track >= s->status.count) { error("SELECT A SONG FIRST"); return; }
    muse_music_file_close(s_file); s_file = NULL;
    char path[sizeof(MUSE_MUSIC_DIRECTORY) + MUSE_MUSIC_NAME + 1];
    snprintf(path, sizeof(path), "%s/%s", MUSE_MUSIC_DIRECTORY, s->names[track]);
    s_file = muse_music_file_open(path, MUSE_AUDIO_PLAY_RATE);
    portENTER_CRITICAL(&s_lock);
    s->status.track = track;
    s->status.playing = s_file != NULL; s->status.paused = false;
    memcpy(s->status.name, s->names[track], sizeof(s->status.name));
    s->reset = true; s->status.revision++;
    portEXIT_CRITICAL(&s_lock);
    error(s_file ? "" : "CANNOT OPEN MP3");
    if (s_file) ESP_LOGI(TAG, "play track %u", track + 1);
}
size_t muse_music_poll(const int16_t **pcm)
{
    *pcm = NULL;
    if (!s) return 0;
    portENTER_CRITICAL(&s_lock);
    bool interrupt = s->interrupted;
    portEXIT_CRITICAL(&s_lock);
    if (interrupt) { muse_music_stop_owned(); return 0; }
    portENTER_CRITICAL(&s_lock);
    bool suspended = s->suspended; s->suspended = false;
    if (suspended && s->status.playing) {
        s->status.playing = false; s->status.paused = true;
        s->reset = true; s->status.revision++;
    }
    portEXIT_CRITICAL(&s_lock);
    if (suspended) {
        command_t unused;
        while (xQueueReceive(s_commands, &unused, 0) == pdTRUE) {}
        return 0;
    }
    command_t command;
    if (xQueueReceive(s_commands, &command, 0) == pdTRUE) {
        switch (command.action) {
        case MUSE_MUSIC_SCAN: scan(); break;
        case MUSE_MUSIC_PLAY: play(command.track); break;
        case MUSE_MUSIC_STOP: muse_music_stop_owned(); break;
        case MUSE_MUSIC_TOGGLE:
            if (s_file) {
                portENTER_CRITICAL(&s_lock);
                s->status.paused = !s->status.paused;
                s->status.playing = !s->status.paused; s->reset = true; s->status.revision++;
                portEXIT_CRITICAL(&s_lock);
            } else if (s->status.count) play(s->status.track);
            break;
        case MUSE_MUSIC_NEXT:
        case MUSE_MUSIC_PREVIOUS:
            if (s->status.count) play((s->status.track +
                (command.action == MUSE_MUSIC_NEXT ? 1 : s->status.count - 1)) % s->status.count);
            break;
        }
    }
    if (!s_file || s->status.paused) return 0;
    int frames = muse_music_file_read(s_file, s->pcm, MUSE_AUDIO_PLAY_CHUNK);
    if (frames <= 0) {
        char why[48];
        snprintf(why, sizeof(why), "%s", frames < 0 ? muse_music_file_error(s_file) : "");
        muse_music_stop_owned(); error(why);
        ESP_LOGI(TAG, "playback ended");
        return 0;
    }
    *pcm = s->pcm;
    return (size_t)frames;
}
