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

#include "HttpHandler.h"
#include <pistache/endpoint.h>
#include "spdlog/spdlog.h"
#include <pistache/http.h>
#include <pistache/http_headers.h>

using namespace Pistache;

void HttpHandler::onRequest(const Http::Request &request, Http::ResponseWriter response) {
    if (request.resource() == "/modem-api/mch_info") {
        response.headers()
            .add<Http::Header::ContentType>("application/json");
        Http::serveFile(response, "../files/mch_info.json")
                .then([](ssize_t bytes) {
                          spdlog::info("Sent {} bytes", bytes);
                      },Async::NoExcept);
    } else {
        response.send(Http::Code::Not_Found);
    }
}