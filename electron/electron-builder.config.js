/**
 * electron-builder configuration.
 * Using a JS file so we can read APPLE_TEAM_ID from the environment —
 * package.json doesn't support env var interpolation.
 */
module.exports = {
  appId: "org.jenkin.sf-bulk-loader",
  productName: "Salesforce Bulk Loader",
  files: ["main.js", "preload.js"],
  extraResources: [
    { from: "../backend/dist/sf_bulk_loader", to: "backend/sf_bulk_loader" },
    { from: "../frontend/dist", to: "frontend/dist" },
  ],
  mac: {
    icon: "build/icon.icns",
    target: [{ target: "zip", arch: ["arm64"] }],
    category: "public.app-category.developer-tools",
    hardenedRuntime: true,
    entitlements: "entitlements.mac.plist",
    entitlementsInherit: "entitlements.mac.plist",
    // Notarize only when secrets are present (release CI). Credentials are
    // read from APPLE_ID and APPLE_APP_SPECIFIC_PASSWORD env vars by electron-builder.
    notarize: process.env.APPLE_TEAM_ID ? { teamId: process.env.APPLE_TEAM_ID } : false,
  },
  win: {
    icon: "build/icon.ico",
    target: [{ target: "nsis", arch: ["x64"] }],
  },
  linux: {
    icon: "build/icon.png",
    target: [{ target: "AppImage", arch: ["x64"] }],
    category: "Development",
  },
};
