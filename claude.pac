// Windows browser (Helium/Chrome/Firefox) proxy auto-config.
// Routes only Claude/Anthropic domains through the Bluetooth bridge; everything
// else goes DIRECT (the ~1 Mbps link is too slow for general browsing).
// Use it via:  helium.exe --proxy-pac-url="file:///C:/Users/<you>/claude.pac"
// The proxy endpoint is the WSL browser listener, reachable from Windows at
// localhost:8888 (WSL2 forwards it) -> Bluetooth -> Linux tinyproxy -> internet.
function FindProxyForURL(url, host) {
    if (dnsDomainIs(host, ".anthropic.com") || host == "anthropic.com" ||
        dnsDomainIs(host, ".claude.ai")     || host == "claude.ai"     ||
        dnsDomainIs(host, ".claude.com")    || host == "claude.com") {
        return "PROXY 127.0.0.1:8888";
    }
    return "DIRECT";
}
