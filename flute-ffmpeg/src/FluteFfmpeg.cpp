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

#include "FluteFfmpeg.h"
#include "spdlog/spdlog.h"
#include "Poco/Delegate.h"
#include <fstream>


FluteFfmpeg::FluteFfmpeg(const libconfig::Config &cfg)
    : _cfg(cfg) {
  _cfg.lookupValue("general.multicast_ip", _transmitter_multicast_ip);
  _cfg.lookupValue("general.multicast_port", _transmitter_multicast_port);
  _cfg.lookupValue("general.mtu", _mtu);
  _cfg.lookupValue("general.rate_limit", _rate_limit);
  _cfg.lookupValue("general.watchfolder_path", _watchfolder_path);
  _cfg.lookupValue("general.service_announcement", _service_announcement);
  _cfg.lookupValue("general.number_of_dash_init_segments", _number_of_dash_init_segments);
  _transmitter = std::make_unique<LibFlute::Transmitter>(_transmitter_multicast_ip, _transmitter_multicast_port, 0,
                                                         _mtu, _rate_limit, io_service);
}

void FluteFfmpeg::send_by_flute(const std::string &path, std::string content_type = "video/mp4") {
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  std::streamsize size = file.tellg();
  file.seekg(0, std::ios::beg);

  char *buffer = (char *) malloc(size);
  file.read(buffer, size);

  if (path == _watchfolder_path + "index.mpd") {
    content_type = DASH_CONTENT_TYPE;
  }


  uint32_t toi = _transmitter->send(path,
                                    content_type,
                                    _transmitter->seconds_since_epoch() + 60, // 1 minute from now
                                    buffer,
                                    (size_t) size
  );

  spdlog::info("Queued {}  for transmission, TOI is {}", path, toi);
}

void FluteFfmpeg::on_file_renamed(const Poco::DirectoryWatcher::DirectoryEvent &changeEvent) {
  std::string path = changeEvent.item.path();
  spdlog::debug("File renamed: Name: {} ", path);

  if (!_sa_sent) {
    send_service_announcement();
    send_init_segments();
    _sa_sent = true;
  }

  send_by_flute(path);
}

void FluteFfmpeg::send_service_announcement() {
  spdlog::info("Sending Service Announcement");
  send_by_flute(_service_announcement, "application/mbms-user-service-description+xml");
}

void FluteFfmpeg::send_init_segments() {
  spdlog::info("Sending init segments");
  for (int i = 0; i < _number_of_dash_init_segments; i++) {
    std::string init_path = _watchfolder_path + "/init-stream" + std::to_string(i) + ".m4s";
    send_by_flute(init_path);
  }

}

void FluteFfmpeg::register_directory_watcher() {
  spdlog::info("Setting up file watcher at {}", _watchfolder_path);
  try {
    auto *watcher = new Poco::DirectoryWatcher(_watchfolder_path);

    watcher->itemMovedTo += Poco::delegate(this, &FluteFfmpeg::on_file_renamed);
  }
  catch (const Poco::FileNotFoundException &error) {
    spdlog::error("Path at {} does not exist. Please create the path first", _watchfolder_path);
    exit(1);
  }
}
