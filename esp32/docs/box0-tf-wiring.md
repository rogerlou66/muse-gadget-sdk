# BOX0 onboard TF wiring evidence

The downloaded Alientek BOX0 A-disk package contains user manuals, Xiaozhi/
Xiaoling source ZIPs, and ATK factory binaries. No schematic or TF driver source
was found. Three BOX0 source ports contain no SD initialization. Vendor
ATK documentation describes SD firmware upgrades and MP3/WAV from MUSIC.

Exact-board wiring was recovered from `dnesp32s3b0_v2.1.bin` (5,678,416 bytes):

- SHA256 `1ea586523f0c941c78d609a271dcb628060b81f057b36b414316313e8f7169a6`.
- Embedded project xiaozhi, version1.6.6, ESP-IDF v5.4.1-dirty.
- IROM at file offset0x370020 mapped to0x42000020.
- DROM error strings identify `atk_dnesp32s3_box_v0::InitializeSPISD()`
  and `./main/boards/atk-dnesp32s3-box-v0/atk_dnesp32s3_box_v0.cc`.

| Signal | GPIO | Evidence in InitializeSPISD at0x42037cc8 |
|---|---:|---|
| MOSI/CMD |17|0x42037ce6 loads17; stores at stack+60, bus offset0|
| MISO/DAT0 |18|0x42037cea loads18; stores at stack+64, bus offset4|
| SCLK |16|0x42037ce1 loads16; stores at stack+68, bus offset8|
| CS/DAT3 |15|0x42037d33 loads15, SPI device offset28; 0x42037da7 loads15, SD slot offset4|
| Bus |SPI3|host argument2 at0x42037d01/0x42037d44/0x42037d90|

Bus config is zeroed at stack+60; WP/HD are-1. Call0x42037d06
initializes that bus; its error names spi_bus_initialize(SPI3_HOST,
&buscfg,SPI_DMA_CH_AUTO), vendor source line220. Call0x42037d49
adds a SPI device; its error names SPI3_HOST and MY_SD_Handle, line230.
The later mount call uses another sdspi_device_config_t with host2,CS15,
CD/WP/INT-1. Two independent CS assignments corroborate GPIO15.
This is executable vendor configuration, not a schematic or continuity test.
The production v3.0 full-flash binary contains an identical IROM segment at
app offset0x100000, corroborating the same initializer (app metadata differs).

Layouts were checked against the matching official ESP-IDF v5.4.1 headers:
[spi_bus_config_t](https://github.com/espressif/esp-idf/blob/v5.4.1/components/esp_driver_spi/include/driver/spi_common.h),
[spi_device_interface_config_t](https://github.com/espressif/esp-idf/blob/v5.4.1/components/esp_driver_spi/include/driver/spi_master.h),
[sdspi_device_config_t](https://github.com/espressif/esp-idf/blob/v5.4.1/components/esp_driver_sdspi/include/driver/sdspi_host.h).

Reproduce by parsing ESP image segments, extracting IROM, and using Xtensa
ESP32-S3 objdump with binary input,VMA0x42000020,start0x42037cc8,stop0x42037dd0.
No vendor app flashing is needed. Generic DNESP32S3 GPIO2 SD-CS examples
are incompatible: BOX0 uses GPIO2 as its power latch.

Muse uses SPI3 SD up to20MHz; LCD stays on SPI2. The voice task mounts/scans/
reads after closing music. Mount failures never format. Rescan checks card
status and releases stale mounts for reinsertion. Use FAT32/MBR,UTF-8 long
filenames,root MUSIC directory,up to64files with names below128UTF-8 bytes.
Files open read-only. exFAT is disabled in ESP-IDF FatFs.
