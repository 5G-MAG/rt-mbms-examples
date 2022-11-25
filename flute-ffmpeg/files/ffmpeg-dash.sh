#!/bin/bash


timestamp="$(date +%s)"

ffmpeg \
\
-re -fflags +genpts -stream_loop -1 -i content/01_llama_drama_1080p.mp4 \
\
-flags +global_header -r 30000/1000 \
\
-filter_complex "drawbox=x=1460:y=0:width=460:height=100:color=black@0.5:t=fill,\
split=2[s0][s1];\
[s0]drawtext=text='720p-2.0M':x=1480:y=20:\
fontfile=/usr/share/fonts/truetype/freefont/FreeSans.ttf:fontsize=80:fontcolor=white,\
scale=1280x720[s0];\
[s1]drawtext=text='540p-1.0M':x=1480:y=20:\
fontfile=/usr/share/fonts/truetype/freefont/FreeSans.ttf:fontsize=80:fontcolor=white,\
scale=960x540[s1]" \
\
-pix_fmt yuv420p \
-c:v libx264 \
\
-b:v:0 2000K -maxrate:v:0 2000K -bufsize:v:0 2000K/2 \
-b:v:1 750K -maxrate:v:1 750K -bufsize:v:1 750K/2 \
\
-g:v 60 -keyint_min:v 60 -sc_threshold:v 0 \
\
-color_primaries bt709 -color_trc bt709 -colorspace bt709 \
\
-c:a aac -ar 48000 -b:a 128k \
\
-map [s0] -map [s1] \
-map 0:a:0 \
\
-preset veryfast \
-tune zerolatency \
\
-use_template 1 \
-use_timeline 1 \
-window_size 50 \
-remove_at_exit 1 \
-adaptation_sets 'id=0,streams=v id=1,streams=a' \
-f dash /home/dsi/5G-MAG/simple-express-server/public/watchfolder/dash/manifest.mpd