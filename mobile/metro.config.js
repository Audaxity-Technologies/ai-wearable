// Metro configuration.
//
// `expo-sqlite`'s web worker imports a .wasm binary, which Metro will not
// resolve unless wasm is registered as an asset extension. Without this the
// web bundle fails outright. Native builds are unaffected; this exists so the
// app can also be previewed in a browser.
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

config.resolver.assetExts.push('wasm');

// wa-sqlite uses SharedArrayBuffer on web, which browsers only expose to
// cross-origin-isolated pages.
config.server = config.server ?? {};
const previousEnhance = config.server.enhanceMiddleware;
config.server.enhanceMiddleware = (middleware, server) => {
  const base = previousEnhance ? previousEnhance(middleware, server) : middleware;
  return (req, res, next) => {
    res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
    res.setHeader('Cross-Origin-Embedder-Policy', 'credentialless');
    return base(req, res, next);
  };
};

module.exports = config;
