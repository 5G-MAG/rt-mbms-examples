<h1 align="center">FLUTE ffmpeg</h1>

## Introduction

The goal of this example project is to provide a tool that enables rt-mbms-mw development without the need for the
rt-mbms-modem. The basic idea is depicted in the illustration below:

![Architecture](files/wiki/flute-ffmpeg-architecture.png)

We use ffmpeg to create a DASH or HLS live stream from a VoD file. The resulting manifest files and segments are written to a
watchfolder and send via rt-libflute as a multicast to the rt-mbms-mw for further processing. rt-wui or a plain dash.js/hls.js
can be used for playback.

## Installation

### Install dependencies
To use this project several dependencies need to be installed:

#### Install Poco

We use the Poco directory watcher to implement the watchfolder behavior. In order to install Poco follow the
instructions [here](https://pocoproject.org/download.html).

In order to build from source:

````
git clone -b master https://github.com/pocoproject/poco.git
cd poco
mkdir cmake-build
cd cmake-build
cmake ..
cmake --build . --config Release
sudo cmake --build . --target install
````

#### Install libpistache

We use pistache as a REST framework to initialize the rt-mbms-mw. We expose only one route that returns the multicast
channel information.

````
sudo add-apt-repository ppa:pistache+team/unstable
sudo apt update
sudo apt install libpistache-dev
````

## Build

### Clone the repository

````
git clone --recurse-submodules https://github.com/5G-MAG/rt-mbms-examples
```` 

### Build setup

````
cd rt-mbms-examples/flute-ffmpeg
mkdir build && cd build
cmake -GNinja ..
````

### Building

````
ninja
````

This will output two files:  
* The watchfolder and FLUTE logic (`flute-ffmpeg`) 
* The simple webserver that provides the mulicast channel information to the middleware (`httpserver`).

## Configuration

Most of the parameters can directly be changed in the configuration file located at `config/config.cfg`. An example
configuration looks the following

````
general : {
          multicast_ip = "238.1.1.111";
          multicast_port = 40101;
          mtu = 1500;
          rate_limit = 1200000;
          watchfolder_path = "/home/dsi/5G-MAG/simple-express-server/public/watchfolder/hls";
          path_to_transmit = ""
          stream_type = "hls";
          transmit_service_announcement = false;
          dash: {
              number_of_init_segments = 3;
              resend_init_in_sec = 30;
              service_announcement = "../files/bootstrap.multipart.dash";
          };
          hls: {
              service_announcement = "../files/bootstrap.multipart.hls";
              media_playlists_to_ignore_in_multicast = []
          }
          webserver_port: 3010;
}
````

### Configure the stream format

We support both DASH and HLS with this sample implementation. Depending on the streaming format that you choose
the `stream_type` setting in the configuration needs to be adjusted accordingly.

### Configure watchfolder output path

We assume that the nginx proxy for rt-mbmbs-modem and rt-mbms-mw has been installed and is running. We reuse the nginx
as a watchfolder. Any other path can be used as well. Using the nginx as a watchfolder enables us to play the generated
DASH and HLS manifests and segments before FLUTE encoding them and multicasting to the rt-mbms-mw.

````
sudo mkdir /var/www/watchfolder_out
sudo chmod -R 777 /var/www/watchfolder_out
````

In case you are not using the default path the configuration file needs to be adjusted accordingly:

````
watchfolder_path = "path/to/folder";
````

### Configure the ffmpeg command

In order to generate a DASH or HLS stream we provide pre-configured ffmpeg scripts `files/ffmpeg-dash.sh`
and `files/ffmpeg-hls.sh` . In case the watchfolder was changed or a different input file should be used the script
needs to be adjusted accordingly.

### Configure the RESTful API

rt-mbms-mw requires a multicast channel information file that is usually queried from the REST API of the modem. As part
of this example project we use a separate webserver that provides the file. By default this server starts with the
default settings that are also used for the rt-mbms-modem.

Configuration changes can be made in `src/HttpHandler.cpp` and `main_server.cpp`.

## Running

#### 1. Start the rt-mbms-mw

See our [Wiki](https://github.com/5G-MAG/Documentation-and-Architecture/wiki/MBMS-Middleware) for details

Important: In order for the manifest files and the media segments to be available from the Middleware flute_ffmpeg
processing needs to be enabled in the configuration of the MW. In addition, we need to define a 60 second `max_file_age` 
for the middleware cache:

Open the configuration file:

````
sudo nano /etc/5gmag-rt.conf 
````

Adjust the configuration accordingly:

````
mw: {
  flute_ffmpeg: {
    enabled:true
  },
  cache: {
    max_file_age: 60,
    max_total_size: 256
  }
}
````

#### 2. Start the HTTP Server

````
cd build
./httpserver
````

#### 3. Start FLUTE ffmpeg

````
cd build
./flute-ffmpeg
````

#### 4. Start the DASH/HLS stream

````
cd files
sh ffmpeg-dash.sh
````

````
cd files
sh ffmpeg-hls.sh
````

#### 4. Start the rt-wui

See our [Wiki](https://github.com/5G-MAG/Documentation-and-Architecture/wiki/Webinterface) for details

#### Other players

The streams can also be played outside of the rt-wui for instance in a plain dash.js or hls.js.


