#!/bin/bash
# Build the whole MBMS stack the way someone else would: fresh clones, a stock container image, and
# only the packages the demo README tells them to install.
#
# This exists because rebuilding locally cannot answer "can anyone else build this". Every dependency
# is already present on a development machine, so an incomplete package list, a stale instruction or
# a library version only that machine has are all invisible there. A README step telling readers to
# apply a patch file that no longer existed was found this way.
#
# The package list is read out of the README rather than restated here, so this checks what readers
# are actually told to install. Cloning happens on the host, so credentials stay outside the
# container and a private-repo permission failure cannot be mistaken for a missing dependency.
#
#   ./check-build-from-clean.sh                     # everything
#   ./check-build-from-clean.sh --quick             # skip the two srsRAN-derived builds
#   ./check-build-from-clean.sh --image ubuntu:24.04
#   ./check-build-from-clean.sh --branch development
#   ./check-build-from-clean.sh --examples-checkout /path/to/rt-mbms-examples
# --examples-checkout exports the committed HEAD, excluding untracked files and local edits.
#
# What it does NOT check: the SoapySDR zmqrx bridge and the demo running. The bridge is built from
# source printed in the tutorial README, and running the chain needs a radio, sudo and a network
# namespace, none of which belong in a build check.
set -uo pipefail

IMAGE=ubuntu:26.04
BRANCH=feature/mbms-broadcast-demo
QUICK=0
EXAMPLES_CHECKOUT=""
WORK=${WORK:-$(mktemp -d)}
mkdir -p "$WORK" || { echo "cannot create $WORK" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --image)  IMAGE="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --examples-checkout) EXAMPLES_CHECKOUT="$2"; shift 2 ;;
        --quick)  QUICK=1; shift ;;
        --keep)   KEEP=1; shift ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
README="$HERE/mbms-broadcast-demo/README.md"
[ -r "$README" ] || { echo "cannot read $README" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

# The apt line as written in the README, so a package added there is picked up here with no edit.
python3 - "$README" > "$WORK/pkgs" <<'PY'
import re, sys
s = open(sys.argv[1]).read()
m = re.search(r'```bash\nsudo apt install (git build-essential.*?)\n```', s, re.S)
if not m:
    sys.exit("could not find the apt install block in the README")
print(' '.join(m.group(1).replace('\\\n', ' ').split()))
PY
[ -s "$WORK/pkgs" ] || exit 1
echo "package list from the README: $(wc -w < "$WORK/pkgs") packages"

SRC="$WORK/tree"
mkdir -p "$SRC/rt-mbms"
# Clone $BRANCH where the repository has it, and fall back to the repository's own default branch
# where it does not, reporting which was used. The components do not all carry the same branch: this
# demo's work touches some of them and not others, and a checker that demanded one name everywhere
# would fail on the untouched ones for a reason that has nothing to do with whether they build.
clone() {
    local repo="$1" parent="$2"; shift 2
    printf '  cloning %-32s' "$repo"
    if git clone --quiet -b "$BRANCH" "$@" "git@github.com:5G-MAG/$repo.git" "$parent/$repo" 2>/dev/null; then
        echo "$(git -C "$parent/$repo" rev-parse --short HEAD)  ($BRANCH)"
    elif git clone --quiet "$@" "git@github.com:5G-MAG/$repo.git" "$parent/$repo" 2>/dev/null; then
        if git -C "$parent/$repo" rev-parse --verify -q HEAD >/dev/null 2>&1; then
            echo "$(git -C "$parent/$repo" rev-parse --short HEAD)  ($(git -C "$parent/$repo" rev-parse --abbrev-ref HEAD), no $BRANCH)"
        else
            # An upstream repository that exists but has never been pushed to. Reported plainly,
            # because the build below will fail on a missing source directory and the reason would
            # otherwise look like a local mistake.
            echo "EMPTY upstream repository, nothing to build"
            rm -rf "$parent/$repo"
            return 0
        fi
    else
        echo "FAILED (no access, or empty repository)"
        return 1
    fi
}
echo "cloning at $BRANCH"
for r in rt-mbms-tx;                                                 do clone "$r" "$SRC/rt-mbms" || exit 1; done
# The modem carries srsRAN at lib/srsran, so it needs the same recursion as the other three: without
# it CMake stops at "does not contain a CMakeLists.txt file", which reads as a broken repository
# rather than as an unfetched submodule.
for r in rt-mbms-modem rt-mbms-gw rt-mbms-bmsc rt-mbms-client;       do clone "$r" "$SRC/rt-mbms" --recurse-submodules || exit 1; done
for r in rt-mbms-application rt-mbms-application-provider;           do clone "$r" "$SRC/rt-mbms" || exit 1; done
# The Cell Broadcast Centre is not an MBMS component and is not cloned into $SRC/rt-mbms: Public
# Warning System alerts reach handsets over the cell's own system information. It is checked here
# anyway because ./07-send-alert.sh needs it, and a reader who cannot build it cannot send one.
clone rt-pws-cbc "$SRC" || exit 1
if [ -n "$EXAMPLES_CHECKOUT" ]; then
    # Export the exact checkout without carrying its credentials, build output or untracked files
    # into the container.
    mkdir -p "$SRC/rt-mbms/rt-mbms-examples" || exit 1
    git -C "$EXAMPLES_CHECKOUT" archive HEAD | tar -x -C "$SRC/rt-mbms/rt-mbms-examples" || exit 1
    echo "rt-mbms-examples from checkout: $(git -C "$EXAMPLES_CHECKOUT" rev-parse --short HEAD)"
else
    clone rt-mbms-examples "$SRC/rt-mbms" || exit 1
fi

SKIP=""
[ "$QUICK" = 1 ] && SKIP="tx modem"

echo "building in $IMAGE"
docker run --rm -v "$SRC:/src" -v "$WORK:/out" "$IMAGE" bash -c '
set -u
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
if apt-get install -y -qq $(cat /out/pkgs) >/out/apt.log 2>&1; then
    echo "PASS  apt install"
else
    echo "FAIL  apt install"
    grep -iE "unable to locate|no installation candidate" /out/apt.log | head -5
    exit 1
fi
echo "using $(g++ --version | head -1), $(cmake --version | head -1)"

# Failures are recorded, not only printed, so this can exit non-zero. A check that always succeeds
# is worse than no check: it reads as evidence while proving nothing.
: > /out/failures
: > /out/stale

# Components already failing for a reason recorded elsewhere, so the run can still gate on anything
# NEW rather than being permanently red. The entry is predicated on the CAUSE, not the component
# name, and a known failure that starts passing is reported as stale and fails the run, so the entry
# has to be removed once the fix lands.
known_reason() {
    case "$1" in
        *) echo "" ;;
    esac
}
skip="'"$SKIP"'"
b() {
    n="$1"; d="$2"; shift 2
    case " $skip " in *" $n "*) echo "SKIP  $n"; return ;; esac
    [ -d "$d" ] || { echo "FAIL  $n (source directory missing)"; echo "$n" >> /out/failures; return; }
    why=$(known_reason "$n")
    if ( cd "$d" && eval "$@" ) > /out/$n.log 2>&1; then
        if [ -n "$why" ]; then
            echo "PASS  $n  -- was a known failure, remove it from known_reason()"
            echo "$n" >> /out/stale
        else
            echo "PASS  $n"
        fi
    elif [ -n "$why" ]; then
        echo "KNOWN $n  ($why)"
        grep -E "ERROR:|error:|undefined reference to|No such file" /out/$n.log | head -2 | sed "s/^/        /"
    else
        echo "FAIL  $n"
        echo "$n" >> /out/failures
        grep -E "ERROR:|error:|undefined reference to|No such file" /out/$n.log | head -3 | sed "s/^/        /"
    fi
}

