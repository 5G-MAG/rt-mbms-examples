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
  _cfg.lookupValue("general.dash.number_of_init_segments", _number_of_dash_init_segments);
  _cfg.lookupValue("general.dash.resend_init_in_sec", _resend_dash_init_in_sec);
  _cfg.lookupValue("general.stream_type", _stream_type);
  _cfg.lookupValue("general.transmit_service_announcement", _transmit_service_announcement);
  _transmitter = std::make_unique<LibFlute::Transmitter>(_transmitter_multicast_ip, _transmitter_multicast_port, 0,
                                                         _mtu, _rate_limit, io_service);

  std::string sa_path =
      _stream_type == "dash" ? "general.dash.service_announcement" : "general.hls.service_announcement";
  _cfg.lookupValue(sa_path, _service_announcement);

}

void FluteFfmpeg::send_by_flute(const std::string &path, std::string content_type = "video/mp4") {
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  std::streamsize size = file.tellg();
  file.seekg(0, std::ios::beg);

  char *buffer = (char *) malloc(size);
  file.read(buffer, size);

  if (path.find("m3u8") && (!path.find("index.m3u8") || !path.find("stream_0.m3u8"))) {
    return;
  }

  if (path.find(".mpd") != std::string::npos) {
    content_type = DASH_CONTENT_TYPE;
  } else if (path.find(".m3u8") != std::string::npos) {
    content_type = HLS_CONTENT_TYPE;
  }

  uint32_t toi = _transmitter->send(path,
                                    content_type,
                                    _transmitter->seconds_since_epoch() + 60, // 1 minute from now
                                    buffer,
                                    (size_t) size
  );

  spdlog::info("Queued {}  for transmission, TOI is {}", path, toi);
}

void FluteFfmpeg::process_file(const Poco::DirectoryWatcher::DirectoryEvent &directoryEvent) {
  std::string path = directoryEvent.item.path();

  if (!_first_transmit_iteration) {
    if (_transmit_service_announcement) {
      send_service_announcement();
    }
    _first_transmit_iteration = true;

    if (_stream_type == "dash") {
      send_dash_init_segments();
    }
  }

  // Resend DASH Init segments periodically
  auto now = std::chrono::high_resolution_clock::now();
  if (_stream_type == "dash" &&
      std::chrono::duration<double, std::milli>(now - _last_send_init_time).count() > _resend_dash_init_in_sec * 1000) {
    send_dash_init_segments();
  }

  send_by_flute(path);
}

void FluteFfmpeg::on_file_renamed(const Poco::DirectoryWatcher::DirectoryEvent &changeEvent) {
  std::string path = changeEvent.item.path();
  spdlog::info("File renamed: Name: {} ", path);

  process_file(changeEvent);
}

void FluteFfmpeg::send_service_announcement() {
  spdlog::info("Sending Service Announcement");
  send_by_flute(_service_announcement, "application/mbms-user-service-description+xml");
}

void FluteFfmpeg::send_dash_init_segments() {
  try {
    spdlog::info("Sending init segments");
    for (int i = 0; i < _number_of_dash_init_segments; i++) {
      std::string init_path = _watchfolder_path + "/init-stream" + std::to_string(i) + ".m4s";
      send_by_flute(init_path);
    }
    _last_send_init_time = std::chrono::high_resolution_clock::now();
  }
  catch (...) {
    spdlog::error("Error sending the DASH init segments");
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
