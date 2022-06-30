#!/bin/bash

ffmpeg -re -stream_loop -1 -i content/01_llama_drama_1080p.mp4 \
-filter_complex "drawbox=x=1460:y=0:width=460:height=100:color=black@0.5:t=fill,\
split=2[v1][v2];\
[v1]drawtext=text='720p-2.0M':x=1480:y=20:\
fontfile=/usr/share/fonts/truetype/freefont/FreeSans.ttf:fontsize=80:fontcolor=white,\
scale=1280x720[v1out];\
[v2]drawtext=text='540p-1.0M':x=1480:y=20:\
fontfile=/usr/share/fonts/truetype/freefont/FreeSans.ttf:fontsize=80:fontcolor=white,\
scale=960x540[v2out]" \
-map [v1out] -c:v:0 libx264 -x264-params "nal-hrd=cbr:force-cfr=1" -b:v:0 2M -maxrate:v:0 2M -minrate:v:0 2M -bufsize:v:0 4M -preset veryfast -g 60 -sc_threshold 0 -keyint_min 60 \
-map [v2out] -c:v:1 libx264 -x264-params "nal-hrd=cbr:force-cfr=1" -b:v:1 1M -maxrate:v:1 1M -minrate:v:1 1M -bufsize:v:1 2M -preset veryfast -g 60 -sc_threshold 0 -keyint_min 60 \
-map a:0 -c:a:0 aac -b:a:0 96k -ac 2 \
-map a:0 -c:a:1 aac -b:a:1 96k -ac 2 \
-f hls \
-hls_time 2 \
-hls_list_size 5 \
-hls_delete_threshold 1 \
-hls_flags independent_segments+delete_segments+temp_file \
-hls_segment_type mpegts \
-hls_segment_filename /home/dsi/5G-MAG/rt-wui/public/watchfolder/stream_%v_data%02d.ts \
-master_pl_name manifest.m3u8 \
-var_stream_map "v:0,a:0 v:1,a:1" /home/dsi/5G-MAG/rt-wui/public/watchfolder/stream_%v.m3u8