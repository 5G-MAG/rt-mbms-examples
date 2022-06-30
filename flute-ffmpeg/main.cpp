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

#include <cstdlib>
#include <string>
#include <libconfig.h++>
#include "src/FluteFfmpeg.h"
#include "spdlog/spdlog.h"

using namespace std;
using namespace libconfig;
using libconfig::FileIOException;
using libconfig::ParseException;

const char *CONFIG_FILE = "../config/config.cfg";


int main() {
  Config cfg;

  // Read and parse the configuration file
  try {
    cfg.readFile(CONFIG_FILE);
  } catch (const FileIOException &fioex) {
    spdlog::error("I/O error while reading config file at {}. Exiting.", CONFIG_FILE);
    exit(1);
  } catch (const ParseException &pex) {
    spdlog::error("Config parse error at {}:{} - {}. Exiting.", pex.getFile(), pex.getLine(), pex.getError());
    exit(1);
  }

  // Start FluteFfmpeg
  auto *ff = new FluteFfmpeg(cfg);
  ff->register_directory_watcher();
  ff->io_service.run();
}


