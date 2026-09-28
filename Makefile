# RIFT RUNNER — SDCC/devkitSMS + banked smsvideo build
DEVKIT   ?= $(HOME)/.devkitsms
SDCC     ?= sdcc
PYTHON   ?= python3
IHX2SMS  ?= $(DEVKIT)/bin/ihx2sms
SMSTEST  ?= $(DEVKIT)/bin/smstest
PROG     := RiftRunner
VIDEO_FRAMES ?= 648

CFLAGS   := -mz80 -I$(DEVKIT)/include -Isrc -Igen \
            --peep-file $(DEVKIT)/include/peep-rules.txt \
            --max-allocs-per-node 20000
LDFLAGS  := -mz80 --no-std-crt0 --data-loc 0xC000
OBJS     := main.rel smsvideo.rel video_data.rel art_data.rel font_data.rel

ASSET_STAMP := gen/assets.stamp
VIDEO_ARGS  := --frames assets/frames.bin --w 256 --h 80 --nframes $(VIDEO_FRAMES) \
               --tile-x 0 --tile-y 7 --cell-tile0 128 --blank-tile 127 \
               --data-bank0 2 --snapivl 8 --thresh 0 --maxupd 18 \
               --palette assets/palette.bin --out gen

.PHONY: all video verify playtest clean rebuild
all: $(PROG).sms

$(ASSET_STAMP): tools/render_assets.py
	$(PYTHON) tools/render_assets.py
	@touch $@

assets/frames.bin assets/palette.bin gen/art_data.c gen/art_data.h gen/font_data.c: $(ASSET_STAMP)

# Encoder output includes the C metadata and the bank-spliced tile stream.
gen/video_data.h: assets/frames.bin assets/palette.bin tools/smsvideo.py
	$(PYTHON) tools/smsvideo.py encode $(VIDEO_ARGS)

gen/video_data.c gen/bank_data.bin: gen/video_data.h
	@test -f $@

video: gen/video_data.h
verify: gen/video_data.h
	$(PYTHON) tools/smsvideo.py verify --frames assets/frames.bin --w 256 --h 80 --nframes $(VIDEO_FRAMES) --out gen

%.rel: src/%.c gen/video_data.h gen/art_data.h
	$(SDCC) $(CFLAGS) -c $< -o $@

video_data.rel: gen/video_data.c gen/video_data.h
	$(SDCC) $(CFLAGS) -c gen/video_data.c -o $@

art_data.rel: gen/art_data.c gen/art_data.h
	$(SDCC) $(CFLAGS) -c gen/art_data.c -o $@

font_data.rel: gen/font_data.c gen/art_data.h
	$(SDCC) $(CFLAGS) -c gen/font_data.c -o $@

$(PROG)_code.ihx: $(OBJS) tools/checked_link.py
	$(PYTHON) tools/checked_link.py $@ $(SDCC) -o $@ $(LDFLAGS) $(DEVKIT)/lib/crt0_sms.rel $(OBJS) \
	      $(DEVKIT)/lib/SMSlib.lib $(DEVKIT)/lib/PSGlib.lib
	@cp $(PROG)_code.noi $(PROG).noi

$(PROG)_code.sms: $(PROG)_code.ihx
	$(IHX2SMS) $< $@

$(PROG).sms: $(PROG)_code.sms gen/bank_data.bin tools/mkrom.py
	$(PYTHON) tools/mkrom.py $(PROG)_code.sms gen/bank_data.bin $@

playtest: $(PROG).sms
	@mkdir -p shots
	$(SMSTEST) $(PROG).sms tests/playtest.txt
	$(PYTHON) tools/check_audio_log.py shots/audio-test.log

clean:
	rm -f *.rel *.ihx *.noi *.map *.sym *.lst *.asm *.rst *.lk *.sms

rebuild: clean all
