// 5G-MAG Reference Tools
// Flute ffmpeg
// Daniel Silhavy
//
// Licensed under the License terms and conditions for use, reproduction, and
// distribution of 5G-MAG software (the “License”).  You may not use this file
// except in compliance with the License.  You may obtain a copy of the License at
// https://www.5g-mag.com/reference-tools.  Unless required by applicable law or
// agreed to in writing, software distributed under the License is distributed on
// an “AS IS” BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
// or implied.
//
// See the License for the specific language governing permissions and limitations
// under the License.
//
#ifndef WATCHFOLDER_FLUTEFFMPEG_H
#define WATCHFOLDER_FLUTEFFMPEG_H

#include <iostream>
#include <boost/asio.hpp>
#include <libconfig.h++>
#include "Poco/DirectoryWatcher.h"
#include "Transmitter.h"

class FluteFfmpeg {
private:
  std::unique_ptr<LibFlute::Transmitter> _transmitter;
  bool _sa_sent = false;
  const libconfig::Config &_cfg;
  std::string _transmitter_multicast_ip = "238.1.1.95";
  unsigned _transmitter_multicast_port = 40085;
  unsigned _mtu = 1500;
  uint32_t _rate_limit = 50000;
  std::string _watchfolder_path = "/var/www/watchfolder_out";
  std::string _service_announcement = "../files/bootstrap.multipart.dash";
  unsigned _number_of_dash_init_segments = 3;
  unsigned _resend_dash_init_in_sec = 20;
  std::chrono::time_point<std::chrono::high_resolution_clock> _last_send_init_time;
  std::string DASH_CONTENT_TYPE = "application/dash+xml";

  void on_file_renamed(const Poco::DirectoryWatcher::DirectoryEvent &changeEvent);

  void send_by_flute(const std::string &path, std::string content_type);

  void send_init_segments();

  void send_service_announcement();

public:
  boost::asio::io_service io_service;

  FluteFfmpeg(const libconfig::Config &cfg);

  void register_directory_watcher();
};


#endif //WATCHFOLDER_FLUTEFFMPEG_H
