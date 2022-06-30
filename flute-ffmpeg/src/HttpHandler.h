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

#ifndef WATCHFOLDER_HTTPHANDLER_H
#define WATCHFOLDER_HTTPHANDLER_H

#include <pistache/endpoint.h>

using namespace Pistache;

class HttpHandler : public Http::Handler {

HTTP_PROTOTYPE(HttpHandler)

    void onRequest(const Http::Request &request, Http::ResponseWriter response);
};


#endif //WATCHFOLDER_HTTPHANDLER_H
