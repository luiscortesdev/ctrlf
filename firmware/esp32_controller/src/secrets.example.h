// Wi-Fi for the pointer. Copy this file to secrets.h (same folder) and fill it in.
// secrets.h is in .gitignore: never put it in the GitHub repo (it's public).
//
// The ESP32 only does 2.4 GHz Wi-Fi: on an iPhone hotspot, turn on Maximize Compatibility.
// The Mac must be on the same network. Wi-Fi with a login page, or with a username
// (eduroam, UFW), won't work: use a phone hotspot.

// Networks to try, in order. A network with an empty name is skipped.
const char *WIFI_NETWORKS[][2] = {
  {"", ""},  // {"network name", "password"}
  {"", ""},
};

// ctrlf.py finds the board by itself; it's also <HOSTNAME>.local (e.g. ping ctrlf.local).
#define HOSTNAME "ctrlf"

// Only someone with this key can move the head or switch the laser on over Wi-Fi.
// Any random letters and digits. ctrlf.py reads it from secrets.h, so they always match.
#define LINK_KEY "change-me"