# -j2, not -j$(nproc): the BM-SC and the client each compile translation units large enough that a
# wider build is killed by the OOM killer on a modest machine, which reads as a build failure.
b tx       /src/rt-mbms/rt-mbms-tx                   "cmake -S . -B build && cmake --build build -j2"
b gw       /src/rt-mbms/rt-mbms-gw                   "cmake -S . -B build && cmake --build build -j2"
b bmsc     /src/rt-mbms/rt-mbms-bmsc                 "cmake -S . -B build && cmake --build build -j2"
# -DCMAKE_POLICY_VERSION_MINIMUM=3.5: lib/srsran opens with cmake_minimum_required(VERSION 2.6) and
# CMake 4 refuses a minimum below 3.5. Kept identical to the modem row in the demo README, so this
# checks the documented command rather than a private one.
b modem    /src/rt-mbms/rt-mbms-modem                "cmake -S . -B build -DCMAKE_POLICY_VERSION_MINIMUM=3.5 && cmake --build build -j2"
b client   /src/rt-mbms/rt-mbms-client               "cmake -S . -B build && cmake --build build -j2"
b app      /src/rt-mbms/rt-mbms-application          "npm install"
b provider /src/rt-mbms/rt-mbms-application-provider "npm install"
# The origin is a dependency-free Node script, so there is nothing to install: check it parses.
b origin   /src/rt-mbms/rt-mbms-examples/scripts/mbms-broadcast-demo "node --check media-server.js"
b cbc      /src/rt-pws-cbc                            "npm install"
# The zmqrx SoapySDR module: not shipped by SoapySDR or by any component, so nothing receives
# without it. Built here because "clone and build the components" is not sufficient otherwise.
b zmqrx    /src/rt-mbms/rt-mbms-examples/scripts/soapy-zmq-bridge "./build.sh"

# No single quotes below: this whole block is inside a single-quoted bash -c, and one would close it.
nf=$(wc -l < /out/failures); ns=$(wc -l < /out/stale); n=$((nf + ns))
[ "$nf" -gt 0 ] && echo "--- $nf new failure(s):" $(cat /out/failures)
[ "$ns" -gt 0 ] && echo "--- $ns known failure(s) now passing, prune known_reason():" $(cat /out/stale)
[ "$n" -eq 0 ] && echo "--- everything attempted built, or failed only for a known and recorded reason"
exit "$n"
'
rc=$?

if [ "${KEEP:-0}" = 1 ]; then
    echo "logs and sources kept in $WORK"
else
    # The container writes its build trees as root, so removal needs the same.
    sudo -n rm -rf "$WORK" 2>/dev/null || rm -rf "$WORK" 2>/dev/null || echo "could not remove $WORK"
fi
exit $rc
