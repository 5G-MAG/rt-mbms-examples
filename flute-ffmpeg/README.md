# rt-mbms-examples - FLUTE FFMPEG

## Installation

### Install dependencies

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

#### Clone the repository

````
git clone --recurse-submodules https://github.com/5G-MAG/rt-mbms-examples
```` 

### Configuration

#### Configure watchfolder output path

We assume that the nginx proxy for rt-mbmbs-modem and rt-mbms-mw has been installed and is running. We reuse the nginx
as a watchfolder. Any other path can be used as well. Using the nginx as a watchfolder enables us to play the generated
DASH manifest and segments before FLUTE encoding them and multicasting to the rt-mbms-mw.

````
sudo mkdir /var/www/watchfolder_out
sudo chmod -R 777 /var/www/watchfolder_out
````

In case you are not using the default path the configuration file needs to be adjusted accordingly:

````
watchfolder_path = "path/to/folder";
````

#### Configure the ffmpeg command

In order to generate a DASH stream we provide a pre-configured ffmpeg script `ffmpeg.sh`. In case the watchfolder was
changed or a different input file should be used the script needs to be adjusted accordingly.

#### Configure the RESTful API

rt-mbms-mw requires a multicast channel information file that is usually queried from the REST API of the modem. As part
of this example project we use a separate webserver that provides the file. By default this server starts with the
default settings that are also used for the rt-mbms-modem.

Configuration changes can be made in `HttpHandler.cpp` and `main.cpp` of the `httpserver` project.

### MBMS-MW Output

Starting FLUTE receiver on 238.1.1.95:40085 for TSI 0