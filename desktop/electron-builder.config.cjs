/**
 * electron-builder configuration: self-contained installers for offline
 * field laptops.
 *
 *   npm run package:win     NSIS installer (.exe), x64
 *   npm run package:mac     DMG for the host architecture (arm64 or x64)
 *   npm run package:linux   AppImage (+ .deb)
 *
 * The frozen spatial engine (resources/engine, built by
 * `python ../packaging/build_engine.py` on the *same* OS/architecture) is
 * shipped as an extra resource, so the installed app needs no Python,
 * Node.js or network access.
 *
 * Set LIVESTOCK_TEST_BUILD=1 to skip the Electron fuse hardening; the
 * Playwright end-to-end test needs the inspector the fuses disable.
 */

const testBuild = process.env.LIVESTOCK_TEST_BUILD === '1'

/** @type {import('electron-builder').Configuration} */
module.exports = {
  appId: 'com.livestockcounter.desktop',
  productName: 'Livestock Counter',
  copyright: 'Copyright © Livestock Counter',
  directories: { output: 'release', buildResources: 'build' },
  files: ['out/**/*', 'package.json'],
  extraResources: [{ from: 'resources/engine', to: 'engine', filter: ['**/*'] }],
  asar: true,
  compression: 'maximum',
  beforePack: './scripts/verify-engine.cjs',
  afterPack: './scripts/after-pack.cjs',
  // Field laptops are offline: no auto-update feed or update metadata.
  publish: null,
  artifactName: 'LivestockCounter-${version}-${os}-${arch}.${ext}',

  electronFuses: testBuild
    ? null
    : {
        runAsNode: false,
        enableCookieEncryption: true,
        enableNodeOptionsEnvironmentVariable: false,
        enableNodeCliInspectArguments: false,
        enableEmbeddedAsarIntegrityValidation: true,
        onlyLoadAppFromAsar: true,
        grantFileProtocolExtraPrivileges: false
      },

  win: {
    target: [{ target: 'nsis', arch: ['x64'] }],
    // Code signing: set CSC_LINK / CSC_KEY_PASSWORD in CI to sign.
    signAndEditExecutable: true
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowElevation: true,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: 'Livestock Counter',
    deleteAppDataOnUninstall: false
  },

  mac: {
    target: [{ target: 'dmg' }],
    category: 'public.app-category.productivity',
    // With a Developer ID certificate (CSC_LINK / CSC_NAME) the app is signed
    // properly; otherwise it is ad-hoc signed so it still launches on Apple
    // Silicon (Gatekeeper then asks the user to allow it once).
    identity: process.env.CSC_LINK || process.env.CSC_NAME ? undefined : '-',
    hardenedRuntime: true,
    gatekeeperAssess: false,
    // The frozen engine loads native Python extension modules.
    entitlements: 'build/entitlements.mac.plist',
    entitlementsInherit: 'build/entitlements.mac.plist',
    // Notarisation runs only when APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID are set.
    notarize: Boolean(process.env.APPLE_ID && process.env.APPLE_TEAM_ID)
  },
  dmg: {
    writeUpdateInfo: false,
    contents: [
      { x: 140, y: 190, type: 'file' },
      { x: 400, y: 190, type: 'link', path: '/Applications' }
    ]
  },

  linux: {
    target: ['AppImage', 'deb'],
    category: 'Science',
    executableName: 'livestock-counter',
    syncDesktopName: true,
    maintainer: 'Livestock Counter',
    synopsis: 'Offline livestock headcount from overlapping drone surveys'
  }
}
